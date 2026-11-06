import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from android_use import portable as runtime
from android_use.installation import InstallError, probe


@pytest.fixture(autouse=True)
def isolated_download_credentials(monkeypatch):
    monkeypatch.delenv("ANDROID_USE_GITHUB_TOKEN", raising=False)


@pytest.fixture
def bundle(tmp_path):
    if os.name == "nt":
        pytest.skip(
            "Protocol fixture uses a POSIX executable; native Windows acceptance uses the built EXE"
        )

    def create(version="1.0.0", platform=None, bad_health=False, bad_protocol=False, extras=None):
        archive = tmp_path / (version + "-" + str(len(list(tmp_path.glob("*.zip")))) + ".zip")
        code = f"""#!{Path(sys.executable).resolve()}
import json,sys
VERSION={version!r}
if sys.argv[-1]=='health':
    print(json.dumps({{'ok':{not bad_health!r},'version':VERSION}}));sys.exit()
for line in sys.stdin:
    msg=json.loads(line)
    if msg['method']=='initialize': result={{'serverInfo':{{'version':VERSION + {".bad" if bad_protocol else ""!r}}}}}
    elif msg['method']=='tools/list': result={{'tools':[{{'name':n}} for n in ('aua_setup','aua_ready','aua_observe')]}}
    else: continue
    print(json.dumps({{'jsonrpc':'2.0','id':msg['id'],'result':result}}),flush=True)
"""
        with ZipFile(archive, "w") as zipfile:
            zipfile.writestr(
                "runtime.json",
                json.dumps(
                    {"schema": 1, "version": version, "platform": platform or runtime.target()}
                ),
            )
            entry = ZipInfo("app/android-use-runtime")
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            zipfile.writestr(entry, code)
            for name, data in (extras or {}).items():
                zipfile.writestr(name, data)
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    return create


def test_install_upgrade_rollback_stable_entry_and_settings_survive(tmp_path, bundle):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    preferences = root / "private.env"
    preferences.write_text("SENTINEL=private-local\n")
    first = runtime.activate(root, *bundle())
    command = first["mcp_config"]
    # For lifecycle tests avoid the automatic background update dispatch.
    launch = [command["command"], *command["args"][:-1], "serve"]
    probe(launch, {**os.environ, "PATH": str(tmp_path / "empty-path")}, "1.0.0")
    second = runtime.activate(root, *bundle("2.0.0"))
    assert second["mcp_config"] == command and second["previous"] == first["release"]
    # Remove original archives: neither runtime needs their source or download directory.
    for archive in tmp_path.glob("*.zip"):
        archive.unlink()
    probe(launch, os.environ.copy(), "2.0.0")
    rolled = runtime.rollback(root)
    assert rolled["version"] == "1.0.0"
    probe(launch, os.environ.copy(), "1.0.0")
    assert runtime.read_json(root / "update-policy.json")["paused"]
    assert preferences.read_text() == "SENTINEL=private-local\n"
    assert len(list((root / "releases").iterdir())) == 2


@pytest.mark.parametrize("failure", ["digest", "platform", "health", "protocol", "version"])
def test_failed_upgrade_keeps_active_release_and_removes_candidate(tmp_path, bundle, failure):
    root = tmp_path / "install"
    first = runtime.activate(root, *bundle())
    before = (root / "current.path").read_bytes()
    archive, digest = bundle(
        "2.0.0",
        platform="wrong" if failure == "platform" else None,
        bad_health=failure == "health",
        bad_protocol=failure == "protocol",
    )
    if failure == "digest":
        digest = "0" * 64
    with pytest.raises(InstallError):
        runtime.activate(
            root, archive, digest, expected_version="3.0.0" if failure == "version" else None
        )
    assert (root / "current.path").read_bytes() == before
    assert runtime.status(root)["release"] == first["release"]
    assert len(list((root / "releases").iterdir())) == 1
    assert not (root / "runtime.lock").exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside",
        "/absolute",
        "app/../../outside",
        "C:/outside",
        "app\\outside",
        "./runtime.json",
        "RUNTIME.JSON",
        "app/.. /outside",
        "app/CON.txt",
    ],
)
def test_archive_paths_and_case_collisions_refused_before_write(tmp_path, bundle, unsafe):
    root = tmp_path / "install"
    with pytest.raises(InstallError, match="unsafe_runtime_archive"):
        runtime.activate(root, *bundle(extras={unsafe: "bad"}))
    assert not (root / "current.path").exists()
    assert not (tmp_path / "outside").exists()


