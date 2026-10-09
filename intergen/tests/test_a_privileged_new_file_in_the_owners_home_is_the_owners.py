# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A file the privileged write creates in the requesting owner's home is that owner's.

An approved write_file runs as root under the privileged runner, so a file it
creates is root's: owned by root:root, with the mode the umask leaves (0644 under
the usual 022). In the home of the person who asked for it, that is a file they
cannot change or remove and every local account can read. Measured on a real
privileged firing: the new file landed root:root 0644 with the installed
package, at the base and at the tip alike.

These cells hold the outcome at the file boundary, where the file is created:

* a NEW file below the requesting owner's home is handed to that owner (their
  uid and primary gid) with mode 0600, through the open descriptor;
* the same through write_file's own execute, the consumer;
* an EXISTING file keeps its owner and mode, a file OUTSIDE the owner's home
  keeps today's behaviour, and a sibling directory whose name merely begins like
  the home is outside it;
* a home reached through a symbolic link is compared as where it resolves;
* the containing directory is not touched, and no private name is left behind;
* outside the privileged runner nothing is handed to anyone: PKEXEC_UID is
  believed only in a process that runs as root, the rule _owning_home states;
* a new file that cannot be handed over is removed again and the write reports
  a failure, so a person never finds a file of root's they asked to be written.

Hermetic: the runner's context is answered in process (the uid the module sees,
PKEXEC_UID, the password-database entry), every file lives in a throwaway
directory, the ownership call is recorded and then made for real with this
account's own uid and gid (which needs no privilege), and the signed never-list
is pinned absent so a write measures this boundary and not the manifest.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import dispatch_token as dt
from intergen.tools import write_file as write_file_tool

WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC


