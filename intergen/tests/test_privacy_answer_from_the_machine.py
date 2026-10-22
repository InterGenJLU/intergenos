# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
""""does this assistant send my data anywhere?" answered from the machine.

The phrasing matched no privacy alias, so the question reached the model, which
answered in generalities. The privacy answer the aliases did reach was a fixed
text saying that no query, response or system data is ever sent anywhere, and
"do you run locally" said that no data leaves the machine unless cloud
escalation is configured. A web search the person asks for sends its search
words out, so both were false on every machine with web search.

The direct-answer route now answers every privacy phrasing from the machine's
own facts: the model serving the router, whether web search is registered, which
Phone-A-Friend providers are configured; and the two fixed texts no longer say
that nothing is ever sent.

Each path that leaves the machine is named with the condition it runs under,
the same ones the identity answers name: a web search the person asks for or
accepts when offered; a network check such as dig or ping, run without asking
only on a direct request in a conversation that has read nothing from outside,
otherwise asked first; the IP address lookup when asked; a Phone-A-Friend send
the person approves. Where the model is given its tools (the native dispatch
mode) it may also run a read-only tool on this machine by itself, and the answer
says so; it never says that the model starts a search or a network check by
itself without asking, nor that something goes out only when the person asks
for it. The fixed texts cannot read the mode, so they say what holds on every
lane. The cases ask through route() on both floors: the direct answer takes the
privacy phrasings first, the identity answer the run-locally ones.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tests.test_security_posture_answers import _ask, _router
from intergen.tool_registry import ToolRegistry

# Each path out with the condition it runs under.
_CONDITIONS = (
    "Something leaves this machine only while I work on something you asked",
    "A web search you ask for, or accept when I offer it, sends its search words",
    "runs without asking only on your direct request in a conversation where "
    "nothing from outside has been read; otherwise I ask you first.",
    "Your IP address is looked up at a public DNS resolver when you ask for it",
)
# A path stated without its condition, or a denial of one.
_FORBIDDEN = (
    "only when you ask",
    "on its own",
    "may also start a read-only tool by itself",
    "without asking you first",
    "What you type here stays on this machine",
    "a web search it starts",
    "a network check it runs",
)
_MODEL_RUNS_LOCAL_TOOLS = (
    "Here the model may also run read-only tools on this machine by itself "
    "while it answers you; nothing that leaves this machine runs without your "
    "word.")
# The privacy phrasings the direct answer takes first, and the run-locally ones
# the identity answer takes.
_PRIVACY_ASKS = ("what about privacy?", "Is my data sent anywhere?",
                 "is my data private?", "where does my data go",
                 "do you send my data", "is my data sent", "data stays local",
                 "are you private?")
_RUN_LOCALLY_ASKS = ("do you run locally?", "are you local?", "where do you run?")


def _routed(*, locked):
    """A real router — the tree's own tool registry, intents and matcher — on
    the floor named, with no model behind it: a model call or a tool run fails
    the case."""
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = mock.Mock(side_effect=AssertionError("unexpected tool run"))
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    llm.chat = mock.Mock(side_effect=AssertionError("unexpected model call"))
    llm.stream_with_tools = mock.Mock(
        side_effect=AssertionError("unexpected model call"))
    return ConversationRouter(tool_registry=registry, semantic_matcher=matcher,
                              llm=llm, lock_dispatch=locked)


class PrivacyAnswer(unittest.TestCase):

    def test_the_privacy_alias(self):
        res = _ask(_router(), "does this assistant send my data anywhere?")
        self.assertIsNotNone(res, "the privacy alias was not answered")
        self.assertEqual(res.source, "direct_answer")
        self.assertIn("TestModel-2B", res.text)
        self.assertIn("no telemetry", res.text)
        self.assertIn("web search", res.text)
        self.assertIn("No Phone-A-Friend provider is set up", res.text)

    def test_the_answer_names_the_machine_facts(self):
        text = _router()._privacy_answer()
        self.assertIn("TestModel-2B", text)
        self.assertIn("no telemetry", text)
        self.assertIn("web search", text)
        self.assertIn("No Phone-A-Friend provider is set up", text)

    def test_a_configured_provider_is_named(self):
        text = _router(providers=["example-cloud"])._privacy_answer()
        self.assertIn("example-cloud", text)
        self.assertIn("only after you approve", text)
        self.assertNotIn("No Phone-A-Friend provider is set up", text)

    def test_without_web_search_none_is_promised(self):
        text = _router(web=False)._privacy_answer()
        self.assertNotIn("web search sends", text)

    def test_an_unknown_model_is_not_named(self):
        text = _router(model=None)._privacy_answer()
        self.assertIn("the model that answers you runs here", text)

    def test_every_privacy_alias_reaches_it(self):
        for text in ("does this assistant send my data anywhere?",
                     "do you send my data", "is my data sent anywhere",
                     "is my data private", "where does my data go",
                     "what about privacy", "does intergen have telemetry?"):
            with self.subTest(text=text):
                res = _ask(_router(), text)
                self.assertIsNotNone(res)
                self.assertIn("no telemetry", res.text)

    def test_an_instruction_to_send_is_not_answered_with_the_privacy_facts(self):
        self.assertIsNone(_ask(_router(), "can you send my data to my email"))

    def test_the_identity_texts_no_longer_say_nothing_is_ever_sent(self):
        for key in ("what about privacy", "do you run locally"):
            with self.subTest(key=key):
                text = ConversationRouter._try_self_awareness(key)
                self.assertNotIn("no queries", text.lower())
                self.assertNotIn("unless you explicitly configure", text.lower())
                self.assertIn("web search", text)

    def test_where_the_model_is_kept_from_dispatch_only_a_request_sends(self):
        r = _router()
        r._lock_dispatch = True
        text = r._privacy_answer()
        self.assertEqual([c for c in _CONDITIONS if c not in text], [])
        self.assertEqual([p for p in _FORBIDDEN if p in text], [])
        self.assertNotIn("by itself", text)

    def test_where_the_model_runs_its_own_tools_the_answer_says_so(self):
        r = _router()
        r._lock_dispatch = False
        text = r._privacy_answer()
        self.assertEqual([c for c in _CONDITIONS if c not in text], [])
        self.assertEqual([p for p in _FORBIDDEN if p in text], [])
        self.assertIn(_MODEL_RUNS_LOCAL_TOOLS, text)
        self.assertIn("such as dig or ping", text)
        self.assertIn("TestModel-2B", text)
        self.assertIn("no telemetry", text)
        self.assertIn("No Phone-A-Friend provider is set up", text)

    def test_the_privacy_alias_reads_the_dispatch_mode(self):
        r = _router()
        r._lock_dispatch = False
        res = _ask(r, "does this assistant send my data anywhere?")
        self.assertEqual(res.source, "direct_answer")
        self.assertIn(_MODEL_RUNS_LOCAL_TOOLS, res.text)

    def test_without_web_search_the_model_is_not_said_to_search(self):
        r = _router(web=False)
        r._lock_dispatch = False
        text = r._privacy_answer()
        self.assertNotIn("web search", text)
        self.assertIn("such as dig or ping", text)

    def test_the_identity_texts_hold_on_every_lane(self):
        for key in ("what about privacy", "do you run locally"):
            with self.subTest(key=key):
                text = ConversationRouter._try_self_awareness(key)
                self.assertNotIn("only when you ask", text)
                self.assertNotIn("on its own", text)
                self.assertIn("while I work on something you asked", text)


class PrivacyThroughRoute(unittest.TestCase):
    """The phrasings asked through route() on both floors."""

    def test_the_privacy_phrasings_name_every_condition_on_both_floors(self):
        for locked in (True, False):
            for ask in _PRIVACY_ASKS:
                with self.subTest(locked=locked, ask=ask):
                    res = _routed(locked=locked).route(ask)
                    self.assertEqual(res.source, "direct_answer")
                    self.assertEqual(
                        [c for c in _CONDITIONS if c not in res.text], [])
                    self.assertEqual(
                        [p for p in _FORBIDDEN if p in res.text], [])
                    if locked:
                        self.assertNotIn("by itself", res.text)
                    else:
                        self.assertIn(_MODEL_RUNS_LOCAL_TOOLS, res.text)

    def test_the_run_locally_phrasings_get_the_identity_answer_on_both_floors(self):
        for locked in (True, False):
            for ask in _RUN_LOCALLY_ASKS:
                with self.subTest(locked=locked, ask=ask):
                    res = _routed(locked=locked).route(ask)
                    self.assertEqual(res.source, "identity")
                    self.assertNotIn("only when you ask", res.text)
                    self.assertNotIn("on its own", res.text)
                    self.assertIn("while I work on something you asked", res.text)


if __name__ == "__main__":
    unittest.main()
