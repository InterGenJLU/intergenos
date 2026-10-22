# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A privileged open holds the object before it opens it.

A path a privileged helper is handed can name a FIFO or a device as easily as
a file, and opening one for reading or writing runs what it does when it is
opened. Each case stages such an object and watches it with two instruments
that cannot be fooled by the code under test: a FIFO's peer blocked in its own
open, which returns only when something opens the FIFO from the other side
(the kernel's own record of the open), and Python's audit hook, which records
every open with its flags, so an O_PATH hold (which opens nothing) is told
apart from an open for reading or writing. The controls show both instruments
see a real open.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from intergen import held_open

_OPENS: list[tuple[str, int]] = []
_RECORDING = [False]


def _record_opens(event: str, args: tuple) -> None:
    if _RECORDING[0] and event == "open" and isinstance(args[0], str):
        _OPENS.append((args[0], args[2] if len(args) > 2 and args[2] else 0))


sys.addaudithook(_record_opens)


def opened_for_io(path: str) -> list[tuple[str, int]]:
    """Every open since the last clear of `path` (by its full path, its last
    name or a /proc/self/fd path) that was not an O_PATH hold."""
    name = os.path.basename(path)
    return [(p, f) for p, f in _OPENS
            if (p == path or p == name or p.startswith("/proc/self/fd/"))
            and not f & os.O_PATH]


class _BlockedPeer:
    """A thread blocked in its own open of a FIFO: `side` O_WRONLY waits for a
    reader, O_RDONLY for a writer. `released` is True once the open returned,
    which happens only when something opened the FIFO from the other side."""

    def __init__(self, fifo: Path, side: int) -> None:
        self.fifo, self.released = fifo, False
        self._thread = threading.Thread(target=self._wait, args=(side,),
                                        daemon=True)
        self._thread.start()
        time.sleep(0.3)

    def _wait(self, side: int) -> None:
        fd = os.open(self.fifo, side)
        self.released = True
        os.close(fd)

    def settle(self) -> bool:
        """Whether the peer was released, after a moment for it to run; then
        release it from this side if nothing did, so its thread ends."""
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
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _OPENS.clear()
        _RECORDING[0] = True

    def tearDown(self) -> None:
        _RECORDING[0] = False
        _OPENS.clear()
        self._tmp.cleanup()

    def fifo(self, name: str = "object") -> Path:
        path = self.root / name
        os.mkfifo(path, 0o600)
        return path


class TheHoldOpensNothingItRefuses(_Scratch):

    def test_control_a_regular_file_opens_as_the_object_held(self) -> None:
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
        # The instrument sees the open for reading the hold makes.
        self.assertEqual(len(opened_for_io(str(path))), 1, _OPENS)

    def test_control_the_peer_instrument_sees_an_open(self) -> None:
        fifo = self.fifo()
        peer = _BlockedPeer(fifo, os.O_WRONLY)
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        os.close(fd)
        self.assertTrue(peer.settle())

    def test_a_fifo_is_refused_and_its_waiting_peers_stay_waiting(self) -> None:
        for flags, side in ((os.O_RDONLY, os.O_WRONLY),
                            (os.O_WRONLY, os.O_RDONLY)):
            with self.subTest(flags=flags):
                fifo = self.fifo(f"fifo-{flags}")
                peer = _BlockedPeer(fifo, side)
                _OPENS.clear()
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.open_held(str(fifo), flags)
                self.assertEqual(refused.exception.refusal, "kind")
                self.assertIn("a FIFO, not a regular file, so nothing was "
                              "opened", str(refused.exception))
                self.assertFalse(peer.settle(), "the FIFO was opened")
                self.assertEqual(opened_for_io(str(fifo)), [])

    def test_a_device_is_refused_before_it_is_opened(self) -> None:
        _OPENS.clear()
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.open_held("/dev/null", os.O_RDONLY)
        self.assertEqual(refused.exception.refusal, "kind")
        self.assertIn("a character device", str(refused.exception))
        self.assertEqual(opened_for_io("/dev/null"), [])

    def test_a_link_at_the_name_or_above_it_is_refused(self) -> None:
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

    def test_a_path_not_written_out_in_full_is_refused(self) -> None:
        (self.root / "file").write_bytes(b"x")
        for text in ("file", f"{self.root}/./file", f"{self.root}/sub/../file",
                     f"{self.root}//file"):
            with self.subTest(text=text):
                with self.assertRaises(held_open.HeldOpenRefused) as refused:
                    held_open.open_held(text, os.O_RDONLY)
                self.assertEqual(refused.exception.refusal, "path")

    def test_the_callers_check_refuses_before_any_open(self) -> None:
        path = self.root / "file"
        path.write_bytes(b"x")
        _OPENS.clear()
        with self.assertRaises(held_open.HeldOpenRefused) as refused:
            held_open.open_held(str(path), os.O_RDONLY,
                                check=lambda info: f"owned by uid {info.st_uid}")
        self.assertEqual(refused.exception.refusal, "check")
        self.assertEqual(str(refused.exception), f"owned by uid {os.getuid()}")
        self.assertEqual(opened_for_io(str(path)), [])


class TheRunnersFileToolHoldsTheObjectFirst(_Scratch):
    """write_file under the privileged runner opens an existing name through
    the hold: a FIFO or a device at the name is refused before it is opened,
    so the refusal's "nothing was" is true. The runner's condition (root with
    the caller's PKEXEC_UID) is stood in for; the walk and the opens are
    real."""

    def setUp(self) -> None:
        super().setUp()
        from intergen import dispatch_token
        self.dt = dispatch_token
        runner = mock.patch.object(dispatch_token, "_under_the_privileged_runner",
                                   return_value=True)
        runner.start()
        self.addCleanup(runner.stop)

    def test_control_a_regular_file_is_read_under_the_runner(self) -> None:
        path = self.root / "notes.txt"
        path.write_text("the notes")
        self.assertEqual(self.dt.read_text_refusing_install_secrets(path),
                         "the notes")

    def test_a_fifo_read_for_its_old_content_is_not_opened(self) -> None:
        fifo = self.fifo("notes.txt")
        peer = _BlockedPeer(fifo, os.O_WRONLY)
        _OPENS.clear()
        with self.assertRaises(self.dt.PathNotOpenedAsChecked) as refused:
            self.dt.read_text_refusing_install_secrets(fifo)
        self.assertIn("so nothing was", str(refused.exception))
        self.assertFalse(peer.settle(), "the FIFO was opened for reading")
        self.assertEqual(opened_for_io(str(fifo)), [])

    def test_a_fifo_written_is_not_opened(self) -> None:
        fifo = self.fifo("notes.txt")
        peer = _BlockedPeer(fifo, os.O_RDONLY)
        _OPENS.clear()
        with self.assertRaises(self.dt.PathNotOpenedAsChecked) as refused:
            self.dt.opened_refusing_install_secrets(fifo, os.O_WRONLY)
        self.assertIn("so nothing was", str(refused.exception))
        self.assertFalse(peer.settle(), "the FIFO was opened for writing")
        self.assertEqual(opened_for_io(str(fifo)), [])

    def test_a_device_is_refused_before_it_is_opened(self) -> None:
        _OPENS.clear()
        with self.assertRaises(self.dt.PathNotOpenedAsChecked) as refused:
            self.dt.opened_refusing_install_secrets("/dev/null", os.O_RDONLY)
        self.assertIn("not a regular file", str(refused.exception))
        self.assertEqual(opened_for_io("/dev/null"), [])


class TheRequestFileIsHeldBeforeItIsRead(_Scratch):
    """privileged_request.read_request holds the request file before it opens
    it: a FIFO at the request's name is refused at once, and the runner never
    waits on it."""

    def _read_within(self, path: Path, seconds: float = 3.0):
        from intergen import privileged_request
        out: dict = {}

        def read() -> None:
            try:
                out["result"] = privileged_request.read_request(
                    str(path), expected_uid=os.getuid())
            except BaseException as exc:  # the refusal is the result
                out["error"] = exc
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(seconds)
        waited = thread.is_alive()
        if waited:  # release the open from this side so the thread ends
            recording, _RECORDING[0] = _RECORDING[0], False
            try:
                mine = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                thread.join(5.0)
                os.close(mine)
            finally:
                _RECORDING[0] = recording
        return waited, out

    def test_a_fifo_at_the_request_name_is_refused_without_waiting(self) -> None:
        from intergen import privileged_request
        fifo = self.fifo("intergen-request-0123456789abcdef0123456789abcdef")
        _OPENS.clear()
        waited, out = self._read_within(fifo)
        self.assertFalse(waited, "read_request waited on the FIFO's open")
        self.assertIsInstance(out.get("error"), privileged_request.RequestError)
        self.assertIn("not a regular file", str(out["error"]))
        self.assertEqual(opened_for_io(str(fifo)), [])


if __name__ == "__main__":
    unittest.main()
