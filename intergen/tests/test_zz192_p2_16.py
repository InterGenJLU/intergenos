# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An internal failure must not put its own exception text in the reply.

THE PROMISE THIS PINS is the one the D-Bus surface states about itself, in
``dbus_daemon._export_dbus``'s method-call handler:

    F5: don't leak internal details (file paths, library exception messages,
    stack snippets) to whichever local process called the bus. Full exception
    with traceback goes to the daemon log; caller sees only a sanitized
    generic error string.

That sanitiser guards only the exceptions that ESCAPE ``ask()``. ``ask()``
catches its own first and returns ``f"I encountered an error: {e}"`` as the
reply's ``response`` — a SUCCESSFUL return value, handed straight to
``invocation.return_value``, so the handler's ``except`` never sees it and the
library's raw text reaches the caller intact.

WHAT THAT COSTS. The reply is the assistant's answer: ``intergen ask`` prints
``response`` to the terminal (cli._deliver_answer) and, because the text is
non-empty, caches it in ``last-answer.json`` for ``intergen last``. So an
``[Errno 2] No such file or directory: '/home/<user>/.config/...'`` raised
anywhere under ``route()`` is delivered as prose, printed, and persisted —
while the daemon log gets ``"Ask failed: %s"`` with no traceback at all. Both
halves of the F5 sentence are unmet on this path, in opposite directions.

REACHABILITY IS NOT HYPOTHETICAL. ``ConversationRouter.route`` carries no
catch-all of its own, and the daemon's own comment above the history write
records this catch-all firing under measurement ("a failure in this bookkeeping
would have replaced a good answer with 'I encountered an error' — measured
while writing this").

The pin drives the SHIPPED ``InterGenDaemon.ask`` with a router that fails the
way a library fails, and reads the reply a local caller would receive. Daemon-
free: no bus, no model, no service — the same construction
``test_dbus_ask_trace_id.py`` uses.
"""

from __future__ import annotations

import json
import unittest

from intergen.dbus_daemon import InterGenDaemon

# A path shaped like the private state the assistant keeps in a user's home.
# The name is unique to this file so a match can only have come from here.
_SECRET_PATH = "/home/zz192p216user/.config/intergen/secret.json"
_EXCEPTION_TEXT = f"[Errno 2] No such file or directory: '{_SECRET_PATH}'"


class _RouterThatFailsWithAPath:
    """A router whose ``route()`` raises the way a library call does.

    Stands in for any failure reached under routing — a missing file, a
    config read, a provider client quoting its own argument — that carries an
    internal detail in its message.
    """

    def route(self, *args, **kwargs):
        raise FileNotFoundError(_EXCEPTION_TEXT)


class AnInternalFailureIsNotLeakedToTheCaller(unittest.TestCase):

    def _reply(self) -> dict:
        d = InterGenDaemon()
        d._router = _RouterThatFailsWithAPath()
        # The harness review surface: deterministic, non-interactive, never
        # set in production (dbus_daemon.ask, "Harness/dyno path").
        d._review_callback_override = lambda *a, **k: "deny"
        return json.loads(d.ask("what is in my configuration?"))

    def test_an_internal_failure_does_not_put_its_exception_text_in_the_reply(self):
        reply = self._reply()
        response = reply.get("response", "")

        self.assertNotIn(
            _SECRET_PATH, response,
            "F5: the caller was handed a filesystem path out of the daemon's "
            "own internals as the assistant's answer. `intergen ask` prints "
            "this and caches it in last-answer.json. The reply was "
            f"{response!r}")
        self.assertNotIn(
            _EXCEPTION_TEXT, response,
            "F5: the library's own exception message reached the caller "
            f"verbatim. The reply was {response!r}")
        self.assertNotIn(
            "/home/", response,
            "F5: a path under a user's home appeared in the reply text; the "
            "caller is owed a generic sentence and a pointer to the daemon "
            f"log. The reply was {response!r}")

    def test_the_failure_is_still_reported_as_a_failure(self):
        """Sanitising the text may not silence the failure.

        The reply must still say it did not handle the turn — that is what
        makes `intergen ask` exit non-zero instead of calling silence an
        answer (test_a_question_that_got_no_answer_exits_non_zero.py).
        """
        reply = self._reply()
        self.assertEqual(reply.get("source"), "error")
        self.assertFalse(reply.get("handled", True))
        self.assertTrue(
            reply.get("response", "").strip(),
            "a sanitised error must still say something, or the caller "
            "cannot tell an error from an empty reply")


if __name__ == "__main__":
    unittest.main()
