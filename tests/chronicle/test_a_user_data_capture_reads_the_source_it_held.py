# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A user-data capture reads the source it held, never a path's new object.

Root captures every home, and each home's owner controls the directories in it.
Each case forces the interleaving a race would produce, through Python's audit
hook: when the capture first acts on the source file (an open by its path, or
the hold of its name), the file's parent directory is exchanged for a link to
another directory holding an object of the same name, and exchanged back when
the staged copy is created. The other object is a file standing for one only
root may read, or a FIFO whose writer is blocked in its own open (released only
if something opens the FIFO). The capture must keep the account's own bytes or
refuse; it must never store, hash or open the other object.
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from chronicle import manifest, paths, userdata

OWN = b"the account's own bytes\n"
ONLY_ROOT = b"BYTES ONLY ROOT MAY READ (stand-in)\n"

_HOOK = {"on": None}


def _audit(event, args):
    action = _HOOK["on"]
    if action is not None and event in ("open", "os.scandir") and args \
            and isinstance(args[0], str):
        action(event, args[0])


sys.addaudithook(_audit)


@pytest.fixture
def home(tmp_path):
    os.umask(0o022)
    src = tmp_path / "src" / "home"
    d = src / "a" / "d"
    d.mkdir(parents=True)
    (d / "secret-name").write_bytes(OWN)
    rootish = tmp_path / "rootish"
    rootish.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.mkdir()
    yield src, d, rootish, target
    _HOOK["on"] = None


class _Exchange:
    """Exchanges `d` for a link to `rootish` at the first act on the source,
    and back at the open of its staged copy."""

    def __init__(self, d, rootish):
        self.d, self.rootish, self.real = d, rootish, d.with_name("d.real")
        self.source = str(d / "secret-name")
        self.events = []

    def __call__(self, event, path):
        if event != "open":
            return
        if not self.events and path in (self.source, "secret-name"):
            os.rename(self.d, self.real)
            os.symlink(self.rootish, self.d)
            self.events.append("exchanged")
        elif self.events == ["exchanged"] and "/userdata/.staging-" in path \
                and path.endswith("/a/d/secret-name"):
            self.restore()

    def restore(self):
        if self.events == ["exchanged"]:
            os.unlink(self.d)
            os.rename(self.real, self.d)
            self.events.append("restored")


def _stored(target, version, path):
    tree = userdata.userdata_tree(target, version)
    return (tree / str(path).lstrip("/")).read_bytes()


def test_control_the_exchange_fires_and_the_source_is_captured_whole(home):
    src, d, _rootish, target = home
    version = userdata.capture([str(src)], target, None, 1, 0, "control")
    assert _stored(target, version, d / "secret-name") == OWN


def test_a_parent_exchanged_around_the_copy_never_stores_the_other_file(home):
    src, d, rootish, target = home
    (rootish / "secret-name").write_bytes(ONLY_ROOT)
    os.chmod(rootish / "secret-name", 0o600)
    exchange = _Exchange(d, rootish)
    _HOOK["on"] = exchange
    try:
        version = userdata.capture([str(src)], target, None, 1, 0, "race")
    except userdata.SourceChangedError:
        version = None
    finally:
        _HOOK["on"] = None
        exchange.restore()
    assert exchange.events[0] == "exchanged", exchange.events
    if version is not None:
        assert _stored(target, version, d / "secret-name") == OWN
        found = manifest.find_version(target, paths.LAYER_USER_DATA, version)
        entry = next(e for e in found["entries"]
                     if e["path"] == str(d / "secret-name"))
        assert entry["sha256"] != userdata._cas.sha256_bytes(ONLY_ROOT)
    for stray in target.rglob("*"):
        if stray.is_file() and not stray.is_symlink():
            assert stray.read_bytes() != ONLY_ROOT, stray


def test_a_parent_exchanged_around_the_unchanged_hash_never_opens_the_other(
        home):
    src, d, rootish, target = home
    first = userdata.capture([str(src)], target, None, 1, 0, "first")
    previous = manifest.find_version(target, paths.LAYER_USER_DATA, first)
    os.mkfifo(rootish / "secret-name", 0o600)
    released = []

    def writer():
        fd = os.open(rootish / "secret-name", os.O_WRONLY)
        released.append(True)
        os.close(fd)

    peer = threading.Thread(target=writer, daemon=True)
    peer.start()
    time.sleep(0.3)
    exchange = _Exchange(d, rootish)
    _HOOK["on"] = exchange
    try:
        try:
            version = userdata.capture([str(src)], target, previous, 2, 0,
                                       "unchanged")
        except userdata.SourceChangedError:
            version = None
    finally:
        _HOOK["on"] = None
        exchange.restore()
        peer.join(1.0)
        was_released = bool(released)
        if not was_released:
            mine = os.open(rootish / "secret-name", os.O_RDWR | os.O_NONBLOCK)
            peer.join(2.0)
            os.close(mine)
    assert exchange.events[0] == "exchanged", exchange.events
    assert not was_released, "the capture opened the FIFO put in the source's place"
    if version is not None:
        assert _stored(target, version, d / "secret-name") == OWN


def test_a_fifo_in_a_home_is_recorded_without_being_opened(home):
    src, d, _rootish, target = home
    fifo = d / "a-fifo"
    os.mkfifo(fifo, 0o600)
    released = []

    def writer():
        fd = os.open(fifo, os.O_WRONLY)
        released.append(True)
        os.close(fd)

    peer = threading.Thread(target=writer, daemon=True)
    peer.start()
    time.sleep(0.3)
    try:
        version = userdata.capture([str(src)], target, None, 1, 0, "fifo")
    finally:
        peer.join(1.0)
        was_released = bool(released)
        if not was_released:
            mine = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            peer.join(2.0)
            os.close(mine)
    assert not was_released
    found = manifest.find_version(target, paths.LAYER_USER_DATA, version)
    entry = next(e for e in found["entries"] if e["path"] == str(fifo))
    assert entry["size"] == 0


def test_a_directory_swapped_for_a_link_as_it_is_listed_refuses_the_capture(
        home):
    src, d, rootish, target = home
    (rootish / "secret-name").write_bytes(ONLY_ROOT)
    os.chmod(rootish / "secret-name", 0o600)
    real = d.with_name("d.real")
    swapped = []

    def swap_as_listed(event, path):
        # os.walk decided d is a directory and lists it by its path: d is
        # exchanged for a link just as that listing starts.
        if event == "os.scandir" and path == str(d) and not swapped:
            os.rename(d, real)
            os.symlink(rootish, d)
            swapped.append(True)

    _HOOK["on"] = swap_as_listed
    try:
        with pytest.raises(userdata.SourceChangedError):
            userdata.capture([str(src)], target, None, 1, 0, "swapped")
    finally:
        _HOOK["on"] = None
        if d.is_symlink():
            os.unlink(d)
            os.rename(real, d)
    assert swapped
    assert not manifest.list_versions(target, paths.LAYER_USER_DATA)
    for stray in target.rglob("*"):
        if stray.is_file() and not stray.is_symlink():
            assert stray.read_bytes() != ONLY_ROOT, stray
