# SPDX-License-Identifier: GPL-3.0-or-later
"""The version repairs read a recorded archive only from pkm's package cache.

Two repairs run on every read-write open (as root on a running system) and
read the archive an installed row records: the release-suffix repair
(release_suffix_verdict, which a read-only open also reads every row
through) and the helper-version repair. A row installed with
`--archive FILE` records the file the person named, wherever they keep it, so
an account that can write that file decided what root wrote into the row.
Each case here puts an account-written archive at the recorded path, or in a
package cache another account can write, and the row must stay as recorded;
the control reads the same archive from the cache and repairs the row.
"""

import io
import os
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from pkm import output
from pkm.database import PackageDB
from tests.pkm.test_a_version_carrying_its_own_release_is_repaired import _make_archive

CACHE = "var/cache/pkm/packages"


@pytest.fixture
def place(tmp_path):
    root = tmp_path / "root"
    (root / CACHE).mkdir(parents=True)
    kept = tmp_path / "kept"  # a directory the person's account writes
    kept.mkdir()
    return root, kept, tmp_path / "pkm.db"


def _seed(db_path, root, **row):
    with PackageDB(db_path, root=str(root)) as db:
        db.add_installed(**row)


def _open(db_path, root, read_only=False):
    level = output.process_level()
    output.set_process_level(output.VERBOSE)
    err = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            db = PackageDB(db_path, root=str(root), read_only=read_only)
    finally:
        output.set_process_level(level)
    return db, err.getvalue()


def _repairs(db_path):
    with sqlite3.connect(db_path) as raw:
        return raw.execute("SELECT count(*) FROM history "
                           "WHERE operation = 'repair-version'").fetchone()[0]


def _suffixed(db_path, root, archive):
    _seed(db_path, root, name="demo", version="1.0-3", release=3,
          archive_path=_make_archive(archive, "demo", "1.0", 3))


def _helper(db_path, root, archive):
    _seed(db_path, root, name="example", version="0.8", release=7,
          install_method="helper", payload_version="0.8",
          archive_path=_make_archive(archive, "example", "1.0", 7))


def test_the_suffix_repair_does_not_read_an_archive_outside_the_cache(place):
    root, kept, db_path = place
    _suffixed(db_path, root, kept / "demo-1.0-3.igos.tar.gz")
    db, said = _open(db_path, root)
    try:
        assert db.get_installed("demo")["version"] == "1.0-3"
    finally:
        db.close()
    assert _repairs(db_path) == 0
    assert "outside pkm's package cache" in said, said


def test_a_read_only_open_does_not_read_an_archive_outside_the_cache(place):
    root, kept, db_path = place
    _suffixed(db_path, root, kept / "demo-1.0-3.igos.tar.gz")
    db, _ = _open(db_path, root, read_only=True)
    try:
        assert db.get_installed("demo")["version"] == "1.0-3"
    finally:
        db.close()


def test_the_helper_repair_does_not_read_an_archive_outside_the_cache(place):
    root, kept, db_path = place
    _helper(db_path, root, kept / "example.igos.tar.gz")
    db, said = _open(db_path, root)
    try:
        assert db.get_installed("example")["version"] == "0.8"
    finally:
        db.close()
    assert _repairs(db_path) == 0
    assert "left as recorded: its archive" in said, said
    assert "outside pkm's package cache" in said, said


@pytest.mark.parametrize("cache", ["writable by others", "a link"])
@pytest.mark.parametrize("repair", ["suffix", "helper"])
def test_a_cache_another_account_can_write_is_not_read(place, cache, repair):
    root, kept, db_path = place
    if cache == "writable by others":
        os.chmod(root / CACHE, 0o777)
    else:
        (root / CACHE).rmdir()
        os.symlink(kept, root / CACHE)
    if repair == "suffix":
        _suffixed(db_path, root, root / CACHE / "demo-1.0-3.igos.tar.gz")
        name, recorded = "demo", "1.0-3"
    else:
        _helper(db_path, root, root / CACHE / "example.igos.tar.gz")
        name, recorded = "example", "0.8"
    db, said = _open(db_path, root)
    try:
        assert db.get_installed(name)["version"] == recorded
    finally:
        db.close()
    assert _repairs(db_path) == 0
    assert "is not a directory only its owner can write" in said, said


@pytest.mark.parametrize("repair", ["suffix", "helper"])
def test_the_same_archive_in_the_cache_repairs_the_row(place, repair):
    root, _kept, db_path = place
    if repair == "suffix":
        _suffixed(db_path, root, root / CACHE / "demo-1.0-3.igos.tar.gz")
        name, repaired = "demo", "1.0"
    else:
        _helper(db_path, root, root / CACHE / "example.igos.tar.gz")
        name, repaired = "example", "1.0"
    db, _ = _open(db_path, root)
    try:
        assert db.get_installed(name)["version"] == repaired
    finally:
        db.close()
    assert _repairs(db_path) == 1
