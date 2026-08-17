# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The delivered answer is traceable to what composed it.

`source` on a result names the ROUTE that handled a turn. It does not say which
artifact the reply was built from, and those differ — a package dispatch can
execute while the reply is composed from disk-summary state, with route, trace
and dispatch all agreeing. Nothing recorded the composition, so that class was
only ever found by reading answers.

These fixtures pin the signal (AnswerLinkage), its presence on every delivered
row including an explicit marker when a route declares nothing, the additive
D-Bus payload that lets a consumer check a reply against its own dispatch, and
the load-guard disclosure that makes a paused polling cycle speak.
"""
import json
import time
import unittest
from unittest import mock

from intergen import state_cache as sc
from intergen.interfaces.types import AnswerLinkage, RouteResult, ToolResult
from intergen.state_cache import CachedValue, StateCache


class LinkageShapeTests(unittest.TestCase):

    def test_a_route_that_declares_nothing_is_undeclared_not_code_owned(self):
        """The distinction the whole signal rests on: 'nobody instrumented this
        path' must never render as 'this answer is deliberately code-owned'."""
        result = RouteResult(text="hi", source="llm_freeform")
        self.assertIsNone(result.answer_linkage)

    def test_as_detail_is_flat_and_complete(self):
        detail = AnswerLinkage(kind="dispatch", tool="manage_packages",
                               call_id="c1", renderer="template").as_detail()
        self.assertEqual(detail, {"kind": "dispatch", "tool": "manage_packages",
                                  "call_id": "c1", "renderer": "template"})


class RouterDeclaresItsCompositionTests(unittest.TestCase):
    """Every composing route states what it built the answer from."""

    def _router(self):
        from intergen.router import ConversationRouter
        return ConversationRouter.__new__(ConversationRouter)

    def test_the_keyword_dispatch_route_links_to_its_tool(self):
        from intergen.router import ConversationRouter
        r = self._router()
        tr = ToolResult(call_id="c7", name="manage_packages",
                        content="pdfarranger 1.11.0 available")
        match = mock.Mock(intent_id="pkg", tool_name="manage_packages")
        r._semantic = mock.Mock(_match_keywords=mock.Mock(return_value=match))
        r._execute_tool_for_intent = mock.Mock(return_value=(None, tr))
        r._append_history = mock.Mock()
        r._synthesize_tool_result = mock.Mock(return_value="No packages matching.")
        with mock.patch.object(ConversationRouter, "_template_synthesis",
                               return_value=None):
            res = ConversationRouter._try_keyword_match(r, "search for a pdf editor")
        self.assertIsNotNone(res.answer_linkage)
        self.assertEqual(res.answer_linkage.kind, "dispatch")
        self.assertEqual(res.answer_linkage.tool, "manage_packages")
        self.assertEqual(res.answer_linkage.call_id, "c7")
        self.assertEqual(res.answer_linkage.renderer, "llm_synth")

    def test_the_template_renderer_is_named_when_it_composes(self):
        from intergen.router import ConversationRouter
        r = self._router()
        tr = ToolResult(call_id="c8", name="run_command", content="box-01")
        match = mock.Mock(intent_id="host", tool_name="run_command")
        r._semantic = mock.Mock(_match_keywords=mock.Mock(return_value=match))
        r._execute_tool_for_intent = mock.Mock(return_value=(None, tr))
        r._append_history = mock.Mock()
        with mock.patch.object(ConversationRouter, "_template_synthesis",
                               return_value="Your hostname is box-01."):
            res = ConversationRouter._try_keyword_match(r, "what is my hostname")
        self.assertEqual(res.answer_linkage.renderer, "template")
        self.assertEqual(res.answer_linkage.call_id, "c8")

    def test_the_linkage_makes_the_reported_substitution_detectable(self):
        """End to end on the signal: a package dispatch whose answer was
        composed from cached disk state is caught by the invariant, using only
        what the route declared."""
        from intergen import safety
        tr = ToolResult(call_id="c9", name="manage_packages",
                        content="pdfarranger 1.11.0 available")
        problems = safety.find_unconsumed_dispatches(
            "Disk usage is available.", [tr],
            AnswerLinkage(kind="cache", renderer="template"))
        self.assertEqual([p[1] for p in problems], ["substituted"])


