# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the DIRECT-ANSWER intent class in the router (ge9b finding #3).

The operator's every-install smoke question "What's the current time?" scored
0.909 against the teaching corpus and routed to a `date`/`timedatectl` tutorial
instead of the time. This class answers an enumerated set of basics in ONE turn,
ranked AHEAD of the explain gate:

  D1 — LOCAL basics: fixed, code-owned, read-only probes (time/date/uptime/
       hostname/disk-free/battery), answered immediately.
  D2 — EXTERNAL basics (weather/daylight/store-open): LOCATION-DEPENDENT. web_search
       ships but NO location source exists in the tree, so D2 gives the honest
       location-absent answer and names the remedy (no fake fetch — Rule 21).
  D3 — routing precedence: the class outranks explain for its enumerated intents;
       everything else is byte-identical (teaching still teaches; the external
       live-data offer still handles stocks/crypto).
  D5 — the operator's five canonical questions + explain/howto regression.

Exercised in isolation on a bare ConversationRouter (__new__), the same lightweight
pattern as test_router_explain / test_router_offer — no LLM, no embedding server.
The probe commands are stubbed via a fake tool registry so the tests are
hermetic (no dependency on the host's `date`/`df`/`uptime`).
"""

from __future__ import annotations

import unittest

from intergen.router import (
    ConversationRouter,
    _DA_TEACH_GUARD_RE,
    _DA_TIME_RE, _DA_DATE_RE, _DA_UPTIME_RE, _DA_HOSTNAME_RE, _DA_DISK_RE,
    _DA_BATTERY_RE, _DA_EXTERNAL_RE, _DA_STORE_OPEN_RE, _DA_EXTERNAL_PLACE_RE,
    _da_render_disk_free, _da_render_uptime, _da_render_time,
    _SEMANTIC_INCOHERENCE_FALLBACK,
)
from intergen.semantic import SemanticMatcher


class _FakeToolResult:
    def __init__(self, content, success=True, blocked=False):
        self.content = content
        self.success = success
        self.blocked = blocked


class _FakeRegistry:
    """A stand-in ToolRegistry: names present + a canned run_command output map."""
    def __init__(self, names, outputs=None):
        self._names = list(names)
        self._outputs = outputs or {}

    def get_all_names(self):
        return list(self._names)

    def execute(self, call, **kwargs):
        if call.name != "run_command":
            return _FakeToolResult("", success=False)
        cmd = call.arguments.get("command", "")
        if cmd in self._outputs:
            return _FakeToolResult(self._outputs[cmd])
        return _FakeToolResult("", success=False)


# Canned probe outputs keyed on the FIXED commands the class issues.
_PROBE_OUTPUTS = {
    "date '+%-I:%M %p %Z'": "3:42 PM CDT",
    "date '+%A, %B %-d, %Y'": "Saturday, July 11, 2026",
    "uptime -p": "up 3 hours, 14 minutes",
    "hostname": "intergenos",
    "df -h --output=size,avail /": " Size Avail\n  250G  118G",
}


def _da_router(*, tool_names=("web_search", "run_command"), outputs=_PROBE_OUTPUTS,
               web=True):
    """A bare router with just what _try_direct_answer touches — no heavy build."""
    names = list(tool_names)
    if not web and "web_search" in names:
        names.remove("web_search")
    r = ConversationRouter.__new__(ConversationRouter)
    r._semantic = SemanticMatcher(embedder=None)   # for _normalize_input
    r._tools = _FakeRegistry(names, outputs)
    r._conversation_history = []
    r._max_history = 20
    r._turn_index = None   # M2b: _append_history reads it; None = memory-disabled
    r._last_semantic_score = None
    r._current_query_type = "general"
    r._memory = None
    r._metrics = None
    r._events = None
    # _run_fixed_command reads these on the execute() call.
    r._ingress_tracker = None
    r._trust_state = None
    r._review_callback = None
    return r


def _answer(r, text):
    """Route a single ask through the class the way _route_impl would (normalized
    body + raw original for the place check)."""
    normalized = r._semantic._normalize_input(text)
    return r._try_direct_answer(normalized, text, 0.0)


# ── D1 — LOCAL basics ────────────────────────────────────────────────────────
class DirectLocalTests(unittest.TestCase):
    def test_current_time_answers_not_teaches(self):
        # The exact operator smoke question that regressed to a tutorial.
        res = _answer(_da_router(), "What's the current time?")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "direct_answer")
        self.assertIn("3:42 PM", res.text)
        self.assertNotIn("timedatectl", res.text)
        self.assertNotIn("date", res.text.lower())  # no how-to about `date`

    def test_date_uptime_hostname_diskfree(self):
        cases = {
            "what's today's date": "Saturday, July 11, 2026",
            "what's my uptime": "up 3 hours, 14 minutes",
            "what's my hostname": "intergenos",
            "how much disk space is free": "118G",
        }
        for q, needle in cases.items():
            res = _answer(_da_router(), q)
            self.assertIsNotNone(res, q)
            self.assertEqual(res.source, "direct_answer", q)
            self.assertIn(needle, res.text, q)

    def test_probe_unavailable_declines_not_fabricates(self):
        # A box lacking the probe's tool: the class must DECLINE (None), never claim
        # a value it could not read (security-first — no unverified capability claim).
        r = _da_router(outputs={})  # run_command returns success=False for every cmd
        self.assertIsNone(_answer(r, "what time is it"))

    def test_battery_no_battery_is_honest(self):
        r = _da_router()
        r._read_battery_state = lambda: "no-battery"   # simulate a desktop
        res = _answer(r, "how much battery do I have")
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "direct_answer")
        self.assertIn("doesn't report a battery", res.text)

    def test_battery_reports_level_and_state(self):
        r = _da_router()
        r._read_battery_state = lambda: "The battery is at 72% and charging."
        res = _answer(r, "battery level")
        self.assertIsNotNone(res)
        self.assertIn("72%", res.text)


# ── D2 — EXTERNAL basics (location-gated, capability-grounded) ────────────────
class DirectExternalTests(unittest.TestCase):
    def test_weather_declines_no_location_names_remedy(self):
        # web_search ships but NO location source → honest location-absent answer.
        for q in ("is it hot outside", "what's the weather", "will it rain tomorrow"):
            res = _answer(_da_router(), q)
            self.assertIsNotNone(res, q)
            self.assertEqual(res.source, "direct_answer_external", q)
            self.assertIn("location", res.text.lower(), q)
            self.assertIn("city or place", res.text.lower(), q)  # names the remedy

    def test_daylight_and_store_open_are_external(self):
        for q in ("how much daylight is left", "is the Walmart open"):
            res = _answer(_da_router(), q)
            self.assertIsNotNone(res, q)
            self.assertEqual(res.source, "direct_answer_external", q)

    def test_no_websearch_says_so(self):
        res = _answer(_da_router(web=False), "is it hot outside")
        self.assertIsNotNone(res)
        self.assertIn("web search isn't available", res.text.lower())

    def test_location_available_yields_to_offer_path(self):
        # When a real location source is registered, D2 does NOT fabricate a fetch —
        # it falls through (None) to the existing search-offer path.
        r = _da_router(tool_names=("web_search", "run_command", "get_location"))
        self.assertTrue(r._location_available())
        self.assertIsNone(_answer(r, "is it hot outside"))

    def test_explicit_place_falls_through_unchanged(self):
        # "weather in Chicago" is a plain web search — leave it to the offer path.
        self.assertIsNone(_answer(_da_router(), "what's the weather in Chicago"))


# ── D3 — precedence: teaching still teaches; the rest is untouched ────────────
class PrecedenceTests(unittest.TestCase):
    def test_teaching_asks_fail_safe_out(self):
        # Instructional/definitional forms must DECLINE here (→ explain answers them).
        for q in (
            "how do I check the time",
            "what command shows the date",
            "how do I see my uptime",
            "what is a hostname",
            "what is uptime",
            "explain what a kernel is",
            "how to check disk space",
        ):
            self.assertIsNone(_answer(_da_router(), q), q)

    def test_non_class_asks_untouched(self):
        # Nothing outside the enumeration is captured (byte-identical routing).
        for q in (
            "what's the dow jones trading at",   # external live-data → offer path
            "restart nginx",                     # a system action
            "is port 22 open",                   # a service/port ask, not a store
            "install firefox",                   # a package action
            "who wrote Hamlet",                  # pure knowledge
        ):
            self.assertIsNone(_answer(_da_router(), q), q)


# ── Detector-level guards (precision) ────────────────────────────────────────
class DetectorGuardTests(unittest.TestCase):
    def test_teach_guard_matches_instructional(self):
        for q in ("what is a hostname", "what is uptime",
                  "what does uptime mean", "explain the date command",
                  "how do I check the time"):
            self.assertTrue(_DA_TEACH_GUARD_RE.search(q), q)

    def test_time_vs_uptime_disambiguation(self):
        self.assertTrue(_DA_TIME_RE.search("what's the current time"))
        self.assertTrue(_DA_UPTIME_RE.search("what's my uptime"))
        # "uptime" contains "time" but the uptime probe is consulted first.
        self.assertFalse(_DA_TIME_RE.search("uptime"))

    def test_store_open_excludes_ports_and_services(self):
        self.assertTrue(_DA_STORE_OPEN_RE.search("is the walmart open"))
        self.assertFalse(_DA_STORE_OPEN_RE.search("is port 22 open"))
        self.assertFalse(_DA_STORE_OPEN_RE.search("is ssh open"))

    def test_explicit_place_detector(self):
        self.assertTrue(_DA_EXTERNAL_PLACE_RE.search("weather in Chicago"))
        self.assertFalse(_DA_EXTERNAL_PLACE_RE.search("is it hot outside"))

    def test_renderers(self):
        self.assertEqual(_da_render_time("3:42 PM CDT"), "It's currently 3:42 PM CDT.")
        self.assertIn("free", _da_render_disk_free(" Size Avail\n 250G 118G"))
        self.assertIn("been up", _da_render_uptime("up 5 minutes"))


# ── D6 — semantic-flag consumption ───────────────────────────────────────────
_UNSET = object()


class _FakeCompletion:
    """Stand-in for the LLMResponse completion result. semantic_flags is the field
    the engine-earn-offload-gate branch adds; omit it entirely to model this branch
    running BEFORE that field lands (getattr must treat it as clean)."""
    def __init__(self, *, text="the model's answer", flags=_UNSET, local=True):
        self.text = text
        self.local = local
        self.tokens_prompt = 5
        self.tokens_completion = 7
        if flags is not _UNSET:
            self.semantic_flags = flags


def _flag_router():
    r = ConversationRouter.__new__(ConversationRouter)
    r._conversation_history = []
    r._max_history = 20
    r._turn_index = None
    return r


class SemanticFlagConsumptionTests(unittest.TestCase):
    def test_non_empty_flags_serve_incoherence_fallback(self):
        r = _flag_router()
        res = r._semantic_flag_fallback(
            _FakeCompletion(flags=["coherence", "topicality"]), "hello")
        self.assertIsNotNone(res)
        self.assertEqual(res.text, _SEMANTIC_INCOHERENCE_FALLBACK)
        self.assertEqual(res.source, "llm_freeform")
        self.assertEqual(res.confidence, 0.0)
        # same history handling as any freeform turn — the exchange is recorded.
        self.assertEqual(len(r._conversation_history), 2)
        self.assertEqual(r._conversation_history[-1].content,
                         _SEMANTIC_INCOHERENCE_FALLBACK)

    def test_empty_flags_route_on(self):
        r = _flag_router()
        self.assertIsNone(
            r._semantic_flag_fallback(_FakeCompletion(flags=[]), "hello"))
        self.assertEqual(len(r._conversation_history), 0)

    def test_absent_field_is_inert_before_it_lands(self):
        # Composed against the contract via getattr: with no semantic_flags attribute
        # (this branch running before the gate lands), the consumer is a no-op.
        r = _flag_router()
        self.assertIsNone(
            r._semantic_flag_fallback(_FakeCompletion(), "hello"))

    def test_fallback_message_matches_the_delivery_catch(self):
        # One voice across surfaces — the router constant is the web delivery-catch
        # nudge verbatim (guards against drift between the two).
        self.assertEqual(
            _SEMANTIC_INCOHERENCE_FALLBACK,
            "Sorry — I didn't quite catch that. Could you rephrase it for me?")


if __name__ == "__main__":
    unittest.main()
