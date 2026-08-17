# SPDX-License-Identifier: GPL-3.0-or-later
"""LLMRouter must not serve a length-truncated reply as a complete answer.

Regression for PI-218-4 (internvl-02 .218 post-install eval): a conversational
reply was cut off mid-sentence ("…I don't have feelings, but") because it hit
the max_tokens cap. check_quality() does not flag truncation (the text is
non-empty, not repetitive, no artifacts), so the response was returned as-is and
the retry-with-more-room path never fired. The fix keys off the checked
finish_reason ("length") rather than guessing from the text:
  1. a length-truncation triggers the retry with a doubled token budget, and
     the COMPLETED reply is what the user receives;
  2. a reply that ends naturally ("stop") is served as-is — no wasted retry.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import EscalationMode, Message, MessageRole


class TruncationRetryTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMRouter()
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.msgs = [Message(role=MessageRole.USER,
                             content="do you have feelings?")]

    def test_length_truncation_triggers_retry_with_more_room(self):
        # Attempt 1 hits the cap mid-sentence (finish_reason "length"); attempt 2
        # (doubled budget) completes (finish_reason "stop"). The completed reply
        # must reach the user, not the truncated fragment.
        calls = {"n": 0}
        truncated = "I don't have feelings, but"
        complete = "I don't have feelings, but I'm glad to help you out."

        def _stream(*a, **k):
            calls["n"] += 1
            self.llm._last_reasoning = ""
            if calls["n"] == 1:
                self.llm._last_finish_reason = "length"
                return iter([truncated])
            self.llm._last_finish_reason = "stop"
            return iter([complete])

        with mock.patch.object(self.llm, "stream", _stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 2, "length-truncation must trigger a retry")
        self.assertEqual(resp.text.strip(), complete)

    def test_complete_reply_is_not_retried(self):
        # A reply that ends naturally (finish_reason "stop") is served as-is —
        # no wasted retry round-trip (the latency the retry would otherwise add).
        calls = {"n": 0}

        def _stream(*a, **k):
            calls["n"] += 1
            self.llm._last_reasoning = ""
            self.llm._last_finish_reason = "stop"
            return iter(["All ", "set ", "here."])

        with mock.patch.object(self.llm, "stream", _stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 1, "a complete reply must not be retried")
        self.assertEqual(resp.text.strip(), "All set here.")


if __name__ == "__main__":
    unittest.main()
