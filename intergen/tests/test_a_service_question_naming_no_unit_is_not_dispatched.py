# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A service question that names no unit is not dispatched.

THE DEFECT, read on the tree in intergen/router.py and
intergen/tools/manage_services.py. The service argument builder ends with a
status read of whatever service name it can find in the sentence, and for a
question that names none ("Is my firewall on?" names no unit: the firewall is
not a unit's name) that read names no unit at all. On the embedding path the
semantic service route dispatched it, and the service tool, which reads nothing
without a name, answered the person's question with its own refusal ("'status'
requires a service name"). The model's own tool turn could send the same read,
and it passed every check before the dispatch, because only a state-changing
action was asked about.

THE RULE these cells hold the code to: the builder builds no status read that
names no unit, records why in words, and the clause goes on to the model's turn
as an ordinary request; and the service tool refuses, in words and before the
dispatcher gate, every action that acts on one unit when the call names none,
for the keyword, semantic and model routes alike.

THE CONTROLS beside the cells are green at both ends: a status question that
names a service is built as it was, the indirect clause's service is still
found, "what services are running" is still a listing, and a listing, which
names no unit by design, still runs.

The router is built the way the daemon builds it with no embedding server; the
semantic match is stood in where a cell needs one. The tool registry's dispatch
is stood in and recorded where a cell reads the builder, and kept real where a
cell reads the tool's refusal; there `subprocess.run` is stood in, records the
command and runs nothing.
"""
from __future__ import annotations

import os
import subprocess
import types
import unittest
from unittest import mock

from intergen import howto
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import HardwareTierLevel, ToolCall, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

# Questions that name no unit, one for each place the builder makes a status
# read: the fallback scan, the "status" word with nothing after it, and the
# fallback again with a state word the "is X running" reader does not read.
NAMING_NO_UNIT = ("Is my firewall on?", "what is the status", "Is the firewall enabled?",
                  "check the status?")
# The actions that read one unit: every read but the four listings.
READS_ONE_UNIT = ("status", "is-active", "is-enabled", "is-failed", "show", "cat",
                  "list-dependencies")


class _Resp:
    """A completion carrying only what the synthesis path reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


class _Case(unittest.TestCase):

    def _router(self) -> ConversationRouter:
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        self.dispatched: list[tuple[str, dict]] = []

        def _execute(call, *a, **kw):
            self.dispatched.append((call.name, dict(call.arguments or {})))
            return ToolResult(call_id=call.call_id, name=call.name,
                              content="active", success=True)

        reg.execute = _execute
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(howto._REPO_DIR)}):
            r = ConversationRouter(
                tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
                lock_dispatch=False, hardware_tier=HardwareTierLevel.TIER_2)
        self.asked: list[str] = []
        r._llm.chat = lambda messages, **kw: self.asked.append("chat") or _Resp("(a model's answer)")
        return r


# ── the cells: red before the correction ────────────────────────────────────

class TheBuilderBuildsNoStatusReadNamingNoUnit(_Case):

    def test_each_place_the_builder_makes_a_status_read(self) -> None:
        r = self._router()
        for question in NAMING_NO_UNIT:
            with self.subTest(question=question):
                self.assertIsNone(r._extract_arguments("manage_services", question))

    def test_the_reason_is_recorded_in_words(self) -> None:
        r = self._router()
        emitted: list[tuple] = []
        with mock.patch("intergen.router.glass.emit",
                        lambda *a, **kw: emitted.append((a, kw))):
            r._extract_arguments("manage_services", "Is my firewall on?")
        self.assertEqual(getattr(r, "_last_dispatch_decline_reason", None), "service_not_named")
        details = [a[2] if len(a) > 2 else kw.get("detail") for a, kw in emitted
                   if a[:2] == ("decision", "dispatch_declined")]
        self.assertEqual([(d["reason"], d["action"], d["why"]) for d in details],
                         [("service_not_named", "status", "no service was named")])


class TheSemanticRouteDispatchesNothingAndTheTurnGoesOn(_Case):

    def test_is_my_firewall_on(self) -> None:
        r = self._router()
        match = types.SimpleNamespace(intent_id="manage_services",
                                      tool_name="manage_services", score=0.9)
        with mock.patch.object(r._semantic, "_match_embeddings", return_value=match):
            result = r._try_semantic_match("Is my firewall on?")
        self.assertEqual(self.dispatched, [])
        self.assertFalse(result.handled, "the semantic route answered: %r" % (result.text,))


class _ToolCase(unittest.TestCase):
    """The registry's own execute; the service manager stood in by subprocess.run."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.ran: list[str] = []

        def _run(args, *a, **kw):
            self.ran.append(" ".join(map(str, args)) if not isinstance(args, str) else args)
            return subprocess.CompletedProcess(args, 0, "active\n", "")

        patcher = mock.patch("subprocess.run", _run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dispatch(self, arguments: dict) -> ToolResult:
        self.cards: list[object] = []
        call = ToolCall(name="manage_services", arguments=dict(arguments),
                        source_of_request=Provenance.USER_DIRECT)
        return self.registry.execute(
            call, review_callback=lambda c, d: self.cards.append(d) or "deny")


class TheToolRefusesAReadNamingNoUnitBeforeTheGate(_ToolCase):

    def test_every_read_of_one_unit_with_no_name(self) -> None:
        for action in READS_ONE_UNIT:
            for arguments in ({"action": action}, {"action": action, "service": ""},
                              {"action": action, "service": "  "},
                              {"action": action, "service": None}):
                with self.subTest(arguments=arguments):
                    result = self._dispatch(arguments)
                    self.assertFalse(result.success)
                    self.assertTrue(result.content.startswith("Validation error:"),
                                    result.content)
                    self.assertIn("'%s' asks about a service and names none" % action,
                                  result.content)
                    self.assertEqual((self.cards, self.ran), ([], []))

    def test_a_name_that_is_not_text(self) -> None:
        result = self._dispatch({"action": "status", "service": 7})
        self.assertTrue(result.content.startswith("Validation error:"), result.content)
        self.assertIn("'status' needs a service name, not 7", result.content)
        self.assertEqual(self.ran, [])


# ── the controls: green at both ends ────────────────────────────────────────

class AServiceQuestionThatNamesOneIsBuiltAsBefore(_Case):

    def test_the_status_reads_that_name_a_service(self) -> None:
        r = self._router()
        for question, service in (("is sshd running", "sshd"),
                                  ("I can't connect via SSH, is the service even on?", "ssh"),
                                  ("is nginx active", "nginx")):
            with self.subTest(question=question):
                self.assertEqual(r._extract_arguments("manage_services", question),
                                 {"action": "status", "service": service})

    def test_what_services_are_running_is_a_listing(self) -> None:
        r = self._router()
        self.assertEqual(r._extract_arguments("manage_services", "what services are running"),
                         {"action": "list", "service": ""})


class AReadThatNamesAUnitOrNoneByDesignStillRuns(_ToolCase):

    def test_status_of_a_named_unit(self) -> None:
        result = self._dispatch({"action": "status", "service": "sshd"})
        self.assertTrue(result.success)
        self.assertEqual(self.ran, ["systemctl status sshd"])

    def test_a_listing_names_no_unit_and_runs(self) -> None:
        result = self._dispatch({"action": "list-units"})
        self.assertTrue(result.success)
        self.assertEqual(self.ran, ["systemctl list-units"])


if __name__ == "__main__":
    unittest.main()
