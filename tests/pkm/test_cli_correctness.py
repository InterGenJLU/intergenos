# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Cache retention and command options preserve the user's selected state."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pkm import cli
from pkm.database import PackageDB


def _cache(monkeypatch, tmp_path, installed, entries, **options):
    directory = tmp_path / "cache"
    directory.mkdir()
    for filename, timestamp in entries:
        path = directory / filename
        path.write_bytes(b"cached payload")
        os.utime(path, (timestamp, timestamp))
    records = {record["name"]: record for record in installed}
    db = SimpleNamespace(list_installed=lambda: list(records.values()),
                         get_installed=records.get)
    args = SimpleNamespace(cache_rollback=False, cache_keep_n=None,
                           cache_all=False)
    vars(args).update(options)
    monkeypatch.setattr(cli, "repo_pkg_cache", lambda: directory)
    return cli.cmd_cache_clean(db, args), {p.name for p in directory.iterdir()}


@pytest.mark.parametrize("name,version", [
    ("dialog", "1.3-20260107"), ("linux-firmware", "20260107"),
    ("example", "1.0"),
])
def test_cache_keeps_installed_version_and_release(
        monkeypatch, tmp_path, name, version):
    installed = f"{name}-{version}-1.igos.tar.gz"
    newer = f"{name}-{version}-2.igos.tar.gz"
    older = f"{name}-0.9-1.igos.tar.gz"
    rc, retained = _cache(
        monkeypatch, tmp_path,
        [{"name": name, "version": version, "release": 1}],
        [(installed, 100), (newer, 300), (older, 200)])
    assert rc == 0
    assert retained == {installed}


def test_exact_installed_archive_wins_over_longer_package_name(
        monkeypatch, tmp_path):
    parent = "dialog-1.3-20260107-1.igos.tar.gz"
    sibling = "dialog-1.3-2.0-1.igos.tar.gz"
    rc, retained = _cache(monkeypatch, tmp_path, [
        {"name": "dialog", "version": "1.3-20260107", "release": 1},
        {"name": "dialog-1.3", "version": "2.0", "release": 1},
    ], [(parent, 100), (sibling, 200)])
    assert rc == 0
    assert retained == {parent, sibling}


def test_cache_without_installed_release_keeps_newest_fallback(monkeypatch, tmp_path):
    newest = "example-1.0-3.igos.tar.gz"
    rc, retained = _cache(
        monkeypatch, tmp_path, [{"name": "example", "version": "1.0", "release": 1}],
        [("example-1.0-2.igos.tar.gz", 100), (newest, 200)])
    assert rc == 0
    assert retained == {newest}


def test_cache_removes_uninstalled_archives_but_retains_unparseable_names(
        monkeypatch, tmp_path, capsys):
    unknown = "unfinished.igos.tar.gz"
    rc, retained = _cache(monkeypatch, tmp_path, [], [
        ("gone-1.0-1.igos.tar.gz", 100), (unknown, 200)])
    assert rc == 0
    assert retained == {unknown}
    output = capsys.readouterr()
    assert "leaving them untouched" in " ".join((output.out + output.err).split())


def test_cache_keep_count_does_not_group_unrelated_name_prefix(monkeypatch, tmp_path):
    installed = "example-1.0-1.igos.tar.gz"
    unrelated = "example-tools-2.0-1.igos.tar.gz"
    rc, retained = _cache(
        monkeypatch, tmp_path, [{"name": "example", "version": "1.0", "release": 1}],
        [(installed, 100), (unrelated, 200)], cache_keep_n=1)
    assert rc == 0
    assert retained == {installed, unrelated}


def test_exact_archives_survive_overlapping_installed_version_stems(monkeypatch, tmp_path):
    first = "example-1-2-1.igos.tar.gz"
    second = "example-1-2-2.igos.tar.gz"
    rc, retained = _cache(monkeypatch, tmp_path, [
        {"name": "example", "version": "1-2", "release": 1},
        {"name": "example-1", "version": "2", "release": 2},
    ], [(first, 100), (second, 200)])
    assert rc == 0
    assert retained == {first, second}


@pytest.mark.parametrize("command", ["install", "remove", "upgrade", "reinstall", "install-helper"])
@pytest.mark.parametrize("flag,attribute", [("-v", "verbose"), ("-q", "quiet")])
def test_verbosity_survives_subcommand_defaults(command, flag, attribute):
    parser = cli.build_parser()
    for arguments in ([flag, command, "example"], [command, "example", flag],
                      [flag, command, "example", flag]):
        args = parser.parse_args(arguments)
        assert getattr(args, attribute) is True
        assert getattr(args, "quiet" if attribute == "verbose" else "verbose") is False


@pytest.fixture
def history_db(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "history.db"
    with PackageDB(path, root=str(root)) as db:
        for n in range(65):
            db.conn.execute(
                "INSERT INTO history (timestamp, operation, package_name, success) VALUES (?, ?, ?, ?)",
                ((datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=n)).isoformat(),
                 "install", f"example-{n:02}", 1))
        db.conn.commit()
    return root, path


def _invoke(root, path, *args):
    return subprocess.run(
        [sys.executable, "-m", "pkm", "--root", str(root), "--db", str(path), *args],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("options,expected,first,last", [
    ([], 50, "example-64", "example-15"),
    (["--limit", "60"], 60, "example-64", "example-05"),
    (["--all"], 65, "example-64", "example-00"),
    (["example-00", "--all"], 1, "example-00", "example-00"),
])
def test_history_cli_reaches_older_rows_without_writes(history_db, options, expected, first, last):
    root, path = history_db
    before = path.read_bytes()
    result = _invoke(root, path, "history", *options)
    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if "[✓]" in line]
    assert len(rows) == expected
    assert first in rows[0]
    assert last in rows[-1]
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


@pytest.mark.parametrize("options", [
    ["--limit", "0"], ["--limit", "-1"], ["--limit", "invalid"],
    ["--limit", "10", "--all"],
    ["--limit", "9223372036854775808"],
])
def test_history_rejects_invalid_or_conflicting_limits(options):
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["history", *options])
    assert raised.value.code == 2


@pytest.mark.parametrize("flag,attribute", [("-v", "verbose"), ("-q", "quiet")])
def test_real_cli_entry_preserves_parsed_verbosity(flag, attribute):
    # Observe the actual CLI entry and parser, stopping before a mutation can
    # be dispatched. This runs safely even when the suite is invoked as root.
    code = """
import json
from pkm import cli
parser = cli.build_parser()
parse = parser.parse_args
def observe(*args, **kwargs):
    result = parse(*args, **kwargs)
    print(json.dumps(vars(result)))
    raise SystemExit(0)
parser.parse_args = observe
cli.build_parser = lambda: parser
cli.main()
"""
    results = []
    for arguments in ([flag, "install", "example"], ["install", "example", flag]):
        result = subprocess.run(
            [sys.executable, "-c", code, *arguments],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
    assert results[0][attribute] is True
