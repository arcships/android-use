import json
from importlib.resources import files

import pytest

from android_use.hotswap import engine


@pytest.fixture
def gradle_root(tmp_path):
    root = tmp_path / "shop"
    (root / "app/src").mkdir(parents=True)
    (root / "settings.gradle.kts").write_text("")
    (root / "gradlew").write_text("")
    return root


def config(root):
    return json.loads((root / engine.AUTO_CONFIG).read_text())


def test_gradle_root_needs_no_setup_and_keeps_project_files_untouched(gradle_root):
    before = sorted(p.name for p in gradle_root.iterdir())
    assert engine.locate(gradle_root) == gradle_root
    assert config(gradle_root) == {
        "type": "gradle",
        "module": ":app",
        "variant": "Debug",
        "gradle": [str(gradle_root / "gradlew")],
    }
    assert sorted(p.name for p in gradle_root.iterdir()) == sorted([*before, "build"])


def test_module_directory_selects_that_module(gradle_root):
    (gradle_root / "feature/pay").mkdir(parents=True)
    assert engine.locate(gradle_root / "feature/pay") == gradle_root
    assert config(gradle_root)["module"] == ":feature:pay"


def test_settings_are_kept_until_explicitly_changed(gradle_root):
    engine.locate(gradle_root, module=":wear", variant="staging")
    assert (config(gradle_root)["module"], config(gradle_root)["variant"]) == (":wear", "Staging")
    stamp = (gradle_root / engine.AUTO_CONFIG).stat().st_mtime_ns
    engine.locate(gradle_root)  # Unchanged file: the next swap stays a swap.
    assert (gradle_root / engine.AUTO_CONFIG).stat().st_mtime_ns == stamp
    assert config(gradle_root)["module"] == ":wear"
    engine.locate(gradle_root, variant="Debug")
    assert config(gradle_root)["variant"] == "Debug"


def test_hand_written_config_wins(gradle_root):
    (gradle_root / "hotswap.json").write_text("{}")
    assert engine.locate(gradle_root / "app") == gradle_root
    assert not (gradle_root / engine.AUTO_CONFIG).exists()


def test_unknown_project_is_reported(tmp_path):
    for target in (tmp_path, tmp_path / "missing"):
        with pytest.raises(engine.Fail) as failure:
            engine.locate(target)
        assert failure.value.code == "project_invalid"


def test_command_line_text():
    swapped = {"result": "swapped", "class_count": 2, "resources": True, "seconds": 0.5,
               "verdict": "OK redefined=2"}
    assert engine.describe(swapped) == "swapped 2 class(es) + resources in 0.50s: OK redefined=2"
    installed = {"result": "installed", "package": "a.b", "seconds": 3, "reason": "why"}
    assert engine.describe(installed).splitlines()[0] == "reinstall: why"
    assert engine.describe({"result": "unchanged"}) == "nothing to swap"


def test_sources_built_on_the_host_ship_with_the_package():
    package = files("android_use.hotswap")
    for name in (
        "agent/agent.cpp",
        "daemon/HotswapDaemon.java",
        "helper/hotswap/agent/HotswapState.java",
        "hotswap.init.gradle",
    ):
        assert package.joinpath(name).is_file()


class FakeTools:
    touched = False

    def __init__(self, serial):
        self.serial = serial


def test_runtime_passes_arguments_and_respects_takeover(rig, monkeypatch):
    runtime, backend = rig
    calls = []

    def run_action(action, project, **options):
        calls.append((action, project, options["refresh"], options["tools"].serial))
        if action == "status":
            return {"action": "status", "toolchain": {"ready": True}}
        return {"action": action, "result": "swapped", "package": "demo.app"}

    monkeypatch.setattr(engine, "Tools", FakeTools)
    monkeypatch.setattr(engine, "run_action", run_action)
    monkeypatch.setattr("android_use.runtime.time.sleep", lambda seconds: None)
    result = runtime.call("hot_swap", project="/work/app", observe="tree")
    assert result["result"] == "swapped" and result["verified"] is False
    assert result["observation"]["tree"]["total_nodes"] == 6
    assert calls == [("swap", "/work/app", "auto", runtime.serial)]
    assert backend.actions == []

    assert runtime.call("pause")["paused"]
    assert runtime.call("hot_swap", project="/work/app")["error"]["code"] == "user_takeover"
    assert runtime.call("hot_swap", project="/work/app", action="status")["ok"]
    assert [c[0] for c in calls] == ["swap", "status"]
    assert runtime.call("hot_swap", project="")["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("touched", [False, True])
def test_runtime_reports_failures_without_replay(rig, monkeypatch, touched):
    runtime, _ = rig

    def run_action(action, project, **options):
        options["tools"].touched = touched
        raise engine.Fail("javac failed:\nMain.java:3: error", "compile_failed")

    monkeypatch.setattr(engine, "Tools", FakeTools)
    monkeypatch.setattr(engine, "run_action", run_action)
    result = runtime.call("hot_swap", project="/work/app")
    assert result["error"]["code"] == "compile_failed"
    assert "Main.java:3" in result["error"]["message"]
    assert result["error"]["uncertain"] is touched
    assert result["action_attempted"] is touched and result["replay_action"] is False
