# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Asking with the service stopped reports the service, and starts nothing.

Measured on this project's own machine on 2026-09-19: with nothing owning the
assistant's bus name, `intergen ask` printed "InterGen daemon not running.
Starting direct session..." and built an assistant inside the asking process —
a model server and all. While that session answered, `systemctl --user
is-active intergen` still read inactive, and when the command returned the
session was gone. The machine's own record of what is running never knew about
it, and the person who asked was told a daemon had started.

What is pinned here:

  * with no service on the bus, the command exits non-zero, names the state of
    the user service and the command that starts it, and constructs no
    assistant at all;
  * the developer path still exists behind --direct, which is never implied,
    and says in plain words that the session it answers from is not the
    managed service;
  * the same rule holds for the frontier-model question;
  * a machine whose service manager cannot be asked reports the state as
    unknown rather than guessing.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from intergen import cli


class _NeverStarted(AssertionError):
    """Raised if the command builds an assistant of its own."""


def _run(argv_tail, *, owner=False, systemctl_state="inactive"):
    """Run `intergen ask ...` with no service on the bus and nothing startable."""
    import intergen.dbus_daemon as daemon_mod
    out, err = io.StringIO(), io.StringIO()
    code = None

    def _refuse(*a, **k):
        raise _NeverStarted("an assistant was constructed in this process")

    probe = mock.Mock(stdout=systemctl_state, stderr="", returncode=0)
    with mock.patch.object(cli, "daemon_has_owner", return_value=owner), \
            mock.patch.object(daemon_mod, "InterGenDaemon", _refuse), \
            mock.patch.object(cli.subprocess, "run", return_value=probe), \
            mock.patch.object(cli.sys, "argv", ["intergen"] + argv_tail):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue(), err.getvalue()


class TheServiceIsReportedNotStartedTests(unittest.TestCase):

    def test_asking_with_no_service_exits_non_zero_and_starts_nothing(self):
        code, out, err = _run(["ask", "what is my hostname?"])
        self.assertEqual(code, 2, err)
        self.assertIn("not running", err)
        self.assertIn("systemctl --user start intergen", err)
        self.assertIn("inactive", err)
        self.assertNotIn("Starting direct session", out)

    def test_the_frontier_question_starts_nothing_either(self):
        code, _out, err = _run(["ask-frontier", "what is my hostname?"])
        self.assertEqual(code, 2, err)
        self.assertIn("systemctl --user start intergen", err)

    def test_a_service_manager_that_cannot_be_asked_says_unknown(self):
        import intergen.dbus_daemon as daemon_mod
        out, err = io.StringIO(), io.StringIO()
        code = None
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
                mock.patch.object(daemon_mod, "InterGenDaemon", object), \
                mock.patch.object(cli.subprocess, "run",
                                  side_effect=FileNotFoundError("systemctl")):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_ask("what is my hostname?")
                except SystemExit as exc:
                    code = exc.code
        self.assertEqual(code, 2)
        self.assertIn("unknown", err.getvalue())

    def test_the_in_process_session_still_exists_behind_direct(self):
        """--direct is the developer path: it answers here and says so."""
        import intergen.dbus_daemon as daemon_mod
        started = {}

        class _Session:
            def __init__(self):
                started["built"] = True

            def start_service(self):
                started["started"] = True

            def ask(self, message):
                return '{"response": "box-01", "handled": true}'

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
                mock.patch.object(daemon_mod, "InterGenDaemon", _Session), \
                mock.patch.object(cli, "_last_answer_path",
                                  return_value=__import__("pathlib").Path(
                                      "/nonexistent/last-answer.json")):
            with redirect_stdout(out), redirect_stderr(err):
                cli.cmd_ask("what is my hostname?", direct=True)
        self.assertTrue(started.get("built"))
        self.assertIn("not the managed service", out.getvalue())
        self.assertIn("box-01", out.getvalue())

    def test_direct_is_never_implied(self):
        """The word has to be on the command line for the session to be built."""
        code, _out, err = _run(["ask", "--direct-ish", "what is my hostname?"])
        self.assertEqual(code, 2, err)

    def test_the_usage_text_states_what_ask_does_without_the_service(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.print_usage()
        text = out.getvalue()
        self.assertIn("--direct", text)
        self.assertIn("exits non-zero", text)


if __name__ == "__main__":
    unittest.main()
