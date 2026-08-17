# SPDX-License-Identifier: GPL-3.0-or-later
"""LLMRouter.chat() must never return an empty response (a silent assistant).

Regression for the dyno's emo_frustrated_generic miss: the local model finished
with an empty content stream, the futile retry produced empty again, and with
escalation NEVER chat() returned text="". Two guarantees are tested:
  1. reasoning-channel recovery — empty content but populated reasoning_content
     surfaces the model's words instead of nothing;
  2. never-empty floor — when nothing usable is produced and no cloud is
     available, a non-empty (non-patronizing) fallback is returned, not "".
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import EscalationMode, Message, MessageRole


class EmptyResponseTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMRouter()
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.msgs = [Message(role=MessageRole.USER,
                             content="NOTHING WORKS on this stupid thing")]

    def test_never_returns_empty_when_model_yields_nothing(self):
        # Every stream attempt yields no content and no reasoning.
        def _empty_stream(*a, **k):
            self.llm._last_reasoning = ""
            return iter([])
        with mock.patch.object(self.llm, "stream", _empty_stream):
            resp = self.llm.chat(self.msgs)
        self.assertTrue(resp.text.strip(), "chat() returned an empty response")
        self.assertEqual(resp.text, self.llm._EMPTY_RESPONSE_FALLBACK)
        # The fallback must not patronize (the emo_* assertions).
        low = resp.text.lower()
        self.assertNotIn("calm down", low)
        self.assertNotIn("i understand your frustration", low)

    def test_recovers_reasoning_channel_when_content_empty(self):
        # Content stream empty, but the model put its answer in reasoning_content.
        recovered = "Your system looks healthy; tell me what is misbehaving."
        def _reasoning_stream(*a, **k):
            self.llm._last_reasoning = recovered
            return iter([])  # no content tokens
        with mock.patch.object(self.llm, "stream", _reasoning_stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(resp.text.strip(), recovered)

    def test_normal_content_is_unaffected(self):
        # A normal non-empty content stream passes through unchanged.
        def _good_stream(*a, **k):
            self.llm._last_reasoning = ""
            return iter(["All ", "good ", "here."])
        with mock.patch.object(self.llm, "stream", _good_stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(resp.text.strip(), "All good here.")


if __name__ == "__main__":
    unittest.main()
