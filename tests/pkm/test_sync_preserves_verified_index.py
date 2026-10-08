"""A refused sync retains the verified files and the index already loaded."""

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import urllib.error

import pytest

from pkm import repo


@pytest.fixture
def cached(tmp_path):
    manager = repo.RepoManager.__new__(repo.RepoManager)
    manager.root = tmp_path / "target"
    manager.repos = {"fixture": {"url": "https://mirror.example/current", "enabled": True}}
    cache = manager.db_cache()
    cache.mkdir(parents=True)
    generated = datetime.now(timezone.utc).isoformat()
    old_data = {"version": 1, "arch": "x86_64", "generated": generated,
                "package_count": 1, "packages": {"old": {"version": "1"}}}
    new_data = dict(old_data, package_count=2, packages={"new": {"version": "2"}, "other": {"version": "1"}})
    old_body = gzip.compress(json.dumps(old_data).encode(), mtime=0)
    new_body = gzip.compress(json.dumps(new_data).encode(), mtime=0)
    old_signature, new_signature = b"previous signature", b"new signature"
    body, signature = cache / "fixture.db", cache / "fixture.db.sig"
    body.write_bytes(old_body)
    signature.write_bytes(old_signature)
    old_index = repo.RepoIndex("fixture", manager.repos["fixture"]["url"], old_data)
    manager.indexes = {"fixture": old_index}
    keyring = tmp_path / "keyring"
    keyring.write_bytes(b"fixture keyring")
    assert repo._save_last_seen_state("fixture", generated, manager.root)
    return locals()


def perform(cached, failure=None):
    manager = cached["manager"]
    downloads = []
    verification = []

    def download(url, destination):
        destination = Path(destination)
        downloads.append(destination)
        if failure == "download":
            destination.write_bytes(b"interrupted body")
            raise urllib.error.URLError("interrupted body")
        payload = cached["new_signature"] if url.endswith(".sig") else cached["new_body"]
        if failure == "parse" and not url.endswith(".sig"):
            payload = gzip.compress(json.dumps(dict(cached["new_data"], version=99)).encode())
        destination.write_bytes(payload)

    def verify(body, signature):
        verification.append((Path(body), Path(signature), cached["body"].read_bytes(), cached["signature"].read_bytes()))
        return False if failure == "signature" else "FIXTURE-PIN"

    with patch.object(repo, "GPG_KEYRING", cached["keyring"]), \
         patch.object(manager, "_download", side_effect=download), \
         patch.object(manager, "_verify_signature", side_effect=verify):
        if failure == "state":
            with patch.object(repo, "_save_last_seen_state", return_value=False):
                result = manager.sync()
        else:
            result = manager.sync()
    return result, downloads, verification


@pytest.mark.parametrize("failure,stage", [
    ("download", "index download"), ("signature", "signature verification FAILED"),
    ("parse", "index rejected"), ("state", "generation state"),
])
def test_a_refused_sync_preserves_the_verified_pair_and_loaded_index(cached, failure, stage):
    result, downloads, _verification = perform(cached, failure)
    assert result[0][1] is False, result
    assert stage in result[0][2]
    assert cached["body"].read_bytes() == cached["old_body"]
    assert cached["signature"].read_bytes() == cached["old_signature"]
    assert cached["manager"].indexes["fixture"] is cached["old_index"]
    assert all(cached["cache"] in path.parents for path in downloads)
    if failure in ("signature", "download", "state"):
        assert all(not path.exists() for path in downloads)
    else:
        assert all(path.exists() for path in downloads)


def test_a_good_sync_verifies_in_staging_then_replaces_and_loads(cached):
    result, downloads, verification = perform(cached)
    assert result[0][1] is True and "2 packages" in result[0][2]
    body, signature, original_body, original_signature = verification[0]
    assert body != cached["body"] and signature != cached["signature"]
    assert original_body == cached["old_body"] and original_signature == cached["old_signature"]
    assert cached["body"].read_bytes() == cached["new_body"]
    assert cached["signature"].read_bytes() == cached["new_signature"]
    assert cached["manager"].indexes["fixture"].package_count == 2
    assert all(not path.exists() for path in downloads)


