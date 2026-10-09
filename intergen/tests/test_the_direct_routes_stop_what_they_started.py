# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both direct routes give back every subsystem their session took.

Both in-process routes print, in their own words, that the session is not the
managed service and ends with this command, then build InterGenDaemon(), call
start_service() (the bus name, the chat and embedding engines, the local web
bind, the watchdog threads and the warm-up thread) and put the one question to
it: ask() on the question route, escalate() on the frontier route. The
daemon's own entry point pairs start_service() with stop_service(), the
shutdown the class documents as stopping all subsystems in reverse order, in a
finally around its main loop. Each route does the same, so its release runs on
the reply that answered, on the reply that answers nothing, where the command
exits 2, and on a reply the route cannot read, which leaves by the exit code
from inside the session, where only a finally still reaches the release. A
shutdown that itself fails is reported on the error stream, naming what may
still be held, and the exit code stays the answer's.

test_zz192_p2_14.py pins the question route's release on the first two
replies. This case pins both routes on all three, and the failed shutdown.

What is pinned here, on each route and each reply:

  * the route stops the session it started, once and last;
  * a shutdown that fails is reported, naming the failure and that the
    subsystems the session started may still be holding resources, nothing
    escapes the command, and the exit code is the answer's;
  * the control: the route announces the session and records the build, the
    start and its own question call, in that order, before anything else; and
    every call the routes make on the stand-in binds on the real daemon's
    method of the same name. Without it, the cases above could be failing
    because the stand-in is never reached rather than because a route does
    not give back what it took.

Hermetic: nothing is constructed, no port is bound, no engine is launched and
nothing is sent; the stand-in records the lifecycle calls in the order the
route makes them.

