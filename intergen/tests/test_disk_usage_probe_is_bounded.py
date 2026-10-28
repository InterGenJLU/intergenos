# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The disk-usage probe measures what is there and stops where it said it would.

A fixture tree with known sizes — a hard link, a symlink loop, a link to a
directory inside the tree, a link out to /proc, a dangling link, a deep chain,
many tiny files — is built in the test's own temporary directory. Every bound
the probe declares is exercised against it and the result must say what it did:

  * the sizes equal the fixture's (apparent and allocated), a hard link once;
  * no symlink is followed — the loop ends, the linked directory is counted
    once, /proc contributes nothing, the dangling link is not an error;
  * the depth limit leaves the deeper directories out AND declares them;
  * the budget ends the walk early AND declares it, with the top levels first;
  * the entries cap bounds what is reported, largest first, empties left out;
  * a directory on another filesystem is not entered and is listed;
  * an unreadable directory is named, never escalated;
  * the walk writes nothing and opens no socket (it only lists and stats).

Each test fails when the mechanism it names is removed (the mutation gate the
lane is landed under).
"""
from __future__ import annotations

import os
import socket
import stat
from types import SimpleNamespace
from unittest import mock

import pytest

from intergen import disk_usage as du

KiB, MiB = 1024, 1024 * 1024


def _write(path: str, size: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        if size:
            chunk = b"x" * min(size, 64 * KiB)
            left = size
            while left > 0:
                n = min(left, len(chunk))
                fh.write(chunk[:n]); left -= n


@pytest.fixture
def tree(tmp_path):
    root = str(tmp_path / "root")
    os.makedirs(root)
    _write(f"{root}/big/a.bin", 4 * MiB)
    _write(f"{root}/big/b.bin", 2 * MiB)
    _write(f"{root}/big/shared.bin", 1 * MiB)
    os.makedirs(f"{root}/medium")
    for i in range(4):
        _write(f"{root}/medium/m{i}.bin", 256 * KiB)
    os.link(f"{root}/big/shared.bin", f"{root}/medium/shared-link.bin")
    for i in range(300):
        _write(f"{root}/small/s{i:03d}", 1)
    os.makedirs(f"{root}/empty")
    p = f"{root}/deep"
    for level in range(1, 6):
        p = f"{p}/l{level}"
        _write(f"{p}/f.bin", 64 * KiB)
    _write(f"{root}/loop/a/data.bin", 128 * KiB)
    os.symlink("../..", f"{root}/loop/a/back")
    os.symlink("big", f"{root}/link-to-big")
    os.symlink("/proc", f"{root}/escape")
    os.symlink("nowhere", f"{root}/dangling")
    return root


APPARENT_TOTAL = 7 * MiB + 4 * 256 * KiB + 300 + 5 * 64 * KiB + 128 * KiB  # the hard link once


def test_the_sizes_are_the_fixtures_and_a_hard_link_counts_once(tree):
    r = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=20)
    assert r.complete and r.total == APPARENT_TOTAL
    by_name = {os.path.basename(e.path): e.size for e in r.entries}
    assert by_name["big"] + by_name["medium"] == 7 * MiB + 4 * 256 * KiB
    assert by_name["small"] == 300 and by_name["deep"] == 5 * 64 * KiB and by_name["loop"] == 128 * KiB
    assert r.files_scanned == 3 + 4 + 300 + 5 + 1  # names minus the hard link's second name
    r2 = du.probe(tree, budget_s=60, size_kind="allocated", max_entries=20)
    assert r2.complete and r2.total >= APPARENT_TOTAL
    assert {os.path.basename(e.path) for e in r2.entries} >= {"big", "medium", "small", "deep", "loop"}


def test_no_symlink_is_followed(tree):
    r = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=20)
    assert r.symlinks_seen == 4  # the loop, the link to big, the link to /proc, the dangling one
    assert r.complete  # the loop ended and /proc was never entered
    names = {os.path.basename(e.path) for e in r.entries}
    assert "link-to-big" not in names and "escape" not in names and "dangling" not in names
    assert r.total == APPARENT_TOTAL  # big counted once, nothing from /proc
    assert not r.unreadable


def test_the_depth_limit_leaves_the_deeper_directories_out_and_declares_them(tree):
    r = du.probe(tree, budget_s=60, depth_limit=3, size_kind="apparent", max_entries=20)
    # deep/l1/l2/l3 is depth 4: not entered; its file and the two below it are not counted
    assert r.total == APPARENT_TOTAL - 3 * 64 * KiB
    assert r.not_entered_depth == 1 and r.not_entered == 1 and not r.complete
    deep = next(e for e in r.entries if os.path.basename(e.path) == "deep")
    assert not deep.complete and deep.size == 2 * 64 * KiB
    assert any("depth limit of 3" in reason for reason in r.reasons())
    full = du.probe(tree, budget_s=60, depth_limit=100, size_kind="apparent")
    assert full.complete and full.total == APPARENT_TOTAL


def test_the_budget_ends_the_walk_early_and_declares_it(tree):
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 0.1
        return ticks["t"]

    r = du.probe(tree, budget_s=0.35, size_kind="apparent", clock=clock, max_entries=20)
    assert not r.complete and r.not_entered_budget >= 1
    assert r.dirs_scanned < 12  # the fixture has 12 directories; the walk stopped
    assert r.total < APPARENT_TOTAL
    assert any("budget ended" in reason for reason in r.reasons())
    assert all(not e.complete or e.dirs == 0 for e in r.entries)  # a partial subtree is marked
    generous = du.probe(tree, budget_s=60, size_kind="apparent")
    assert generous.complete and generous.total == APPARENT_TOTAL and generous.not_entered == 0


def test_the_entries_cap_bounds_the_report_largest_first_without_empties(tree):
    r = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=2)
    assert [os.path.basename(e.path) for e in r.entries] == ["big", "medium"]
    assert r.total == APPARENT_TOTAL  # the cap bounds the listing, not the measurement
    full = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=100)
    assert "empty" not in {os.path.basename(e.path) for e in full.entries}
    sizes = [e.size for e in full.entries]
    assert sizes == sorted(sizes, reverse=True)


def test_a_directory_on_another_filesystem_is_not_entered_and_is_listed(tree):
    real = du._lstat

    def other_device_for_medium(entry):
        st = real(entry)
        if entry.is_dir(follow_symlinks=False) and os.path.basename(entry.path) == "medium":
            return SimpleNamespace(st_dev=st.st_dev + 1, st_ino=st.st_ino, st_nlink=st.st_nlink,
                                   st_size=st.st_size, st_blocks=st.st_blocks, st_mode=st.st_mode)
        return st

    with mock.patch.object(du, "_lstat", side_effect=other_device_for_medium):
        r = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=20)
    assert r.other_filesystems == [f"{tree}/medium"]
    assert "medium" not in {os.path.basename(e.path) for e in r.entries}
    assert r.total == APPARENT_TOTAL - 4 * 256 * KiB  # medium's own files out; the hard link now counts in big only
    assert r.complete  # another filesystem is a boundary, not a gap


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_an_unreadable_directory_is_named_never_escalated(tree):
    locked = f"{tree}/locked"
    os.makedirs(locked)
    _write(f"{locked}/secret.bin", 64 * KiB)
    os.chmod(locked, 0)
    try:
        r = du.probe(tree, budget_s=60, size_kind="apparent", max_entries=20)
    finally:
        os.chmod(locked, stat.S_IRWXU)
    assert r.unreadable == [locked] and not r.complete
    assert any("not readable by this account" in reason for reason in r.reasons())
    assert r.total == APPARENT_TOTAL  # nothing guessed for the locked directory


def test_the_walk_writes_nothing_and_opens_no_socket(tree):
    before = sorted(os.walk(tree))
    with mock.patch("builtins.open", side_effect=AssertionError("the probe opened a file")), \
            mock.patch.object(socket, "socket", side_effect=AssertionError("the probe opened a socket")), \
            mock.patch.object(os, "remove", side_effect=AssertionError("the probe removed a file")), \
            mock.patch.object(os, "unlink", side_effect=AssertionError("the probe unlinked a file")), \
            mock.patch.object(os, "mkdir", side_effect=AssertionError("the probe made a directory")):
        r = du.probe(tree, budget_s=60, size_kind="apparent")
    assert r.complete and r.total == APPARENT_TOTAL
    assert sorted(os.walk(tree)) == before


def test_a_missing_root_declines_honestly(tmp_path):
    r = du.probe(str(tmp_path / "nowhere"), budget_s=1)
    assert not r.root_readable and not r.complete and r.total == 0
    assert r.unreadable == [str(tmp_path / "nowhere")]


def test_the_bounds_refuse_nonsense():
    with pytest.raises(ValueError):
        du.probe("/", budget_s=-1)
    with pytest.raises(ValueError):
        du.probe("/", size_kind="bytes")
    with pytest.raises(ValueError):
        du.answer("everything")


def test_human_size_is_binary_with_one_decimal():
    assert du.human_size(0) == "0 bytes" and du.human_size(1) == "1 byte"
    assert du.human_size(1023) == "1023 bytes" and du.human_size(1024) == "1.0 KiB"
    assert du.human_size(7 * MiB) == "7.0 MiB" and du.human_size(1536 * MiB) == "1.5 GiB"
