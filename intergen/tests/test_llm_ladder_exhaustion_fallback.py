# SPDX-License-Identifier: GPL-3.0-or-later
"""A reply the gate NAMED as unservable must not be served when retries run out.

Release 149 gave the honest fallback to exactly one reason: output measured as
not-language (plus the empty case). Every other reason the quality gate produces
— a repetition blowup, an echo of the question, a template artifact, a reply cut
off at the token cap — still had its text SERVED once the retry ladder was spent.
The gate named the defect and then handed the user the defective reply anyway,
which is the silent failure the gate exists to end.

This pins the rule for the WHOLE reason set: if the gate rejected both attempts,
what ships is the honest fallback sentence, never the rejected text.

The agentic path is deliberately NOT changed here — its exhausted-ladder answer
is None so the caller delivers the tool's OWN result, which is better than any
generic sentence and was ruled so in release 154. This file is the conversational
path.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import EscalationMode, Message, MessageRole

# Verbatim from the sealed baseline run, 2B tier, trace c215bca41ac7.
HTOP_GARBAGE = '"""""，""""##""\n\n"-" \n\n\n\n\n<\nn"\n\n\n\n<\n"\n\n"\n"\n"\n####\n\n""##\n、"\n""\n\n"、 \n\n"， \n""\n"\n\n""\n，##  \n  \n\n\n \n####\n\n\n\n\n \n\n\n\n\n\n\n\n，\n\n\n\n\n"\n\n"##\n  \n  \n\n\n\n\n\n"\n"'

QUESTION = "Is sshd enabled?"
GOOD = "sshd is enabled and running."


class LadderExhaustionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMRouter()
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.msgs = [Message(role=MessageRole.USER, content=QUESTION)]

    def _stream_returning(self, *replies, finish="stop"):
        calls = {"n": 0}

        def _stream(*a, **k):
            self.llm._last_reasoning = ""
            self.llm._last_finish_reason = finish
            reply = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return iter([reply])

        return calls, _stream

    def _serve(self, *replies, finish="stop"):
        calls, stream = self._stream_returning(*replies, finish=finish)
        with mock.patch.object(self.llm, "stream", stream):
            resp = self.llm.chat(self.msgs)
        return calls, resp

    def _assert_fallback(self, resp, rejected_text, reason):
        self.assertEqual(
            resp.text, self.llm._EMPTY_RESPONSE_FALLBACK,
            f"a twice-rejected ({reason}) reply must ship the honest fallback")
        self.assertNotIn(rejected_text.strip()[:24], resp.text)
        self.assertFalse(resp.quality_passed)

    def test_repetitive_reply_is_replaced_by_the_fallback(self):
        text = "yes " * 40
        calls, resp = self._serve(text, text)
        self.assertEqual(calls["n"], 2)
        self._assert_fallback(resp, text, "repetitive")

    def test_echo_of_the_question_is_replaced_by_the_fallback(self):
        calls, resp = self._serve(QUESTION, QUESTION)
        self.assertEqual(calls["n"], 2)
        self._assert_fallback(resp, QUESTION, "echo")

    def test_template_artifact_reply_is_replaced_by_the_fallback(self):
        text = "<|im_start|>assistant sshd is enabled."
        calls, resp = self._serve(text, text)
        self.assertEqual(calls["n"], 2)
        self._assert_fallback(resp, "<|im_start|>", "artifacts")
        self.assertNotIn("<|im_start|>", resp.text,
                         "a template artifact must never reach the user")

    def test_truncated_reply_is_replaced_by_the_fallback(self):
        # A reply cut off at the token cap is real language, but the gate has
        # named it incomplete twice over. Serving a sentence that stops
        # mid-thought as if it were the answer is the silent failure.
        text = "sshd is enabled and I was about to say"
        calls, resp = self._serve(text, text, finish="length")
        self.assertEqual(calls["n"], 2)
        self._assert_fallback(resp, text, "truncated")

    def test_degenerate_reply_keeps_the_release_149_behavior(self):
        calls, resp = self._serve(HTOP_GARBAGE, HTOP_GARBAGE)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.text, self.llm._EMPTY_RESPONSE_FALLBACK)
        self.assertFalse(resp.quality_passed)

    def test_a_good_reply_is_untouched(self):
        calls, resp = self._serve(GOOD)
        self.assertEqual(calls["n"], 1, "a good reply pays for no retry")
        self.assertEqual(resp.text, GOOD)

    def test_a_good_retry_still_reaches_the_user(self):
        calls, resp = self._serve(HTOP_GARBAGE, GOOD)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.text, GOOD,
                         "the fallback replaces a rejected reply, never a "
                         "reply the gate passed")
        self.assertTrue(resp.quality_passed)


class ServableTextContractTests(unittest.TestCase):
    """The predicate itself: a named reason means the text is not servable."""

    def setUp(self):
        self.llm = LLMRouter()

    def test_every_reason_yields_the_fallback(self):
        for reason in ("degenerate", "empty", "repetitive", "echo",
                       "artifacts", "truncated"):
            with self.subTest(reason=reason):
                self.assertEqual(
                    self.llm._servable_text("some rejected text", reason),
                    self.llm._EMPTY_RESPONSE_FALLBACK)

    def test_an_unnamed_reason_still_yields_the_fallback(self):
        # A reason added later must not silently fall through to "serve it".
        self.assertEqual(
            self.llm._servable_text("some rejected text", "a_future_reason"),
            self.llm._EMPTY_RESPONSE_FALLBACK)

    def test_no_reason_serves_the_text(self):
        self.assertEqual(self.llm._servable_text(GOOD, ""), GOOD)

    def test_no_reason_but_empty_text_yields_the_fallback(self):
        self.assertEqual(self.llm._servable_text("   ", ""),
                         self.llm._EMPTY_RESPONSE_FALLBACK)


if __name__ == "__main__":
    unittest.main()
