import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from android_use import installation as inst


@pytest.fixture
def source(tmp_path):
    def create(version="1.0.0", name="download with spaces"):
        root = tmp_path / name
        files = {
            ".codex-plugin/plugin.json": json.dumps({"name": "android-use", "version": version,
                                                     "mcpServers": "./.mcp.json"}),
            "pyproject.toml": '[project]\nname="android-use"\nversion="' + version + '"\n',
            "uv.lock": "lock", "skills/android-use/SKILL.md": "skill", ".mcp.json": "{}",
            "src/android_use/server.py": '''import json, sys
from pathlib import Path
import tomllib
version=tomllib.loads((Path(__file__).parents[2]/'pyproject.toml').read_text())['project']['version']
for line in sys.stdin:
    msg=json.loads(line)
    if msg['method']=='initialize':
        result={'serverInfo':{'version':version}}
    elif msg['method']=='tools/list':
        result={'tools':[{'name':n} for n in ('aua_setup','aua_ready','aua_observe')]}
    else: continue
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
''',
            "src/android_use/assets/phone-screen.html": "compiled screen",
            ".env": "SECRET=must-not-copy", ".venv/secret": "excluded",
            "ui/node_modules/secret": "excluded", "ui/test-results/secret": "excluded",
        }
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return root
    return create


@pytest.fixture
def uv(tmp_path):
    if os.name == "nt":
        pytest.skip("fake executable fixture uses a POSIX shebang")
    path = tmp_path / "fake-uv"
    path.write_text('#!' + str(Path(sys.executable).resolve()) + '''
import os,sys
from pathlib import Path
if sys.argv[1]=='sync': sys.exit(0)
root=Path(sys.argv[sys.argv.index('--directory')+1])
os.execv(sys.executable,[sys.executable,str(root/'src/android_use/server.py')])
''')
    path.chmod(0o755)
    return path


def do_install(source, uv, tmp_path, **kwargs):
    return inst.install(source, root=tmp_path / "stable install", home=tmp_path / "codex",
                        project=tmp_path / "project", uv=uv, **kwargs)


def test_upgrade_survives_deleted_sources_and_plugin_caches(source, uv, tmp_path):
    first = source()
    result = do_install(first, uv, tmp_path)
    root = Path(result["root"])
    config = result["mcp_config"]
    before_launcher = config.copy()
    old = inst.read_json(root / "current.json")
    old_cache = tmp_path / ".codex/plugins/cache/android-use/1.0.0"
    old_cache.mkdir(parents=True)
    second = source("2.0.0", "new archive")
    result = do_install(second, uv, tmp_path)
    assert result["mcp_config"] == before_launcher
    shutil.rmtree(first)
    shutil.rmtree(second)
    shutil.rmtree(old_cache.parent.parent)
    # Every invocation is a new host process; the stable command still handshakes.
    inst.probe([config["command"], *config["args"]], os.environ.copy(), "2.0.0")
    current = inst.read_json(root / "current.json")
    assert current != old and (root / old["release"]).is_dir()
    release = root / current["release"]
    assert (release / "src/android_use/assets/phone-screen.html").read_text() == "compiled screen"
    assert not (release / ".env").exists()
    assert not (release / ".venv/secret").exists()
    wrapper = json.loads((root / "plugin/.mcp.json").read_text())
    assert wrapper["mcpServers"]["android_use"]["args"] == config["args"]
    if os.name != "nt":
        assert (root / "current.json").stat().st_mode & 0o777 == 0o600


def test_failed_handshake_keeps_previous_runtime_and_settings(source, uv, tmp_path):
    first = source()
    do_install(first, uv, tmp_path)
    root = tmp_path / "stable install"
    before = (root / "current.json").read_bytes()
    second = source("2.0.0", "broken upgrade")
    (second / "src/android_use/server.py").write_text("raise SystemExit(1)")
    with pytest.raises(inst.InstallError, match="mcp_probe_failed"):
        do_install(second, uv, tmp_path)
    assert (root / "current.json").read_bytes() == before
    assert len(list((root / "releases").iterdir())) == 1
    inst.probe(inst.launch_arguments(root)[0], os.environ.copy(), "1.0.0")