NOT PINNED HERE: what outlives the process. The running engine releasing a
card, the serving card's sysfs power/control value and an engine child
outliving its parent need intergen/llama_manager.py and real hardware, which
is outside this case.
"""

from __future__ import annotations

import inspect
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from intergen import cli

_QUESTION = "what is my hostname?"


class _RecordingSession:
    """Stands in for InterGenDaemon and records the lifecycle calls it is sent.

    Nothing is constructed, no port is bound, no engine is launched and nothing
    is sent: the point of the recorder is the ORDER of the calls the command
    makes on the session object it built. Given a failure, its shutdown records
    the call and then raises it, as a release that itself fails would.
    """

    def __init__(self, calls: list[str], reply: str,
                 shutdown_failure: Exception | None) -> None:
        self._calls = calls
        self._reply = reply
        self._shutdown_failure = shutdown_failure
        calls.append("built")

    def start_service(self) -> None:
        self._calls.append("start_service")

    def ask(self, message: str) -> str:
        self._calls.append("ask")
        return self._reply

    def escalate(self, message: str) -> str:
        self._calls.append("escalate")
        return self._reply

    def stop_service(self) -> None:
        self._calls.append("stop_service")
        if self._shutdown_failure is not None:
            raise self._shutdown_failure


def _run_direct(command: str, reply: str,
                shutdown_failure: Exception | None = None):
    """Run one in-process route with nothing owning the bus name."""
    import intergen.dbus_daemon as daemon_mod

    calls: list[str] = []
    out, err = io.StringIO(), io.StringIO()
    code = escaped = None
    with tempfile.TemporaryDirectory(prefix="igos-direct-lifecycle-") as tmp:
        cached = Path(tmp) / "last-answer.json"
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
                mock.patch.object(
                    daemon_mod, "InterGenDaemon",
                    lambda: _RecordingSession(calls, reply,
                                              shutdown_failure)), \
                mock.patch.object(cli, "_last_answer_path",
                                  return_value=cached):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    getattr(cli, command)(_QUESTION, direct=True)
                except SystemExit as exc:
                    code = exc.code
                except Exception as exc:  # noqa: BLE001 -- reported
                    escaped = exc
    return calls, code, escaped, out.getvalue(), err.getvalue()


class _TheRouteOnOneReply:
    """The three readings, taken on the route and reply each subclass names."""

    command = ""                # the route, by its name in intergen.cli
    question_call = ""          # the session method the route asks
    label = ""
    reply = ""
    owned: int | None = None    # the exit code the answer owns

    def _order_before_the_release(self) -> list[str]:
        return ["built", "start_service", self.question_call]

    def _whole_order(self) -> list[str]:
        return self._order_before_the_release() + ["stop_service"]

    def test_the_route_announces_the_session_and_calls_it_in_order(self):
        """The control: the stand-in is reached, in order, before a release."""
        calls, code, escaped, out, err = _run_direct(self.command, self.reply)
        self.assertIsNone(escaped, f"{self.label}: {escaped!r}")
        # The sentence this case holds the route to, printed by the route.
        self.assertIn("ends with this command", out)
        self.assertEqual(
            calls[:3], self._order_before_the_release(),
            f"{self.label}: the route did not build, start and ask the "
            f"session it announced, in that order; the calls were {calls}")
        self.assertEqual(code, self.owned, f"{self.label}: {err}")

    def test_the_route_stops_what_it_started(self):
        calls, code, escaped, _out, err = _run_direct(self.command,
                                                      self.reply)
        self.assertIsNone(escaped, f"{self.label}: {escaped!r}")
        self.assertIn(
            "stop_service", calls,
            f"{self.label}: the route built a session, started every "
            f"subsystem in it and never stopped it, while telling the person "
            f"the session ends with this command; the calls made were {calls}")
        self.assertEqual(
            calls, self._whole_order(),
            f"{self.label}: the session was not stopped once and last; the "
            f"calls were {calls}")
        self.assertEqual(code, self.owned, f"{self.label}: {err}")

    def test_a_failed_shutdown_is_reported_and_the_answer_keeps_the_code(self):
        failure = RuntimeError("the engine manager did not stop")
        calls, code, escaped, _out, err = _run_direct(
            self.command, self.reply, shutdown_failure=failure)
        self.assertEqual(
            calls, self._whole_order(),
            f"{self.label}: the shutdown was not reached once and last; the "
            f"calls were {calls}")
        self.assertIsNone(
            escaped,
            f"{self.label}: the failed shutdown left the command as "
            f"{escaped!r} instead of being reported")
        self.assertIn("did not stop cleanly", err)
        self.assertIn("RuntimeError: the engine manager did not stop", err)
        self.assertIn(
            "subsystems it started may still be holding resources", err,
            f"{self.label}: the report does not say what may still be held; "
            f"the error stream was {err!r}")
        self.assertEqual(
            code, self.owned,
            f"{self.label}: the exit code must stay the answer's, not the "
            f"teardown's; the error stream was {err!r}")


class _TheQuestionRoute(_TheRouteOnOneReply):
    command = "cmd_ask"
    question_call = "ask"


class _TheFrontierRoute(_TheRouteOnOneReply):
    command = "cmd_ask_frontier"
    question_call = "escalate"


# A reply the route cannot read leaves it by the exit code from inside the
# session, so only a finally still reaches the release.
_CANNOT_BE_READ = "not json at all"


class TheQuestionRouteOnAnAnsweredReplyTests(_TheQuestionRoute,
                                             unittest.TestCase):
    label = "the question route, an answered reply"
    reply = json.dumps({"response": "box-01", "handled": True})
    owned = None


class TheQuestionRouteOnAReplyThatAnswersNothingTests(_TheQuestionRoute,
                                                      unittest.TestCase):
    label = "the question route, a reply that answers nothing"
    reply = json.dumps({"response": "", "handled": False})
    owned = 2


class TheQuestionRouteOnAReplyItCannotReadTests(_TheQuestionRoute,
                                                unittest.TestCase):
    label = "the question route, a reply it cannot read"
    reply = _CANNOT_BE_READ
    owned = 2


class TheFrontierRouteOnAnAnsweredSendTests(_TheFrontierRoute,
                                            unittest.TestCase):
    label = "the frontier route, an answered send"
    reply = json.dumps({"response": "box-01", "handled": True, "sent": True})
    owned = None


class TheFrontierRouteOnAReplyThatAnswersNothingTests(_TheFrontierRoute,
                                                      unittest.TestCase):
    label = "the frontier route, a reply that answers nothing"
    reply = json.dumps({"response": "", "sent": False})
    owned = 2


class TheFrontierRouteOnAReplyItCannotReadTests(_TheFrontierRoute,
                                                unittest.TestCase):
    label = "the frontier route, a reply it cannot read"
    reply = _CANNOT_BE_READ
    owned = 2


class TheStandInAnswersOnlyWhatTheRealDaemonWouldTests(unittest.TestCase):

    def test_every_call_the_routes_make_binds_on_the_real_daemon(self):
        """The control: the routes' calls would reach the real session."""
        from intergen.dbus_daemon import InterGenDaemon

        for name, args in (("start_service", ()),
                           ("ask", (_QUESTION,)),
                           ("escalate", (_QUESTION,)),
                           ("stop_service", ())):
            real = getattr(InterGenDaemon, name, None)
            self.assertTrue(callable(real),
                            f"InterGenDaemon has no method {name}")
            try:
                inspect.signature(real).bind(object(), *args)
            except TypeError as exc:
                self.fail(f"a route calls {name} with {args!r}, which the "
                          f"real daemon's method refuses: {exc}")


if __name__ == "__main__":
    unittest.main()
