# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The system-map lane must not serve corrupt LLM synthesis.

CLASS EXTENSION, found by code-read (decided 2026-08-11): the round-1 battery
measured the keyword/semantic fast path serving LLM-burned garbage, and that
lane was fixed to consult the corruption screen and quality gate and to answer
from the tool's own output on rejection. A sweep for the same class found ONE
remaining synthesis lane serving model text with no instrument consulted:
``_try_system_map``, the grounded system-state answer. It holds the live system
data in hand — the exact analog of the fast path's ``full_output`` — so a
rejected synthesis serves THAT, never the garbage and never a generic apology.

No system-map garbage serve was measured in round 1 (the lane engaged rarely);
this closes the class before it is measured the expensive way.

Covered here:
  1. an instrument-flagged synthesis is not served; the live data is;
  2. an exhausted quality ladder (the generic apology) is replaced by the data;
  3. a clean synthesis is served unchanged and pays nothing;
  4. rejected synthesis with no usable data falls back to the honest sentence;
  5. the answer linkage names the true composer on both outcomes.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.interfaces.types import LLMResponse
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter as R
from intergen.semantic_health import assess_semantic_health

LIVE_DATA = (
    "services: gdm active, NetworkManager active, cups active\n"
    "failed units: 0\n"
)
GOOD_SYNTHESIS = "Everything looks healthy — no failed services."
# Structural garbage in the shape round 1 measured on the sibling lane.
GARBAGE = "0清.\n.\n00.\n运行清明.\n.\n0.(词 (\xa0 (（ ( ( 清网络清清"


def _real_flags(text):
    """The shipped screen's own verdict for these bytes (never invented)."""
    return assess_semantic_health(text, system_prompt="",
                                  conversation_texts=[]).flags


def _router():
    """A router with only what this unit needs — no daemon, no LLM server."""
    r = object.__new__(R)
    r._llm = object.__new__(LLMRouter)
    r._llm._last_finish_reason = "stop"
    r._llm._last_semantic_flags = []
    r._last_synthesis_rejection = None
    r._conversation_history = []
    r._max_history = 6
    return r


def _answering(text, *, flags=None, quality_passed=True):
    def _chat(messages, **kw):
        return LLMResponse(text=text, model="local", local=True,
                           quality_passed=quality_passed,
                           semantic_flags=(list(flags) if flags is not None
                                           else _real_flags(text)))
    return _chat


class SystemMapSynthesisScreenTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def _serve(self, model_text, *, flags=None, quality_passed=True,
               data=LIVE_DATA):
        with mock.patch.object(self.r._llm, "chat",
                               _answering(model_text, flags=flags,
                                          quality_passed=quality_passed)), \
             mock.patch.object(self.r, "_append_history"):
            return self.r._try_system_map("is everything running ok?", data)

    def test_flagged_garbage_is_not_served_the_live_data_is(self):
        result = self._serve(GARBAGE)
        self.assertNotIn("清", result.text,
                         "instrument-flagged garbage must not be served")
        self.assertIn("failed units: 0", result.text,
                      "the live data in hand is the honest answer")

    def test_an_exhausted_quality_ladder_is_not_passed_through(self):
        result = self._serve(LLMRouter._EMPTY_RESPONSE_FALLBACK,
                             quality_passed=False)
        self.assertNotIn("Could you rephrase", result.text)
        self.assertIn("failed units: 0", result.text)

    def test_a_clean_synthesis_is_served_unchanged(self):
        result = self._serve(GOOD_SYNTHESIS)
        self.assertEqual(result.text, GOOD_SYNTHESIS)
        self.assertEqual(result.answer_linkage.renderer,
                         "system_map_synthesis")

    def test_rejection_with_no_usable_data_serves_the_honest_sentence(self):
        result = self._serve(GARBAGE, data="   ")
        self.assertNotIn("清", result.text)
        self.assertTrue(result.text.strip(),
                        "the user must not get an empty message")

    def test_the_linkage_names_the_true_composer_on_rejection(self):
        result = self._serve(GARBAGE)
        self.assertEqual(result.answer_linkage.renderer,
                         "system_map_data_verbatim",
                         "served bytes came from the data, not the model — "
                         "the linkage must say so")


if __name__ == "__main__":
    unittest.main()
