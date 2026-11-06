"""Transactional local installs and a cache-independent stdio launcher (stdlib only)."""

import argparse
import copy
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path

MARKET = "android-use-managed"
PLUGIN = "android-use@" + MARKET
NAMES = {"android_use", "android-use"}
ROOT_FILES = (
    "pyproject.toml", "uv.lock", "README.md", "README.zh-CN.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", ".env.example", ".codex-plugin/plugin.json", ".mcp.json",
)
DIRECTORIES = ("src", "skills", "scripts", "docs", "ui")
EXCLUDED = {".git", ".venv", "node_modules", "__pycache__", "test-results",
            "playwright-report", ".pytest_cache", ".ruff_cache", "dist"}


class InstallError(Exception):
    pass


def install_root():
    return Path(os.environ.get("ANDROID_USE_INSTALL_DIR", "~/.local/share/android-use/install")).expanduser().resolve()


def codex_root():
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".android-use-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def read_toml(path):
    return tomllib.loads(path.read_text()) if path.exists() else {}


def enabled_plugins(config):
    return [name for name, value in config.get("plugins", {}).items()
            if name.startswith("android-use@") and value.get("enabled", True)]


def unrelated_config(config):
    """Comparison boundary for rollback; never overwrite concurrent unrelated edits."""
    value = copy.deepcopy(config)
    for name in NAMES:
        value.get("mcp_servers", {}).pop(name, None)
    for name in list(value.get("plugins", {})):
        if name.startswith("android-use@"):
            value["plugins"].pop(name)
    value.get("marketplaces", {}).pop(MARKET, None)
    for name in ("plugins", "marketplaces", "mcp_servers"):
        if value.get(name) == {}:
            value.pop(name)
    return value


def diagnose(home=None, project=None):
    """Read configuration only; never return credentials or launch a phone action."""
    home = Path(home or codex_root())
    project = Path(project or Path.cwd())
    issues = []
    effective = {"plugins": {}, "mcp_servers": {}}
    origins = {}
    for scope, path in [("user", home / "config.toml"),
                        ("project", project / ".codex/config.toml")]:
        try:
            config = read_toml(path)
        except (OSError, ValueError):
            issues.append({"code": "config_unreadable", "scope": scope, "path": str(path)})
            continue
        for kind in effective:
            for name, value in config.get(kind, {}).items():
                effective[kind][name] = {**effective[kind].get(name, {}), **value}
                origins[(kind, name)] = {"scope": scope, "path": str(path)}
    plugins = enabled_plugins(effective)
    if len(plugins) > 1:
        issues.append({"code": "duplicate_plugins", "scope": "effective"})
    servers = {n: v for n, v in effective["mcp_servers"].items()
               if n in NAMES and v.get("enabled") is not False}
    if len(servers) > 1:
        issues.append({"code": "duplicate_mcp_servers", "scope": "effective"})
    for name, server in servers.items():
        location = {**origins[("mcp_servers", name)], "server": name}
        if plugins:
            issues.append({"code": "mcp_plugin_conflict", **location})
        if location["scope"] == "project":
            issues.append({"code": "project_mcp_override", **location})
        args = server.get("args", [])
        if any("/plugins/cache/" in str(arg).replace("\\", "/") for arg in args):
            issues.append({"code": "versioned_cache_path", **location})
        for flag in ("--directory", "--env-file"):
            if flag in args:
                i = args.index(flag) + 1
                if i >= len(args) or not Path(args[i]).expanduser().exists():
                    issues.append({"code": "missing_launch_path", **location, "flag": flag})
        command = server.get("command", "")
        if command and Path(command).is_absolute() and not Path(command).is_file():
            issues.append({"code": "missing_launcher", **location})
    return {"ok": not issues, "issues": issues, "restart_required_after_update": True}


def header_path(line):
    if not re.match(r"^\s*\[(?!\[).+\]\s*(?:#.*)?$", line):
        return None
    try:
        value = tomllib.loads(line + "\n__android_use_marker__ = true\n")
        result = []
        while "__android_use_marker__" not in value:
            if len(value) != 1:
                return None
            name, value = next(iter(value.items()))
            result.append(name)
        return tuple(result)
    except ValueError:
        return None


