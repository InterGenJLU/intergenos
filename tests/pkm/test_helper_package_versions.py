# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper package upgrades compare archive identities, not application builds."""

import io
import os
import sqlite3
import tarfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, output
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.version import is_upgradable


def archive(path, name, version, release):
    body = (f"pkgname = {name}\npkgver = {version}\n"
            f"pkgrel = {release}\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    return str(path)


@pytest.fixture
def db(tmp_path):
    value = PackageDB(tmp_path / "pkm.db", root=str(tmp_path / "root"))
    yield value
    value.close()


def ingest(db, name, payload, release, reattach=False):
    manifest = {
        "version_installed": payload, "release_installed": release,
        "files": [], "symlinks": [], "depends": [],
    }
    installer = PackageInstaller(db, root=str(db.root))
    ok, message, declined = installer._register_helper_footprint(
        name, manifest, Path(db.root) / f"usr/bin/igos-install-{name}",
        reattach=reattach)
    assert ok, message
    assert not declined
    return db.get_installed(name)


def listing(db, what, remote=None):
    repo = SimpleNamespace(get_package=lambda name: remote)
    out = io.StringIO()
    with patch.object(cli, "repo_manager", return_value=repo), \
            patch.object(cli, "_app_status", return_value="[installed]"), \
            redirect_stdout(out):
        cli.cmd_list(db, SimpleNamespace(what=what, tier=None))
    return out.getvalue()


def test_a_payload_below_one_does_not_offer_the_same_archive_again(db):
    remote = {"name": "codex", "version": "1.0", "release": 7}
    pkg_id = db.add_installed("codex", "1.0", release=7)
    for _ in range(2):
        row = ingest(db, "codex", "0.159.2", 7)
        assert row["id"] == pkg_id
        assert row["version"] == "1.0"
        assert row["payload_version"] == "0.159.2"
        assert not is_upgradable(row, remote)
        assert "codex" not in listing(db, "upgradable", remote)


@pytest.mark.parametrize("name,payload,release", [
    ("codex", "0.159.2", 7),
    ("vscode", "1.143.0-1792533444", 8),
])
def test_a_helper_release_upgrade_is_offered_exactly_once(db, name, payload, release):
    db.add_installed(name, "1.0", release=release)
    row = ingest(db, name, payload, release)
    remote = {"name": name, "version": "1.0", "release": release + 1}
    assert is_upgradable(row, remote)
    assert name in listing(db, "upgradable", remote)

    # The archive install writes its build before the kept payload is attached.
    db.add_installed(name, "1.0", release=release + 1, replace_existing=True)
    row = ingest(db, name, payload, release, reattach=True)
    assert row["version"] == "1.0"
    assert row["release"] == release + 1
    assert row["payload_version"] == payload
    assert not is_upgradable(row, remote)
    assert name not in listing(db, "upgradable", remote)


@pytest.mark.parametrize("name,version,release", [
    ("nvidia", "580.159.04", 20),
    ("cuda-toolkit", "13.3.1", 11),
    ("ge-proton", "11.1", 9),
])
def test_vendor_versioned_packages_keep_their_identity(db, name, version, release):
    db.add_installed(name, version, release=release)
    row = ingest(db, name, version, release)
    assert row["version"] == row["payload_version"] == version
    assert not is_upgradable(row, {"version": version, "release": release})
    assert is_upgradable(row, {"version": version, "release": release + 1})


def test_installed_list_names_both_the_package_and_application(db):
    db.add_installed("codex", "1.0", release=7, install_method="helper",
                     payload_version="0.159.2")
    text = listing(db, "installed")
    assert "1.0-7" in text
    assert "payload 0.159.2" in text


def test_info_names_both_the_package_and_application_after_ingestion(db):
    db.add_installed("codex", "1.0", release=7)
    ingest(db, "codex", "0.159.2", 7)
    out = io.StringIO()
    with redirect_stdout(out):
        cli.cmd_info(db, SimpleNamespace(package="codex"))
    assert "codex 1.0-7" in out.getvalue()
    assert "payload_version" in out.getvalue()
    assert "0.159.2" in out.getvalue()


def seed(db, tmp_path, name, package, old, release, payload=None, method="helper"):
    # The repairs read a recorded archive only from pkm's package cache under
    # the database's root, so the archive is made there.
    cache = Path(db.root) / "var/cache/pkm/packages"
    cache.mkdir(parents=True, exist_ok=True)
    path = archive(cache / f"{name}.igos.tar.gz", name, package, release)
    db.add_installed(name, old, release=release, install_method=method,
                     payload_version=payload, archive_path=path)
    return path


def reopen(db, read_only=False):
    path, root = db.db_path, db.root
    db.close()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        other = PackageDB(path, root=str(root), read_only=read_only)
    # Leave the fixture's object open for its teardown without another migration.
    db.conn = other.conn
    db.read_only = other.read_only
    db._installed_cols = other._installed_cols
    db._files_cols = other._files_cols
    return out.getvalue(), err.getvalue()


def test_old_rows_are_repaired_once_with_payloads_preserved(db, tmp_path):
    seed(db, tmp_path, "codex", "1.0", "0.159.2", 7, "0.159.2")
    seed(db, tmp_path, "vscode", "1.0", "1.143.0", 8)
    seed(db, tmp_path, "nvidia", "580.159.04", "580.159.04", 20, "580.159.04")
    nvidia_before = db.get_installed("nvidia")
    out, err = reopen(db)
    assert out == ""
    for name, payload in [("codex", "0.159.2"), ("vscode", "1.143.0")]:
        row = db.get_installed(name)
        assert row["version"] == "1.0"
        assert row["payload_version"] == payload
        assert sum(name in line for line in err.splitlines()) == 1
    assert db.get_installed("nvidia") == nvidia_before
    history = db.conn.execute(
        "SELECT operation, package_name, old_version, new_version, method "
        "FROM history ORDER BY id").fetchall()
    assert history == [
        ("repair-version", "codex", "0.159.2", "1.0", "helper-archive-pkginfo"),
        ("repair-version", "vscode", "1.143.0", "1.0", "helper-archive-pkginfo"),
    ]
    assert reopen(db) == ("", "")
    assert db.conn.execute("SELECT count(*) FROM history").fetchone()[0] == 2
    assert db.conn.total_changes == 0


def test_repair_keeps_an_existing_payload_even_when_it_differs_from_old_version(db, tmp_path):
    seed(db, tmp_path, "codex", "1.0", "0.158.0", 7, "0.159.2")
    reopen(db)
    row = db.get_installed("codex")
    assert row["version"] == "1.0"
    assert row["payload_version"] == "0.159.2"


def test_repair_fills_payload_before_the_older_release_suffix_repair(db, tmp_path):
    seed(db, tmp_path, "example", "1.0", "1.0-8", 8)
    reopen(db)
    row = db.get_installed("example")
    assert row["version"] == "1.0"
    assert row["payload_version"] == "1.0-8"


def test_read_only_open_neither_repairs_nor_reports_helper_rows(db, tmp_path):
    seed(db, tmp_path, "codex", "1.0", "0.159.2", 7, "0.159.2")
    assert reopen(db, read_only=True) == ("", "")
    assert db.get_installed("codex")["version"] == "0.159.2"
    assert db.conn.execute("SELECT count(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["missing", "corrupt", "other-name", "other-release",
                                  "empty-version", "non-helper"])
