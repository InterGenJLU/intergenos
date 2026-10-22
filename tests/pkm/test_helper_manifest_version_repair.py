# SPDX-License-Identifier: GPL-3.0-or-later
"""A helper repair survives manifest import, removal and interrupted writes."""

import hashlib
import io
import sqlite3
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pkm.database import PackageDB, _parse_manifest
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover


def cached(root, file_name="example.igos.tar.gz"):
    # The repairs read a recorded archive only from pkm's package cache under
    # the database's root, so the archives these cases record are made there.
    cache = Path(root) / "var/cache/pkm/packages"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / file_name


def seed(tmp_path, with_file=True):
    root = tmp_path / "root"
    root.mkdir()
    archive = cached(root)
    data = b"pkgname = example\npkgver = 1.0\npkgrel = 7\n"
    with tarfile.open(archive, "w:gz") as tf:
        member = tarfile.TarInfo(".PKGINFO")
        member.size = len(data)
        tf.addfile(member, io.BytesIO(data))
    db_path = tmp_path / "pkm.db"
    with PackageDB(db_path, root=str(root)) as db:
        pkg_id = db.add_installed("example", "0.8", release=7,
                                  install_method="helper", payload_version="0.8",
                                  archive_path=str(archive))
        files = ["opt/example/app"] if with_file else []
        if files:
            target = root / files[0]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"application bytes")
        db.add_files(pkg_id, files)
        inst = PackageInstaller(db, root=str(root))
        digest = inst._write_manifest("example", "0.8", files, release=7,
                                      hashes=db.get_file_checksums("example"))
        db.set_manifest_sha256("example", digest)
        before = db.get_installed("example")
    legacy = root / "var/lib/igos/packages/example-0.8"
    return root, db_path, legacy, legacy.read_bytes(), before


def raw_record(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT version, payload_version, manifest_sha256 FROM installed "
                            "WHERE name = 'example'").fetchone()


@pytest.mark.parametrize("with_file", [True, False])
def test_repair_import_remove_import_does_not_restore_the_row(tmp_path, with_file):
    root, db_path, legacy, original, before = seed(tmp_path, with_file)
    canonical = legacy.with_name("example-1.0")
    with PackageDB(db_path, root=str(root)) as db:
        row = db.get_installed("example")
        assert row["version"] == "1.0"
        assert row["payload_version"] == "0.8"
        assert canonical.exists() and not legacy.exists()
        corrected = canonical.read_bytes()
        meta = _parse_manifest(corrected.decode())
        assert (meta["name"], meta["version"], meta["release"], meta["payload_version"]) == (
            "example", "1.0", 7, "0.8")
        assert corrected.split(b"FILE LIST:", 1)[1] == original.split(b"FILE LIST:", 1)[1]
        assert row["manifest_sha256"] == hashlib.sha256(corrected).hexdigest()
        assert {key for key in before if before[key] != row[key]} == {"version", "manifest_sha256"}
        assert db.import_manifests(canonical.parent) == 0
        assert db.get_installed("example") == row
        ok, message = PackageRemover(db, root=str(root)).remove(
            "example", force=True, run_pre_remove_hook=False, run_post_remove_hook=False)
        assert ok, message
        assert not canonical.exists() and not legacy.exists()
        assert db.import_manifests(canonical.parent) == 0
        assert db.get_installed("example") is None


def test_new_helper_manifest_carries_payload_into_a_fresh_database(tmp_path):
    root = tmp_path / "root"
    with PackageDB(tmp_path / "writer.db", root=str(root)) as db:
        db.add_installed("example", "1.0", release=7)
        ok, message, _ = PackageInstaller(db, root=str(root))._register_helper_footprint(
            "example", {"version_installed": "0.8", "files": [], "symlinks": [],
                        "depends": []}, root / "usr/bin/igos-install-example")
        assert ok, message
    with PackageDB(tmp_path / "reader.db", root=str(root)) as db:
        assert db.import_manifests(root / "var/lib/igos/packages") == 1
        row = db.get_installed("example")
        assert (row["version"], row["release"], row["payload_version"], row["install_method"]) == (
            "1.0", 7, "0.8", "helper")


