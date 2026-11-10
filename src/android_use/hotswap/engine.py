"""Hot swap code into a running, debuggable Android app.

Uses only the JDK, the Android SDK/NDK and (for Kotlin sources) a Kotlin compiler.
Standard library only, and nothing is printed outside main(): the MCP server calls
run_action() in process, where stdout belongs to the protocol.

  hotswap -p PROJECT status  toolchain and project check; changes nothing
  hotswap -p PROJECT build   full build, install, launch
  hotswap -p PROJECT swap    push source edits into the running process
  hotswap -p PROJECT watch   swap on every save
  hotswap -p GRADLE_ROOT gradle-setup [--module :app] [--variant Debug]

A Gradle project needs no setup: its root (or a module directory below it) is
recognised and Gradle keeps doing the full builds, while its own compiler settings are
reused for the swaps. A plain project holds a hand-written hotswap.json (see
script/hotswap/app/hotswap.json). A swap compiles the edited files,
finds the classes whose bytes differ from what the process runs, and has a JVMTI agent
redefine them in place. Edited resources are relinked and loaded into the process the
same way. Whatever cannot be swapped (manifest, build files, hierarchy changes, a dead
process) falls back to a full build and reinstall.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent  # agent, helper and daemon sources live here
ABI_TRIPLES = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "armv7a-linux-androideabi",
    "x86_64": "x86_64-linux-android",
    "x86": "i686-linux-android",
}


class Fail(Exception):
    def __init__(self, message, code="hotswap_failed"):
        super().__init__(message)
        self.code = code


def cache_dir():
    """Built agent, helper and daemon: outside the package, which may be read-only."""
    base = os.environ.get("ANDROID_USE_HOTSWAP_CACHE")
    if not base:
        state = os.environ.get("ANDROID_USE_STATE_DIR", "~/.local/share/android-use")
        base = Path(state).expanduser() / "hotswap"
    return Path(base)


def sdk_dir():
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(name):
            return Path(os.environ[name])
    home = Path.home()
    found = [p for p in (home / "Library/Android/sdk", home / "Android/Sdk") if p.is_dir()]
    return found[0] if found else home / "Library/Android/sdk"


def jvmti_header():
    """The NDK has no jvmti.h; any JDK's copy will do."""
    if os.environ.get("JVMTI_H"):
        return os.environ["JVMTI_H"]
    homes = [os.environ.get("JAVA_HOME", ""), str(Path.home() / "Library/Java"),
             "/Library/Java/JavaVirtualMachines", "/usr/lib/jvm"]
    return next((str(p) for h in homes if h and Path(h).is_dir()
                 for p in Path(h).rglob("jvmti.h")), None)


def run(cmd, check=True, **kwargs):
    kwargs.setdefault("timeout", 900)  # e.g. adb waiting forever for an unplugged device
    try:
        done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kwargs)
    except OSError as error:
        raise Fail(f"cannot run {cmd[0]}: {error}", "toolchain_missing") from None
    except subprocess.TimeoutExpired:
        raise Fail(f"{Path(str(cmd[0])).name} timed out (device disconnected?)") from None
    if check and done.returncode:
        raise Fail(f"{Path(str(cmd[0])).name} failed:\n{(done.stdout + done.stderr).strip()}")
    return done


def newest(directory):
    def key(path):
        return [int(n) for n in re.findall(r"\d+", path.name)]

    directory = Path(directory)
    items = sorted((p for p in directory.iterdir() if p.is_dir()), key=key) if (
        directory.is_dir()) else []
    if not items:
        raise Fail(f"nothing installed under {directory}", "toolchain_missing")
    return items[-1]


class Daemon:
    """Client of daemon/HotswapDaemon.java: the compilers, kept warm in one JVM."""

    def __init__(self, d8_jar):
        source = HERE / "daemon/HotswapDaemon.java"
        digest = hashlib.sha1(source.read_bytes() + str(d8_jar).encode()).hexdigest()[:12]
        self.dir = cache_dir() / "daemon" / digest
        self.port_file = self.dir / "port"
        if not (self.dir / "HotswapDaemon.class").exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            run(["javac", "-nowarn", "-cp", d8_jar, "-d", self.dir, source])
        try:
            self.call("ping", [])
        except (OSError, ValueError):
            self.port_file.unlink(missing_ok=True)
            with open(self.dir / "daemon.log", "ab") as log:
                subprocess.Popen(["java", "-cp", f"{self.dir}{os.pathsep}{d8_jar}", "HotswapDaemon",
                                  str(self.port_file)], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, start_new_session=True)
            deadline = time.time() + 15
            while not self.port_file.exists():
                if time.time() > deadline:
                    raise Fail("compiler daemon did not start") from None
                time.sleep(0.05)

    def call(self, tool, args):
        port, token = self.port_file.read_text().split()
        with socket.create_connection(("127.0.0.1", int(port)), timeout=5) as link:
            link.settimeout(600)
            message = b""
            for text in (token, tool):
                raw = text.encode()
                message += struct.pack(">I", len(raw)) + raw
            message += struct.pack(">I", len(args))
            for text in args:
                raw = str(text).encode()
                message += struct.pack(">I", len(raw)) + raw
            link.sendall(message)
            reply = b""
            while True:
                chunk = link.recv(65536)
                if not chunk:
                    break
                reply += chunk
        if len(reply) < 8:
            raise ValueError("short reply from compiler daemon")
        code, length = struct.unpack(">iI", reply[:8])
        return code, reply[8:8 + length].decode(errors="replace")


