import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("runtime_builder", ROOT / "scripts/build_runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_build_inputs_retry_partial_download_then_atomically_reuse_verified_cache(
    tmp_path, monkeypatch
):
    calls = []
    data = b"pinned-build-input"
    item = {
        "filename": "upstream.zip",
        "url": "https://example.test/upstream.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    cached = tmp_path / item["filename"]
    cached.write_bytes(b"older corrupt cache")

    def fetch(url, destination):
        calls.append(url)
        destination.write_bytes(b"partial" if len(calls) == 1 else data)
        if len(calls) == 1:
            raise OSError("interrupted read")

    monkeypatch.setattr(builder, "fetch", fetch)
    monkeypatch.setattr(builder.time, "sleep", lambda _: None)
    assert builder.locked_input(tmp_path, item).read_bytes() == data
    assert builder.locked_input(tmp_path, item) == cached
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == [cached]


def test_wrong_upstream_checksum_never_replaces_cached_input(tmp_path, monkeypatch):
    cached = tmp_path / "upstream.zip"
    cached.write_bytes(b"existing")
    item = {"filename": cached.name, "url": "https://example.test/upstream.zip", "sha256": "0" * 64}
    monkeypatch.setattr(
        builder, "fetch", lambda _, destination: destination.write_bytes(b"untrusted")
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        builder.locked_input(tmp_path, item)
    assert cached.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [cached]


def test_statically_linked_optional_gpl_modules_are_rejected(monkeypatch):
    import runtime_licenses

    monkeypatch.setattr(runtime_licenses.sys, "builtin_module_names", ("_dbm", "_ssl"))
    with pytest.raises(ValueError, match="statically includes"):
        runtime_licenses.check_interpreter()
    monkeypatch.setattr(runtime_licenses.sys, "builtin_module_names", ("_ssl", "sys"))
    runtime_licenses.check_interpreter()


def test_native_package_contains_original_notices_and_mpl_source_form(tmp_path):
    import json

    import runtime_licenses

    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "app/android-use-runtime").write_bytes(b"test-native-runtime")
    (bundle / "app/Python").write_bytes(b"\xcf\xfa\xed\xfe" + b"test-framework-library")
    runtime_licenses.collect(ROOT, bundle)
    licenses = bundle / "licenses"
    assert (licenses / "CPython-LICENSE.txt").is_file()
    assert (licenses / "native/LICENSE.uiautomator-server.txt").is_file()
    assert (licenses / "sources/certifi/core.py").is_file()
    inventory = json.loads((licenses / "inventory.json").read_text())
    assert any(
        p["name"].lower() == "certifi" and p["declared_license"] == "MPL-2.0"
        for p in inventory["packages"]
    )
    assert {"app/android-use-runtime", "app/Python"} <= {
        item["file"] for item in inventory["native_files"]
    }


def test_unexpected_gdbm_native_binary_blocks_packaging(tmp_path):
    import runtime_licenses

    bundle = tmp_path / "bundle"
    (bundle / "app").mkdir(parents=True)
    (bundle / "app/libgdbm.so").write_bytes(b"unexpected-native-library")
    with pytest.raises(ValueError, match="DBM/readline binary"):
        runtime_licenses.collect(ROOT, bundle)


def test_changed_upstream_notice_blocks_packaging(tmp_path):
    import json

    import runtime_licenses

    root = tmp_path / "source"
    (root / "docs/licenses").mkdir(parents=True)
    (root / "LICENSE").write_text("Project license")
    (root / "THIRD_PARTY_NOTICES.md").write_text("Notices")
    (root / "docs/licenses/COPYING.txt").write_text("changed notice")
    (root / "docs/licenses/sources.json").write_text(
        json.dumps([{"file": "COPYING.txt", "sha256": "0" * 64}])
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(ValueError, match="notice was modified"):
        runtime_licenses.collect(root, bundle)


def test_git_checkout_preserves_pinned_license_bytes_under_windows_line_endings(tmp_path):
    import subprocess

    repository = tmp_path / "git checkout"
    (repository / "docs/licenses").mkdir(parents=True)
    hooks = repository / "empty-hooks"
    hooks.mkdir()
    original = b"upstream license\noriginal LF bytes\n"
    notice = repository / "docs/licenses/LICENSE.fixture.txt"
    notice.write_bytes(original)
    (repository / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())

    def git(*args):
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "core.autocrlf=true",
                "-c",
                "core.hooksPath=" + str(hooks),
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    git("init")
    git("add", ".")
    git("commit", "-m", "Fixture")
    notice.unlink()
    git("checkout", "--", "docs/licenses/LICENSE.fixture.txt")
    assert notice.read_bytes() == original