def test_import_of_an_older_manifest_keeps_a_recorded_payload(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    # Force re-registration of an old artifact without running the repair first.
    with PackageDB(db_path, root=str(root), read_only=True) as reader:
        assert reader.get_installed("example")["payload_version"] == "0.8"
    with patch.object(PackageDB, "_repair_helper_package_versions", return_value=0):
        with PackageDB(db_path, root=str(root)) as db:
            db.conn.execute("UPDATE installed SET manifest_sha256 = NULL")
            db.conn.commit()
            assert db.import_manifests(legacy.parent) == 1
            assert db.get_installed("example")["payload_version"] == "0.8"


def test_failed_publication_keeps_the_original_and_database(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    with patch("pkm.database.os.replace", side_effect=OSError("publication failed")):
        with pytest.raises(OSError, match="publication failed"):
            PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == original
    assert raw_record(db_path) == ("0.8", "0.8", before["manifest_sha256"])
    with PackageDB(db_path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"
        assert len(db.get_history("example")) == 1


def test_failed_commit_keeps_the_original_and_can_resume(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    with patch.object(PackageDB, "log_operation", side_effect=RuntimeError("history failed")):
        with pytest.raises(RuntimeError, match="history failed"):
            PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == original
    assert raw_record(db_path) == ("0.8", "0.8", before["manifest_sha256"])
    assert legacy.with_name("example-1.0").exists()
    with PackageDB(db_path, root=str(root)) as db:
        assert db.get_installed("example")["version"] == "1.0"
        assert len(db.get_history("example")) == 1
        assert not legacy.exists()


@pytest.mark.parametrize("stored_payload", ["0.8", "0.9"])
def test_interrupted_cleanup_resumes_without_a_second_history_row(tmp_path, capsys, stored_payload):
    root, db_path, legacy, original, before = seed(tmp_path)
    with sqlite3.connect(db_path) as raw:
        raw.execute("UPDATE installed SET payload_version = ?", (stored_payload,))
    unlink = Path.unlink
    def fail_legacy(path, *args, **kwargs):
        if path == legacy:
            raise OSError("cleanup interrupted")
        return unlink(path, *args, **kwargs)
    with patch.object(Path, "unlink", fail_legacy):
        with pytest.raises(OSError, match="cleanup interrupted"):
            PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == original
    assert raw_record(db_path)[0] == "1.0"
    with PackageDB(db_path, root=str(root)) as db:
        assert len(db.get_history("example")) == 1
        assert not legacy.exists()
    assert "completed helper manifest repair" in capsys.readouterr().err
    with PackageDB(db_path, root=str(root)) as db:
        assert len(db.get_history("example")) == 1
    assert capsys.readouterr().err == ""


def test_manifest_named_for_the_payload_is_found_when_the_old_row_differs(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    with sqlite3.connect(db_path) as raw:
        raw.execute("UPDATE installed SET version = '0.7'")
    with PackageDB(db_path, root=str(root)) as db:
        assert db.get_installed("example")["payload_version"] == "0.8"
        assert not legacy.exists()
        assert legacy.with_name("example-1.0").exists()


@pytest.mark.parametrize("problem", ["changed-source", "different-destination"])
def test_a_conflicting_manifest_is_reported_without_replacing_it(tmp_path, problem):
    root, db_path, legacy, original, before = seed(tmp_path)
    target = legacy.with_name("example-1.0")
    if problem == "changed-source":
        legacy.write_bytes(original + b"opt/example/unrecorded\n")
    else:
        target.write_bytes(b"an unrelated manifest\n")
    legacy_before = legacy.read_bytes()
    with pytest.raises(ValueError, match="cannot repair example"):
        PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == legacy_before
    assert raw_record(db_path) == ("0.8", "0.8", before["manifest_sha256"])
    if problem == "different-destination":
        assert target.read_bytes() == b"an unrelated manifest\n"


def test_a_repair_that_updates_no_row_stops_before_legacy_cleanup(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    with sqlite3.connect(db_path) as raw:
        raw.executescript("CREATE TRIGGER ignore_repair BEFORE UPDATE ON installed "
                          "BEGIN SELECT RAISE(IGNORE); END;")
    with pytest.raises(sqlite3.IntegrityError, match="helper record changed"):
        PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == original
    assert raw_record(db_path) == ("0.8", "0.8", before["manifest_sha256"])


def test_publication_reads_back_a_destination_created_after_planning(tmp_path):
    root, db_path, legacy, original, before = seed(tmp_path)
    publish = PackageDB._publish_helper_manifest
    def intervening_writer(plan):
        plan["target"].write_bytes(b"different bytes\n")
        return publish(plan)
    with patch.object(PackageDB, "_publish_helper_manifest", staticmethod(intervening_writer)):
        with pytest.raises(OSError, match="read-back differs"):
            PackageDB(db_path, root=str(root))
    assert legacy.read_bytes() == original
    assert raw_record(db_path) == ("0.8", "0.8", before["manifest_sha256"])
