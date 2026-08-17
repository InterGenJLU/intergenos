# SPDX-License-Identifier: GPL-3.0-or-later
"""The agentic tool-synthesis path must pass the same quality gate as the rest.

continue_after_tool_call() is the serving path taken whenever the model narrates
a tool's result. Until this change it returned the model's text to the user
after only an is-it-empty check: it never called check_quality, so every shape
the serving floor rejects on the conversational path — output that is not
language, a repetition blowup, an echo of the question, a template artifact, a
reply cut off at the token cap — reached the user unchecked on exactly the path
where a tool had just run.

These pin the gate on that path:
  1. the measured non-linguistic reply is rejected there too, and rejection
     means the caller gets None so it can serve the tool's OWN result;
  2. the ladder is the same shape — one retry, and a good second attempt is what
     reaches the user;
  3. a good first attempt is not retried (a working turn pays nothing);
  4. the gate's other reasons (repetitive, echo, artifacts) apply here as well;
  5. an empty generation is now RETRIED with more room rather than abandoned,
     and a truncated one likewise;
  6. a transport failure is still a single None with no retry — retrying a
     request that could not be made buys nothing.

The garbage string is verbatim from the sealed baseline capture of 2026-08-07
(2B tier, trace c215bca41ac7), the same reply that motivated the serving-floor
detector in release 149.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import Message, MessageRole, ToolCall
from intergen.interfaces.provenance import Provenance

# Verbatim from the sealed baseline run, 2B tier, trace c215bca41ac7.
HTOP_GARBAGE = '"""""，""""##""\n\n"-" \n\n\n\n\n<\nn"\n\n\n\n<\n"\n\n"\n"\n"\n####\n\n""##\n、"\n""\n\n"、 \n\n"， \n""\n"\n\n""\n，##  \n  \n\n\n \n####\n\n\n\n\n \n\n\n\n\n\n\n\n，\n\n\n\n\n"\n\n"##\n  \n  \n\n\n\n\n\n"\n"'

GOOD = "htop is not installed. I can install it with `pkm install htop`."


def _tool_call() -> ToolCall:
    return ToolCall(name="check_package", arguments={"name": "htop"},
                    call_id="call_0",
                    source_of_request=Provenance.USER_DIRECT)


class AgenticQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"
        self.msgs = [Message(role=MessageRole.USER, content="get me htop")]
        self.call = _tool_call()

    def _attempts(self, *texts):
        """Patch the single-generation helper to return these texts in order."""
        calls = {"n": 0}

        def _attempt(payload):
            text = texts[min(calls["n"], len(texts) - 1)]
            calls["n"] += 1
            return text

        return calls, _attempt

    def _synthesize(self):
        return self.llm.continue_after_tool_call(
            self.msgs, self.call, "htop: not installed")

    def test_degenerate_synthesis_is_rejected_and_retried(self):
        calls, attempt = self._attempts(HTOP_GARBAGE, HTOP_GARBAGE)
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2, "a rejected synthesis must be retried once")
        self.assertIsNone(
            resp, "twice-degenerate synthesis must return None so the caller "
                  "serves the tool's own result")

    def test_good_retry_reaches_the_user(self):
        calls, attempt = self._attempts(HTOP_GARBAGE, GOOD)
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.text, GOOD)
        self.assertTrue(resp.quality_passed)

    def test_good_first_attempt_is_not_retried(self):
        calls, attempt = self._attempts(GOOD)
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 1, "a good synthesis must not pay for a retry")
        self.assertEqual(resp.text, GOOD)

    def test_repetitive_synthesis_is_rejected(self):
        calls, attempt = self._attempts("yes " * 40)
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2)
        self.assertIsNone(resp)

    def test_echo_of_the_question_is_rejected(self):
        calls, attempt = self._attempts("get me htop")
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2)
        self.assertIsNone(resp)

    def test_template_artifacts_are_rejected(self):
        calls, attempt = self._attempts(
            "<think>the user wants htop</think> htop is not installed.")
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2)
        self.assertIsNone(resp)

    def test_empty_generation_is_retried_with_more_room(self):
        seen = []

        def _attempt(payload):
            seen.append(payload["max_tokens"])
            return "" if len(seen) == 1 else GOOD

        with mock.patch.object(self.llm, "_synthesis_attempt", _attempt):
            resp = self._synthesize()
        self.assertEqual(len(seen), 2, "an empty synthesis must be retried, not "
                                       "abandoned on the first attempt")
        self.assertEqual(seen[1], seen[0] * 2,
                         "the retry must give the model more room")
        self.assertEqual(resp.text, GOOD)

    def test_truncated_generation_is_retried_with_more_room(self):
        seen = []

        def _attempt(payload):
            seen.append(payload["max_tokens"])
            # A reply cut off at the cap: the text itself looks fine.
            self.llm._last_finish_reason = "length" if len(seen) == 1 else "stop"
            return "htop is not installed and I was about to say" if len(seen) == 1 else GOOD

        with mock.patch.object(self.llm, "_synthesis_attempt", _attempt):
            resp = self._synthesize()
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], seen[0] * 2)
        self.assertEqual(resp.text, GOOD)

    def test_transport_failure_returns_none_without_retrying(self):
        calls = {"n": 0}

        def _attempt(payload):
            calls["n"] += 1
            return None

        with mock.patch.object(self.llm, "_synthesis_attempt", _attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 1,
                         "a request that could not be made must not be retried")
        self.assertIsNone(resp)


class OneGateNotTwoTests(unittest.TestCase):
    """Both serving paths must consult the SAME predicate, not two copies."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"

    def test_both_paths_call_the_same_gate(self):
        seen = []
        real = LLMRouter._gate_reason

        def _spy(router, text, user_msg):
            seen.append(text)
            return real(router, text, user_msg)

        with mock.patch.object(LLMRouter, "_gate_reason", _spy):
            with mock.patch.object(self.llm, "_synthesis_attempt",
                                   lambda payload: GOOD):
                self.llm.continue_after_tool_call(
                    [Message(role=MessageRole.USER, content="get me htop")],
                    _tool_call(), "htop: not installed")
        self.assertEqual(seen, [GOOD],
                         "the agentic path must reach the shared gate")

    def test_gate_reason_flags_truncation_off_the_finish_reason(self):
        # The rule that cannot live inside check_quality, pinned on the shared
        # function so neither path can lose it.
        self.llm._last_finish_reason = "length"
        self.assertEqual(
            self.llm._gate_reason("A perfectly ordinary sentence that stops", "hi"),
            "truncated")
        self.llm._last_finish_reason = "stop"
        self.assertEqual(
            self.llm._gate_reason("A perfectly ordinary sentence that stops", "hi"),
            "")


if __name__ == "__main__":
    unittest.main()
