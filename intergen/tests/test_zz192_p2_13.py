# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A start that was REFUSED must not be reported as a start in progress.

The single-instance guard is fail-closed by design: when `_claim_bus_name()`
cannot PROVE sole ownership of the bus name — the session bus is unreachable,
or gi/Gio is not installed — `start_service()` binds nothing and returns before
any subsystem is built, recording the reason in `_bus_verify_failed`. That is
correct, and it is not in question here.

What is in question is what the person is then told. `ask()` has one reply for
a router that does not exist:

    "InterGen is starting up, please wait."

That sentence is a promise about the future: a start is under way, and waiting
will be rewarded. After a refused start it is false in both halves — nothing is
starting, and waiting changes nothing, because this process has already decided
it will not bind a resource and the direct session ends with the command.

The route is not hypothetical and not developer-only. With the service down,
`intergen ask` prints the state and points the person at the in-process
session ("or ask without the service: intergen ask --direct ..."). On a machine
whose session bus is unreachable that session refuses to start, and the person
is told to wait for it.

Pinned here:

  * the reply to a question asked of a daemon whose start was REFUSED names the
    refusal and does not claim a start is in progress;
  * the `intergen ask --direct` route shows the person that same refusal;
  * a daemon that really is still starting keeps its own sentence — the reply
    for a refusal is a second reply, not a replacement for the first.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli
from intergen.dbus_daemon import InterGenDaemon

# The sentence pinned at intergen/dbus_daemon.py:341, and the promise it makes.
_A_START_IN_PROGRESS = "starting up"

# Any of these says the start did not happen. The wording is the daemon's to
# choose; what is pinned is that the reply says the start was refused rather
# than that one is under way.
_A_REFUSED_START = (
    "could not start",
    "did not start",
    "was not started",
    "cannot start",
    "failed to start",
    "refused",
    "will not start",
    "is not starting",
    "unable to start",
)


def _a_daemon_whose_start_was_refused() -> InterGenDaemon:
    """A daemon that ran the real `start_service()` and was refused by the
    fail-closed single-instance guard.

    Only the guard itself is replaced, and it is replaced with exactly what the
    real one does when it cannot verify ownership (see `_claim_bus_name`: set
    `_bus_verify_failed`, return False). Everything `start_service()` does
    before and after that call is the shipped code.
    """
    daemon = InterGenDaemon()

    def _cannot_verify_sole_ownership() -> bool:
        daemon._bus_verify_failed = True
        return False

    daemon._claim_bus_name = _cannot_verify_sole_ownership
    daemon.start_service()
    return daemon


class ARefusedStartIsNotAStartInProgress(unittest.TestCase):

    def test_a_daemon_that_refused_to_start_does_not_say_it_is_starting_up(self):
        daemon = _a_daemon_whose_start_was_refused()

        # The state under test, read back before anything is claimed about it:
        # the guard refused, the reason was recorded, and nothing was built.
        self.assertTrue(daemon._duplicate_instance,
                        "the guard did not refuse — the case is not set up")
        self.assertTrue(daemon._bus_verify_failed,
                        "the refusal reason was not recorded")
        self.assertIsNone(daemon._router,
                          "a router was built despite the refused start")

        reply = json.loads(daemon.ask("is my disk encrypted?"))
        response = (reply.get("response") or "").lower()

        # The turn produced no answer, and says so. True today.
        self.assertFalse(reply.get("handled", True),
                         f"a refused start reported a handled turn: {reply!r}")

        # The turn must not promise a start that was refused.
        self.assertNotIn(
            _A_START_IN_PROGRESS, response,
            "the daemon refused to start (the bus name could not be verified) "
            "and told the person to wait for a start that will never happen: "
            f"{reply.get('response')!r}")

        # And it must say what actually happened.
        self.assertTrue(
            any(phrase in response for phrase in _A_REFUSED_START),
            "the reply to a refused start names neither the refusal nor its "
            f"reason: {reply.get('response')!r}")


class TheRealGuardsOwnFaultBranchReachesTheSameSentence(unittest.TestCase):
    """The same reply, with the shipped guard running — nothing stubbed.

    The cell above replaces `_claim_bus_name` with what the real one does. This
    cell replaces nothing: it makes `import gi` fail, which is one of the two
    environment faults the guard exists to catch (a Python without PyGObject),
    and lets the shipped `_claim_bus_name` take its own fail-closed branch. No
    bus is contacted on this path — the guard never gets that far.
    """

    def test_a_python_without_gi_refuses_the_start_and_still_says_starting_up(self):
        daemon = InterGenDaemon()
        with mock.patch.dict(sys.modules, {"gi": None}):
            daemon.start_service()

        # The shipped guard refused, on its own, for a real reason.
        self.assertTrue(daemon._duplicate_instance,
                        "the shipped guard did not refuse — the case is not "
                        "set up")
        self.assertTrue(daemon._bus_verify_failed,
                        "the shipped guard recorded no verify fault")
        self.assertIsNone(daemon._router)

        reply = json.loads(daemon.ask("is my disk encrypted?"))
        response = (reply.get("response") or "").lower()
        self.assertFalse(reply.get("handled", True))
        self.assertNotIn(
            _A_START_IN_PROGRESS, response,
            "the shipped single-instance guard refused the start (gi/Gio "
            "unavailable) and the reply still promises a start in progress: "
            f"{reply.get('response')!r}")


class TheDirectRouteShowsThePersonTheRefusal(unittest.TestCase):

    def test_ask_direct_does_not_tell_the_person_to_wait_for_a_refused_start(self):
        daemon = _a_daemon_whose_start_was_refused()

        with TemporaryDirectory() as d:
            out, err = io.StringIO(), io.StringIO()
            code = None
            with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
                    mock.patch("intergen.dbus_daemon.InterGenDaemon",
                               return_value=daemon), \
                    mock.patch.object(cli, "_last_answer_path",
                                      return_value=Path(d) / "last-answer.json"):
                with redirect_stdout(out), redirect_stderr(err):
                    try:
                        cli.cmd_ask("is my disk encrypted?", direct=True)
                    except SystemExit as exc:
                        code = exc.code
            printed = out.getvalue() + err.getvalue()

        # The question was not answered, and the exit code says so. True today.
        self.assertEqual(code, 2, printed)

        self.assertNotIn(
            _A_START_IN_PROGRESS, printed.lower(),
            "`intergen ask --direct` announced an in-process session, was "
            "refused by the single-instance guard, and then told the person "
            f"to wait for it to start:\n{printed}")


class ADaemonThatIsReallyStartingKeepsItsOwnSentence(unittest.TestCase):

    def test_a_start_still_under_way_may_still_say_so(self):
        """The refusal reply is a SECOND reply, not a replacement.

        A daemon partway through `start_service()` has no router either, and
        for it the sentence is true. Nothing here asks for that to change.
        """
        daemon = InterGenDaemon()
        self.assertIsNone(daemon._router)
        self.assertFalse(daemon._duplicate_instance)
        self.assertFalse(daemon._bus_verify_failed)

        reply = json.loads(daemon.ask("is my disk encrypted?"))
        self.assertFalse(reply.get("handled", True))
        self.assertIn(_A_START_IN_PROGRESS,
                      (reply.get("response") or "").lower())


if __name__ == "__main__":
    unittest.main()
