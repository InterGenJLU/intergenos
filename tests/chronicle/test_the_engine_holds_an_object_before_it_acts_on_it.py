# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The backup engine holds an object before it acts on it (chronicle.held_open).

Each case stages a FIFO, a device, a link or a directory another account could
change, and watches with two instruments the code under test cannot fool: a
FIFO's peer blocked in its own open, which returns only when something opens
the FIFO from the other side, and Python's audit hook, which records every
open with its flags, so an O_PATH hold (which opens nothing) is told apart from
an open for reading or writing. The controls show both instruments see a real
open.
"""

import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from chronicle import held_open

_OPENS = []
_RECORDING = [False]


def _record_opens(event, args):
    if _RECORDING[0] and event == "open" and isinstance(args[0], str):
        _OPENS.append((args[0], args[2] if len(args) > 2 and args[2] else 0))


sys.addaudithook(_record_opens)


def opened_for_io(path):
    """Every open since the last clear of `path` (by its full path, its last
    name or a /proc/self/fd path) that was not an O_PATH hold."""
    name = os.path.basename(path)
    return [(p, f) for p, f in _OPENS
            if (p == path or p == name or p.startswith("/proc/self/fd/"))
            and not f & os.O_PATH]


class _BlockedPeer:
    """A thread blocked in its own open of a FIFO; `released` is True once
    that open returned, which happens only when something opened the FIFO
    from the other side."""

    def __init__(self, fifo, side):
        self.fifo, self.released = fifo, False
        self._thread = threading.Thread(target=self._wait, args=(side,),
                                        daemon=True)
        self._thread.start()
        time.sleep(0.3)

    def _wait(self, side):
        fd = os.open(self.fifo, side)
        self.released = True
        os.close(fd)

    def settle(self):
        self._thread.join(1.0)
        released = self.released
        if not released:
            recording, _RECORDING[0] = _RECORDING[0], False
            try:
                mine = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
                self._thread.join(2.0)
                os.close(mine)
            finally:
                _RECORDING[0] = recording
        return released


class _Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _OPENS.clear()
        _RECORDING[0] = True

    def tearDown(self):
        _RECORDING[0] = False
        _OPENS.clear()
        self._tmp.cleanup()


class TheHoldOpensNothingItRefuses(_Scratch):

    def test_control_a_regular_file_opens_as_the_object_held(self):
        path = self.root / "file"
        path.write_bytes(b"the bytes")
        _OPENS.clear()
        fd, info = held_open.open_held(str(path), os.O_RDONLY)
        try:
            self.assertEqual(os.read(fd, 64), b"the bytes")
            self.assertEqual((info.st_dev, info.st_ino),
                             (os.stat(path).st_dev, os.stat(path).st_ino))
        finally:
            os.close(fd)
        self.assertEqual(len(opened_for_io(str(path))), 1, _OPENS)

    def test_control_the_peer_instrument_sees_an_open(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        peer = _BlockedPeer(fifo, os.O_WRONLY)
        os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
        self.assertTrue(peer.settle())

    def test_a_fifo_is_refused_and_its_waiting_peer_stays_waiting(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        peer = _BlockedPeer(fifo, os.O_WRONLY)
        _OPENS.clear()
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.open_held(str(fifo), os.O_RDONLY)
        self.assertEqual(refused.exception.refusal, "kind")
        self.assertIn("a FIFO, not a regular file, so nothing was opened",
                      str(refused.exception))
        self.assertFalse(peer.settle(), "the FIFO was opened")
        self.assertEqual(opened_for_io(str(fifo)), [])

    def test_a_device_is_refused_before_it_is_opened(self):
        _OPENS.clear()
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.open_held("/dev/null", os.O_RDONLY)
        self.assertEqual(refused.exception.refusal, "kind")
        self.assertIn("a character device", str(refused.exception))
        self.assertEqual(opened_for_io("/dev/null"), [])

    def test_a_link_at_the_name_or_above_it_is_refused(self):
        target = self.root / "target"
        target.mkdir()
        (target / "file").write_bytes(b"x")
        (self.root / "link").symlink_to(target / "file")
        (self.root / "linked-dir").symlink_to(target)
        for path, refusal in ((self.root / "link", "link"),
                              (self.root / "linked-dir" / "file", "walk")):
            with self.subTest(path=path):
                _OPENS.clear()
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.open_held(str(path), os.O_RDONLY)
                self.assertEqual(refused.exception.refusal, refusal)
                self.assertEqual(opened_for_io(str(target / "file")), [])

    def test_a_path_not_written_out_in_full_is_refused(self):
        (self.root / "file").write_bytes(b"x")
        for text in ("file", f"{self.root}/./file", f"{self.root}/sub/../file",
                     f"{self.root}//file"):
            with self.subTest(text=text):
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.open_held(text, os.O_RDONLY)
                self.assertEqual(refused.exception.refusal, "path")

    def test_the_callers_check_refuses_before_any_open(self):
        path = self.root / "file"
        path.write_bytes(b"x")
        _OPENS.clear()
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.open_held(str(path), os.O_RDONLY,
                                check=lambda info: "not this one")
        self.assertEqual(refused.exception.refusal, "check")
        self.assertEqual(str(refused.exception), "not this one")
        self.assertEqual(opened_for_io(str(path)), [])


class TheWalkCreatesAndReadsByDescriptor(_Scratch):

    def test_a_missing_directory_is_made_at_the_descriptor_above_it(self):
        fd = held_open.held_directory(str(self.root / "a" / "b"), create=True)
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        self.assertTrue(stat.S_ISDIR(info.st_mode))
        self.assertEqual(os.stat(self.root / "a" / "b").st_ino, info.st_ino)

    def test_a_link_on_the_way_is_refused_even_when_creating(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / "a").symlink_to(elsewhere)
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.held_directory(str(self.root / "a" / "b"), create=True)
        self.assertEqual(refused.exception.refusal, "walk")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_a_held_links_target_is_read_from_the_link_itself(self):
        (self.root / "link").symlink_to("some/where")
        dir_fd, name, _text = held_open.held_parent(str(self.root / "link"))
        try:
            held, info = held_open.hold_at(dir_fd, name)
            try:
                self.assertTrue(stat.S_ISLNK(info.st_mode))
                self.assertEqual(held_open.read_link(held), "some/where")
            finally:
                os.close(held)
        finally:
            os.close(dir_fd)

    def test_a_directory_opens_through_its_hold_and_a_file_does_not(self):
        (self.root / "dir").mkdir()
        (self.root / "file").write_bytes(b"x")
        dir_fd = held_open.held_directory(str(self.root))
        try:
            held, info = held_open.hold_at(dir_fd, "dir")
            opened = held_open.opened_directory(held, info, "dir")
            os.fchmod(opened, 0o700)
            os.close(opened)
            os.close(held)
            self.assertEqual(stat.S_IMODE(os.stat(self.root / "dir").st_mode),
                             0o700)
            held, info = held_open.hold_at(dir_fd, "file")
            try:
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.opened_directory(held, info, "file")
                self.assertEqual(refused.exception.refusal, "kind")
            finally:
                os.close(held)
        finally:
            os.close(dir_fd)


class AStoreRootAnotherAccountCouldChangeIsRefused(_Scratch):

    def mine(self):
        return {0, os.geteuid()}

    def test_control_a_tree_of_the_engines_own_directories_passes(self):
        store = self.root / "store"
        store.mkdir(mode=0o700)
        held_open.require_owned_tree(str(store), self.mine())

    def test_a_root_its_group_or_others_can_write_is_refused(self):
        store = self.root / "store"
        store.mkdir()
        for mode in (0o770, 0o707, 0o1777):
            with self.subTest(mode=oct(mode)):
                os.chmod(store, mode)
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.require_owned_tree(str(store), self.mine())
                self.assertEqual(refused.exception.refusal, "owner")
                self.assertIn("writable by its group or by other accounts",
                              str(refused.exception))

    def test_a_parent_others_can_write_is_refused_unless_it_is_sticky(self):
        parent = self.root / "parent"
        parent.mkdir()
        (parent / "store").mkdir(mode=0o700)
        os.chmod(parent, 0o777)
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.require_owned_tree(str(parent / "store"), self.mine())
        self.assertEqual(refused.exception.filename, str(parent))
        os.chmod(parent, 0o1777)
        held_open.require_owned_tree(str(parent / "store"), self.mine())

    def test_a_link_on_the_way_is_refused(self):
        real = self.root / "real"
        real.mkdir(mode=0o700)
        (self.root / "link").symlink_to(real)
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.require_owned_tree(str(self.root / "link"), self.mine())
        self.assertEqual(refused.exception.refusal, "walk")

    def test_a_directory_owned_by_another_uid_is_refused(self):
        store = self.root / "store"
        store.mkdir(mode=0o700)
        others = {uid for uid in (0, os.geteuid()) if uid != os.geteuid()}
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.require_owned_tree(str(store), others or {65534})
        self.assertEqual(refused.exception.refusal, "owner")
        self.assertIn("is owned by uid", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