class LoadGuardDisclosureTests(unittest.TestCase):
    """A paused polling cycle says so instead of silently ageing out."""

    def _cache(self):
        cache = StateCache()
        cache._cache["load_average"] = CachedValue(
            value="0.1 0.2 0.3 1/500 1", timestamp=time.monotonic(),
            command="t", stale_after=sc._DYNAMIC_INTERVAL)
        cache._cache["failed_services"] = CachedValue(
            value="", timestamp=time.monotonic(), command="t",
            stale_after=sc._DYNAMIC_INTERVAL)
        return cache

    def test_no_notice_while_polling_is_running(self):
        self.assertIsNone(self._cache().poll_pause_notice())

    def test_the_load_guard_records_the_pause(self):
        cache = StateCache()
        with mock.patch.object(sc.os, "getloadavg", return_value=(99.0, 0, 0)), \
             mock.patch.object(sc.os, "cpu_count", return_value=4):
            cache._poll_all({"load_average": [["true"]]}, sc._DYNAMIC_INTERVAL)
        notice = cache.poll_pause_notice()
        self.assertIsNotNone(notice)
        self.assertIn("PAUSED", notice)
        self.assertIn("sustained load", notice)

    def test_the_pause_clears_when_load_drops(self):
        cache = StateCache()
        with mock.patch.object(sc.os, "getloadavg", return_value=(99.0, 0, 0)), \
             mock.patch.object(sc.os, "cpu_count", return_value=4):
            cache._poll_all({"load_average": [["true"]]}, sc._DYNAMIC_INTERVAL)
        self.assertIsNotNone(cache.poll_pause_notice())
        completed = mock.Mock(stdout="0.1 0.2 0.3 1/1 1", returncode=0)
        with mock.patch.object(sc.os, "getloadavg", return_value=(0.1, 0, 0)), \
             mock.patch.object(sc.os, "cpu_count", return_value=4), \
             mock.patch.object(StateCache, "_run_pipeline", return_value=completed), \
             mock.patch.object(sc.time, "sleep"):
            cache._poll_all({"load_average": [["true"]]}, sc._DYNAMIC_INTERVAL)
        self.assertIsNone(cache.poll_pause_notice())

    def test_the_notice_rides_at_the_top_of_a_health_answer(self):
        cache = self._cache()
        cache._poll_paused_since = time.monotonic() - 300
        cache._poll_paused_load = (12.0, 4)
        with mock.patch.object(StateCache, "refresh_dynamic", return_value=False):
            data = cache.get_system_map_data("is anything failing on this machine?")
        self.assertTrue(data.startswith("NOTE — background state polling is PAUSED"),
                        "the pause must be the first thing the synthesis reads")
        self.assertIn("have not advanced", data)

    def test_an_unpaused_health_answer_carries_no_notice(self):
        with mock.patch.object(StateCache, "refresh_dynamic", return_value=False):
            data = self._cache().get_system_map_data(
                "is anything failing on this machine?")
        self.assertNotIn("PAUSED", data)


class AskPayloadIsAdditiveTests(unittest.TestCase):
    """The D-Bus reply gains tool_results without disturbing what was there.

    Asserted by INVOKING ask() and reading the real payload, not by scraping the
    source — the point of the gate is that existing consumers keep working, and
    only the emitted JSON can show that.
    """

    _EXISTING = ("response", "full_output", "source", "handled", "tool_calls",
                 "used_llm", "escalated", "escalation_offer", "trace_id")

    def _ask(self, result):
        """Daemon-free invocation — mock router, no model, no bus (the same
        construction the trace-id and review-callback pins use)."""
        from intergen.dbus_daemon import InterGenDaemon
        daemon = InterGenDaemon()
        daemon._router = mock.Mock()
        daemon._router.route.return_value = result
        with mock.patch("intergen.review_modal.make_review_callback",
                        return_value=None):
            return json.loads(daemon.ask("what packages do I have installed?"))

    def _result(self):
        tr = ToolResult(call_id="c1", name="manage_packages",
                        content="bash 5.3.0 installed")
        return RouteResult(
            text="You have 1 package installed.", source="keyword",
            handled=True, tool_results=[tr], used_llm=True,
            answer_linkage=AnswerLinkage(kind="dispatch",
                                         tool="manage_packages",
                                         call_id="c1", renderer="llm_synth"))

    def test_every_pre_existing_field_is_still_emitted(self):
        payload = self._ask(self._result())
        for field in self._EXISTING:
            with self.subTest(field=field):
                self.assertIn(field, payload,
                              f"{field} must not disappear — existing consumers "
                              "read it")

    def test_tool_results_are_now_readable_without_scraping_the_answer(self):
        payload = self._ask(self._result())
        self.assertEqual(payload["tool_results"], [{
            "call_id": "c1", "name": "manage_packages", "success": True,
            "executed": True, "blocked": False,
            "content": "bash 5.3.0 installed"}])

    def test_the_reply_carries_what_composed_it(self):
        payload = self._ask(self._result())
        self.assertEqual(payload["answer_linkage"], {
            "kind": "dispatch", "tool": "manage_packages",
            "call_id": "c1", "renderer": "llm_synth"})

    def test_a_route_declaring_nothing_reports_undeclared(self):
        """An uninstrumented path is visible on the wire, not silently absent
        and not disguised as a code-owned answer."""
        payload = self._ask(RouteResult(text="hello", source="llm_freeform",
                                        handled=True))
        self.assertEqual(payload["answer_linkage"], {"kind": "undeclared"})

    def test_a_turn_with_no_dispatch_carries_an_empty_result_list(self):
        payload = self._ask(RouteResult(text="hello", source="llm_freeform",
                                        handled=True))
        self.assertEqual(payload["tool_results"], [])


