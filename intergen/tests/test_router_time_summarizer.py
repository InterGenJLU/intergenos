# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Parity-lock for the deterministic time-of-day summarizer (PI-218-3).

A time query routes to ``date`` at the P1 keyword path; its single-line output
must be wrapped by ``_template_synthesis`` into a deterministic reply rather than
falling through to ``None`` -> LLM synthesis. The synthesis call was ~22s on the
Tier-2 2B/iGPU floor (measured on a development machine: single "what time is it" ~23s, llm True
before; 13ms, llm False after) and made a decomposed time sub keep the mixed
time+memory compound at ~22s instead of the ~40ms all-state class.

``_template_synthesis`` returning a non-None string IS the deterministic path:
the router serves that text directly with used_llm False. Returning None is the
LLM-fallback signal. So this test pins (1) every time phrase that maps to ``date``
yields the settled "It's currently {out}." text, and (2) the ordering guard — an
uptime ask, whose key "uptime" contains the substring "time", is claimed by the
uptime branch ABOVE and never hijacked into the time template.
"""
from __future__ import annotations

import unittest

from intergen.router import ConversationRouter

# A representative single-line `date` output (what the selector's `date` command
# returns): full date-time, which is why the settled wording is "It's currently
# {out}." (it reads correctly with a full date-time, unlike a bare-clock phrasing).
_DATE_OUT = "Sat Jun 27 06:11:35 PM CDT 2026"


class TimeSummarizerParityTests(unittest.TestCase):
    def test_time_phrases_resolve_to_deterministic_currently_template(self):
        # Every phrase the selector maps to `date` must wrap deterministically.
        for phrase in (
            "what time is it",
            "time is it",
            "the time",
            "current time",
            "time of day",
        ):
            with self.subTest(phrase=phrase):
                result = ConversationRouter._template_synthesis(phrase, _DATE_OUT)
                # Non-None => deterministic template => used_llm False (NOT the
                # None -> ~22s LLM synthesis path).
                self.assertIsNotNone(
                    result,
                    f"{phrase!r} fell through to LLM synthesis (None)",
                )
                self.assertEqual(result, f"It's currently {_DATE_OUT}.")

    def test_uptime_ask_not_hijacked_by_time_branch(self):
        # "uptime" contains the substring "time"; the uptime branch is matched
        # FIRST, so an uptime ask must render as uptime, never "It's currently".
        uptime_out = "18:11:35 up 6:28, 1 user, load average: 0.10, 0.20, 0.15"
        result = ConversationRouter._template_synthesis(
            "what is the uptime", uptime_out)
        self.assertIsNotNone(result)
        self.assertTrue(
            result.startswith("System uptime:"),
            f"uptime ask rendered as {result!r}",
        )
        self.assertNotIn("It's currently", result)


if __name__ == "__main__":
    unittest.main()
