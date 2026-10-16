# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/check-config-baselines.py: every /etc baseline is the registered stock.

Run as the build runs it (a subprocess over a root with a real pkm database),
with the classes the gate judges set up one at a time: a stale baseline is
RED, an unregistered /etc file with a baseline row is RED, a residue row of a
removed package is counted and never a verdict, an owner that registered no
checksum is judged against the live bytes.
"""
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pkm.database import PackageDB  # noqa: E402

GATE = ROOT / "scripts" / "check-config-baselines.py"
A, B = "a" * 64, "b" * 64


def _root(tmp_path):
    root = tmp_path / "root"
    (root / "var/lib/igos").mkdir(parents=True)
    (root / "etc").mkdir()
    db = PackageDB(str(root / "var/lib/igos/pkm.db"), root=str(root))
    pid = db.add_installed(name="demo", version="1.0", release=3, tier="core",
                           description="fixture")
    db.add_files(pid, ["etc/", "etc/demo.conf", "usr/bin/", "usr/bin/demo"],
                 hashes={"etc/demo.conf": A, "usr/bin/demo": B})
    db.close()
    return root


def _run(root):
    return subprocess.run([sys.executable, "-I", str(GATE), "--chroot", str(root)],
                          capture_output=True, text=True, timeout=30)


def _sql(root, statement, params=()):
    conn = sqlite3.connect(str(root / "var/lib/igos/pkm.db"))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def test_a_baseline_equal_to_the_registered_stock_passes(tmp_path):
    result = _run(_root(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 configuration baseline(s)" in result.stdout
    assert "PASS" in result.stdout and "0 RED" in result.stdout


def test_a_stale_baseline_is_red_and_names_the_row(tmp_path):
    root = _root(tmp_path)
    _sql(root, "UPDATE config_files SET original_checksum = ? WHERE path = ?",
         ("f" * 64, "etc/demo.conf"))
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "STALE baseline" in result.stdout
    assert "/etc/demo.conf [demo-1.0-3: baseline ffffffffffff" in result.stdout
    assert "registered aaaaaaaaaaaa" in result.stdout
    assert "pkm refresh-baseline" in result.stdout


def test_a_residue_row_is_counted_not_judged(tmp_path):
    root = _root(tmp_path)
    _sql(root, "INSERT INTO config_files (path, package_id, original_checksum) "
               "VALUES (?, NULL, ?)", ("etc/gone.conf", "9" * 64))
    result = _run(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "residue row of a removed package (no owner, not on disk): 1" in result.stdout
    assert "/etc/gone.conf" in result.stdout


def test_a_baseline_for_an_unregistered_file_on_disk_is_red(tmp_path):
    root = _root(tmp_path)
    (root / "etc/orphan.conf").write_bytes(b"who owns me\n")
    _sql(root, "INSERT INTO config_files (path, package_id, original_checksum) "
               "VALUES (?, NULL, ?)", ("etc/orphan.conf", "9" * 64))
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no installed package registers" in result.stdout
    assert "/etc/orphan.conf" in result.stdout


@pytest.mark.parametrize("live_matches", [True, False])
def test_an_owner_without_a_checksum_is_judged_against_the_live_bytes(tmp_path, live_matches):
    root = _root(tmp_path)
    content = b"stock bytes\n"
    (root / "etc/demo.conf").write_bytes(content)
    live = hashlib.sha256(content).hexdigest()
    _sql(root, "UPDATE files SET checksum = NULL WHERE path = ?", ("etc/demo.conf",))
    _sql(root, "UPDATE config_files SET original_checksum = ? WHERE path = ?",
         (live if live_matches else "f" * 64, "etc/demo.conf"))
    result = _run(root)
    assert "judged against the live bytes: 1" in result.stdout
    assert result.returncode == (0 if live_matches else 1), result.stdout + result.stderr


def test_a_missing_database_is_a_setup_error(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    result = _run(root)
    assert result.returncode == 2
    assert "pkm database not found" in result.stderr