class EveryDeliveredTurnDeclaresCompositionTests(unittest.TestCase):
    """Every delivered turn carries its composition record.

    Measured on live traffic: 247 of 770 delivered turns across thirteen
    routes carried no composition declaration — the sharpest being refusals
    delivered with unrecorded provenance. The invariant is enforced at the
    construction layer: every RouteResult the router builds as a delivered
    turn (handled=True) declares answer_linkage, so a NEW composing site
    cannot ship undeclared either.
    """

    def test_every_delivered_construction_declares_its_composition(self):
        import ast
        import inspect

        from intergen import router as router_mod
        tree = ast.parse(inspect.getsource(router_mod))
        missing = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "RouteResult"):
                continue
            handled = next((k.value for k in node.keywords
                            if k.arg == "handled"), None)
            if not (isinstance(handled, ast.Constant)
                    and handled.value is True):
                continue  # a decline / fall-through, never delivered
            if "answer_linkage" not in {k.arg for k in node.keywords}:
                missing.append(node.lineno)
        self.assertEqual(
            missing, [],
            "router.py delivers a turn without declaring its composition "
            f"(RouteResult construction at line(s) {missing})")

    def test_a_safety_refusal_declares_its_composition(self):
        """The sharpest undeclared class: a REFUSAL's provenance is recorded —
        kind=code names it deliberately code-owned, never uninstrumented."""
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._ingress_tracker = mock.Mock()
        r._lock_dispatch = True
        r._memory = None
        r._metrics = None
        r._events = None
        r._state_cache = None
        r._howto = None
        r._tools = None
        r._turn_index = None
        r._first_interaction = False
        r._hardware_tier = None
        r._max_history = 20
        r._conversation_history = []
        r._handed_off_commands = set()
        r._semantic = mock.Mock(
            _match_keywords=mock.Mock(
                return_value=mock.Mock(intent_id=None)),
            _match_embeddings=mock.Mock(
                return_value=mock.Mock(score=None, intent_id=None,
                                       runner_up_score=0.0)),
            _normalize_input=mock.Mock(side_effect=lambda s: s))
        res = r.route("wipe my drive")
        self.assertEqual(res.source, "safety_decline")
        self.assertTrue(res.handled)
        self.assertIsNotNone(res.answer_linkage)
        self.assertEqual(res.answer_linkage.kind, "code")
        self.assertEqual(res.answer_linkage.renderer, "safety_decline")

    def test_a_memory_turn_declares_its_composition(self):
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._pending_memory_offer = None
        r._memory = mock.Mock(
            format_transparency_response=mock.Mock(
                return_value="I know these facts about you: none yet."))
        from intergen.memory import MemoryManager
        with mock.patch.object(MemoryManager, "is_transparency_request",
                               return_value=True):
            res = ConversationRouter._try_memory(r, "what do you know about me?")
        self.assertTrue(res.handled)
        self.assertIsNotNone(res.answer_linkage)
        self.assertEqual(res.answer_linkage.kind, "code")
        self.assertEqual(res.answer_linkage.renderer, "memory_template")


if __name__ == "__main__":
    unittest.main()