def test_failed_dependency_install_never_changes_pointer(source, uv, tmp_path, monkeypatch):
    first = source()
    do_install(first, uv, tmp_path)
    root = tmp_path / "stable install"
    before = (root / "current.json").read_bytes()
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["uv", "sync"])
    monkeypatch.setattr(inst.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        do_install(source("2.0.0", "dependency failure"), uv, tmp_path)
    assert (root / "current.json").read_bytes() == before
    assert not (root / "upgrade.lock").exists()


def test_concurrent_upgrade_refuses_before_mutation(source, uv, tmp_path):
    root = tmp_path / "stable install"
    (root / "upgrade.lock").mkdir(parents=True)
    with pytest.raises(inst.InstallError, match="upgrade_in_progress"):
        do_install(source(), uv, tmp_path)
    assert not (root / "current.json").exists()


OLD_CONFIG = '''# Keep my comments.
model = "example"
[mcp_servers.android_use]
command = "uv"
args = ["run", "--env-file", "ENV_FILE", "--directory", "/missing/.codex/plugins/cache/android-use/1.0.0", "android-use-mcp"]
[mcp_servers.android_use.env]
ANDROID_USE_ADB = "/custom/adb"
[mcp_servers.other]
command = "other"
args = ["unchanged"]
[plugins."android-use@android-use-local"]
enabled = true
[plugins."other@market"]
enabled = true
'''


def test_diagnostics_are_read_only_and_do_not_expose_secrets(tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    path = home / "config.toml"
    original = OLD_CONFIG.replace('command = "uv"', 'command = "/missing/uv"')
    path.write_text(original)
    result = inst.diagnose(home, tmp_path)
    assert {i["code"] for i in result["issues"]} == {
        "mcp_plugin_conflict", "versioned_cache_path", "missing_launch_path", "missing_launcher"}
    assert path.read_text() == original
    assert "ENV_FILE" not in json.dumps(result)
    assert "/custom/adb" not in json.dumps(result)


def test_migration_preserves_unrelated_config_and_comments():
    updated, previous = inst.migrate_config(OLD_CONFIG)
    config = tomllib.loads(updated)
    assert "# Keep my comments." in updated
    assert "android_use" not in config["mcp_servers"]
    assert config["mcp_servers"]["other"] == {"command": "other", "args": ["unchanged"]}
    assert not config["plugins"]["android-use@android-use-local"]["enabled"]
    assert config["plugins"]["other@market"]["enabled"]
    assert previous["env"] == {"ANDROID_USE_ADB": "/custom/adb"}


@pytest.mark.parametrize("header", ['[mcp_servers.android_use]', '[mcp_servers."android_use"]'])
def test_migrate_single_server_and_quoted_headers(header):
    updated, previous = inst.migrate_config(header + '\nargs=["android-use-mcp"]\n')
    assert not tomllib.loads(updated).get("mcp_servers")
    assert previous["args"] == ["android-use-mcp"]


@pytest.mark.parametrize("config", [
    '[mcp_servers.android_use]\ncommand="unrecognized"\n',
    'mcp_servers={android_use={args=["android-use-mcp"]}}\n',
])
def test_migration_refuses_unrecognized_or_inline_layout(config):
    with pytest.raises(inst.InstallError):
        inst.migrate_config(config)


def test_project_override_is_detected_even_with_user_plugin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('[plugins."android-use@android-use-local"]\nenabled=true\n')
    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex/config.toml").write_text('[mcp_servers.android_use]\ncommand="uv"\n')
    result = inst.diagnose(home, project)
    assert {i["code"] for i in result["issues"]} == {"project_mcp_override", "mcp_plugin_conflict"}
    (project / ".codex/config.toml").write_text(
        '[plugins."android-use@android-use-local"]\nenabled=false\n')
    assert inst.diagnose(home, project)["ok"]


def test_codex_conflict_requires_explicit_migration(source, uv, tmp_path):
    home = tmp_path / "codex"
    home.mkdir()
    path = home / "config.toml"
    path.write_text(OLD_CONFIG)
    with pytest.raises(inst.InstallError, match="configuration_conflict"):
        do_install(source(), uv, tmp_path, codex=True)
    assert path.read_text() == OLD_CONFIG
    assert not (tmp_path / "stable install").exists()


def test_registration_failure_rolls_back_environment_pointer_and_config(source, uv, tmp_path,
                                                                        monkeypatch):
    do_install(source(), uv, tmp_path)
    root = tmp_path / "stable install"
    home = tmp_path / "codex"
    home.mkdir()
    env_file = tmp_path / "private.env"
    env_file.write_text("TYPESAFE_API_KEY=private-secret\n")
    path = home / "config.toml"
    original = OLD_CONFIG.replace("ENV_FILE", str(env_file))
    path.write_text(original)
    before = {name: (root / name).read_bytes() for name in
              ("current.json", "launch.json", "launcher.py", "plugin/.mcp.json")}
    run = inst.subprocess.run
    def fail_register(args, **kwargs):
        if "plugin" in args:
            raise subprocess.CalledProcessError(1, args)
        return run(args, **kwargs)
    monkeypatch.setattr(inst.subprocess, "run", fail_register)
    monkeypatch.setattr(inst.shutil, "which", lambda name: str(uv))
    with pytest.raises(subprocess.CalledProcessError):
        do_install(source("2.0.0", "registration failure"), uv, tmp_path, codex=True, migrate=True)
    assert path.read_text() == original
    assert not (root / "launch.env").exists()
    for name, value in before.items():
        assert (root / name).read_bytes() == value
    assert len(list((root / "releases").iterdir())) == 1


def test_bad_pointer_cannot_escape_install_directory(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    with pytest.raises(inst.InstallError, match="installed_runtime_missing"):
        inst.launch_arguments(tmp_path, {"release": str(external), "uv": "uv"})


def test_missing_runtime_fails_without_stdout_protocol_pollution(tmp_path):
    launch = tmp_path / "launcher.py"
    shutil.copyfile(inst.__file__, launch)
    result = subprocess.run([sys.executable, str(launch), "serve"], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "not_installed" in result.stderr


def test_successful_codex_migration_keeps_env_and_other_plugins(source, uv, tmp_path,
                                                               monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    original_env = tmp_path / "original.env"
    original_env.write_text("TYPESAFE_API_KEY=private-secret\n")
    config = home / "config.toml"
    config.write_text(OLD_CONFIG.replace("ENV_FILE", str(original_env)))
    real_run = inst.subprocess.run
    def register(args, **kwargs):
        if "plugin" not in args:
            return real_run(args, **kwargs)
        if "--json" in args:
            with config.open("a") as f:
                f.write('\n[plugins."' + inst.PLUGIN + '"]\nenabled=true\n')
        payload = json.dumps({"version": "1.0.0", "pluginId": inst.PLUGIN,
                              "installedPath": str(tmp_path / "stable install/plugin")})
        return subprocess.CompletedProcess(args, 0, stdout=payload)
    monkeypatch.setattr(inst.subprocess, "run", register)
    monkeypatch.setattr(inst.shutil, "which", lambda name: str(uv))
    result = do_install(source(), uv, tmp_path, codex=True, migrate=True)
    root = Path(result["root"])
    original_env.unlink()
    assert (root / "launch.env").read_text() == "TYPESAFE_API_KEY=private-secret\n"
    profile = inst.read_json(root / "launch.json")
    assert profile["env"] == {"ANDROID_USE_ADB": "/custom/adb"}
    assert profile["env_file"] == "launch.env"
    cfg = tomllib.loads(config.read_text())
    assert "android_use" not in cfg["mcp_servers"]
    assert cfg["mcp_servers"]["other"]["command"] == "other"
    assert cfg["plugins"]["other@market"]["enabled"]
    assert inst.diagnose(home, tmp_path / "project")["ok"]
    assert "private-secret" not in json.dumps(result)
    assert "plugins/cache" not in json.dumps(result["mcp_config"])
    if os.name != "nt":
        assert (root / "launch.env").stat().st_mode & 0o777 == 0o600


def test_concurrent_config_edit_is_preserved_during_rollback(source, uv, tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    config = home / "config.toml"
    config.write_text('model="before"\n')
    wrapper = inst.plugin_wrapper
    def edit(*args):
        wrapper(*args)
        config.write_text('model="edited-by-user"\n')
    monkeypatch.setattr(inst, "plugin_wrapper", edit)
    with pytest.raises(inst.InstallError, match="configuration_changed"):
        do_install(source(), uv, tmp_path, codex=True)
    assert config.read_text() == 'model="edited-by-user"\n'
    assert not (tmp_path / "stable install/current.json").exists()


def test_real_mcp_upgrade_and_fresh_host_after_deleted_sources(tmp_path, monkeypatch):
    """Actual Android Use SDK processes and uv environments; no device/model calls."""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required for the real upgrade regression")
    monkeypatch.setenv("UV_OFFLINE", "1")
    checkout = Path(__file__).resolve().parents[1]
    previous = tmp_path / "old release source"
    latest = tmp_path / "latest release source"
    version = json.loads((checkout / ".codex-plugin/plugin.json").read_text())["version"]
    for source_root, ver in [(previous, "0.0.1"), (latest, version)]:
        for path, rel in inst.source_files(checkout):
            dst = source_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dst)
        for name in (".codex-plugin/plugin.json", "pyproject.toml", "uv.lock",
                     "src/android_use/__init__.py"):
            path = source_root / name
            # Only this project's own entry: a locked dependency may share the version.
            old = f'name = "android-use"\nversion = "{version}"' if name == "uv.lock" else version
            assert path.read_text().count(old) == 1
            path.write_text(path.read_text().replace(old, old.replace(version, ver)))
    root = tmp_path / "stable install with spaces"
    env_file = tmp_path / "environment with spaces.env"
    env_file.write_text("ANDROID_USE_UPGRADE_SENTINEL=private-fixture\n")
    first = inst.install(previous, root, tmp_path / "home", tmp_path, uv, env_file=env_file)
    second = inst.install(latest, root, tmp_path / "home", tmp_path, uv)
    assert first["mcp_config"] == second["mcp_config"]
    shutil.rmtree(previous)
    shutil.rmtree(latest)
    env_file.unlink()
    assert not list((root / "releases").rglob(".upgrade.env"))
    config = second["mcp_config"]
    verified = inst.probe([config["command"], *config["args"]], os.environ.copy(), version)
    assert verified["tool_count"] >= 26


def test_registration_failure_does_not_overwrite_concurrent_config_edit(source, uv, tmp_path,
                                                                       monkeypatch):
    do_install(source(), uv, tmp_path)
    home = tmp_path / "codex"
    home.mkdir()
    config = home / "config.toml"
    config.write_text('model="before"\n')
    real_run = inst.subprocess.run
    def fail_with_edit(args, **kwargs):
        if "plugin" in args:
            config.write_text('model="edited-by-user"\n')
            raise subprocess.CalledProcessError(1, args)
        return real_run(args, **kwargs)
    monkeypatch.setattr(inst.subprocess, "run", fail_with_edit)
    monkeypatch.setattr(inst.shutil, "which", lambda name: str(uv))
    with pytest.raises(inst.InstallError, match="concurrent_configuration_edit_runtime_retained"):
        do_install(source("2.0.0", "concurrent edit"), uv, tmp_path, codex=True)
    assert config.read_text() == 'model="edited-by-user"\n'
    root = tmp_path / "stable install"
    assert inst.read_json(root / "current.json")["version"] == "2.0.0"
    inst.probe(inst.launch_arguments(root)[0], os.environ.copy(), "2.0.0")
