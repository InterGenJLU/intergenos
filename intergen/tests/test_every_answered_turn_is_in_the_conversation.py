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
from intergen.router import ConversationRouter, RouteResult
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


class TheToolPathAnswerThatUsedNoTool(unittest.TestCase):
    """The member of the class that was measured in the field.

    A turn routed to the tool path where the MODEL ANSWERS IN TEXT without
    calling a tool returns from its own exit in ``_try_llm_tools`` — "the model
    answered in text without calling a tool" — and that exit is one of the
    nine that record nothing. The branch beside it, the one where a tool did
    run, records the turn.

    Measured in the field on another machine (CS-4, 2026-09-20): three
    consecutive turns, every one of them ``route/decided source=llm_tools``,
    every one of them ``prompt/assembled history_msgs=0``, and no
    ``decision/history_write`` row for any of them. The one turn that did
    record was answered by code, not by the model. The turn after that
    assembled with a history holding only the code-answered exchange and told
    the person "You didn't ask me anything yet".

    The model is stubbed to answer in text, which is what makes this the
    tool-path-without-a-tool case rather than a tool dispatch.
    """

    def _daemon_answering_in_text(self, text):
        d = _DaemonStandIn.__new__(_DaemonStandIn)
        _DaemonStandIn.__init__(d)
        # Tools are offered (the lock is off), and the model answers in words.
        d._router = ConversationRouter(
            tool_registry=_REG,
            semantic_matcher=SemanticMatcher(embedder=None),
            llm=LLMRouter(config=None),
            lock_dispatch=False)
        d._router._llm.stream_with_tools = (
            lambda messages, tools=None, **kw: iter([text]))
        return d

    def test_a_tool_path_turn_answered_in_words_is_still_remembered(self):
        d = self._daemon_answering_in_text("Jupiter is larger than Saturn.")
        answer = d.say("Is Jupiter or Saturn larger?")
        self.assertIn("jupiter", answer.lower(),
                      "the stubbed model's answer did not reach the person")
        pairs = [(m.role.value, m.content) for m in d._conversation.history]
        self.assertIn(("user", "Is Jupiter or Saturn larger?"), pairs,
                      "a tool-path turn answered in words was not recorded, so "
                      f"the next turn cannot see it: {pairs}")

    def test_the_next_turn_is_built_on_it(self):
        """What the person experiences: the follow-up has something to refer
        to."""
        d = self._daemon_answering_in_text("Jupiter is larger than Saturn.")
        d.say("Is Jupiter or Saturn larger?")
        seen = {}
        real = d._router._llm.stream_with_tools

        def capture(messages, tools=None, **kw):
            seen["messages"] = list(messages)
            return iter(["Jupiter."])

        d._router._llm.stream_with_tools = capture
        d.say("Which of those two did you just say?")
        contents = [getattr(m, "content", "") for m in seen.get("messages", [])]
        self.assertTrue(
            any("Is Jupiter or Saturn larger?" in c for c in contents),
            "the second turn was assembled without the first one in it; the "
            f"model was handed {contents}")


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


class AFollowUpIsNotAFreshQuestion(unittest.TestCase):
    """The fast routes cannot see the conversation, so they stand aside for a
    message that carries its subject from an earlier turn.

    Measured in the field on a second machine (CS-4, 2026-09-20): turn one was
    answered by the explain route in 19 milliseconds with no model call, turn
    two by the keyword route in 14 milliseconds — it ran ``free -h`` — and
    neither route could know it was continuing anything. The person was having
    a conversation; the assistant was answering two unrelated sentences.
    """

    def test_the_first_message_is_never_treated_as_a_follow_up(self):
        """With nothing behind it, a message is a first message whatever its
        wording — the fast routes must keep every one of those turns."""
        d = _DaemonStandIn()
        self.assertEqual(len(d._conversation.history), 0)
        answer = d.say("what about it?")
        self.assertTrue(answer.strip(), "a first message got no answer at all")

    def test_a_carried_subject_does_not_reach_the_fast_routes(self):
        """The four routes that read only this message are not consulted.

        Patched with ``mock.patch.object``, which restores them however the
        test ends — a hand-rolled spy that fails to unwind would leave the
        router patched for every test that runs after it.
        """
        from unittest import mock
        from intergen.router import ConversationRouter as CR
        d = _DaemonStandIn()
        d.say("thanks")                      # now there IS an earlier turn
        reached = []
        with mock.patch.object(CR, "_try_explain", autospec=True,
                               side_effect=lambda self, *a, **k: (
                                   reached.append("explain"), (None, False))[1]), \
             mock.patch.object(CR, "_try_keyword_match", autospec=True,
                               side_effect=lambda self, *a, **k: (
                                   reached.append("keyword"),
                                   RouteResult(handled=False))[1]), \
             mock.patch.object(CR, "_try_semantic_match", autospec=True,
                               side_effect=lambda self, *a, **k: (
                                   reached.append("semantic"),
                                   RouteResult(handled=False))[1]), \
             mock.patch.object(CR, "_try_state_cache", autospec=True,
                               side_effect=lambda self, *a, **k: (
                                   reached.append("state_cache"), None)[1]):
            d.say("Which of those did you just say?")
        self.assertEqual(
            reached, [],
            "a message that refers to an earlier turn was answered by a route "
            f"that cannot see one: {reached}")

    def test_a_fresh_question_still_reaches_them(self):
        """The guard must not cost the fast routes their own work."""
        from intergen.router import depends_on_an_earlier_turn as carries
        for fresh in ("how much memory do I have?",
                      "explain what a kernel is",
                      "is /etc/fstab readable?",
                      "what is the current time?",
                      "install vim"):
            with self.subTest(message=fresh):
                self.assertFalse(carries(fresh),
                                 "a question that names its own subject was "
                                 "taken for a follow-up")

    def test_the_shapes_that_do_carry_their_subject(self):
        from intergen.router import depends_on_an_earlier_turn as carries
        for follow_up in ("Which of those two did you just say is larger?",
                          "what about it?",
                          "are those installed?",
                          "What was the first thing I asked you?",
                          "you said it was fine — was it?"):
            with self.subTest(message=follow_up):
                self.assertTrue(carries(follow_up),
                                "a message that only makes sense on top of an "
                                "earlier turn was taken for a fresh question")


if __name__ == "__main__":
    unittest.main()
