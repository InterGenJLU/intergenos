# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`ask --direct` gives back every subsystem the session it announced took.

The in-process answer prints, in its own words, "this session is not the
managed service and ends with this command" (intergen/cli.py:686) and then
calls InterGenDaemon().start_service(), which claims the bus name, launches the
chat and embedding engines, binds 127.0.0.1:8089, and starts the two watchdog
threads and the warm-up thread. The command never calls stop_service() — the
method the same class documents as "Graceful shutdown — stop all subsystems in
reverse order", and the only path that reaches LlamaManager.stop() and through
it _release_serving_card, which puts the serving card's runtime power/control
back to the value recorded in memory before it was pinned to "on".

The daemon's own entry point pairs the two: intergen/dbus_daemon.py calls
`daemon.start_service()`, then `daemon.stop_service()` in a finally, and
stop_service() again on each of its early-exit error paths. The command line's
in-process route is the one caller of start_service() that takes the subsystems
and does not give them back — and it is the caller that tells the person the
session ends when the command does.

What is pinned here:

  * the in-process route stops the session it started, in order, before it
    returns;
  * it stops it on the reply that answers nothing too, where the command exits
    non-zero — so the teardown has to be a finally, not a trailing call.

NOT PINNED HERE: what outlives the process. The engines are separate processes
and llama_manager asks the kernel for PR_SET_PDEATHSIG SIGKILL on each child,
so an engine should not outlive the command on Linux; the serving card's sysfs
power/control is the residue only stop() puts back, and showing that needs
intergen/llama_manager.py and a real card, which is outside this case.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from intergen import cli


class _RecordingSession:
    """Stands in for InterGenDaemon and records the lifecycle calls it is sent.

    Nothing is constructed, no port is bound and no engine is launched: the
    point of the recorder is the ORDER of the calls the command makes on the
    session object it built.
    """

    def __init__(self, calls: list[str], reply: str) -> None:
        self._calls = calls
        self._reply = reply
        calls.append("built")

    def start_service(self) -> None:
        self._calls.append("start_service")

    def ask(self, message: str) -> str:
        self._calls.append("ask")
        return self._reply

    def stop_service(self) -> None:
        self._calls.append("stop_service")


def _ask_direct(reply: str):
    """Run the in-process question route with nothing owning the bus name."""
    import intergen.dbus_daemon as daemon_mod

    calls: list[str] = []
    out, err = io.StringIO(), io.StringIO()
    code = None
    with tempfile.TemporaryDirectory(prefix="igos-direct-lifecycle-") as tmp:
        cached = Path(tmp) / "last-answer.json"
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
                mock.patch.object(daemon_mod, "InterGenDaemon",
                                  lambda: _RecordingSession(calls, reply)), \
                mock.patch.object(cli, "_last_answer_path",
                                  return_value=cached):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_ask("what is my hostname?", direct=True)
                except SystemExit as exc:
                    code = exc.code
    return calls, code, out.getvalue(), err.getvalue()


class TheDirectSessionEndsWithTheCommandTests(unittest.TestCase):

    def test_the_direct_route_stops_what_it_started(self):
        calls, code, out, err = _ask_direct(
            '{"response": "box-01", "handled": true}')
        # The sentence this case holds the route to, printed by the route.
        self.assertIn("ends with this command", out)
        self.assertEqual(calls[:3], ["built", "start_service", "ask"], calls)
        self.assertIn(
            "stop_service", calls,
            "the in-process route built a session, started every subsystem in "
            "it and never stopped it, while telling the person the session "
            f"ends with this command; the calls made were {calls}")
        self.assertEqual(
            calls[-1], "stop_service",
            f"the session was stopped, but not last; the calls were {calls}")
        self.assertIsNone(code, err)

    def test_it_stops_the_session_on_the_reply_that_answers_nothing(self):
        calls, code, _out, err = _ask_direct(
            '{"response": "", "handled": false}')
        self.assertEqual(code, 2, err)
        self.assertIn(
            "stop_service", calls,
            "the route exited non-zero with the session it started still "
            "standing, so the teardown cannot be a trailing call — it has to "
            f"run on the way out; the calls made were {calls}")


if __name__ == "__main__":
    unittest.main()
