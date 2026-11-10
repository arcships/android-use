#!/usr/bin/env python3
"""End-to-end check of hotswap.py against a connected device, using the lab app.

  verify.py [-s SERIAL] [--gradle]

Edits the lab sources, swaps, and reads the app's own log line to confirm what the
running process executes. Sources are restored afterwards. --gradle also runs the
Compose sample in gradle-sample/ (run `hotswap.py -p gradle-sample gradle-setup` first).
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
SRC = APP / "src/lab/hotswap"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--serial")
    parser.add_argument("--gradle", action="store_true")
    args = parser.parse_args()
    adb = ["adb", *(["-s", args.serial] if args.serial else [])]
    tool = [sys.executable, str(HERE / "hotswap.py"), *(["-s", args.serial] if args.serial else [])]
    results = []
    target = {"package": "lab.hotswap", "project": []}

    def device(*command):
        try:
            return subprocess.run([*adb, *command], capture_output=True, text=True, timeout=30).stdout
        except subprocess.TimeoutExpired:
            sys.exit("the device stopped answering (disconnected?)")

    def sh(command):
        return device("shell", command)

    def hotswap(*extra):
        started = time.time()
        done = subprocess.run([*tool, *target["project"], *extra], capture_output=True, text=True)
        return done.returncode, (done.stdout + done.stderr).strip(), time.time() - started

    def line(wanted=None, timeout=12):
        """Newest log line from the app, waiting until it contains `wanted`."""
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            # Only this app's process: other lab apps log under the same tag.
            pid = sh(f"pidof {target['package']}").strip() or "0"
            out = device("logcat", "-d", f"--pid={pid}", "-s", "hotswap-app:I").strip().splitlines()
            out = [text for text in out if "hotswap-app: " in text]
            last = out[-1].split("hotswap-app: ", 1)[-1] if out else ""
            if last and (wanted is None or wanted in last):
                return last
            time.sleep(0.3)
        return last

    def field(text, name):
        found = re.search(rf"{name}=(\d+)", text)
        return int(found.group(1)) if found else -1

    def edit(name, old, new):
        path = SRC / name
        text = path.read_text()
        assert old in text, f"{old!r} not in {name}"
        path.write_text(text.replace(old, new))
        time.sleep(0.02)

    def check(title, ok, detail):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {title}: {detail}")

    backup = Path(tempfile.mkdtemp())
    shutil.copytree(APP / "src", backup / "src")
    shutil.copytree(APP / "res", backup / "res")
    try:
        code, out, took = hotswap("build")
        start = line("java A")
        check("full build", code == 0 and "java A" in start, f"{took:.1f}s")
        pid = field(start, "pid")
        # Give the views state worth keeping: typed text and a scroll offset.
        sh("input text hello")
        sh("input swipe 540 1000 540 500 300")
        offset = None
        for _ in range(8):  # some phones let a fling coast for a while: wait for it to stop
            time.sleep(1.5)
            typed = line("input=hello")
            if field(typed, "scroll") == offset:
                break
            offset = field(typed, "scroll")
        check("typed text and scrolled", "input=hello" in typed and offset > 0,
              f"input=hello scroll={offset}")

        def swap_case(title, wanted, verdict="", same_pid=True, extra=()):
            code, out, took = hotswap(*extra, "swap")
            seen = line(wanted)
            ok = code == 0 and wanted in seen and verdict in out
            ok = ok and (field(seen, "pid") == pid) == same_pid
            check(title, ok, f"{took:.2f}s | {out.splitlines()[-1] if out else ''} | {seen}")
            return seen

        edit("Message.java", '"java A"', '"java B"')
        swap_case("java method body", "java B", "structural=0")

        edit("Greeter.kt", '"A")', '"B")')
        swap_case("kotlin method body", "KOTLIN B")

        edit("Util.kt", '" x1"', '" x2"')
        swap_case("kotlin inline function in another file", "x2")

        edit("Message.java", 'return "java B";',
             'return "java C" + extra();\n    }\n\n    static int calls;\n\n'
             '    static String extra() {\n        return "+" + (++calls > 0 ? "m" : "");')
        swap_case("added method and field", "java C+m", "structural=1")

        (SRC / "Extra.java").write_text(
            'package lab.hotswap;\n\npublic class Extra {\n'
            '    public static String value() {\n        return "new1";\n    }\n}\n')
        edit("Message.java", 'return "java C" + extra();', 'return "java D" + Extra.value();')
        swap_case("brand-new class", "java Dnew1", "added=1")

        edit("Extra.java", '"new1"', '"new2"')
        swap_case("edit the class added by a swap", "java Dnew2", "added=0")

        edit("Message.java", 'return "java D" + Extra.value();',
             'java.util.function.Supplier<String> s = () -> "lam1";\n'
             '        return "java E" + s.get();')
        swap_case("java lambda introduced", "java Elam1")
        edit("Message.java", '"lam1"', '"lam2"')
        swap_case("java lambda body edited", "java Elam2")

        before = line()
        edit("Message.java", '"java E"', '"java F"')
        after = swap_case("--refresh none leaves the activity alone", "java F",
                          extra=("--refresh", "none"))
        check("  activity not rebuilt", field(after, "creations") == field(before, "creations"),
              f"creations {field(before, 'creations')} -> {field(after, 'creations')}")
        check("  process state kept across all swaps",
              field(after, "ticks") > field(start, "ticks") and field(after, "pid") == pid,
              f"ticks {field(start, 'ticks')} -> {field(after, 'ticks')}, pid {pid}")

        edit("Message.java", '"java F"', '"java G')
        code, out, took = hotswap("swap")
        seen = line()
        check("compile error is reported, app untouched",
              code != 0 and "java F" in seen and "Message.java" in out,
              f"exit {code} | {out.splitlines()[0][:40]} | {seen}")
        edit("Message.java", '"java G', '"java G"')
        swap_case("recovers after the error is fixed", "java G")

        colors = APP / "res/values/colors.xml"
        colors.write_text(colors.read_text().replace("#FF336699", "#FFCC0000"))
        styled = swap_case("style edit (a colour) is applied", "color=ffcc0000", "resources=1")
        time.sleep(2.5)  # past the moment the rebuilt window is still settling
        styled = line("color=ffcc0000")
        check("  view state kept: typed text and scroll position",
              "input=hello" in styled and field(styled, "scroll") == offset,
              f"after 10 swaps: input=hello scroll {offset} -> {field(styled, 'scroll')}")

        strings = APP / "res/values/strings.xml"
        strings.write_text(strings.read_text().replace("res 1", "res 2"))
        swap_case("resource value edited, no restart", "res 2", "resources=1")

        # A name sorting before "title" would take title's ID unless IDs are kept stable.
        strings.write_text(strings.read_text().replace(
            "<resources>", '<resources>\n    <string name="another">x</string>').replace(
            "res 2", "res 3"))
        swap_case("resource added before an existing one", "res 3", "resources=1")

        package = "lab.hotswap"
        sh(f"am force-stop {package}")
        sh(f"am start -n {package}/.MainActivity")
        stale = line("java A")
        check("restarted process runs the stale APK", "java A" in stale and "res 1" in stale, stale)
        pid = field(stale, "pid")
        swap_case("next swap re-applies code and resources", "java Glam2 | KOTLIN B x2 | res 3")

        edit("Message.java", "public class Message {", "public class Message implements Runnable {\n"
             "    public void run() {\n    }\n")
        edit("Message.java", '"java G"', '"java H"')
        swap_case("hierarchy change falls back to reinstall", "java Hlam2 | KOTLIN B x2 | res 3",
                  "swap refused", same_pid=False)
    finally:
        for name in ("src", "res"):
            shutil.rmtree(APP / name)
            shutil.copytree(backup / name, APP / name)
        shutil.rmtree(backup)
        hotswap("build")
    if args.gradle:
        sample = HERE / "gradle-sample"
        code_dir = sample / "app/src/main/java/lab/hotswap/gradle"
        strings = sample / "app/src/main/res/values/strings.xml"
        target.update(package="lab.hotswap.gradle", project=["-p", str(sample)])
        saved = {p: p.read_text() for p in [*code_dir.iterdir(), strings]}

        def change(path, old, new):
            assert old in path.read_text(), old
            path.write_text(path.read_text().replace(old, new))

        def gradle_case(title, wanted, verdict=""):
            code, out, took = hotswap("swap")
            seen = line(wanted)
            ok = code == 0 and wanted in seen and verdict in out and field(seen, "pid") == pid
            check("gradle: " + title, ok, f"{took:.2f}s | {out.splitlines()[-1] if out else ''}")

        try:
            code, out, took = hotswap("build")
            start = line("compose A | kotlin A | java A | res 1")
            check("gradle: full build of the Compose sample", code == 0 and "res 1" in start,
                  f"{took:.1f}s")
            pid = field(start, "pid")
            sh("input swipe 540 1000 540 500 300")
            time.sleep(1.5)
            before = line()
            change(code_dir / "JavaMessage.java", '"java A"', '"java B"')
            gradle_case("java method body", "java B")
            change(code_dir / "Labels.kt", '"kotlin A"', '"kotlin B"')
            gradle_case("kotlin method body", "kotlin B")
            change(code_dir / "Screen.kt", '"compose A"', '"compose B"')
            gradle_case("composable body", "compose B")
            change(strings, "res 1", "res 2")
            gradle_case("resource value edited, no restart", "res 2", "resources=1")
            change(strings, "<resources>", '<resources>\n    <string name="another">x</string>')
            change(strings, "res 2", "res 3")
            gradle_case("resource added before an existing one", "res 3", "resources=1")
            after = line("res 3")
            # The remembered counter ticks in step with a static one: any reset of the
            # composition would break the pairing.
            kept = (field(after, "creations") == 1 and field(before, "scroll") > 0
                    and field(after, "scroll") == field(before, "scroll")
                    and field(after, "local") - field(before, "local")
                    == field(after, "ticks") - field(before, "ticks"))
            check("gradle: Compose state kept, activity never rebuilt", kept,
                  f"creations {field(after, 'creations')}, remembered counter "
                  f"{field(before, 'local')} -> {field(after, 'local')} over "
                  f"{field(after, 'ticks') - field(before, 'ticks')} ticks, "
                  f"scroll {field(before, 'scroll')} -> {field(after, 'scroll')}")
            change(code_dir / "Screen.kt", '        Spacer(Modifier.height(2000.dp))\n',
                   '        Badge()\n        Spacer(Modifier.height(2000.dp))\n')
            (code_dir / "Screen.kt").write_text(
                (code_dir / "Screen.kt").read_text()
                + '\n@Composable\nfun Badge() {\n    Column { Text("badge") }\n}\n')
            change(code_dir / "Labels.kt", '"kotlin B"', '"kotlin C"')
            gradle_case("new composable function", "kotlin C", "structural=1")
            check("gradle:   still the same activity", field(line(), "creations") == 1, "creations 1")
        finally:
            for path, text in saved.items():
                path.write_text(text)
            hotswap("build")
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