def test_repair_requires_the_installed_helpers_own_archive(db, tmp_path, kind):
    path = Path(seed(db, tmp_path, "example", "1.0", "0.8", 7, "0.8",
                     method="archive" if kind == "non-helper" else "helper"))
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"unreadable archive")
    elif kind == "other-name":
        archive(path, "different", "1.0", 7)
    elif kind == "other-release":
        archive(path, "example", "1.0", 8)
    elif kind == "empty-version":
        archive(path, "example", "", 7)
    before = db.get_installed("example")
    reopen(db)
    assert db.get_installed("example") == before
    assert db.conn.execute("SELECT count(*) FROM history").fetchone()[0] == 0


def test_repair_is_reported_at_quiet_level(db, tmp_path):
    seed(db, tmp_path, "codex", "1.0", "0.159.2", 7, "0.159.2")
    previous = output.process_level()
    try:
        output.set_process_level(output.QUIET)
        out, err = reopen(db)
    finally:
        output.set_process_level(previous)
    assert out == ""
    assert len(err.splitlines()) == 1
    assert "codex" in err


def test_repair_does_not_open_a_nonregular_archive(db, tmp_path):
    path = Path(seed(db, tmp_path, "example", "1.0", "0.8", 7, "0.8"))
    path.unlink()
    os.mkfifo(path)
    with patch("pkm.repo._read_package_meta", side_effect=AssertionError("opened FIFO")):
        reopen(db)
    assert db.get_installed("example")["version"] == "0.8"


def test_repair_rolls_back_every_row_if_its_history_write_fails(db, tmp_path):
    seed(db, tmp_path, "codex", "1.0", "0.159.2", 7, "0.159.2")
    seed(db, tmp_path, "vscode", "1.0", "1.143.0", 8, "1.143.0")
    db.conn.executescript(
        "CREATE TRIGGER refuse_history BEFORE INSERT ON history "
        "WHEN NEW.package_name = 'vscode' "
        "BEGIN SELECT RAISE(ABORT, 'history write failed'); END;")
    with pytest.raises(sqlite3.IntegrityError, match="history write failed"):
        reopen(db)
    with sqlite3.connect(db.db_path) as raw:
        assert raw.execute("SELECT name, version FROM installed ORDER BY name").fetchall() == [
            ("codex", "0.159.2"), ("vscode", "1.143.0")]
        assert raw.execute("SELECT count(*) FROM history").fetchone()[0] == 0
