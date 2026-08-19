# SPDX-License-Identifier: GPL-3.0-or-later
"""Served output must never carry a leaked model thinking block.

MEASURED CASE — anchor-0018 of the sealed judge anchor set (set t7-judge-anchor-v1,
trace 85dac3669bb0, 2B tier, "Is NetworkManager running?"): the model emitted a
page of bare digits, then a closing "</think>" tag, then the correct answer. The
whole thing was SERVED. The judge graded it PASS on every dimension, because the
real answer sits at the tail where the judge read it — which is exactly why a
judge score cannot be the gate for this class.

Why the existing checks did not stop it: check_quality's artifact list already
NAMES <think> and </think>, so the reply was flagged "artifacts" — but an
artifacts-flagged reply is still SERVED once the retry ladder is spent (only the
degenerate reason is replaced by the honest fallback, per release 149). Naming a
defect is not handling it.

THE SPLIT, decided by measurement (stated in the delivery): across all 441
replies of the three sealed baseline runs, exactly ONE carries a reasoning tag —
this one — and it carries only the stray CLOSER form. Stripping through that
closer leaves "The tool returned that NetworkManager.service is active
(running).", which is substantive and correct. So a strippable leak SERVES THE
CLEAN REMAINDER, and a reply that is only a thinking block strips to nothing and
lands in the ladder as "empty", which routes it to the honest fallback.

Covered here:
  1. the measured anchor: the leak is removed and the real answer survives;
  2. the three leak shapes (complete block, stray closer, unclosed opener) and
     the no-tag case, which must pass through untouched;
  3. a reply that is ONLY a thinking block reaches the user as the fallback,
     never as the block;
  4. the gate judges the STRIPPED text — the text that will actually be served;
  5. the reasoning-channel RECOVERY path still surfaces a real answer that the
     model put in reasoning_content without tagging it.
"""
from __future__ import annotations

import json
import os
import pathlib
import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import EscalationMode, Message, MessageRole

# Verbatim from anchor-0018 (sealed set t7-judge-anchor-v1, trace 85dac3669bb0).
ANCHOR_0018 = (
    "3\n1\n\n\n33313\n2\n133\n\n\n33\n\n11\n3\n33\n\n13\n\n\n\n3\n\n3\n"
    "\n3\n\n\n\n3\n\n13\n\n\n\n\n\n3\"\n323\n\n33\n\n\n\n\n\n\"\n\n\n\n"
    "331333333\n\n33\n3\n\n2\n\n\n\n\n\n\n\n\n13\n1\n\n\n1\n33\n\n\n\n"
    "\n11\n\n3\n33\n\n\n\n</think>\n\nThe tool returned that NetworkMan"
    "ager.service is active (running)."
)
ANCHOR_0018_ANSWER = (
    "The tool returned that NetworkManager.service is active (running)."
)