class APrivilegedNewFileInTheOwnersHomeIsTheOwners(unittest.TestCase):

    def setUp(self) -> None:
        self.uid = os.getuid()
        self.gid = os.getgid()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o750)
        self.outside = self.root / "elsewhere"
        self.outside.mkdir()
        self.chowned: list[tuple[int, int, int]] = []
        self._real_fchown = os.fchown
        # A fixed umask, so "today's behaviour" has one meaning in every cell.
        self._umask = os.umask(0o022)
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(write_file_tool, "_policy_loaded", True))
        stack.enter_context(mock.patch.object(write_file_tool, "_policy_cache", None))
        self.addCleanup(stack.close)

    def tearDown(self) -> None:
        os.umask(self._umask)
        self._tmp.cleanup()

    # --- the privileged runner's context, answered in process ----------------

    def _record_fchown(self, fd: int, uid: int, gid: int) -> None:
        self.chowned.append((os.fstat(fd).st_ino, uid, gid))
        self._real_fchown(fd, uid, gid)

    def _as_the_runner(self, home: Path | None = None):
        """Run as the privileged runner does, for the account this test runs as.

        The module believes PKEXEC_UID only in a process that runs as root, so the
        uid it reads is answered as 0; the password database answers the
        requesting account with ``home`` and this account's own gid, so the
        ownership call the module makes needs no privilege.
        """
        entry = mock.MagicMock()
        entry.pw_dir = str(home if home is not None else self.home)
        entry.pw_uid = self.uid
        entry.pw_gid = self.gid
        fake_pwd = mock.MagicMock()
        fake_pwd.getpwuid.return_value = entry
        fake_pwd.getpwnam.return_value = entry
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(dt, "pwd", fake_pwd))
        stack.enter_context(mock.patch.dict(os.environ, {"PKEXEC_UID": str(self.uid)}))
        stack.enter_context(mock.patch.object(dt.os, "getuid", return_value=0))
        stack.enter_context(mock.patch.object(dt.os, "fchown", self._record_fchown))
        return stack

    def _create(self, path: Path) -> None:
        os.close(dt.opened_refusing_install_secrets(path, WRITE_FLAGS))

    def _mode(self, path: Path) -> int:
        return stat.S_IMODE(os.stat(path).st_mode)

    def _write(self, path: Path, content: str = "written\n"):
        return write_file_tool.WriteFileTool().execute(
            {"path": str(path), "content": content})

    # --- a new file below the owner's home ------------------------------------

    def test_a_new_file_below_the_owners_home_is_handed_to_the_owner_0600(
            self) -> None:
        target = self.home / "new.txt"
        with self._as_the_runner():
            self._create(target)
        ino = os.stat(target).st_ino
        self.assertEqual(
            self.chowned, [(ino, self.uid, self.gid)],
            "a new file created in the requesting owner's home under the runner "
            "must be handed to that owner's uid and primary gid, once, through "
            "its descriptor; recorded ownership calls: "
            f"{self.chowned}")
        self.assertEqual(
            self._mode(target), 0o600,
            f"the new file's mode is {oct(self._mode(target))}; a file written "
            "for the person into their own home is theirs alone, 0600")

    def test_write_file_creates_the_owners_file_0600_under_the_runner(
            self) -> None:
        target = self.home / "note.txt"
        with self._as_the_runner():
            result = self._write(target, "hello\n")
        self.assertTrue(result.success, result.content)
        self.assertEqual(target.read_text(), "hello\n")
        self.assertEqual(self.chowned, [(os.stat(target).st_ino, self.uid, self.gid)])
        self.assertEqual(self._mode(target), 0o600)

    def test_a_home_reached_through_a_link_is_compared_as_where_it_resolves(
            self) -> None:
        link = self.root / "home-link"
        link.symlink_to(self.home, target_is_directory=True)
        target = self.home / "through-link.txt"
        with self._as_the_runner(home=link):
            self._create(target)
        self.assertEqual(self.chowned, [(os.stat(target).st_ino, self.uid, self.gid)])
        self.assertEqual(self._mode(target), 0o600)

    def test_a_new_file_in_a_directory_below_the_home_is_handed_over_too(
            self) -> None:
        docs = self.home / "docs"
        docs.mkdir(mode=0o750)
        target = docs / "deep.txt"
        with self._as_the_runner():
            self._create(target)
        self.assertEqual(self.chowned, [(os.stat(target).st_ino, self.uid, self.gid)])
        self.assertEqual(self._mode(target), 0o600)

    # --- what keeps today's behaviour ----------------------------------------

    def test_an_existing_file_keeps_its_owner_and_mode(self) -> None:
        target = self.home / "existing.txt"
        target.write_text("before\n")
        os.chmod(target, 0o640)
        with self._as_the_runner():
            result = self._write(target, "after\n")
        self.assertTrue(result.success, result.content)
        self.assertEqual(target.read_text(), "after\n")
        self.assertEqual(self.chowned, [], "an existing file is not handed to anyone")
        self.assertEqual(self._mode(target), 0o640)

    def test_a_new_file_outside_the_owners_home_keeps_todays_behaviour(
            self) -> None:
        target = self.outside / "new.txt"
        with self._as_the_runner():
            self._create(target)
        self.assertEqual(self.chowned, [])
        self.assertEqual(self._mode(target), 0o644)

    def test_a_sibling_whose_name_begins_like_the_home_is_outside_it(
            self) -> None:
        sibling = self.root / "home2"
        sibling.mkdir()
        target = sibling / "new.txt"
        with self._as_the_runner():
            self._create(target)
        self.assertEqual(self.chowned, [],
                         "home2 is not below home: the comparison is by whole "
                         "path components, never by the text of the path")
        self.assertEqual(self._mode(target), 0o644)

    def test_outside_the_runner_nothing_is_handed_to_anyone(self) -> None:
        """PKEXEC_UID in an ordinary process is ordinary environment."""
        target = self.home / "not-root.txt"
        with mock.patch.dict(os.environ, {"PKEXEC_UID": str(self.uid)}), \
                mock.patch.object(dt.os, "fchown", self._record_fchown):
            self._create(target)
        self.assertEqual(self.chowned, [])
        self.assertEqual(self._mode(target), 0o644)

    def test_the_containing_directory_is_not_touched_and_no_private_name_is_left(
            self) -> None:
        docs = self.home / "docs"
        docs.mkdir(mode=0o750)
        before = os.stat(docs)
        with self._as_the_runner():
            self._create(docs / "only.txt")
        after = os.stat(docs)
        self.assertEqual(
            (stat.S_IMODE(after.st_mode), after.st_uid, after.st_gid, after.st_ino),
            (stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid, before.st_ino))
        self.assertNotIn(after.st_ino, [ino for ino, _u, _g in self.chowned])
        self.assertEqual(sorted(os.listdir(docs)), ["only.txt"])

    # --- a new file that cannot be handed over --------------------------------

    def test_a_new_file_that_cannot_be_handed_over_is_removed_and_reported(
            self) -> None:
        not_handed = getattr(dt, "CreatedFileNotHandedToOwner", None)
        self.assertIsNotNone(
            not_handed,
            "dispatch_token names no failure for a new file that could not be "
            "handed to its owner, so such a file is left as root's")
        target = self.home / "refused-chown.txt"

        def refuse(fd: int, uid: int, gid: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        with self._as_the_runner(), \
                mock.patch.object(dt.os, "fchown", refuse):
            with self.assertRaises(not_handed) as caught:
                self._create(target)
        self.assertIsInstance(caught.exception, OSError)
        self.assertFalse(os.path.lexists(target),
                         "the file that could not be handed over is still there")
        self.assertEqual(os.listdir(self.home), [], "a name was left behind")

    def test_a_hand_over_failure_of_any_kind_is_reported_and_the_file_removed(
            self) -> None:
        """Not only an OSError: whatever the hand-over raises is that failure."""
        not_handed = getattr(dt, "CreatedFileNotHandedToOwner", None)
        self.assertIsNotNone(not_handed)
        target = self.home / "unexpected-failure.txt"

        def unexpected(fd: int, uid: int, gid: int) -> None:
            raise TypeError("gid should be integer")

        with self._as_the_runner(), \
                mock.patch.object(dt.os, "fchown", unexpected):
            with self.assertRaises(not_handed):
                self._create(target)
        self.assertFalse(os.path.lexists(target))
        self.assertEqual(os.listdir(self.home), [])

    def test_write_file_reports_a_new_file_it_could_not_hand_over(self) -> None:
        target = self.home / "refused-through-the-tool.txt"

        def refuse(fd: int, uid: int, gid: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        with self._as_the_runner(), \
                mock.patch.object(dt.os, "fchown", refuse):
            result = self._write(target, "never kept\n")
        self.assertFalse(result.success,
                         f"the write reported success: {result.content!r}")
        self.assertIn("could not be handed", result.content)
        self.assertFalse(os.path.lexists(target))
        self.assertEqual(os.listdir(self.home), [])


if __name__ == "__main__":
    unittest.main()
