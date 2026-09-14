"""Reuse only verified bytes and refuse a source that changes during capture."""

import os
from pathlib import Path

import pytest

from chronicle import api, config, engine, paths, userdata


@pytest.fixture
def capture_store(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    document = source / "document"
    document.write_bytes(b"AAAA")
    cfg = config.Config()
    cfg.user_data_paths = [str(source)]
    eng = engine.Engine(local_root=tmp_path / "local", config=cfg)
    target = tmp_path / "target"
    target.mkdir()
    eng.target_adopt(target, target_class="directory")
    return eng, document


def capture(eng):
    return eng.capture(paths.LAYER_USER_DATA)["version_id"]


def stored(eng, document, version):
    manifest = eng.get_manifest(paths.LAYER_USER_DATA, version)
    entry = next(e for e in manifest["entries"] if e["path"] == str(document))
    return userdata.read_file(eng.target_root(), version, entry)


def test_preserved_nanosecond_timestamp_does_not_reuse_old_bytes(capture_store):
    eng, document = capture_store
    stamp = 1_800_000_000_123_456_789
    os.utime(document, ns=(stamp, stamp))
    first = capture(eng)
    document.write_bytes(b"BBBB")
    os.utime(document, ns=(stamp, stamp))

    second = capture(eng)

    assert stored(eng, document, first).read_bytes() == b"AAAA"
    assert stored(eng, document, second).read_bytes() == b"BBBB"


def test_corrupt_previous_copy_is_not_reused(capture_store):
    eng, document = capture_store
    first = capture(eng)
    old = stored(eng, document, first)
    old.write_bytes(b"BAD!")

    second = capture(eng)

    assert stored(eng, document, second).read_bytes() == b"AAAA"
    assert stored(eng, document, second).stat().st_ino != old.stat().st_ino
    assert eng.verify(paths.LAYER_USER_DATA, second)["ok"]


def test_verified_unchanged_file_still_reuses_its_inode(capture_store):
    eng, document = capture_store
    first = capture(eng)
    second = capture(eng)

    assert stored(eng, document, first).stat().st_ino == stored(
        eng, document, second
    ).stat().st_ino


def test_source_change_refuses_capture_and_removes_staging(
    capture_store, monkeypatch
):
    eng, document = capture_store
    real_copy = userdata.shutil.copy2

    def copy_then_change(source, target, *args, **kwargs):
        result = real_copy(source, target, *args, **kwargs)
        if Path(source) == document:
            document.write_bytes(b"BBBB")
        return result

    monkeypatch.setattr(userdata.shutil, "copy2", copy_then_change)
    with pytest.raises(RuntimeError, match="changed during capture"):
        capture(eng)

    assert not eng.list_versions(paths.LAYER_USER_DATA)
    assert not list((eng.target_root() / "userdata").glob(".staging-*"))


def test_cleanup_failure_is_visible_to_capture_clients(capture_store, monkeypatch):
    eng, document = capture_store
    real_copy = userdata.shutil.copy2

    def copy_then_change(source, target, *args, **kwargs):
        result = real_copy(source, target, *args, **kwargs)
        if Path(source) == document:
            document.write_bytes(b"BBBB")
        return result

    def fail_cleanup(_path):
        raise OSError("cleanup could not remove staging")

    monkeypatch.setattr(userdata.shutil, "copy2", copy_then_change)
    monkeypatch.setattr(userdata.shutil, "rmtree", fail_cleanup)

    response = api.dispatch(
        eng, {"verb": "capture", "args": {"layer": paths.LAYER_USER_DATA}}
    )

    assert not response["ok"]
    assert "source changed during capture" in response["error"]
    assert "cleanup could not remove staging" in response["error"]
