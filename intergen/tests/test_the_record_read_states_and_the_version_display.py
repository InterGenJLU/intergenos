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
   that reads it. Only "active" establishes that the service is running and
   only "inactive" or "failed" that it is not; a state in transition, an error
   from the service manager, or no answer at all establishes neither, and is
   reported as establishing neither. The frontier question command had the
   same branch and said the assistant was running whenever the name had an
   owner; it reads the service state the same way.

   And a call the assistant ANSWERED, with the error it returns on purpose —
   a question over the size limit, or an internal error it has logged — came
   back to both commands looking like a call that never finished, so each said
   the call did not complete in time, and the question command added that the
   assistant might still be loading and to try again in a moment. For a
   question over the size limit that advice can never succeed. The commands
   now report the assistant's own sentence instead.
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

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from intergen import cli, package_record  # noqa: E402


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


def _with_a_stale_owner(command, service_state: str = "", *,
                        manager_error: str = "",
                        probe_raises: BaseException | None = None):
    """Run ``command`` where the bus name has an owner, the call never
    finishes, and the user service reports ``service_state`` — or the service
    manager answers only with ``manager_error``, or cannot be asked at all.
    Returns the exit code and everything the command printed."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    probe = mock.Mock()
    probe.stdout = service_state + "\n" if service_state else ""
    probe.stderr = manager_error
    if probe_raises is not None:
        run = mock.Mock(side_effect=probe_raises)
    else:
        run = mock.Mock(return_value=probe)
    with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
            mock.patch.object(cli, "try_dbus", return_value=None), \
            mock.patch.object(cli.subprocess, "run", run), \
            mock.patch.object(cli, "_AskFillers", _NoFillers, create=True):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                command()
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue() + err.getvalue()


# Answers from the service manager that say neither that the service is
# running nor that it is not: (label, arrangement, the text that must be shown).
_STATES_THAT_ESTABLISH_NEITHER = [
    ("a start in progress", {"service_state": "activating"}, "activating"),
    ("a stop in progress", {"service_state": "deactivating"}, "deactivating"),
    ("the service manager answered with an error",
     {"manager_error": "Failed to connect to user scope bus via local "
                       "transport: No such file or directory"},
     "Failed to connect to user scope bus"),
    ("the service manager could not be asked",
     {"probe_raises": FileNotFoundError("systemctl")}, "unknown"),
]


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
    def _ask_with_a_stale_owner(service_state: str = "", **arrangement):
        return _with_a_stale_owner(
            lambda: cli.cmd_ask("is my disk encrypted?"), service_state,
            **arrangement)

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
        # "is active", not "active": the shorter text is inside "inactive".
        self.assertIn("is active", said)
        self.assertIn("loading", said,
                      "with the service really running, still loading is the "
                      "reading that holds")

    def test_a_state_that_establishes_neither_reading_asserts_neither(self) -> None:
        """A start or a stop in progress, an error from the service manager,
        and no answer at all say neither that the service is running nor that
        it is not, so the command must say neither."""
        for label, arrangement, shown in _STATES_THAT_ESTABLISH_NEITHER:
            with self.subTest(label):
                code, said = self._ask_with_a_stale_owner(**arrangement)
                self.assertEqual(code, 2)
                self.assertIn(shown, said,
                              "the state that was read must be shown")
                self.assertNotIn("not running", said,
                                 "a state that does not say the service is "
                                 "stopped must not be reported as stopped")
                self.assertNotIn("waiting will not clear", said)
                self.assertNotIn("InterGen is running", said,
                                 "nor may it be reported as running")


class TheFrontierCommandReadsTheServiceStateToo(unittest.TestCase):
    """Finding 3, on the other question command. It said the assistant was
    running whenever the name had an owner and the call returned nothing."""

    @staticmethod
    def _escalate_with_a_stale_owner(service_state: str = "", **arrangement):
        return _with_a_stale_owner(
            lambda: cli.cmd_ask_frontier("what changed in the last kernel?"),
            service_state, **arrangement)

    def test_it_does_not_say_running_when_the_service_is_not(self) -> None:
        code, said = self._escalate_with_a_stale_owner("inactive")
        self.assertEqual(code, 2)
        self.assertIn("is inactive", said,
                      "the state that was read must be shown")
        self.assertNotIn("InterGen is running", said,
                         "the service manager says it is not")
        self.assertIn("not running", said)

    def test_it_says_running_when_the_service_is(self) -> None:
        code, said = self._escalate_with_a_stale_owner("active")
        self.assertEqual(code, 2)
        self.assertIn("is active", said)
        self.assertIn("InterGen is running", said,
                      "with the service really running, that is the reading "
                      "that holds")

    def test_a_state_that_establishes_neither_reading_asserts_neither(self) -> None:
        for label, arrangement, shown in _STATES_THAT_ESTABLISH_NEITHER:
            with self.subTest(label):
                code, said = self._escalate_with_a_stale_owner(**arrangement)
                self.assertEqual(code, 2)
                self.assertIn(shown, said,
                              "the state that was read must be shown")
                self.assertNotIn("not running", said)
                self.assertNotIn("InterGen is running", said)


def _answered_with(error, service_state: str = "active"):
    """Run both commands where the bus name has an owner and the call comes
    back with ``error`` instead of a value. The service manager says
    ``service_state``, so a command that ignores the error and reads the state
    instead is caught saying the wrong thing. Returns, per command, the exit
    code and everything the command printed."""
    def call(method, *args, **options):
        failure = options.get("failure")
        if failure is not None:
            failure.append(error)
        return None

    probe = mock.Mock()
    probe.stdout = service_state + "\n"
    probe.stderr = ""
    said = {}
    for name, command in (("ask", cli.cmd_ask),
                          ("ask-frontier", cli.cmd_ask_frontier)):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
                mock.patch.object(cli, "try_dbus", side_effect=call), \
                mock.patch.object(cli.subprocess, "run", return_value=probe), \
                mock.patch.object(cli, "_AskFillers", _NoFillers, create=True):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    command("is my disk encrypted?")
                except SystemExit as exc:
                    code = exc.code
        said[name] = (code, out.getvalue() + err.getvalue())
    return said


class TheAssistantsOwnRefusalIsReportedAsARefusal(unittest.TestCase):
    """A call the assistant answered with its own error is not a call that
    never came back."""

    def test_a_question_over_the_size_limit_is_reported_in_its_own_words(self) -> None:
        refusal = Gio.DBusError.new_for_dbus_error(
            "com.intergenos.InterGen.Error",
            "Message too large (max 4096 bytes).")
        for name, (code, said) in _answered_with(refusal).items():
            with self.subTest(name):
                self.assertEqual(code, 2)
                self.assertIn("Message too large (max 4096 bytes).", said,
                              "the assistant's own sentence must be shown")
                self.assertNotIn("GDBus.Error", said,
                                 "the bus library's prefix is not the sentence")
                self.assertNotIn("did not complete", said,
                                 "the call completed: it was answered")
                self.assertNotIn("try again in a moment", said,
                                 "waiting never makes an over-long question fit")

    def test_an_internal_error_is_reported_in_its_own_words(self) -> None:
        refusal = Gio.DBusError.new_for_dbus_error(
            "com.intergenos.InterGen.Error",
            "Internal error — check daemon logs for details.")
        for name, (code, said) in _answered_with(refusal).items():
            with self.subTest(name):
                self.assertEqual(code, 2)
                self.assertIn("Internal error", said)
                self.assertNotIn("loading", said)

    def test_an_error_from_anything_else_is_left_to_the_service_state(self) -> None:
        # A name owned by something that is not the assistant answers with the
        # bus's own error; that is the stale-owner case, read from the state.
        foreign = Gio.DBusError.new_for_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod", "No such interface")
        for name, (code, said) in _answered_with(foreign, "inactive").items():
            with self.subTest(name):
                self.assertEqual(code, 2)
                self.assertIn("is inactive", said)
                self.assertIn("not running", said)
                self.assertNotIn("No such interface", said)

    def test_a_call_that_timed_out_is_left_to_the_service_state(self) -> None:
        timed_out = GLib.Error.new_literal(
            Gio.io_error_quark(), "Timeout was reached",
            int(Gio.IOErrorEnum.TIMED_OUT))
        code, said = _answered_with(timed_out, "active")["ask"]
        self.assertEqual(code, 2)
        self.assertIn("is active", said)
        self.assertIn("loading", said,
                      "a call that really did not finish, with the service "
                      "running, may still be loading")


class _NoFillers:
    """The waiting animation, with nothing to animate."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
