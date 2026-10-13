# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A read sees what the last command committed.

The database runs in write-ahead-log mode: a commit lands in pkm.db-wal and
reaches pkm.db itself only when the log is checkpointed. pkm's read-only open
used `immutable=1`, which promises SQLite the file never changes and so never
reads the log at all. Measured on a build root on 2026-10-13: a heal ran
`pkm install X` then `pkm verify X` for 193 packages, and 181 of the verifies
answered "Package 'X' is not installed" — every row was committed, and every
one was still in the log. A fresh database with one committed row reproduced
it: an immutable open saw 0 rows, a mode=ro open saw 1.

Two changes, both here:

* A read-only open uses `mode=ro`, which reads the log, while pkm.db-wal
  holds frames. With no log, or an empty one, the database file alone is
  complete and `immutable=1` reads it, creating nothing beside it (the
  no-sidecar tests in test_database_init.py and its siblings hold as they
  were). `immutable=1` is also the fallback where `mode=ro` cannot open: a
  reader who may not create the log's index file beside a database it does
  not own.
* Every command that opens the database for writing folds the log into the
  database file at its end (PRAGMA wal_checkpoint(TRUNCATE)), so even a
  reader that opens immutable reads what the command committed. A fold that
  another reader holds up is reported, never silent.
"""
import io
import os
import shutil
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

import pkm.cli as cli
from pkm import output, rootpaths
from pkm.database import PackageDB


def _immutable_held(db_path):
    """`held` for demo as an immutable reader sees it: the database file
    alone, the log never read."""
    c = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        return c.execute(
            "SELECT held FROM installed WHERE name = 'demo'").fetchone()[0]
    finally:
        c.close()


def test_a_fresh_read_only_open_sees_a_row_committed_moments_ago(tmp_path):
    db_path = tmp_path / "pkm.db"
    writer = PackageDB(db_path, root=str(tmp_path))
    try:
        writer.add_installed("demo", "1.0", install_method="archive")
        # The writer stays open, so nothing has folded the log yet.
        assert Path(str(db_path) + "-wal").stat().st_size > 0
        reader = PackageDB(db_path, root=str(tmp_path), read_only=True)
        try:
            assert reader.get_installed("demo") is not None, (
                "a read-only open must see a committed row at once")
            assert reader.read_mode == "ro"
        finally:
            reader.close()
    finally:
        writer.close()


def test_a_reader_that_cannot_open_the_log_reads_the_folded_file(tmp_path):
    """The ordinary user reading the root-owned database after the writer's
    fold: the log is empty or gone, so the open is immutable=1 — which reads
    the database file alone, complete once the writer has folded, and needs
    nothing written in a directory that is not the reader's."""
    home = tmp_path / "igos"
    home.mkdir()
    db_path = home / "pkm.db"
    writer = PackageDB(db_path, root=str(tmp_path))
    writer.add_installed("demo", "1.0", install_method="archive")
    writer.checkpoint_wal()
    writer.close()
    os.chmod(home, 0o555)
    try:
        if os.access(home, os.W_OK):
            pytest.skip("this account can write a 0555 directory (root)")
        reader = PackageDB(db_path, root=str(tmp_path), read_only=True)
        try:
            assert reader.read_mode == "immutable"
            assert reader.get_installed("demo") is not None
        finally:
            reader.close()
    finally:
        os.chmod(home, 0o755)


def test_a_log_with_frames_that_cannot_be_opened_falls_back_to_the_file(
        tmp_path):
    """The log holds frames, but mode=ro cannot open it: no index file beside
    it, and a directory the reader cannot write (a log a writer left behind
    when it stopped, read by an account that does not own the directory).
    The open falls back to immutable=1, reads what the database file holds,
    and leaves the directory as it found it; the log's own frames are read
    once a writer folds them."""
    live = tmp_path / "live"
    live.mkdir()
    home = tmp_path / "igos"
    home.mkdir()
    writer = PackageDB(live / "pkm.db", root=str(tmp_path))
    try:
        writer.add_installed("folded", "1.0", install_method="archive")
        writer.checkpoint_wal()
        writer.add_installed("logged", "1.0", install_method="archive")
        shutil.copy2(live / "pkm.db", home / "pkm.db")
        shutil.copy2(live / "pkm.db-wal", home / "pkm.db-wal")
    finally:
        writer.close()
    assert (home / "pkm.db-wal").stat().st_size > 0
    os.chmod(home, 0o555)
    try:
        if os.access(home, os.W_OK):
            pytest.skip("this account can write a 0555 directory (root)")
        reader = PackageDB(home / "pkm.db", root=str(tmp_path), read_only=True)
        try:
            assert reader.read_mode == "immutable"
            assert reader.get_installed("folded") is not None
        finally:
            reader.close()
        assert sorted(p.name for p in home.iterdir()) == [
            "pkm.db", "pkm.db-wal"]
    finally:
        os.chmod(home, 0o755)


def test_a_read_only_open_does_not_checkpoint(tmp_path):
    db_path = tmp_path / "pkm.db"
    PackageDB(db_path, root=str(tmp_path)).close()
    reader = PackageDB(db_path, root=str(tmp_path), read_only=True)
    try:
        assert reader.checkpoint_wal() is None
    finally:
        reader.close()


def _run_pkm(argv):
    """Run pkm's own entry point in this process, as root would run it. The
    install root --root sets is process-wide; it is put back afterwards so no
    later test runs against this one's scratch root."""
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    prior_root = cli._INSTALL_ROOT
    prior_level = output.process_level()
    try:
        with patch.object(sys, "argv", ["pkm", *argv]), \
             patch("os.geteuid", return_value=0), \
             redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                rc = e.code or 0
    finally:
        cli.set_install_root(prior_root)
        output.set_process_level(prior_level)
    return rc, out.getvalue(), err.getvalue()


def _scratch_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    db_path = Path(rootpaths.db_path(root))
    db = PackageDB(db_path, root=str(root))
    db.add_installed("demo", "1.0", install_method="archive")
    db.close()
    return root, db_path


def test_a_command_that_writes_folds_the_log_at_its_end(tmp_path):
    """Another connection holds the database open, so the command's own close
    is not the last one and SQLite does not fold the log on its own — the
    state the build root was in. The command folds it itself."""
    root, db_path = _scratch_root(tmp_path)
    holder = sqlite3.connect(str(db_path))
    try:
        holder.execute("SELECT count(*) FROM installed").fetchall()

        rc, out, err = _run_pkm(["--root", str(root), "hold", "demo"])

        assert rc == 0, out + err
        assert _immutable_held(db_path) == 1, (
            "the command's change must be in the database file itself")
        assert Path(str(db_path) + "-wal").stat().st_size == 0
    finally:
        holder.close()


def test_a_fold_another_reader_holds_up_is_reported(tmp_path):
    root, db_path = _scratch_root(tmp_path)
    reader = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                             isolation_level=None, timeout=0.1)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM installed").fetchall()

        rc, out, err = _run_pkm(["--root", str(root), "hold", "demo"])

        assert rc == 0, out + err
        said = " ".join(err.split())
        assert "write-ahead log" in said and "not folded" in said, err
    finally:
        reader.execute("COMMIT")
        reader.close()
