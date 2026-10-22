# SPDX-License-Identifier: GPL-3.0-or-later
"""Missing helper archives do not discard the package's recorded identity."""

import hashlib
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pkm.database import PackageDB
from pkm import output
from pkm.installer import PackageInstaller
from pkm.version import is_upgradable
from tests.pkm.test_helper_manifest_version_repair import cached, seed
from tests.pkm.test_a_version_carrying_its_own_release_is_repaired import _make_archive


def history(db_path, version="1.0", **changes):
    values = dict(timestamp="2026-01-01T00:00:00+00:00", operation="install",
                  package_name="example", new_version=version, method="archive", success=1)
    values.update(changes)
    with sqlite3.connect(db_path) as raw:
        raw.execute("INSERT INTO history (timestamp, operation, package_name, "
                    "new_version, method, success) VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(values[key] for key in ("timestamp", "operation", "package_name",
                                                 "new_version", "method", "success")))


def missing_archive(tmp_path):
    state = seed(tmp_path)
    cached(state[0]).unlink()
    return state


@pytest.mark.parametrize("payload", ["0.159.2", "1.143.0-1792533444", "155.0.8059.84-1",
                                    "2.1.270", "26.1013.40934"])
def test_history_repairs_both_version_orders_once(tmp_path, capsys, payload):
    root, path, legacy, original, before = missing_archive(tmp_path)
    history(path)
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE installed SET payload_version = ?", (payload,))
    with PackageDB(path, root=str(root)) as db:
        row = db.get_installed("example")
        assert (row["version"], row["release"], row["payload_version"]) == ("1.0", 7, payload)
        assert not is_upgradable(row, {"version": "1.0", "release": 7})
        assert is_upgradable(row, {"version": "1.0", "release": 8})
        repairs = [entry for entry in db.get_history("example")
                   if entry["operation"] == "repair-version"]
        assert len(repairs) == 1 and repairs[0]["method"] == "helper-install-history"
        assert db.import_manifests(legacy.parent) == 0
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1 and "history" in lines[0] and "example" in lines[0]
    assert not legacy.exists()
    corrected = legacy.with_name("example-1.0")
    assert corrected.read_bytes().split(b"FILE LIST:", 1)[1] == original.split(b"FILE LIST:", 1)[1]
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example") == row
        assert len([h for h in db.get_history("example") if h["operation"] == "repair-version"]) == 1
    assert capsys.readouterr().err == ""


def test_only_the_latest_successful_archive_install_establishes_the_version(tmp_path):
    root, path, *_ = missing_archive(tmp_path)
    history(path, "0.9")
    history(path, "1.0")
    history(path, "9.0", success=0)
    history(path, "8.0", method="helper")
    history(path, "7.0", operation="upgrade")
    history(path, "6.0", package_name="different")
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_empty_latest_history_version_does_not_select_an_older_install(tmp_path, value):
    root, path, *_ = missing_archive(tmp_path)
    history(path, "1.0")
    history(path, value)
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "0.8"
        assert not any(h["operation"] == "repair-version" for h in db.get_history("example"))


def test_a_matching_present_archive_remains_the_first_source(tmp_path):
    root, path, *_ = seed(tmp_path)
    history(path, "9.0")
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"
        assert db.get_history("example")[0]["method"] == "helper-archive-pkginfo"


@pytest.mark.parametrize("problem", ["corrupt", "other-name", "directory"])
def test_history_does_not_override_a_present_unusable_archive(tmp_path, problem):
    root, path, *_ = seed(tmp_path)
    history(path)
    archive = cached(root)
    if problem == "corrupt":
        archive.write_bytes(b"not an archive")
    elif problem == "other-name":
        _make_archive(archive, "different", "1.0", 7)
    else:
        archive.unlink()
        archive.mkdir()
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "0.8"


def test_read_only_open_does_not_apply_or_report_history_repairs(tmp_path, capsys):
    root, path, *_ = missing_archive(tmp_path)
    history(path)
    with PackageDB(path, root=str(root), read_only=True) as db:
        assert db.get_installed("example")["version"] == "0.8"
    assert capsys.readouterr().err == ""


def test_history_repair_cleanup_resumes_with_a_different_payload(tmp_path, capsys):
    root, path, legacy, original, before = missing_archive(tmp_path)
    history(path)
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE installed SET payload_version = '0.9'")
    unlink = Path.unlink
    def interrupted(file, *args, **kwargs):
        if file == legacy:
            raise OSError("cleanup interrupted")
        return unlink(file, *args, **kwargs)
    with patch.object(Path, "unlink", interrupted):
        with pytest.raises(OSError, match="cleanup interrupted"):
            PackageDB(path, root=str(root))
    assert legacy.read_bytes() == original
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"
        assert len([h for h in db.get_history("example") if h["operation"] == "repair-version"]) == 1
        assert not legacy.exists()
    assert "completed helper manifest repair" in capsys.readouterr().err
    with PackageDB(path, root=str(root)):
        pass
    assert capsys.readouterr().err == ""


def test_a_real_archive_install_records_the_header_version_used_after_eviction(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "pkm.db"
    archive = Path(_make_archive(cached(root, "example-1.0-7.igos.tar.gz"), "example", "1.0", 7))
    with PackageDB(path, root=str(root)) as db:
        ok, message = PackageInstaller(db, root=str(root)).install(
            "example", archive_path=archive,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        assert ok, message
        entry = db.get_history("example")[0]
        assert (entry["operation"], entry["method"], entry["new_version"], entry["success"]) == (
            "install", "archive", "1.0", 1)
        # Reconstruct the legacy merge while keeping the real install history.
        db.update_helper_merge(db.get_installed("example")["id"], "example", "0.8", 7,
                               payload_version="0.8")
    archive.unlink()
    with PackageDB(path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"
        assert db.get_installed("example")["payload_version"] == "0.8"


@pytest.mark.parametrize("problem", ["missing", "no-path", "failed-history", "empty-history",
                                    "corrupt", "other-name", "unreadable"])
def test_unrepaired_rows_are_reported_once_on_each_default_open(tmp_path, capsys, problem):
    root, path, legacy, original, before = seed(tmp_path)
    archive = cached(root)
    if problem in {"missing", "no-path", "failed-history", "empty-history"}:
        archive.unlink()
        if problem == "no-path":
            with sqlite3.connect(path) as raw:
                raw.execute("UPDATE installed SET archive_path = NULL")
        elif problem == "failed-history":
            history(path, success=0)
        elif problem == "empty-history":
            history(path, "")
    elif problem == "corrupt":
        archive.write_bytes(b"not an archive")
    elif problem == "other-name":
        _make_archive(archive, "different", "1.0", 7)
    stat = os.stat
    def archive_stat(target, *args, **kwargs):
        if problem == "unreadable" and Path(target) == archive:
            raise PermissionError("archive cannot be read")
        return stat(target, *args, **kwargs)
    capsys.readouterr()
    with patch.object(output, "process_level", return_value=output.NORMAL), \
            patch("pkm.database.os.stat", archive_stat):
        for _ in range(2):
            with PackageDB(path, root=str(root)) as db:
                assert db.get_installed("example")["version"] == "0.8"
                assert not any(h["operation"] == "repair-version" for h in db.get_history("example"))
            lines = capsys.readouterr().err.splitlines()
            assert len(lines) == 1
            assert "example" in lines[0] and "left as recorded:" in lines[0]
    assert legacy.read_bytes() == original