def migrate_config(text):
    """Remove only owned Android Use overrides, preserving unrelated TOML/comments."""
    before = tomllib.loads(text)
    expected = copy.deepcopy(before)
    migrated = {}
    names = []
    for name, spec in before.get("mcp_servers", {}).items():
        if name not in NAMES:
            continue
        args = spec.get("args", [])
        if not any(a in {"android-use-mcp", "android_use.server"} or
                   str(a).endswith("/android-use-mcp") for a in args):
            raise InstallError("unrecognized_mcp_override: remove or migrate it explicitly")
        if migrated and migrated != spec:
            raise InstallError("multiple_mcp_overrides: choose the intended settings first")
        migrated = spec
        names.append(name)
        del expected["mcp_servers"][name]
    old_plugins = [p for p in enabled_plugins(before) if p != PLUGIN]
    for name in old_plugins:
        expected["plugins"][name]["enabled"] = False
    result = []
    section = ()
    for line in text.splitlines(keepends=True):
        header = header_path(line.rstrip("\r\n"))
        if header is not None:
            section = header
        if len(section) >= 2 and section[0] == "mcp_servers" and section[1] in names:
            continue
        if len(section) == 2 and section[0] == "plugins" and section[1] in old_plugins:
            if re.match(r"^\s*enabled\s*=", line):
                continue
            result.append(line)
            if header is not None:
                result.append("enabled = false\n")
            continue
        result.append(line)
    updated = "".join(result)
    # Unsupported inline tables are refused, rather than rewriting someone else's settings.
    parsed = tomllib.loads(updated)
    if expected.get("mcp_servers") == {} and "mcp_servers" not in parsed:
        expected.pop("mcp_servers")
    if parsed != expected:
        raise InstallError("unsupported_config_layout: migrate the Android Use entry manually")
    return updated, migrated


def source_files(source):
    for name in ROOT_FILES:
        path = source / name
        if path.is_file():
            yield path, Path(name)
    for name in DIRECTORIES:
        for path in (source / name).rglob("*"):
            rel = path.relative_to(source)
            if any(p in EXCLUDED or p.startswith(".") for p in rel.parts):
                continue
            if path.is_symlink():
                raise InstallError("source_symlink: use a clean release archive")
            if path.is_file():
                yield path, rel


def launch_arguments(root, current=None, profile=None):
    current = current or read_json(root / "current.json")
    profile = profile if profile is not None else read_json(root / "launch.json", {})
    if not current:
        raise InstallError("not_installed: run scripts/install.py install")
    release = (root / current["release"]).resolve()
    if not release.is_relative_to((root / "releases").resolve()) or not release.is_dir():
        raise InstallError("installed_runtime_missing: reinstall from a verified release")
    args = [current["uv"], "run", "--frozen", "--no-sync", "--directory", str(release)]
    if profile.get("env_file"):
        env_file = root / profile["env_file"]
        if not env_file.is_file() or env_file.resolve().parent != root.resolve():
            raise InstallError("saved_environment_missing: reinstall with --env-file")
        # uv splits whitespace in --env-file values, even when argv is a list.
        # --directory resolves this relative path; the managed layout has no
        # spaces in the two suffix components (releases/<id>).
        args += ["--env-file", os.path.relpath(env_file, release)]
    args.append("android-use-mcp")
    return args, {**os.environ, **profile.get("env", {})}


def probe(args, env, version, timeout=30):
    """Actual initialize + tools/list over stdio, with no phone/model calls."""
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding="utf-8")
        messages = queue.Queue()

        def read():
            for line in process.stdout:
                messages.put(line)
            messages.put(None)

        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout

        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()

        def response(identifier):
            while True:
                try:
                    line = messages.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    raise InstallError("mcp_probe_timeout") from None
                if line is None:
                    raise InstallError("mcp_probe_failed: inspect the verified runtime")
                try:
                    value = json.loads(line)
                except ValueError:
                    raise InstallError("mcp_stdout_not_protocol") from None
                if value.get("id") == identifier:
                    if "error" in value:
                        raise InstallError("mcp_probe_rejected")
                    return value["result"]
                if time.monotonic() >= deadline:
                    raise InstallError("mcp_probe_timeout")
        try:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "android-use-upgrade-check", "version": "1"}}})
            initialized = response(1)
            if initialized["serverInfo"]["version"] != version:
                raise InstallError("mcp_version_mismatch")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in response(2)["tools"]}
            if not {"aua_setup", "aua_ready", "aua_observe"} <= names:
                raise InstallError("mcp_tools_missing")
            return {"version": version, "tool_count": len(names)}
        except (OSError, KeyError, TypeError):
            raise InstallError("mcp_probe_failed") from None
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()