class ReasoningLeakStripTests(unittest.TestCase):
    def test_measured_anchor_keeps_the_answer_and_drops_the_leak(self):
        kept = LLMRouter._strip_reasoning_leak(ANCHOR_0018)
        self.assertEqual(kept, ANCHOR_0018_ANSWER)
        self.assertNotIn("</think>", kept)
        self.assertNotIn("33313", kept)

    def test_measured_anchor_passes_the_gate_only_after_stripping(self):
        llm = object.__new__(LLMRouter)
        llm._last_finish_reason = "stop"
        q = "Is NetworkManager running?"
        # Before: the artifact list names it, which is why it was merely flagged.
        self.assertEqual(llm.check_quality(ANCHOR_0018, q), "artifacts")
        # After: what is actually served is a clean, substantive answer.
        self.assertEqual(
            llm.check_quality(LLMRouter._strip_reasoning_leak(ANCHOR_0018), q), "")

    def test_complete_block_is_removed_in_place(self):
        self.assertEqual(
            LLMRouter._strip_reasoning_leak(
                "Yes. <think>because systemctl said so</think> It is active."),
            "Yes.   It is active.")

    def test_unclosed_opener_drops_the_rest(self):
        self.assertEqual(
            LLMRouter._strip_reasoning_leak(
                "Here is the answer. <think>wait, let me reconsider forever"),
            "Here is the answer.")

    def test_reply_that_is_only_a_thinking_block_strips_to_nothing(self):
        self.assertEqual(
            LLMRouter._strip_reasoning_leak("<think>hmm let me see</think>"), "")

    def test_text_without_a_reasoning_tag_is_untouched(self):
        # The load-bearing false-positive direction: 440 of the 441 sealed
        # replies carry no tag, and none of them may be altered.
        for sample in [
            "NetworkManager is running.",
            "Run `pkm install htop` — it lands in /usr/bin/htop.",
            "Here is the `df -h` output:\n\n```\n/dev/root 982G 64G 868G 7% /\n```",
            "I thought about it and the answer is 42.",   # the word, not the tag
            "Use the <code>think</code> helper.",          # a different tag
        ]:
            self.assertEqual(LLMRouter._strip_reasoning_leak(sample), sample)

    def test_anchor_text_matches_the_sealed_item_if_present(self):
        """Guard the verbatim copy above against drift from the sealed set.

        The sealed anchor set is evidence, not tree content, so its location is
        given by INTERGEN_ANCHOR_SET_DIR (the directory holding items/). The test
        SKIPS when that is unset or the item is absent, so it never depends on a
        particular machine's layout and never blocks a run that has no copy.
        """
        root = os.environ.get("INTERGEN_ANCHOR_SET_DIR")
        if not root:
            self.skipTest("INTERGEN_ANCHOR_SET_DIR not set — sealed set not here")
        p = pathlib.Path(root) / "items" / "anchor-0018.json"
        if not p.exists():
            self.skipTest(f"anchor-0018 not present under {root}")
        self.assertEqual(json.loads(p.read_text())["response_text"], ANCHOR_0018)


class ReasoningLeakServingTests(unittest.TestCase):
    """The leak must not reach the user through the serving ladder."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.msgs = [Message(role=MessageRole.USER,
                             content="Is NetworkManager running?")]

    def _stream_returning(self, *replies):
        calls = {"n": 0}

        def _stream(*a, **k):
            self.llm._last_reasoning = ""
            self.llm._last_finish_reason = "stop"
            reply = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return iter([reply])

        return calls, _stream

    def test_the_measured_reply_is_served_clean_and_is_not_retried(self):
        calls, stream = self._stream_returning(ANCHOR_0018)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(resp.text, ANCHOR_0018_ANSWER)
        self.assertNotIn("</think>", resp.text)
        self.assertEqual(calls["n"], 1,
                         "the remainder is a good answer — no retry is owed")

    def test_only_a_thinking_block_serves_the_fallback_not_the_block(self):
        block = "<think>" + ("the user asked about the network " * 20) + "</think>"
        calls, stream = self._stream_returning(block, block)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 2, "an empty remainder must be retried")
        self.assertEqual(resp.text, self.llm._EMPTY_RESPONSE_FALLBACK)
        self.assertNotIn("<think>", resp.text)
        self.assertFalse(resp.quality_passed)

    def test_a_leaked_reply_then_a_clean_retry_serves_the_retry(self):
        good = "NetworkManager is running."
        calls, stream = self._stream_returning("<think>thinking</think>", good)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.text, good)

    def test_agentic_path_strips_the_leak_too(self):
        # The synthesis path gained the same gate in release 154; it must also
        # see stripped text, or the leak simply moves to the tool-narration lane.
        with mock.patch.object(self.llm, "_parse_sse_stream",
                               lambda resp: iter([ANCHOR_0018])):
            with mock.patch("urllib.request.urlopen",
                            mock.MagicMock(return_value=mock.MagicMock())):
                text = self.llm._synthesis_attempt({"max_tokens": 400})
        self.assertEqual(text, ANCHOR_0018_ANSWER)
        self.assertNotIn("</think>", text)

    def test_reasoning_channel_recovery_still_surfaces_an_untagged_answer(self):
        # _recover_empty_content exists because some models put the whole real
        # answer in reasoning_content. That answer carries no tags, so the strip
        # must leave it alone — otherwise the strip breaks a working recovery.
        recovered = "NetworkManager.service is active (running)."

        def _stream(*a, **k):
            self.llm._last_reasoning = recovered
            self.llm._last_finish_reason = "stop"
            return iter([""])

        with mock.patch.object(self.llm, "stream", _stream):
            resp = self.llm.chat(self.msgs)
        self.assertEqual(resp.text, recovered)


if __name__ == "__main__":
    unittest.main()
