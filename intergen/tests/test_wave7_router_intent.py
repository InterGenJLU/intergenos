# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8 wave-7 — two 9B-lane router-behavior fixes, FIRE->OBSERVE against original input.

LEG 1 — dual-reading system nouns. "what is a kernel?" / "how do kernels work?" /
"what is memory?" are TEACH asks and must reach the model's teaching answer, not the
system_info keyword/semantic dispatch or the system-map cache (which report THIS
machine's live state). The live-state reading ("what kernel am I running", "how much
memory", "what is MY disk usage") carries a possessive/quantity/running signal and is
unaffected. Swept across the class (kernel/memory/disk/cpu/service/driver/process/...).

LEG 2 — compound capability-framed asks. A compound whose one half is a capability
question and whose other half is a separate actionable ask ("can you read files and
also check my disk?", and the head form "check my disk and can you read files?") must
DECOMPOSE so both halves are answered — the capability intercept must not fire WHOLE
and silently eat the actionable clause. The verb-compound whose object rides the tail
("can you start and stop services?") stays a single whole capability question.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.decomposer import analyze_query
from intergen.interfaces.types import RouteResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

_REG = ToolRegistry()
_REG.discover_tools()


def _router(lock=True):
    sm = SemanticMatcher(embedder=None)
    register_all_intents(sm)
    return ConversationRouter(tool_registry=_REG, semantic_matcher=sm,
                              llm=LLMRouter(config=None), lock_dispatch=lock)


# The route sources that mean "answered from live state / a dispatch", i.e. NOT a
# teaching answer.
_LIVE_STATE_SOURCES = {"keyword", "semantic", "system_map", "system_info"}

_TEACH = (
    "what is a kernel?", "how do kernels work?", "what is memory?", "what is disk?",
    "what is a service?", "how do services work", "what is a driver?",
    "what is a process?", "what does swap mean", "explain the cpu scheduler",
    "tell me about processes", "what are threads",
)
_LIVE = (
    "what kernel am I running?", "how much memory do I have", "what is my disk usage",
    "how much disk space is left", "what's my cpu usage",
    "what kernel version is this machine on",
)


class Leg1DualReadingSystemNouns(unittest.TestCase):
    def test_teach_detector_splits_teach_from_live(self):
        r = _router()
        for q in _TEACH:
            self.assertTrue(r._is_system_noun_teach(q), q)
        for q in _LIVE:
            self.assertFalse(r._is_system_noun_teach(q), q)

    def test_teach_asks_do_not_route_to_live_state(self):
        # FIRE->OBSERVE against original input: a teach ask reaches the teaching
        # answer, never the live-state dispatch / system-map cache.
        for q in _TEACH:
            src = _router().route(q, decide_only=True).source
            self.assertNotIn(src, _LIVE_STATE_SOURCES, f"{q!r} -> {src}")

    def test_teach_holds_in_both_postures(self):
        for q in ("what is a kernel?", "how do kernels work?"):
            for lock in (True, False):
                self.assertTrue(_router(lock)._is_system_noun_teach(q), (q, lock))


class Leg2CompoundCapability(unittest.TestCase):
    def _whole(self, q):
        r = _router()
        return r._capability_is_whole_ask(analyze_query(q, r._hardware_tier))

    def test_capability_plus_action_decomposes_either_order(self):
        # The capability answer must not eat the actionable clause — tail OR head.
        self.assertFalse(self._whole("can you read files and also check my disk?"))
        self.assertFalse(self._whole("check my disk and can you read files?"))
        self.assertFalse(
            self._whole("can you start services and also list my processes?"))

    def test_verb_compound_stays_whole(self):
        # One capability question split on its verbs (object rides the tail).
        self.assertTrue(self._whole("can you start and stop services?"))

    def test_single_capability_is_whole(self):
        self.assertTrue(self._whole("can you read files?"))

    def test_compound_surfaces_both_halves_e2e(self):
        # Drive route() on the compound with _route_single stubbed per clause; the
        # capability answer does not fire whole, so the compound decomposes and BOTH
        # clause answers appear — neither is dropped.
        r = _router()

        def _echo(sub_query, **kwargs):   # **kwargs: accepts the trail_scope tag
            return RouteResult(text=f"[ANS:{sub_query.strip().rstrip('?')}]",
                               source="stub", handled=True)

        with mock.patch.object(r, "_route_single", side_effect=_echo):
            res = r.route("can you read files and also check my disk?",
                          decide_only=True)
        self.assertEqual(res.source, "decomposed")
        self.assertIn("read files", res.text)
        self.assertIn("check my disk", res.text)


if __name__ == "__main__":
    unittest.main()
