# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Three ways the installed release was reported as known, or as absent, when
it was neither.

The reader that answers "which release of this package is installed" promises
three distinct states: a record was read, the database was read and holds no
row for this package, and nothing could be established. A second reading on
2026-09-22 found two places where that promise was not kept, and a third where
a command asserts a condition it has not checked.

1. THE READER COULD NOT TELL "NOT THERE" FROM "CANNOT LOOK". It asked whether
   the path was a regular file before opening it, and that question answers no
   for a database behind a directory this user cannot traverse exactly as it
   answers no for a database that does not exist. A machine whose record was
   merely out of reach was told it had no record at all, and the version
   command said so in those words. Not reachable on a default installation,
   where the directory and the database are both world-readable; reachable the
   moment either is tightened.

2. THE DISPLAY READ AN ABSENT ANSWER AS A CONFIDENT ONE. The status payload
   carries a field saying whether the release in the version string was really
   read. A payload built by a daemon older than that field carries no such
   field, and the display treated its absence as "known", printing a bare
   version with nothing marking it — byte for byte what it prints when the
   release IS known. The payload answers the question by itself: the record's
   form carries the release after a hyphen and the running code's bare version
   does not.

3. A COMMAND ASSERTED THAT THE ASSISTANT WAS RUNNING WITHOUT LOOKING. When the
   bus name has an owner but the call does not finish, the command told the
   person the assistant is running and may still be loading, and to try again
   in a moment. Where the name is owned by something that is not the managed
   service, that is advice to wait for a condition that will not clear. The
   service state is one call away and this tree already carries the routine
   that reads it.
"""

from __future__ import annotations

import io
import os
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli, package_record


def _write_record(path: Path, version: str = "0.1.0", release: int = 296) -> None:
    """A package database holding one live row for this package.

    The table and columns are the ones the reader's own query names. That is
    load-bearing: a database with the wrong table name is not a database with
    no row, it is a database that cannot be read, and a case built on one would
    measure the failure path while appearing to measure the success path. The
    case below reads a reachable copy of this database and expects a record,
    which is what catches that mistake.
    """
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE installed (name TEXT, version TEXT, "
                "release INTEGER, superseded_by TEXT)")
    con.execute("INSERT INTO installed VALUES (?, ?, ?, NULL)",
                ("intergen", version, release))
    con.commit()
    con.close()


class TheReaderTellsNotThereFromCannotLook(unittest.TestCase):
    """Finding 1."""

    def test_a_reachable_record_is_read(self) -> None:
        """The control that makes the two cases below mean something: the
        scratch database this file writes really does read as a record."""
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "pkm.db"
            _write_record(db)
            state, record = package_record.read_record(db)
        self.assertEqual(state, package_record.READ_RECORD)
        self.assertEqual(record, ("0.1.0", 296))

    def test_a_database_that_is_not_there_is_no_record(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "there-is-no-database-here.db"
            state, record = package_record.read_record(missing)
        self.assertEqual(state, package_record.READ_NO_RECORD)
        self.assertIsNone(record)

    @unittest.skipIf(os.geteuid() == 0,
                     "the superuser traverses a directory with no permissions, "
                     "so this case cannot be produced as root")
    def test_a_database_this_user_cannot_reach_is_unreadable(self) -> None:
        with TemporaryDirectory() as tmp:
            closed = Path(tmp) / "closed"
            closed.mkdir()
            db = closed / "pkm.db"
            _write_record(db)
            os.chmod(closed, 0o000)
            try:
                # The negative control for this case: the arrangement must
                # really deny the read, or the case proves nothing.
                with self.assertRaises(OSError) as denied:
                    db.stat()
                self.assertNotIsInstance(denied.exception, FileNotFoundError)
                state, record = package_record.read_record(db)
            finally:
                os.chmod(closed, 0o700)
        self.assertEqual(
            state, package_record.READ_UNREADABLE,
            "a record that could not be looked at is not a record that is "
            "not there")
        self.assertIsNone(record)

    @unittest.skipIf(os.geteuid() == 0, "as above")
    def test_the_version_command_says_unreadable_not_no_record(self) -> None:
        with TemporaryDirectory() as tmp:
            closed = Path(tmp) / "closed"
            closed.mkdir()
            db = closed / "pkm.db"
            _write_record(db)
            os.chmod(closed, 0o000)
            try:
                text, known, state = package_record.version_status(db)
            finally:
                os.chmod(closed, 0o700)
        self.assertFalse(known)
        self.assertEqual(state, package_record.READ_UNREADABLE)


class TheDisplayDoesNotReadAnAbsentAnswerAsAConfidentOne(unittest.TestCase):
    """Finding 2."""

    @staticmethod
    def _rendered(status: dict) -> str:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            cli.print_status(status)
        return out.getvalue()

    def test_a_payload_without_the_field_and_a_bare_version_says_unknown(self) -> None:
        shown = self._rendered({"running": True, "version": "0.1.0"})
        self.assertIn("release unknown", shown,
                      "a bare version was not read from the package record, "
                      "and a payload that does not say so must not be read as "
                      "though it had")

    def test_a_payload_without_the_field_and_a_full_identity_says_nothing(self) -> None:
        shown = self._rendered({"running": True, "version": "0.1.0-296"})
        self.assertIn("0.1.0-296", shown)
        self.assertNotIn("release unknown", shown,
                         "an identity carrying its release was read from the "
                         "record, so nothing is unknown about it")

    def test_the_field_is_still_believed_when_it_is_there(self) -> None:
        said_known = self._rendered(
            {"running": True, "version": "0.1.0-296", "release_known": True})
        self.assertNotIn("release unknown", said_known)
        said_unknown = self._rendered(
            {"running": True, "version": "0.1.0-296", "release_known": False})
        self.assertIn("release unknown", said_unknown,
                      "a payload that says the release was not read is "
                      "believed, whatever the version string looks like")

    def test_a_payload_with_no_version_at_all_is_not_called_known(self) -> None:
        shown = self._rendered({"running": True})
        self.assertIn("release unknown", shown)


class TheCommandAsksTheServiceStateBeforeSayingWhatIsRunning(unittest.TestCase):
    """Finding 3."""

    @staticmethod
    def _ask_with_a_stale_owner(service_state: str):
        """The bus name has an owner, the call never finishes, and the user
        service reports ``service_state``."""
        out, err = io.StringIO(), io.StringIO()
        code = None
        probe = mock.Mock()
        probe.stdout = service_state + "\n"
        probe.stderr = ""
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
                mock.patch.object(cli, "try_dbus", return_value=None), \
                mock.patch.object(cli.subprocess, "run", return_value=probe), \
                mock.patch.object(cli, "_AskFillers", _NoFillers, create=True):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_ask("is my disk encrypted?")
                except SystemExit as exc:
                    code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_it_says_the_service_is_not_running_when_it_is_not(self) -> None:
        code, said = self._ask_with_a_stale_owner("inactive")
        self.assertEqual(code, 2)
        self.assertIn("inactive", said,
                      "the state that was read must be shown")
        self.assertNotIn("try again in a moment", said,
                         "waiting will not clear a name owned by something "
                         "that is not the managed service")

    def test_it_still_offers_to_wait_when_the_service_is_running(self) -> None:
        code, said = self._ask_with_a_stale_owner("active")
        self.assertEqual(code, 2)
        self.assertIn("active", said)
        self.assertIn("loading", said,
                      "with the service really running, still loading is the "
                      "reading that holds")


class _NoFillers:
    """The waiting animation, with nothing to animate."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
