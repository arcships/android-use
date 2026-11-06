import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