def test_symlink_archive_refused(tmp_path, bundle):
    archive, _ = bundle()
    with ZipFile(archive, "a") as output:
        link = ZipInfo("app/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, "../../outside")
    with pytest.raises(InstallError, match="unsafe_runtime_archive"):
        runtime.activate(
            tmp_path / "runtime", archive, hashlib.sha256(archive.read_bytes()).hexdigest()
        )


def test_concurrent_install_and_failed_rollback_leave_pointer_unchanged(
    tmp_path, bundle, monkeypatch
):
    root = tmp_path / "install"
    runtime.activate(root, *bundle())
    runtime.activate(root, *bundle("2.0.0"))
    before = (root / "current.path").read_bytes()
    (root / "runtime.lock").mkdir()
    with pytest.raises(InstallError, match="upgrade_in_progress"):
        runtime.activate(root, *bundle("3.0.0"))
    (root / "runtime.lock").rmdir()
    monkeypatch.setattr(runtime, "verify", lambda _: (_ for _ in ()).throw(InstallError("failed")))
    with pytest.raises(InstallError, match="failed"):
        runtime.rollback(root)
    assert (root / "current.path").read_bytes() == before


def test_atomic_promotion_failure_leaves_old_runtime(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    runtime.activate(root, *bundle())
    before = (root / "current.path").read_bytes()
    original = runtime.atomic_write

    def fail(path, data):
        if path.name == "current.path":
            raise OSError("simulated disk failure")
        return original(path, data)

    monkeypatch.setattr(runtime, "atomic_write", fail)
    with pytest.raises(OSError):
        runtime.activate(root, *bundle("2.0.0"))
    assert (root / "current.path").read_bytes() == before
    assert len(list((root / "releases").iterdir())) == 1


def release_downloads(monkeypatch, archive, digest, *, version="2.0.0", checksum=None):
    name = f"android-use-runtime-{runtime.target()}.zip"
    prefix = f"https://github.com/{runtime.REPOSITORY}/releases/download/v{version}/"
    info = {
        "tag_name": "v" + version,
        "assets": [
            {"name": item, "browser_download_url": prefix + item} for item in [name, "SHA256SUMS"]
        ],
    }
    requested = []

    def fetch(url, destination, limit=runtime.MAX_DOWNLOAD):
        requested.append(url)
        if "api.github.com" in url:
            destination.write_text(json.dumps(info))
        elif url.endswith("SHA256SUMS"):
            destination.write_text(
                checksum if checksum is not None else digest + "  " + name + "\n"
            )
        else:
            destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(runtime, "fetch", fetch)
    return info, requested


def test_download_update_validates_release_and_preserves_old_on_network_failure(
    tmp_path, bundle, monkeypatch
):
    root = tmp_path / "install"
    first = runtime.activate(root, *bundle())
    release_downloads(monkeypatch, *bundle("2.0.0"))
    assert runtime.checked_update(root)["version"] == "2.0.0"
    assert not runtime.read_json(root / "update-policy.json")["paused"]
    runtime.rollback(root)
    assert runtime.status(root)["release"] == first["release"]
    monkeypatch.setattr(runtime, "fetch", lambda *_: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError):
        runtime.checked_update(root)
    assert runtime.status(root)["version"] == "1.0.0"
    assert not (root / "download.lock").exists()


@pytest.mark.parametrize("failure", ["asset", "checksum", "archive_version", "prerelease"])
def test_invalid_download_never_promotes(tmp_path, bundle, monkeypatch, failure):
    root = tmp_path / "install"
    runtime.activate(root, *bundle())
    info, _ = release_downloads(
        monkeypatch,
        *bundle("9.0.0" if failure == "archive_version" else "2.0.0"),
        checksum="" if failure == "checksum" else None,
    )
    if failure == "asset":
        info["assets"][0]["browser_download_url"] = "https://untrusted.example/payload"
    if failure == "prerelease":
        info["prerelease"] = True
    with pytest.raises(InstallError):
        runtime.checked_update(root)
    assert runtime.status(root)["version"] == "1.0.0"


def test_automatic_check_cooldown_and_rollback_pause(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    runtime.activate(root, *bundle())
    calls = []
    monkeypatch.setattr(runtime, "update", lambda *_: calls.append(True))
    runtime.checked_update(root, automatic=True)
    assert not calls
    runtime.write_json(root / "last-check.json", {"time": 0})
    runtime.write_json(root / "update-policy.json", {"paused": True})
    runtime.checked_update(root, automatic=True)
    assert not calls


def test_frozen_daemon_dispatch_and_bundled_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app/android-use-runtime"))
    for name in ("ANDROID_USE_ADB", "ANDROID_USE_FFMPEG", "ANDROID_USE_OCR", "ADBUTILS_ADB_PATH"):
        monkeypatch.setenv(name, "/stale/runtime/tool")
    runtime.configure_bundle()
    assert os.environ["ANDROID_USE_ADB"] == str(
        tmp_path / "tools/adb" / ("adb.exe" if os.name == "nt" else "adb")
    )
    assert os.environ["ANDROID_USE_FFMPEG"].startswith(str(tmp_path / "tools"))


def test_stale_download_cannot_replace_a_more_recent_install(tmp_path, bundle):
    root = tmp_path / "install"
    first = runtime.activate(root, *bundle())
    current = runtime.activate(root, *bundle("3.0.0"))
    with pytest.raises(InstallError, match="runtime_changed_during_download"):
        runtime.activate(root, *bundle("2.0.0"), expected_current=first["release"])
    assert runtime.status(root)["release"] == current["release"]


def test_latest_check_never_downgrades_a_newer_installed_version(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    runtime.activate(root, *bundle("3.0.0"))
    _, requested = release_downloads(monkeypatch, *bundle("2.0.0"))
    assert runtime.checked_update(root)["version"] == "3.0.0"
    assert len(requested) == 1


def test_corrupt_update_metadata_does_not_block_current_mcp(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    first = runtime.activate(root, *bundle())
    (root / "last-check.json").write_text("broken JSON")
    monkeypatch.setenv("ANDROID_USE_AUTO_UPDATE", "1")
    selected = []
    monkeypatch.setattr(os, "execv", lambda entry, args: selected.append(entry))
    runtime.auto_serve(root)
    assert selected == [str(runtime.executable(root / "releases" / first["release"]))]


def test_background_process_failure_still_launches_current_runtime(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    first = runtime.activate(root, *bundle())
    runtime.write_json(root / "last-check.json", {"time": 0})
    monkeypatch.setenv("ANDROID_USE_AUTO_UPDATE", "1")
    monkeypatch.setattr(
        runtime.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("unavailable"))
    )
    selected = []
    monkeypatch.setattr(os, "execv", lambda entry, args: selected.append(entry))
    runtime.auto_serve(root)
    assert selected == [str(runtime.executable(root / "releases" / first["release"]))]


def test_optional_ocr_install_and_failed_upgrade_preserve_component(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX executable fixture")
    monkeypatch.setattr(runtime, "target", lambda: "macos-arm64")
    root = tmp_path / "install"

    def component(api="vision-v1"):
        archive = tmp_path / (api + ".zip")
        with ZipFile(archive, "w") as output:
            output.writestr(
                "ocr.json", json.dumps({"schema": 1, "api": api, "platform": "macos-arm64"})
            )
            binary = ZipInfo("ocr")
            binary.external_attr = (stat.S_IFREG | 0o755) << 16
            output.writestr(binary, f'#!{Path(sys.executable).resolve()}\nprint("[]")\n')
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    assert runtime.ocr_binary(root) is None
    assert runtime.install_ocr(root, *component())["ocr_installed"]
    before = (root / "ocr.path").read_bytes()
    assert runtime.ocr_binary(root).exists()
    with pytest.raises(InstallError, match="invalid_ocr_component"):
        runtime.install_ocr(root, *component("incompatible-v2"))
    assert (root / "ocr.path").read_bytes() == before
    assert len(list((root / "components").iterdir())) == 1


def test_absent_optional_ocr_never_compiles_swift(tmp_path, monkeypatch):
    from android_use.backend import UseError
    from android_use.ocr import VisionOCR

    monkeypatch.setenv("ANDROID_USE_OCR", str(tmp_path / "not-installed"))
    with pytest.raises(UseError, match="Optional local OCR is not installed"):
        VisionOCR().recognize(None, 0)


def test_cli_relative_install_root_returns_absolute_stable_config(
    tmp_path, bundle, monkeypatch, capsys
):
    archive, digest = bundle()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANDROID_USE_RUNTIME_DIR", str(tmp_path / "before"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "android-use-runtime",
            "--root",
            "relative root",
            "install",
            "--archive",
            str(archive),
            "--sha256",
            digest,
        ],
    )
    assert runtime.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["mcp_config"]["args"][0]).is_absolute()
    assert runtime.current_id(tmp_path / "relative root") == result["release"]


def test_private_asset_auth_is_scoped_and_removed_on_redirect(tmp_path, monkeypatch):
    import httpx

    seen = []
    original = httpx.Client
    monkeypatch.setenv("ANDROID_USE_GITHUB_TOKEN", "private-test-token")

    def transport(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        if request.url.host == "api.github.com":
            return httpx.Response(
                302, headers={"Location": "https://objects.githubusercontent.com/runtime.zip"}
            )
        return httpx.Response(200, content=b"verified-by-caller")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs),
    )
    output = tmp_path / "download"
    runtime.fetch(f"https://api.github.com/repos/{runtime.REPOSITORY}/releases/assets/12", output)
    assert seen[0][1] == "Bearer private-test-token"
    assert seen[1][1] is None
    assert output.read_bytes() == b"verified-by-caller"


def test_downloader_refuses_plain_http_redirect_and_size_overflow(tmp_path, monkeypatch):
    import httpx

    original = httpx.Client
    requests = []

    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://insecure.example/file"})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(redirect), **kwargs),
    )
    with pytest.raises(InstallError, match="requires_https"):
        runtime.fetch("https://example.test/file", tmp_path / "file")
    assert len(requests) == 1
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"too large")),
            **kwargs,
        ),
    )
    with pytest.raises(InstallError, match="too_large"):
        runtime.fetch("https://example.test/file", tmp_path / "file", limit=2)