def test_a_missing_keyring_keeps_the_unverified_download_apart_from_the_verified_pair(cached):
    cached["keyring"].unlink()
    result, downloads, verification = perform(cached)
    assert result[0][1] is False and "keyring not found" in result[0][2]
    assert "LEFT" in result[0][2] and not verification
    assert cached["body"].read_bytes() == cached["old_body"]
    assert cached["signature"].read_bytes() == cached["old_signature"]
    assert cached["manager"].indexes["fixture"] is cached["old_index"]
    assert len(downloads) == 2 and all(path.exists() for path in downloads)
    assert downloads[0].read_bytes() == cached["new_body"]
    assert downloads[1].read_bytes() == cached["new_signature"]
    assert all(str(path.parent) in result[0][2] for path in downloads)


def test_a_failed_second_cache_replace_restores_the_pair_and_generation(cached):
    original_replace = repo.os.replace
    state = repo._state_dir(cached["manager"].root) / "fixture.json"
    old_state = state.read_bytes()
    refused = []

    def replace(source, destination):
        if Path(destination) == cached["signature"] and not refused:
            refused.append(True)
            raise OSError("second cache replacement refused")
        return original_replace(source, destination)

    with patch.object(repo.os, "replace", side_effect=replace):
        result, _downloads, _verification = perform(cached)
    assert refused and result[0][1] is False
    assert "cache replacement" in result[0][2]
    assert cached["body"].read_bytes() == cached["old_body"]
    assert cached["signature"].read_bytes() == cached["old_signature"]
    assert state.read_bytes() == old_state
    assert cached["manager"].indexes["fixture"] is cached["old_index"]


@pytest.mark.parametrize("persist", [False, True])
def test_parsing_keeps_the_existing_default_and_can_leave_persistence_to_sync(cached, persist):
    manager = cached["manager"]
    arguments = ("fixture", manager.repos["fixture"]["url"], cached["body"])
    with patch.object(repo, "_save_last_seen_state", return_value=True) as save:
        parsed = manager._parse_index(*arguments) if persist else manager._parse_index(*arguments, persist_generation=False)
    assert parsed.package_count == 1
    if persist:
        save.assert_called_once_with("fixture", cached["generated"], manager.root)
    else:
        save.assert_not_called()


def test_a_failed_rollback_reports_incomplete_restoration_and_keeps_the_saved_files(cached):
    original_replace = repo.os.replace
    state = repo._state_dir(cached["manager"].root) / "fixture.json"
    old_state = state.read_bytes()

    def replace(source, destination):
        if Path(destination) == cached["signature"]:
            raise OSError("signature replacement refused")
        if Path(destination) == cached["body"] and Path(source).name.startswith("previous-"):
            raise OSError("restoration refused")
        return original_replace(source, destination)

    with patch.object(repo.os, "replace", side_effect=replace):
        result, downloads, _verification = perform(cached)
    assert result[0][1] is False
    assert "cache rollback failed" in result[0][2]
    assert "cache restoration incomplete" in result[0][2]
    assert "previous cached index and signature unchanged" not in result[0][2]
    saved = downloads[0].parent
    assert str(saved) in result[0][2]
    assert (saved / "previous-fixture.db").read_bytes() == cached["old_body"]
    assert (saved / "previous-fixture.db.sig").read_bytes() == cached["old_signature"]
    assert (saved / "previous-fixture.json").read_bytes() == old_state
    assert cached["body"].read_bytes() == cached["new_body"]
    assert cached["signature"].read_bytes() == cached["old_signature"]
    assert cached["manager"].indexes["fixture"] is cached["old_index"]


@pytest.mark.parametrize("failure", ["gzip", "json"])
def test_every_validation_failure_keeps_and_names_the_candidate(cached, failure):
    cached["new_body"] = (b"not a gzip stream" if failure == "gzip" else
                          gzip.compress(b'{"packages": ]'))
    state = repo._state_dir(cached["manager"].root) / "fixture.json"
    old_state = state.read_bytes()
    result, downloads, verification = perform(cached)
    assert result[0][1] is False
    assert verification, "the candidate must reach validation after signature verification"
    assert "index validation failed" in result[0][2]
    assert ("BadGzipFile" if failure == "gzip" else "JSONDecodeError") in result[0][2]
    assert len(downloads) == 2 and all(path.exists() for path in downloads)
    assert all(str(path.parent) in result[0][2] for path in downloads)
    assert downloads[0].read_bytes() == cached["new_body"]
    assert downloads[1].read_bytes() == cached["new_signature"]
    assert cached["body"].read_bytes() == cached["old_body"]
    assert cached["signature"].read_bytes() == cached["old_signature"]
    assert state.read_bytes() == old_state
    assert cached["manager"].indexes["fixture"] is cached["old_index"]
