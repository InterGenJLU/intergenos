# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read-only queries preserve legacy schemas and use one package lookup."""

import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from pkm.database import PackageDB, SCHEMA


@pytest.fixture(params=(False, True), ids=("legacy", "current"))
def database(tmp_path, request):
    path = tmp_path / "pkm.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        connection.execute("ALTER TABLE installed ADD COLUMN metadata_note TEXT")
        package_id = connection.execute(
            "INSERT INTO installed (name, version, release, metadata_note) "
            "VALUES ('example', '1.2', 3, 'preserve additional metadata')"
        ).lastrowid
        connection.executemany(
            "INSERT INTO files (package_id, path, is_dir, source) VALUES (?, ?, ?, ?)",
            [(package_id, "usr/bin", 1, None),
             (package_id, "usr/bin/example", 0, "archive"),
             (package_id, "opt/example/run", 0, "helper")],
        )
        if not request.param:
            connection.execute("ALTER TABLE files DROP COLUMN source")
    return path, request.param


def assert_database_unchanged(path, before):
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_get_files_reads_legacy_rows_without_migration(database, tmp_path):
    path, has_source = database
    before = path.read_bytes()
    with PackageDB(path, root=str(tmp_path), read_only=True) as db:
        assert db.get_files("example") == [
            {"path": "opt/example/run", "is_dir": False,
             "source": "helper" if has_source else None},
            {"path": "usr/bin", "is_dir": True, "source": None},
            {"path": "usr/bin/example", "is_dir": False,
             "source": "archive" if has_source else None},
        ]
        assert db.get_files("missing") == []
        assert ("source" in db._table_columns("files")) is has_source
    assert_database_unchanged(path, before)


def test_cli_files_reads_legacy_rows_without_migration(database, tmp_path):
    path, _ = database
    before = path.read_bytes()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["IGOS_TRACE_ROOT"] = str(tmp_path / "trace")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pkm", "--root", str(tmp_path),
         "--db", str(path), "files", "example"],
        cwd=Path(__file__).resolve().parents[2], env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "  Files in example (3):",
        "    /opt/example/run",
        "  d /usr/bin",
        "    /usr/bin/example",
    ]
    assert_database_unchanged(path, before)


@pytest.mark.parametrize("name", ("example", "missing"))
def test_get_installed_uses_one_select_and_preserves_every_column(database, tmp_path, name):
    path, _ = database
    before = path.read_bytes()
    with PackageDB(path, root=str(tmp_path), read_only=True) as db:
        expected_cursor = db.conn.execute(
            "SELECT * FROM installed WHERE name = ?", (name,)
        )
        expected_row = expected_cursor.fetchone()
        expected = (dict(zip([column[0] for column in expected_cursor.description],
                             expected_row)) if expected_row is not None else None)
        statements = []
        db.conn.set_trace_callback(statements.append)
        actual = db.get_installed(name)
        db.conn.set_trace_callback(None)
        assert actual == expected
        assert len(statements) == 1, statements
        assert statements[0].lstrip().upper().startswith("SELECT ")
        if actual is not None:
            assert actual["metadata_note"] == "preserve additional metadata"
    assert_database_unchanged(path, before)