def test_private_release_uses_validated_api_asset_endpoints(tmp_path, bundle, monkeypatch):
    root = tmp_path / "install"
    runtime.activate(root, *bundle())
    archive, digest = bundle("2.0.0")
    monkeypatch.setenv("ANDROID_USE_GITHUB_TOKEN", "test-only")
    name = f"android-use-runtime-{runtime.target()}.zip"
    base = f"https://api.github.com/repos/{runtime.REPOSITORY}/releases/assets/"
    prefix = f"https://github.com/{runtime.REPOSITORY}/releases/download/v2.0.0/"
    info = {
        "tag_name": "v2.0.0",
        "assets": [
            {
                "name": "SHA256SUMS",
                "url": base + "1",
                "browser_download_url": prefix + "SHA256SUMS",
            },
            {"name": name, "url": base + "2", "browser_download_url": prefix + name},
        ],
    }
    requested = []

    def fetch(url, destination, limit=runtime.MAX_DOWNLOAD):
        requested.append(url)
        if url.endswith("/latest"):
            destination.write_text(json.dumps(info))
        elif url.endswith("/1"):
            destination.write_text(digest + "  " + name + "\n")
        else:
            destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(runtime, "fetch", fetch)
    assert runtime.checked_update(root)["version"] == "2.0.0"
    assert requested[1:] == [base + "1", base + "2"]
