# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn the assistant answered is a turn it can be asked about.

WHAT WAS MEASURED, 2026-09-20, on the installed release with the real router
and no model: two answers that the router itself marks as delivered —

    "thanks"  -> "Happy to help. I'm right here if anything else comes up."
                 (source gratitude_closure, handled=True)
    "yes"     -> "I don't have anything staged to confirm right now — what
                 would you like me to do?"  (source affirmative_no_offer,
                 handled=True)

— left the conversation EMPTY. The exchange the person just had was not in
the buffer the next turn is assembled from, and not in the verbatim transcript
an ordinal question reads, so the assistant could not say what had just been
said to it.

WHY IT HAPPENS, and why it is a class rather than one bug: the write into the
conversation is made by each answering path for itself. Nine of the router's
functions build a delivered answer and never call ``_append_history``; twenty
do call it. The browser server does not depend on that at all — it writes the
delivered answer ONCE, at its own delivery boundary, and the idempotency guard
at the top of ``_append_history`` makes that blanket write safe next to the
paths that already wrote. The D-Bus daemon, which is what ``intergen ask`` and
every desktop surface speak to, had no such writer: it depended entirely on
each path remembering. A path that forgets loses the turn on the command line
and keeps it on the web, which is exactly the shape of a defect that appears on
one machine and not on another.

WHAT IS PINNED HERE: the ANSWER a person gets, not a counter. After a turn the
assistant answered, asking it what was just said produces that thing. The tests
drive the SHIPPED ``InterGenDaemon.ask`` method, taken unbound, so they cannot
pass against a re-implementation of it.
"""

from __future__ import annotations

import json
import unittest

from intergen.conversation_state import new_conversation_state
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

_REG = ToolRegistry()
_REG.discover_tools()


class _DaemonStandIn:
    """A daemon holding a real router and a real conversation.

    ``ask`` is the SHIPPED method, taken unbound from InterGenDaemon, so what
    these tests exercise is the delivery path the desktop actually speaks to.
    """

    from intergen.dbus_daemon import InterGenDaemon as _Real
    ask = _Real.ask

    def __init__(self) -> None:
        self._requests_handled = 0
        self._paused = False
        self._last_error = None
        self._metrics = None
        self._review_callback_override = lambda *a, **k: "deny"
        self._conversation = new_conversation_state()
        self._router = ConversationRouter(
            tool_registry=_REG,
            semantic_matcher=SemanticMatcher(embedder=None),
            llm=LLMRouter(config=None),
            lock_dispatch=True)

    # The daemon starts a bounded wiki-index pass after a turn. It is not part
    # of delivery and needs no daemon here.
    def _resume_wiki_embedding_after_turn(self) -> None:
        return None

    def _held_game_names(self):
        return []

    def say(self, message: str) -> str:
        """One turn, as the command line makes it: ask, read the answer."""
        return json.loads(self.ask(message))["response"]


class ATurnTheAssistantAnsweredIsATurnItCanBeAskedAbout(unittest.TestCase):

    def test_it_can_say_what_was_said_to_it_first(self):
        d = _DaemonStandIn()
        first = d.say("thanks")
        self.assertTrue(first.strip(), "the first turn produced no answer at all")
        answer = d.say("What was the first thing I asked you?")
        self.assertIn(
            "thanks", answer.lower(),
            "the assistant answered a turn and then could not say what that "
            f"turn was; it replied {answer!r}")

    def test_a_second_code_owned_path_is_remembered_the_same_way(self):
        """The same promise on a different answering path.

        One path remembering proves nothing about the next one: the defect
        being pinned is that each path decides for itself.
        """
        d = _DaemonStandIn()
        d.say("yes")
        answer = d.say("What was the first thing I asked you?")
        self.assertIn(
            "yes", answer.lower(),
            "an answered turn on a second path was lost as well; the "
            f"assistant replied {answer!r}")

    def test_the_conversation_holds_the_exchange_the_person_had(self):
        """The buffer the NEXT turn is assembled from carries the exchange.

        Stated as the pair that was exchanged, not as a length: what matters is
        that the person's words and the answer they were given are both there.
        """
        d = _DaemonStandIn()
        given = d.say("thanks")
        pairs = [(m.role.value, m.content) for m in d._conversation.history]
        self.assertIn(("user", "thanks"), pairs,
                      f"the person's turn is not in the conversation: {pairs}")
        self.assertIn(("assistant", given), pairs,
                      f"the answer they were given is not in it either: {pairs}")


class RecordingTheTurnCannotCostThePersonTheirAnswer(unittest.TestCase):
    """The bookkeeping is not allowed to eat the answer, and not allowed to
    disappear either.

    The whole turn runs inside one catch-all in ``ask``. Measured while this
    change was being written: against a router with no ``_append_history`` at
    all, the new write raised, the catch-all caught it, and a delivered answer
    came back as "I encountered an error" — a worse outcome than the lost turn
    it was added to prevent. So the write reports its own failure and the
    answer still goes out.
    """

    class _RouterThatCannotRecord:
        """A router that answers but does not implement the history write."""

        def route(self, message, **kw):
            from intergen.router import RouteResult
            return RouteResult(text="the answer the person asked for",
                               source="stub", handled=True)

    def _daemon(self):
        d = _DaemonStandIn.__new__(_DaemonStandIn)
        _DaemonStandIn.__init__(d)
        d._router = self._RouterThatCannotRecord()
        return d

    def test_the_answer_still_reaches_the_person(self):
        d = self._daemon()
        payload = json.loads(d.ask("anything"))
        self.assertEqual(payload["source"], "stub",
                         "a failure to record the turn replaced the answer")
        self.assertEqual(payload["response"], "the answer the person asked for")

    def test_the_failure_to_record_is_reported_not_swallowed(self):
        """Silence here would be the worst of both: the turn is gone AND
        nothing says so."""
        from unittest import mock
        from intergen import dbus_daemon as dd
        d = self._daemon()
        rows = []
        with mock.patch.object(dd.glass, "emit",
                               side_effect=lambda *a, **k: rows.append((a, k))):
            with self.assertLogs("intergen.dbus_daemon", level="ERROR") as logged:
                d.ask("anything")
        self.assertTrue(
            any(a[:2] == ("delivery", "history_write_failed") for a, k in rows),
            f"no row says the turn was not recorded: {[a for a, _ in rows]}")
        self.assertTrue(
            any("NOT recorded" in line for line in logged.output),
            f"nothing in the journal names it: {logged.output}")


if __name__ == "__main__":
    unittest.main()
