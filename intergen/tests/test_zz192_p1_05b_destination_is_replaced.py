# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Item 192, P1-05 — the capture's destination is replaced, never written through.

The first pass on this surface confined the destination to a non-symlink file of
the daemon's own shape directly inside the temp directory. A second, independent
read reproduced a way past it: the handler CHECKS the destination and then the
capture OPENS it, and those are two operations on a path any peer in the session
can alter in between. A symbolic link placed in that window is followed by
Gio.File.replace under FileCreateFlags.NONE — the capture lands on whatever the
link points at, outside the directory the caller was confined to, and the link is
left in place for the next one.

GioReplaceFollowsASymlinkTests drives exactly the call the capture makes, with
the same libgio the compositor links, and shows both halves: NONE follows the
link, REPLACE_DESTINATION replaces it and leaves the file outside untouched. It
is the control for this case — the way past the guard is measured, not assumed,
and it keeps passing after the fix because it is a statement about libgio and not
about this file.

WHAT THIS CASE DOES NOT DO. It does not execute the GJS handler: extension.js
imports resource:///org/gnome/shell modules that exist only inside the running
compositor, so no test on this machine can call into it. The pin below holds the
capture to the atomic flag; the handler itself running is the installed-system
leg at the chain's landing tier, recorded as that item's own finding.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

_EXT_JS = (Path(__file__).resolve().parents[1]
           / "panel" / "extension" / "extension.js")

# A name of this project's own namespace that nothing ships and nothing owns.
# The daemon's real name is never claimed, watched or disturbed by these cases.
_PROBE_NAME = "com.intergenos.InterGenShellCallerProbe"

# The shape the daemon's tool creates, so the probe paths are the ones the
# surface would accept (tempfile.mkstemp(prefix=..., suffix=...)).
_SHOT_PREFIX = "intergen-shot-"
_SHOT_SUFFIX = ".png"


def _read_source() -> str:
    if not _EXT_JS.is_file():
        raise AssertionError(f"extension source not found at {_EXT_JS}")
    return _EXT_JS.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The text of one top-level `function name(...) { ... }`, braces balanced.

    The pins below are about what ONE function does, so they must not be
    satisfied by a token somewhere else in a 600-line file.
    """
    start = source.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"{name} is not in {_EXT_JS.name}")
    open_brace = source.find("{", start)
    depth = 0
    for i in range(open_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"{name}'s body is not brace-balanced")


class GioReplaceFollowsASymlinkTests(unittest.TestCase):
    """The destination boundary, on the real libgio the compositor links."""

    def setUp(self) -> None:
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)
        self.target = os.path.join(self._outside.name, "outside-the-directory")
        self._written = []
        self.addCleanup(self._remove_written)

    def _remove_written(self) -> None:
        for p in self._written:
            if os.path.lexists(p):
                os.remove(p)

    def _shot_path(self, tag: str) -> str:
        p = os.path.join(GLib.get_tmp_dir(),
                         f"{_SHOT_PREFIX}p1-05b-{tag}-{os.getpid()}{_SHOT_SUFFIX}")
        if os.path.lexists(p):
            os.remove(p)
        self._written.append(p)
        return p

    def _capture_through(self, path: str, flags) -> None:
        """The call _shellCaptureToFile makes, with the bytes a capture writes."""
        stream = Gio.File.new_for_path(path).replace(None, False, flags, None)
        stream.write(b"\x89PNG\r\n\x1a\n")
        stream.close(None)

    def test_the_shipped_flag_writes_through_a_link_placed_after_the_check(self) -> None:
        """RED on the first pass: the guard checks, then the open follows a link.

        This is the reproduction, not a wish: FileCreateFlags.NONE is what the
        capture used, and the file outside the directory ends up holding the
        capture's bytes."""
        Path(self.target).write_text("the bytes outside the directory\n")
        shot = self._shot_path("none")
        os.symlink(self.target, shot)          # the window between check and open
        self._capture_through(shot, Gio.FileCreateFlags.NONE)
        self.assertTrue(os.path.islink(shot),
                        "the link is still a link, ready for the next capture")
        self.assertEqual(Path(self.target).read_bytes(), b"\x89PNG\r\n\x1a\n",
                         "the capture was written THROUGH the link, outside the "
                         "directory the caller was confined to")

    def test_replace_destination_replaces_the_link_instead(self) -> None:
        """The same call under the atomic flag: the link is what gets replaced."""
        Path(self.target).write_text("the bytes outside the directory\n")
        shot = self._shot_path("replace")
        os.symlink(self.target, shot)
        self._capture_through(shot, Gio.FileCreateFlags.REPLACE_DESTINATION)
        self.assertFalse(os.path.islink(shot),
                         "the link itself was replaced by a regular file")
        self.assertEqual(Path(self.target).read_text(),
                         "the bytes outside the directory\n",
                         "the file outside the directory is untouched")
        self.assertEqual(Path(shot).read_bytes(), b"\x89PNG\r\n\x1a\n")


class TheCaptureOpensWithTheAtomicFlagTests(unittest.TestCase):
    """The destination guard, pinned in the function that makes the call."""

    def test_the_capture_replaces_the_destination_rather_than_writing_through_it(self) -> None:
        body = _function_body(_read_source(), "_shellCaptureToFile")
        self.assertIn("REPLACE_DESTINATION", body,
                      "the capture's own open must replace the destination, so a "
                      "link placed after the check is replaced and not followed")
        self.assertNotRegex(
            body, r"Gio\.FileCreateFlags\.NONE",
            "FileCreateFlags.NONE is the flag that follows a link")


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