class Tools:
    def __init__(self, serial, use_daemon=True):
        self.sdk = sdk_dir()
        self.build_tools = newest(self.sdk / "build-tools")
        self.serial = serial or os.environ.get("ANDROID_SERIAL")
        self.adb_path = os.environ.get("ANDROID_USE_ADB") or shutil.which("adb") or "adb"
        self.use_daemon = use_daemon
        self.daemon = None
        self.touched = False  # True once something was installed on or pushed to the device

    def invoke(self, tool, command, args):
        """Run a compiler in the warm daemon, or as a fresh process when that fails."""
        if self.use_daemon:
            try:
                self.daemon = self.daemon or Daemon(self.build_tools / "lib/d8.jar")
                code, output = self.daemon.call(tool, [str(a) for a in args])
            except (OSError, ValueError, Fail):
                self.use_daemon = False
            else:
                if code:
                    raise Fail(f"{tool} failed:\n{output.strip()}", self.failure(tool))
                return
        try:
            run([*command, *(args[1:] if tool == "kotlinc" else args)])
        except Fail as error:
            if error.code == "hotswap_failed":
                error.code = self.failure(tool)
            raise

    @staticmethod
    def failure(tool):
        return "compile_failed" if tool in ("javac", "kotlinc") else "hotswap_failed"

    def javac(self, args):
        self.invoke("javac", ["javac"], args)

    def d8(self, args):
        self.invoke("d8", [self.tool("d8")], args)

    def kotlin(self, cfg, args):
        command = self.kotlinc(cfg)
        if cfg.get("kotlin_compiler_classpath"):
            compiler, extra = cfg["kotlin_compiler_classpath"], []
        else:
            home = Path(command[-1]).resolve().parent.parent
            compiler = sorted(str(p) for p in (home / "lib").glob("*.jar") if re.match(
                r"kotlin-(compiler|stdlib|reflect|script-runtime)\.jar|kotlinx-coroutines-core|"
                r"trove4j|annotations-", p.name))
            extra = ["-kotlin-home", str(home)]
        self.invoke("kotlinc", command, [os.pathsep.join(compiler), *extra, *args])

    def tool(self, name):
        return self.build_tools / name

    def android_jar(self, cfg):
        if cfg.get("boot"):
            return Path(cfg["boot"][0])
        return newest(self.sdk / "platforms") / "android.jar"

    def kotlinc(self, cfg):
        """Command prefix that runs the Kotlin JVM compiler."""
        if cfg.get("kotlin_compiler_classpath"):
            return ["java", "-cp", os.pathsep.join(cfg["kotlin_compiler_classpath"]),
                    "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler"]
        found = os.environ.get("KOTLINC") or shutil.which("kotlinc") or (
            "/Applications/Android Studio.app/Contents/plugins/Kotlin/kotlinc/bin/kotlinc")
        if not Path(found).exists():
            raise Fail("Kotlin sources need a compiler: set KOTLINC", "toolchain_missing")
        # Android Studio ships its launcher script without the executable bit.
        return [found] if os.access(found, os.X_OK) else ["bash", found]

    def kotlin_stdlib(self, cfg):
        return Path(self.kotlinc(cfg)[-1]).resolve().parent.parent / "lib/kotlin-stdlib.jar"

    def adb(self, *args, check=True, **kwargs):
        kwargs.setdefault("timeout", 180)
        return run([self.adb_path, *(["-s", self.serial] if self.serial else []), *args],
                   check, **kwargs)

    def shell(self, command, check=True):
        return self.adb("shell", command, check=check).stdout.strip()

    def helper(self, cfg):
        """Build the in-app helper class as a dex; return (path, content hash)."""
        sources = sorted((HERE / "helper").rglob("*.java"))
        digest = hashlib.sha1(b"".join(p.read_bytes() for p in sources)).hexdigest()[:12]
        out = cache_dir() / "helper" / digest
        if not (out / "classes.dex").exists():
            (out / "classes").mkdir(parents=True, exist_ok=True)
            android_jar = self.android_jar(cfg)
            self.javac(["-nowarn", "-Xlint:-options", "-proc:none", "-source", "8", "-target", "8",
                        "-bootclasspath", android_jar, "-d", out / "classes", *sources])
            self.d8(["--lib", android_jar, "--min-api", "26", "--output", out,
                     *(out / "classes").rglob("*.class")])
        return out / "classes.dex", digest

    def agent(self):
        """Build the agent for the device ABI; return (path, content hash)."""
        abi = self.shell("getprop ro.product.cpu.abi")
        source = HERE / "agent/agent.cpp"
        if abi not in ABI_TRIPLES:
            raise Fail(f"unsupported device ABI {abi!r}", "device_error")
        version = hashlib.sha1(source.read_bytes()).hexdigest()[:12]
        out = cache_dir() / "agent" / version / abi / "libhotswap.so"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            ndk = newest(self.sdk / "ndk")
            cxx = newest(ndk / "toolchains/llvm/prebuilt") / "bin/clang++"
            # Take jvmti.h from a JDK but keep the NDK's own jni.h.
            header = jvmti_header()
            if not header:
                raise Fail("jvmti.h not found in any JDK; set JVMTI_H", "toolchain_missing")
            shutil.copy(header, out.parent / "jvmti.h")
            # Static libc++: the app process has no libc++_shared.so to resolve against.
            run([cxx, f"--target={ABI_TRIPLES[abi]}26", "-std=c++17", "-shared", "-fPIC", "-O2",
                 "-static-libstdc++", "-I", out.parent, source, "-llog", "-o", out])
        return out, hashlib.sha256(out.read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------- class files

def class_abi(data):
    """Hash of what other classes can depend on: hierarchy and non-private members."""
    def u2(at):
        return struct.unpack_from(">H", data, at)[0]

    count, pos, pool, index = u2(8), 10, {}, 1
    while index < count:
        tag = data[pos]
        if tag == 1:
            length = u2(pos + 1)
            pool[index] = data[pos + 3:pos + 3 + length]
            pos += 3 + length
        elif tag in (3, 4):
            pool[index] = data[pos + 1:pos + 5]
            pos += 5
        elif tag in (5, 6):
            pool[index] = data[pos + 1:pos + 9]
            pos += 9
            index += 1
        elif tag in (7, 8, 16, 19, 20):
            pool[index] = u2(pos + 1)
            pos += 3
        elif tag in (9, 10, 11, 12, 17, 18):
            pos += 5
        elif tag == 15:
            pos += 4
        else:
            raise Fail(f"unknown constant pool tag {tag}")
        index += 1

    def name(at):
        value = pool.get(at)
        return pool.get(value, b"") if isinstance(value, int) else value

    shape = [u2(pos), name(u2(pos + 2)), name(u2(pos + 4))]
    interfaces = u2(pos + 6)
    shape.append(sorted(name(u2(pos + 8 + 2 * i)) for i in range(interfaces)))
    pos += 8 + 2 * interfaces
    for _ in range(2):  # fields, then methods
        members = []
        total = u2(pos)
        pos += 2
        for _ in range(total):
            flags, member, descriptor, attributes = (u2(pos + i) for i in (0, 2, 4, 6))
            pos += 8
            constant = None
            for _ in range(attributes):
                length = struct.unpack_from(">I", data, pos + 2)[0]
                if pool.get(u2(pos)) == b"ConstantValue":  # inlined into callers by javac
                    constant = name(u2(pos + 6))
                pos += 6 + length
            if not flags & 0x0002:  # private members are invisible to other classes
                members.append((flags, pool[member], pool[descriptor], constant))
        shape.append(sorted(members, key=repr))
    return hashlib.sha1(repr(shape).encode()).hexdigest()


def compose_shape(data):
    """(calls, keys) for one class file: every call it makes on the Compose Composer, in
    order, and the group keys of its restartable composable functions.

    The calls decide where remembered values live. If an edit leaves them unchanged,
    the function can simply run again over its existing state; if not, its groups
    (the keys) have to be reset, or old values would be read back in the wrong places.
    """
    def u2(at):
        return struct.unpack_from(">H", data, at)[0]

    count, pos, index = u2(8), 10, 1
    utf, classes, pairs, methods, integers = {}, {}, {}, {}, {}
    calls, keys = [], []
    while index < count:
        tag = data[pos]
        if tag == 1:
            length = u2(pos + 1)
            utf[index] = data[pos + 3:pos + 3 + length]
            pos += 3 + length
        elif tag == 3:
            integers[index] = struct.unpack_from(">i", data, pos + 1)[0]
            pos += 5
        elif tag == 4:
            pos += 5
        elif tag in (5, 6):
            pos += 9
            index += 1
        elif tag in (7, 8, 16, 19, 20):
            if tag == 7:
                classes[index] = u2(pos + 1)
            pos += 3
        elif tag in (9, 10, 11, 12, 17, 18):
            if tag == 11:
                methods[index] = (u2(pos + 1), u2(pos + 3))
            elif tag == 12:
                pairs[index] = u2(pos + 1)
            pos += 5
        elif tag == 15:
            pos += 4
        else:
            return calls, keys
        index += 1
    targets = {struct.pack(">H", i): utf.get(pairs.get(pair)) for i, (owner, pair) in methods.items()
               if utf.get(classes.get(owner)) == b"androidx/compose/runtime/Composer"}
    at = data.find(b"\xb9", pos)
    while at != -1:  # invokeinterface <method> <count> 0, maybe after ldc / ldc_w <int>
        name = targets.get(data[at + 1:at + 3])
        if name and data[at + 4] == 0:
            constant = None
            if data[at - 2] == 0x12 and data[at - 1] in integers:
                constant = integers[data[at - 1]]
            elif data[at - 3] == 0x13 and u2(at - 2) in integers:
                constant = integers[u2(at - 2)]
            calls.append((name, constant))
            if name == b"startRestartGroup" and constant is not None:
                keys.append(constant)
        at = data.find(b"\xb9", at + 1)
    return calls, keys


def class_files(root):
    root = Path(root)
    return {str(p.relative_to(root))[:-6].replace(os.sep, "."): p for p in root.rglob("*.class")}


def digests(root):
    return {n: hashlib.sha1(p.read_bytes()).hexdigest() for n, p in class_files(root).items()}


# -------------------------------------------------------------------- project

class Project:
    def __init__(self, directory, tools):
        self.dir = Path(directory).resolve()
        self.tools = tools
        self.work = self.dir / "build/hotswap"
        self.current = self.work / "current"   # classes matching the sources
        self.state_path = self.work / "state.json"
        self.gradle_info = self.dir / "build/hotswap-gradle.json"
        # Resource IDs of the installed build. Relinking must keep them: the new table is
        # laid over the installed one, and an ID meaning two different resources lets
        # the system pick the old entry whenever its locale or density matches better.
        self.ids = self.dir / "build/hotswap-ids.txt"
        self.load()

    def load(self):
        self.config_path = self.dir / "hotswap.json"
        if not self.config_path.exists():
            self.config_path = self.dir / AUTO_CONFIG  # written by locate() for Gradle
        try:
            self.cfg = json.loads(self.config_path.read_text())
        except (OSError, ValueError):
            raise Fail(f"{self.dir} has no usable hotswap.json and is not a Gradle project",
                       "project_invalid") from None
        if self.cfg.get("type") == "gradle" and self.gradle_info.exists():
            self.cfg.update(json.loads(self.gradle_info.read_text()))
        self.package = self.cfg.get("package")
        self.min_api = str(self.cfg.get("min_api", 26))

    def activity(self):
        if self.cfg.get("activity"):
            return f"{self.package}/{self.cfg['activity']}"
        found = self.tools.shell(f"cmd package resolve-activity --brief {self.package}")
        return found.splitlines()[-1]

    def path(self, value):
        return (self.dir / value).resolve()

    def sources(self):
        files = []
        for root in self.cfg["sources"]:
            for suffix in ("*.java", "*.kt"):
                files += [p for p in self.path(root).rglob(suffix)]
        return sorted(set(files))

    def other_inputs(self):
        """Files that can only take effect through a reinstall."""
        files = [self.config_path]
        manifests = self.cfg.get("manifests") or [self.cfg["manifest"]]
        for name in [*manifests, *self.cfg.get("build_files", [])]:
            if self.path(name).is_file():
                files.append(self.path(name))
        return files

    def res_inputs(self):
        return [p for root in self.cfg.get("res", []) for p in self.path(root).rglob("*")
                if p.is_file()]

    def snapshot(self):
        stat = lambda p: [p.stat().st_mtime_ns, p.stat().st_size]  # noqa: E731
        return {"sources": {str(p): stat(p) for p in self.sources()},
                "res": {str(p): stat(p) for p in self.res_inputs()},
                "other": {str(p): stat(p) for p in self.other_inputs()}}

    def state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def save(self, state):
        self.state_path.write_text(json.dumps(state))

    def classpath(self):
        """Libraries, plus (Gradle) classes Gradle generates that no source here produces."""
        entries = [*self.cfg.get("classpath", []), *self.cfg.get("classes_dirs", [])]
        return [str(self.path(e)) for e in entries if self.path(e).exists()]

    def boot(self):
        if self.cfg.get("boot"):
            return self.cfg["boot"]
        return [str(self.tools.android_jar(self.cfg)), str(self.tools.tool("core-lambda-stubs.jar"))]

    def compile(self, kotlin, java, out, extra_classpath=()):
        """Compile the given files into `out`, resolving the rest from classes on disk."""
        out.mkdir(parents=True, exist_ok=True)
        classpath = [str(out), *map(str, extra_classpath), *self.classpath()]
        if kotlin:
            self.tools.kotlin(self.cfg, [
                "-no-jdk", "-nowarn", *self.cfg.get("kotlinc_args", ["-jvm-target", "1.8"]),
                "-cp", os.pathsep.join(classpath + self.boot()), "-d", out,
                *kotlin, *java])  # Java files are only read here, for cross references
        if java:
            self.tools.javac([
                "-nowarn", "-Xlint:-options", "-proc:none", "-encoding", "UTF-8",
                *self.cfg.get("javac_args", ["-source", "8", "-target", "8"]),
                "-bootclasspath", os.pathsep.join(self.boot()),
                "-cp", os.pathsep.join(classpath), "-d", out, *java])

    # ------------------------------------------------------------ full build

    def build(self, reason=None):
        started = time.time()
        self.tools.touched = True
        if self.cfg.get("type") == "gradle":
            self.build_gradle()
        else:
            self.build_simple()
        state = {"snapshot": self.snapshot(), "apk": digests(self.current), "pid": None,
                 "live": None, "res_dirty": False, "inline": self.inline_files(self.sources())}
        self.save(state)
        self.tools.shell(f"am start -n {self.activity()}")
        result = {"result": "installed", "package": self.package,
                  "seconds": round(time.time() - started, 2)}
        if reason:
            result["reason"] = reason
        return result

    def build_gradle(self):
        cfg = self.cfg
        variant = cfg["variant"]
        self.gradle_info.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        if self.tools.serial:
            env["ANDROID_SERIAL"] = self.tools.serial
        # One Gradle run installs the app and re-exports its compile settings, so the
        # swaps always use the classpath and compiler flags of the build just installed.
        run([*self.gradle(), f"-Dhotswap.out={self.gradle_info}",
             f"{cfg['module']}:install{variant}", f"{cfg['module']}:hotswapInfo"],
            cwd=self.dir, env=env)
        self.load()
        self.record_ids(self.linked_resources())
        shutil.rmtree(self.work, ignore_errors=True)
        # Baseline = every source compiled by this tool, not Gradle's class files: later
        # diffs then compare like with like and only real edits show up as changes.
        sources = self.sources()
        self.compile([p for p in sources if p.suffix == ".kt"],
                     [p for p in sources if p.suffix == ".java"], self.current)
        self.extract_r_classes()

    def gradle(self):
        cfg = self.cfg
        return [*cfg["gradle"], "-q", "-I", HERE / "hotswap.init.gradle",
                f"-Dhotswap.module={cfg['module']}", f"-Dhotswap.variant={cfg['variant']}"]

    def linked_resources(self):
        """The resource package Gradle linked last."""
        linked = sorted((Path(self.cfg["module_dir"]) / "build/intermediates").rglob("*.ap_"),
                        key=lambda p: p.stat().st_mtime)
        if not linked:
            raise Fail("Gradle produced no linked resource package (*.ap_)")
        return linked[-1]

    def record_ids(self, package):
        """Write the resource IDs of a linked package in aapt2's --stable-ids format."""
        dump = run([self.tools.tool("aapt2"), "dump", "resources", package]).stdout
        name = re.search(r"Package name=(\S+) id=7f", dump)
        if not name:
            raise Fail(f"no application resources in {package}")
        entries = re.findall(r"resource (0x7f[0-9a-f]{6}) (\S+)", dump)
        self.ids.write_text("".join(f"{name.group(1)}:{entry} = {number}\n"
                                    for number, entry in entries))

    def r_jars(self):
        return [Path(p) for p in self.cfg.get("classpath", []) if Path(p).name == "R.jar"]

    def extract_r_classes(self):
        """Gradle keeps R classes in R.jar, not in sources; track them like compiled ones."""
        for jar in self.r_jars():
            if jar.exists():
                with zipfile.ZipFile(jar) as archive:
                    archive.extractall(self.current)

    def build_simple(self):
        tools, work = self.tools, self.work
        libs_cache = self.dir / "build/hotswap-libs"
        shutil.rmtree(work, ignore_errors=True)
        (work / "gen").mkdir(parents=True)
        android_jar = tools.android_jar(self.cfg)
        self.link_resources(work / "unsigned.apk")

        sources = self.sources() + sorted((work / "gen").rglob("*.java"))
        kotlin = [p for p in sources if p.suffix == ".kt"]
        java = [p for p in sources if p.suffix == ".java"]
        self.compile(kotlin, java, self.current)

        libs = [Path(p) for p in self.classpath()]
        if kotlin:
            libs.append(tools.kotlin_stdlib(self.cfg))
        dex_dirs = []
        for lib in libs:  # library dex is cached: it only changes with the jar
            target = libs_cache / hashlib.sha1(lib.read_bytes()).hexdigest()[:16]
            if not target.exists():
                target.mkdir(parents=True)
                run([tools.tool("d8"), "--lib", android_jar, "--min-api", self.min_api,
                     "--output", target, lib])
            dex_dirs.append(target)
        (work / "dex").mkdir()
        run([tools.tool("d8"), "--lib", android_jar, "--min-api", self.min_api,
             *[a for lib in libs for a in ("--classpath", lib)],
             "--output", work / "dex", *class_files(self.current).values()])
        with zipfile.ZipFile(work / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as apk:
            dex_files = [p for d in [work / "dex", *dex_dirs] for p in sorted(d.glob("*.dex"))]
            for index, dex in enumerate(dex_files):
                apk.write(dex, "classes.dex" if index == 0 else f"classes{index + 1}.dex")
        run([tools.tool("zipalign"), "-f", "4", work / "unsigned.apk", work / "aligned.apk"])
        keystore = self.dir / "build/debug.keystore"
        if not keystore.exists():
            run(["keytool", "-genkeypair", "-keystore", keystore, "-storepass", "android",
                 "-keypass", "android", "-alias", "hotswap", "-keyalg", "RSA", "-keysize", "2048",
                 "-validity", "10000", "-dname", "CN=hotswap"])
        run([tools.tool("apksigner"), "sign", "--ks", keystore, "--ks-pass", "pass:android",
             "--out", work / "app.apk", work / "aligned.apk"])
        tools.adb("install", "-r", "-t", work / "app.apk")

    def link_resources(self, out, keep_ids=False):
        """Plain project: compile and link resources into `out`, regenerating R.java."""
        tools, work = self.tools, self.work
        fresh = self.ids.with_suffix(".new")
        link = [tools.tool("aapt2"), "link", "-o", out, "-I", tools.android_jar(self.cfg),
                "--manifest", self.path(self.cfg["manifest"]), "--java", work / "gen",
                "--auto-add-overlay", "--emit-ids", fresh]
        if keep_ids and self.ids.exists():
            link += ["--stable-ids", self.ids]
        for index, root in enumerate(self.cfg.get("res", [])):
            if self.path(root).is_dir():
                run([tools.tool("aapt2"), "compile", "--dir", self.path(root),
                     "-o", work / f"res{index}.zip"])
                link.append(work / f"res{index}.zip")
        run(link)
        fresh.replace(self.ids)

    def relink_resources(self):
        """Link the edited resources into work/resources.apk; True if the R classes changed."""
        out = self.work / "resources.apk"
        if self.cfg.get("type") != "gradle":
            generated = lambda: {p: p.read_bytes() for p in (self.work / "gen").rglob("*.java")}  # noqa: E731
            before = generated()
            self.link_resources(out, keep_ids=True)
            return before != generated()
        cfg = self.cfg
        digest = lambda: [hashlib.sha1(p.read_bytes()).hexdigest()  # noqa: E731
                          for p in self.r_jars() if p.exists()]
        before = digest()
        run([*self.gradle(), f"-Dhotswap.stableIds={self.ids}",
             f"{cfg['module']}:process{cfg['variant']}Resources"], cwd=self.dir)
        linked = self.linked_resources()
        self.record_ids(linked)  # now also covers resources added by this edit
        shutil.copy(linked, out)
        if before == digest():
            return False
        self.extract_r_classes()
        return True

    # ------------------------------------------------------------------ swap

    @staticmethod
    def inline_files(files):
        """Kotlin files declaring inline functions: their bodies are copied into callers."""
        return [str(p) for p in files
                if p.suffix == ".kt" and re.search(r"\binline\b", p.read_text(errors="ignore"))]

    def swap(self, refresh="auto"):
        started = time.time()
        state = self.state()
        if not state.get("snapshot"):
            return self.build("no previous build")
        now, before = self.snapshot(), state["snapshot"]
        if now["other"] != before["other"]:
            return self.build("manifest or build configuration changed")
        res_changed = now.get("res") != before.get("res")
        if set(before["sources"]) - set(now["sources"]):
            return self.build("a source file was deleted")
        changed = [Path(p) for p, stamp in now["sources"].items()
                   if before["sources"].get(p) != stamp]

        pid = self.tools.shell(f"pidof {self.package}", check=False)
        if not pid:
            return self.build("the app is not running")
        r_changed = False
        if res_changed:
            r_changed = self.relink_resources()
            state["res_dirty"] = True
        if changed or r_changed:
            # New resource IDs are constants compiled into every class that uses R.
            self.recompile(changed, state, r_changed)
        if changed or res_changed:
            state["snapshot"] = now
            state["inline"] = sorted(set(state.get("inline", [])) | set(self.inline_files(changed)))
            self.save(state)

        # A restarted process runs the installed APK again: earlier swaps are gone.
        same_process = state.get("pid") == pid and state.get("live")
        live = state["live"] if same_process else state["apk"]
        latest = digests(self.current)
        names = sorted(n for n, digest in latest.items() if live.get(n) != digest)
        resources = self.work / "resources.apk"
        if not (res_changed or (not same_process and state.get("res_dirty"))):
            resources = None
        if not names and not resources:
            return {"result": "unchanged", "package": self.package}
        files = class_files(self.current)
        verdict = self.push([files[n] for n in names], refresh, resources)
        if not verdict.startswith("OK"):
            return self.build(f"swap refused ({verdict})")
        state.update(pid=pid, live=latest)
        self.save(state)
        return {"result": "swapped", "package": self.package, "class_count": len(names),
                "classes": names[:40], "resources": bool(resources), "verdict": verdict,
                "applied": {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", verdict)},
                "seconds": round(time.time() - started, 2)}

    def pending(self):
        """What the next swap would do, without doing it."""
        state = self.state()
        if not state.get("snapshot"):
            return {"built": False, "next_swap": "full build (no previous build)"}
        now, before = self.snapshot(), state["snapshot"]
        changed = sorted(p for kind in ("sources", "res") for p, stamp in now[kind].items()
                         if before.get(kind, {}).get(p) != stamp)
        report = {"built": True, "changed_files": len(changed), "changed": changed[:20]}
        if now["other"] != before["other"]:
            report["next_swap"] = "full build (manifest or build configuration changed)"
        elif set(before["sources"]) - set(now["sources"]):
            report["next_swap"] = "full build (a source file was deleted)"
        else:
            report["next_swap"] = "swap in place" if changed else "nothing to do"
        return report

    def recompile(self, changed, state, everything_first=False):
        patch = self.work / "patch"
        shutil.rmtree(patch, ignore_errors=True)
        files = class_files(self.current)
        before = {n: class_abi(p.read_bytes()) for n, p in files.items()}
        everything = self.sources() + sorted((self.work / "gen").rglob("*.java"))
        wide = everything_first or any(
            str(p) in state.get("inline", []) for p in changed) or bool(self.inline_files(changed))
        for _ in ("narrow", "wide"):
            chosen = everything if wide else changed
            shutil.rmtree(patch, ignore_errors=True)
            self.compile([p for p in chosen if p.suffix == ".kt"],
                         [p for p in chosen if p.suffix == ".java"], patch, [self.current])
            # A changed signature, constant or hierarchy can break classes that were not
            # recompiled, so recompile everything once and let the byte diff pick.
            abi_changed = any(before.get(n) != class_abi(p.read_bytes())
                              for n, p in class_files(patch).items() if n in before)
            if wide or not abi_changed:
                break
            wide = True
        self.reset_keys = set()
        for name, path in class_files(patch).items():
            new = path.read_bytes()
            if b"androidx/compose/runtime/Composer" not in new or name not in files:
                continue
            old_calls, old_keys = compose_shape(files[name].read_bytes())
            new_calls, new_keys = compose_shape(new)
            if old_calls != new_calls:
                self.reset_keys.update(old_keys, new_keys)
        shutil.copytree(patch, self.current, dirs_exist_ok=True)

    def push(self, classes, refresh, resources=None):
        tools, work = self.tools, self.work
        merged, stage = work / "swap-merged", work / "swap-stage"
        for directory in (merged, stage):
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True)
        android_jar = tools.android_jar(self.cfg)
        common = ["--lib", android_jar, "--min-api", self.min_api]
        # Pass 1 desugars (lambdas become extra classes); pass 2 splits to one class per
        # dex, which is the only form ART accepts for redefinition.
        split = work / "swap-split"
        shutil.rmtree(split, ignore_errors=True)
        split.mkdir()
        if classes:
            tools.d8([*common, "--classpath", self.current,
                      *[a for jar in self.classpath() for a in ("--classpath", jar)],
                      "--output", merged, *classes])
            tools.d8([*common, "--no-desugaring", "--file-per-class", "--output", split,
                      *sorted(merged.glob("*.dex"))])

        remote = f"/data/data/{self.package}/code_cache/hotswap"
        plan = [f"refresh {refresh}"]
        for index, dex in enumerate(sorted(split.rglob("*.dex"))):
            name = str(dex.relative_to(split))[:-4].replace(os.sep, ".")
            shutil.copy(dex, stage / f"{index}.dex")
            plan.append(f"class {name} {remote}/{index}.dex")
            # R classes hold resource IDs in static fields that redefinition leaves stale.
            if re.search(r"(^|\.)R(\$\w+)?$", name):
                plan.append(f"statics {name} {remote}/{index}.dex")
        if resources:
            shutil.copy(resources, stage / "resources.apk")
            plan.append(f"resources {remote}/resources.apk")
        if refresh == "auto":
            plan += [f"compose {key}" for key in sorted(getattr(self, "reset_keys", ()))]
        helper, helper_digest = tools.helper(self.cfg)
        helper_remote = f"/data/data/{self.package}/code_cache/hotswap-helper-{helper_digest}.dex"
        plan.append(f"helper {helper_remote}")
        (stage / "plan.txt").write_text("\n".join(plan) + "\n")

        agent, digest = tools.agent()
        tools.touched = True
        package = self.package
        base = "/data/local/tmp/hotswap"
        staging = f"{base}/{package}-{int(time.time() * 1000)}"
        pushed_agent = f"{base}/agent-{digest}.so"
        pushed_helper = f"{base}/helper-{helper_digest}.dex"
        # The agent stays mapped in the app after its first attach; overwriting it in
        # place would corrupt the running code, so it is copied once per content hash.
        agent_remote = f"/data/data/{package}/code_cache/hotswap-agent-{digest}.so"
        tools.adb("push", stage, staging)
        # One device round trip: install the agent if missing, copy the plan and dex
        # files into the app (read-only: Android 14+ refuses code from a writable
        # file), attach, and wait for the agent's verdict.
        script = f"""
if ! run-as {package} ls {agent_remote} {helper_remote} >/dev/null 2>&1; then
  [ -f {pushed_agent} ] && [ -f {pushed_helper} ] || {{ echo NEED_AGENT; exit; }}
  chmod a+rx {base}; chmod a+r {pushed_agent} {pushed_helper}
  run-as {package} sh -c '[ -f {agent_remote} ] || cp {pushed_agent} {agent_remote}; [ -f {helper_remote} ] || {{ cp {pushed_helper} {helper_remote} && chmod 444 {helper_remote}; }}' || {{ echo FAIL cannot install the agent; exit; }}
fi
chmod a+rx {base}; chmod -R a+rX {staging}
run-as {package} sh -c 'rm -rf {remote} && mkdir -p {remote} && cp {staging}/* {remote}/ && chmod 444 {remote}/*' || {{ echo FAIL cannot copy into the app; exit; }}
rm -rf {staging}
error=$(am attach-agent {package} {agent_remote}={remote}/plan.txt 2>&1) || {{ echo "FAIL attach: $error" | head -1; exit; }}
i=0
while [ $i -lt 160 ]; do
  run-as {package} cat {remote}/plan.txt.result 2>/dev/null && exit
  sleep 0.05; i=$((i+1))
done
echo "FAIL the agent did not report (not debuggable, or attach blocked?)"
"""
        verdict = tools.shell(script, check=False)
        if verdict == "NEED_AGENT":
            tools.adb("push", agent, pushed_agent)
            tools.adb("push", helper, pushed_helper)
            verdict = tools.shell(script, check=False)
        return verdict.splitlines()[0] if verdict else "FAIL no answer from the device"

AUTO_CONFIG = "build/hotswap-config.json"


def locate(directory, module=None, variant=None, gradle=None):
    """Project root for `directory`: the nearest hotswap.json, else the Gradle root.

    A Gradle root gets its settings in build/, not in the project's own files; pointing
    at a module directory selects that module.
    """
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise Fail(f"{directory} is not a directory", "project_invalid")
    for root in (directory, *directory.parents):
        if (root / "hotswap.json").is_file():
            return root
        if any((root / name).is_file() for name in ("settings.gradle", "settings.gradle.kts")):
            break
    else:
        raise Fail(f"{directory}: no hotswap.json and no Gradle settings file here or above",
                   "project_invalid")
    path = root / AUTO_CONFIG
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    inferred = ":" + ":".join(directory.relative_to(root).parts) if directory != root else None
    wrapper = root / "gradlew"
    launcher = gradle or (str(wrapper) if wrapper.exists() else shutil.which("gradle"))
    if not launcher and not saved.get("gradle"):
        raise Fail("no ./gradlew in the project and no gradle on PATH", "toolchain_missing")
    variant = variant or saved.get("variant") or "Debug"
    wanted = {"type": "gradle", "module": module or inferred or saved.get("module") or ":app",
              "variant": variant[:1].upper() + variant[1:],
              "gradle": [launcher] if gradle or not saved.get("gradle") else saved["gradle"]}
    if wanted != saved:  # a new module or variant: the changed file forces a full build
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(wanted, indent=2) + "\n")
    return root


def toolchain():
    """Host tools a swap needs; nothing is built or started."""
    sdk = sdk_dir()

    def latest(sub):
        try:
            return newest(sdk / sub)
        except Fail:
            return None

    build_tools, platform, ndk = latest("build-tools"), latest("platforms"), latest("ndk")
    agents = cache_dir() / "agent"
    report = {
        "sdk": str(sdk) if sdk.is_dir() else None,
        "build_tools": build_tools.name if build_tools else None,
        "platform": platform.name if platform else None,
        "ndk": ndk.name if ndk else None,
        "javac": shutil.which("javac"),
        "java": shutil.which("java"),
        "adb": os.environ.get("ANDROID_USE_ADB") or shutil.which("adb"),
        "agent_built_for": sorted({p.parent.name for p in agents.rglob("libhotswap.so")}),
    }
    missing = [text for ok, text in (
        (report["sdk"], "Android SDK (set ANDROID_HOME)"),
        (build_tools, "SDK build-tools (d8, aapt2)"),
        (platform, "an SDK platform (android.jar)"),
        (report["javac"] and report["java"], "a JDK on PATH (java, javac)"),
        (report["adb"], "adb"),
    ) if not ok]
    if not report["agent_built_for"]:  # only needed to compile the agent once per ABI
        if not ndk:
            missing.append("Android NDK (to build the agent once)")
        if not jvmti_header():
            missing.append("jvmti.h from a JDK (set JAVA_HOME or JVMTI_H)")
    report.update(missing=missing, ready=not missing)
    return report


def run_action(action, project, serial=None, refresh="auto", module=None, variant=None,
               gradle=None, use_daemon=True, tools=None):
    """One call for agents: `status`, `swap` or `build`. Returns a dict, raises Fail.

    Pass a Tools instance to learn (tools.touched) whether the device was changed
    before a failure.
    """
    if action == "status":
        report = {"action": "status", "toolchain": toolchain()}
        root = locate(project, module, variant, gradle)
        report["project"] = str(root)
        if report["toolchain"]["build_tools"]:
            target = Project(root, tools or Tools(serial, use_daemon))
            report.update(type=target.cfg.get("type", "plain"), package=target.package,
                          config=str(target.config_path), **target.pending())
            if target.package and report["toolchain"]["adb"]:
                try:
                    pid = target.tools.shell(f"pidof {target.package}", check=False)
                    report["running"] = bool(pid)
                except Fail as error:
                    report["device_error"] = str(error)
        return report
    root = locate(project, module, variant, gradle)
    target = Project(root, tools or Tools(serial, use_daemon))
    result = target.build() if action == "build" else target.swap(refresh)
    return {"action": action, "project": str(root), **result}


def describe(result):
    """The dict from run_action as the lines the command line prints."""
    lines = []
    if result.get("reason"):
        lines.append(f"reinstall: {result['reason']}")
    if result["result"] == "installed":
        lines.append(f"built, installed and launched {result['package']} "
                     f"in {result['seconds']:.1f}s")
    elif result["result"] == "unchanged":
        lines.append("nothing to swap")
    else:
        what = f"{result['class_count']} class(es)" + (
            " + resources" if result["resources"] else "")
        lines.append(f"swapped {what} in {result['seconds']:.2f}s: {result['verdict']}")
    return "\n".join(lines)


def main(default_project=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-p", "--project", default=default_project, required=not default_project)
    parser.add_argument("-s", "--serial")
    parser.add_argument("--refresh", choices=["auto", "recreate", "none"], default="auto",
                        help="after a swap: auto = recompose Compose UI in place and rebuild "
                             "other activities with their saved view state (default); "
                             "recreate = rebuild every activity; none = leave the UI alone")
    parser.add_argument("--no-daemon", action="store_true",
                        help="run each compiler as a fresh process instead of the warm daemon")
    parser.add_argument("--module", help="Gradle module path (default :app)")
    parser.add_argument("--variant", help="Gradle build variant (default Debug)")
    parser.add_argument("--gradle", help="Gradle launcher (default ./gradlew)")
    parser.add_argument("command", choices=["status", "build", "swap", "watch", "gradle-setup"])
    args = parser.parse_args()

    def act(action):
        return run_action(action, args.project, args.serial, args.refresh, args.module,
                          args.variant, args.gradle, not args.no_daemon)

    try:
        if args.command == "gradle-setup":
            root = Path(args.project).resolve()
            wrapper = root / "gradlew"
            gradle = args.gradle or (str(wrapper) if wrapper.exists() else shutil.which("gradle"))
            if not gradle:
                raise Fail("no ./gradlew and no gradle on PATH: pass --gradle")
            (root / "hotswap.json").write_text(json.dumps(
                {"type": "gradle", "module": args.module or ":app",
                 "variant": args.variant or "Debug", "gradle": [gradle]}, indent=2) + "\n")
            print(f"wrote {root / 'hotswap.json'}; next: build")
        elif args.command == "status":
            print(json.dumps(act("status"), indent=2))
        elif args.command in ("build", "swap"):
            print(describe(act(args.command)))
        else:
            print("watching for saves; Ctrl-C to stop")
            project = Project(locate(args.project, args.module, args.variant, args.gradle),
                              Tools(args.serial, not args.no_daemon))
            seen = None
            while True:
                snapshot = project.snapshot()
                if snapshot != seen:
                    seen = snapshot
                    if snapshot != project.state().get("snapshot"):
                        try:
                            print(describe(project.swap(args.refresh)), flush=True)
                        except Fail as error:  # e.g. a compile error: wait for the next save
                            print(error, flush=True)
                time.sleep(0.3)
    except Fail as error:
        sys.exit(str(error))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