def plugin_wrapper(root, release, python):
    wrapper = root / "plugin"
    wrapper.mkdir(exist_ok=True)
    for name in (".codex-plugin", "skills"):
        if (wrapper / name).exists():
            shutil.rmtree(wrapper / name)
        shutil.copytree(release / name, wrapper / name)
    write_json(wrapper / ".mcp.json", {"mcpServers": {"android_use": {
        "type": "stdio", "command": python, "args": [str(root / "launcher.py"), "serve"]}}})
    write_json(root / ".agents/plugins/marketplace.json", {
        "name": MARKET, "interface": {"displayName": "Android Use Managed"}, "plugins": [{
            "name": "android-use", "source": {"source": "local", "path": "./plugin"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity"}]})


def install(source, root=None, home=None, project=None, uv=None, codex=False,
            migrate=False, env_file=None, python="3.13"):
    root = Path(root or install_root()).resolve()
    source = Path(source).resolve()
    home = Path(home or codex_root()).resolve()
    project = Path(project or Path.cwd()).resolve()
    if source == root or source.is_relative_to(root):
        raise InstallError("source_inside_install: use a separate verified release")
    issues = diagnose(home, project)["issues"]
    if codex and issues and not migrate:
        raise InstallError("configuration_conflict: run doctor, then use --migrate-codex")
    if codex and any(i["scope"] == "project" for i in issues):
        raise InstallError("project_configuration_conflict: resolve the project override first")
    config = home / "config.toml"
    original_config = config.read_bytes() if config.exists() else None
    config_text = original_config.decode() if original_config else ""
    migrated_text, previous = migrate_config(config_text) if codex else (config_text, {})
    # Disabling an existing plugin is migration too, even without a direct MCP override.
    if codex and not migrate and migrated_text != config_text:
        raise InstallError("configuration_conflict: use --migrate-codex for the existing install")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / "upgrade.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise InstallError("upgrade_in_progress: inspect upgrade.lock if an installer crashed") from None
    release = None
    backup = None
    changed = False
    config_written = False
    try:
        manifest = read_json(source / ".codex-plugin/plugin.json")
        version = manifest["version"] if manifest else ""
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.-]+)?", version):
            raise InstallError("invalid_release_version")
        for name in ("pyproject.toml", "uv.lock", "src/android_use/server.py", "skills"):
            if not (source / name).exists():
                raise InstallError("incomplete_release")
        if tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"] != version:
            raise InstallError("release_version_mismatch")
        uv = str(Path(uv or shutil.which("uv") or "").resolve())
        if not Path(uv).is_file():
            raise InstallError("uv_missing")
        base_python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
        if "/plugins/cache/" in base_python.replace("\\", "/"):
            raise InstallError("unstable_python: run installer with uv-managed Python")
        profile = read_json(root / "launch.json", {})
        profile["env"] = {**profile.get("env", {}), **previous.get("env", {})}
        old_args = previous.get("args", [])
        if not env_file and "--env-file" in old_args:
            i = old_args.index("--env-file") + 1
            if i >= len(old_args):
                raise InstallError("invalid_environment_argument")
            env_file = old_args[i]
        environment = Path(env_file).expanduser().read_bytes() if env_file else None
        if environment is not None:
            profile["env_file"] = "launch.env"
        release = root / "releases" / (version + "-" + uuid.uuid4().hex[:12])
        release.mkdir(parents=True)
        for path, rel in source_files(source):
            if path.is_symlink():
                raise InstallError("source_symlink")
            dest = release / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        subprocess.run([uv, "sync", "--frozen", "--python", python, "--project", str(release)],
                       check=True, stdout=sys.stderr, stderr=sys.stderr)
        current = {"release": str(release.relative_to(root)), "version": version, "uv": uv}
        candidate_profile = copy.deepcopy(profile)
        # Probe the candidate using the original environment file, before any live file changes.
        if environment is not None:
            candidate_profile.pop("env_file", None)
        args, env = launch_arguments(root, current, candidate_profile)
        probe_env = release / ".upgrade.env"
        if environment is not None:
            atomic_write(probe_env, environment)
            args[-1:-1] = ["--env-file", probe_env.name]
        try:
            verified = probe(args, env, version)
        finally:
            probe_env.unlink(missing_ok=True)
        if (config.read_bytes() if config.exists() else None) != original_config:
            raise InstallError("configuration_changed_during_upgrade: retry after other edits finish")
        backup = root / "backups" / uuid.uuid4().hex
        backup.mkdir(parents=True, mode=0o700)
        tracked = ["launcher.py", "current.json", "launch.json", "launch.env",
                   ".agents/plugins/marketplace.json"]
        existed = {}
        for name in tracked:
            path = root / name
            existed[name] = path.exists()
            if path.exists():
                dst = backup / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dst)
                os.chmod(dst, 0o600)
        if (root / "plugin").exists():
            shutil.copytree(root / "plugin", backup / "plugin")
        if original_config is not None:
            atomic_write(backup / "codex-config.toml", original_config)
        write_json(backup / "files.json", existed)
        changed = True
        atomic_write(root / "launcher.py", Path(__file__).read_bytes())
        if environment is not None:
            atomic_write(root / "launch.env", environment)
        write_json(root / "launch.json", profile)
        write_json(root / "current.json", current)
        # The generated plugin launches the stable file, never a versioned cache path.
        plugin_wrapper(root, release, base_python)
        verified = probe([base_python, str(root / "launcher.py"), "serve"],
                         os.environ.copy(), version)
        if codex:
            if (config.read_bytes() if config.exists() else None) != original_config:
                raise InstallError("configuration_changed_during_upgrade")
            atomic_write(config, migrated_text.encode())
            config_written = True
            command = shutil.which("codex")
            if not command:
                raise InstallError("codex_missing")
            cli_env = {**os.environ, "CODEX_HOME": str(home)}
            subprocess.run([command, "plugin", "marketplace", "add", str(root)],
                           env=cli_env, check=True, stdout=sys.stderr, stderr=sys.stderr)
            added = subprocess.run([command, "plugin", "add", PLUGIN, "--json"],
                                   env=cli_env, check=True, stdout=subprocess.PIPE,
                                   stderr=sys.stderr, text=True)
            installed = json.loads(added.stdout)
            if installed.get("version") != version or installed.get("pluginId") != PLUGIN:
                raise InstallError("plugin_registration_mismatch")
            cached = Path(installed["installedPath"]) / ".mcp.json"
            if read_json(cached) != read_json(root / "plugin/.mcp.json"):
                raise InstallError("cached_launcher_mismatch")
            if not diagnose(home, project)["ok"]:
                raise InstallError("post_install_configuration_conflict")
        return {"ok": True, **verified, "root": str(root), "backup": str(backup),
                "mcp_config": {"command": base_python, "args": [str(root / "launcher.py"), "serve"]},
                "restart_required": True, "phone_actions": False}
    except Exception as exc:
        if config_written:
            try:
                external_edit = unrelated_config(read_toml(config)) != unrelated_config(
                    tomllib.loads(config_text))
            except (OSError, ValueError):
                external_edit = True
            if external_edit:
                # The candidate was already verified. Keep a working launcher,
                # rather than deleting it or clobbering someone else's edits.
                raise InstallError(
                    "concurrent_configuration_edit_runtime_retained: inspect backup and retry"
                ) from exc
        if changed:
            for name, exists in read_json(backup / "files.json").items():
                path = root / name
                if exists:
                    atomic_write(path, (backup / name).read_bytes())
                else:
                    path.unlink(missing_ok=True)
            if (root / "plugin").exists():
                shutil.rmtree(root / "plugin")
            if (backup / "plugin").exists():
                shutil.copytree(backup / "plugin", root / "plugin")
            if config_written:
                if original_config is None:
                    config.unlink(missing_ok=True)
                else:
                    atomic_write(config, original_config)
        if release is not None and release.exists():
            shutil.rmtree(release)
        raise
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description="Android Use stable installation and upgrade")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--codex-home", type=Path)
    doctor.add_argument("--project", type=Path)
    command = sub.add_parser("install")
    command.add_argument("--source", type=Path, default=Path.cwd())
    command.add_argument("--root", type=Path)
    command.add_argument("--codex-home", type=Path)
    command.add_argument("--project", type=Path)
    command.add_argument("--uv")
    command.add_argument("--python", default="3.13")
    command.add_argument("--codex", action="store_true")
    command.add_argument("--migrate-codex", action="store_true")
    command.add_argument("--env-file", type=Path)
    sub.add_parser("serve")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            launch, env = launch_arguments(Path(__file__).resolve().parent)
            os.execve(launch[0], launch, env)
        elif args.command == "doctor":
            result = diagnose(args.codex_home, args.project)
        else:
            result = install(args.source, args.root, args.codex_home, args.project, args.uv,
                             args.codex or args.migrate_codex, args.migrate_codex,
                             args.env_file, args.python)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        output = sys.stderr if args.command == "serve" else sys.stdout
        print(json.dumps({"ok": False, "error": message}), file=output)
        return 1


if __name__ == "__main__":
    sys.exit(main())
