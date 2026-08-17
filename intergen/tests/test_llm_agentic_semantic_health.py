# SPDX-License-Identifier: GPL-3.0-or-later
"""The agentic tool-synthesis path must be screened for corruption like the rest.

The completion-boundary semantic-health screen (intergen.semantic_health) ran in
exactly one place: stream(). The agentic serving path does not use stream() — it
generates through _synthesis_attempt() — so a tool-narration turn was never
screened, its LLMResponse carried no semantic_flags at all, and a backend serving
structural garbage was invisible on that path. That is the same unscreened-path
class the quality gate closed in release 154, one screen over.

Covered here:
  1. every agentic generation is screened, and the screen sees the RAW generation
     (what stream() screens), not the post-strip text;
  2. the screen's flags reach the caller on LLMResponse.semantic_flags, which is
     the interface contract the engine-side reaction ladder consumes;
  3. a flagged synthesis feeds the SAME retry-then-fallback ladder the quality
     gate uses on this path — one retry, and on exhaustion None, so the caller
     serves the tool's own result;
  4. a clean synthesis is not retried and carries no flags;
  5. a screen failure never breaks the turn;
  6. ONE screen, not two: both serving paths reach the same assess_semantic_health
     predicate.

The corruption specimen is a charset-sanity case (control bytes), which the
shipped screen flags and the text-shape quality gate does not — so these tests
pin the screen specifically, not the gate that already runs here.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.llm import LLMRouter
from intergen.interfaces.types import Message, MessageRole, ToolCall
from intergen.interfaces.provenance import Provenance
from intergen.semantic_health import FLAG_CHARSET, assess_semantic_health

# Ordinary language carrying control bytes — charset_sanity trips, the text-shape
# quality gate does not (it is language, not repetitive, not an echo).
CORRUPT = "NetworkManager\x00 is \x07active and running normally on this host."
GOOD = "NetworkManager.service is active (running)."


def _tool_call() -> ToolCall:
    return ToolCall(name="manage_services", arguments={"action": "status"},
                    call_id="call_0",
                    source_of_request=Provenance.USER_DIRECT)


class AgenticScreenRunsTests(unittest.TestCase):
    """The screen fires on the agentic generation itself."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"

    def _generate(self, text, payload=None):
        with mock.patch.object(self.llm, "_parse_sse_stream",
                               lambda resp: iter([text])):
            with mock.patch("urllib.request.urlopen",
                            mock.MagicMock(return_value=mock.MagicMock())):
                return self.llm._synthesis_attempt(payload or {"max_tokens": 400})

    def test_corrupt_generation_is_flagged_by_the_screen(self):
        self.llm._last_semantic_flags = []
        self._generate(CORRUPT)
        self.assertIn(FLAG_CHARSET, self.llm._last_semantic_flags,
                      "the agentic generation must be screened")

    def test_clean_generation_leaves_no_flags(self):
        self.llm._last_semantic_flags = ["stale_from_a_previous_turn"]
        self._generate(GOOD)
        self.assertEqual(self.llm._last_semantic_flags, [],
                         "a clean agentic generation must clear the flags, not "
                         "inherit the previous turn's")

    def test_the_screen_sees_the_raw_generation(self):
        seen = {}

        def _spy(response_text, *, system_prompt="", conversation_texts=None):
            seen["text"] = response_text
            return assess_semantic_health(
                response_text, system_prompt=system_prompt,
                conversation_texts=conversation_texts)

        with mock.patch("intergen.semantic_health.assess_semantic_health", _spy):
            self._generate("  <think>x</think> " + GOOD + "  ")
        self.assertIn("<think>", seen.get("text", ""),
                      "the screen must see the raw generation, exactly as "
                      "stream() screens its raw completion")

    def test_the_screen_is_given_the_turn_s_own_prompt_and_user_texts(self):
        seen = {}

        def _spy(response_text, *, system_prompt="", conversation_texts=None):
            seen["system_prompt"] = system_prompt
            seen["conversation_texts"] = list(conversation_texts or [])
            return assess_semantic_health(response_text)

        payload = {
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": "SYSTEM PROMPT TEXT"},
                {"role": "user", "content": "Is NetworkManager running?"},
                {"role": "assistant", "content": None, "tool_calls": []},
                {"role": "tool", "tool_call_id": "call_0", "content": "active"},
            ],
        }
        with mock.patch("intergen.semantic_health.assess_semantic_health", _spy):
            self._generate(GOOD, payload)
        self.assertEqual(seen.get("system_prompt"), "SYSTEM PROMPT TEXT",
                         "the echo check needs the live system prompt")
        self.assertIn("Is NetworkManager running?",
                      seen.get("conversation_texts", []),
                      "the foreign-script exemption needs the user's own turns")

    def test_a_screen_failure_never_breaks_the_turn(self):
        def _boom(*a, **k):
            raise RuntimeError("screen exploded")

        with mock.patch("intergen.semantic_health.assess_semantic_health", _boom):
            text = self._generate(GOOD)
        self.assertEqual(text, GOOD, "a screen failure must not cost the answer")
        self.assertEqual(self.llm._last_semantic_flags, [])

    def test_a_transport_failure_is_not_screened(self):
        self.llm._last_semantic_flags = []
        with mock.patch("urllib.request.urlopen",
                        mock.MagicMock(side_effect=OSError("refused"))):
            text = self.llm._synthesis_attempt({"max_tokens": 400})
        self.assertIsNone(text)
        self.assertEqual(self.llm._last_semantic_flags, [],
                         "there is no generation to screen")


class AgenticFlagsReachTheCallerTests(unittest.TestCase):
    """The flags must surface on the response and drive the existing ladder."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"
        self.msgs = [Message(role=MessageRole.USER,
                             content="Is NetworkManager running?")]
        self.call = _tool_call()

    def _attempts(self, *specs):
        """Fake generations as (text, flags) pairs — what a real
        _synthesis_attempt leaves behind: the text, and the screen's verdict on
        it in _last_semantic_flags."""
        calls = {"n": 0}

        def _attempt(payload):
            text, flags = specs[min(calls["n"], len(specs) - 1)]
            calls["n"] += 1
            self.llm._last_semantic_flags = list(flags)
            return text

        return calls, _attempt

    def _synthesize(self):
        return self.llm.continue_after_tool_call(
            self.msgs, self.call, "NetworkManager.service: active")

    def test_clean_response_carries_empty_flags_and_is_not_retried(self):
        calls, attempt = self._attempts((GOOD, []))
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(resp.text, GOOD)
        self.assertEqual(resp.semantic_flags, [])

    def test_flagged_synthesis_is_retried_then_yields_none(self):
        calls, attempt = self._attempts((GOOD, [FLAG_CHARSET]),
                                        (GOOD, [FLAG_CHARSET]))
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2,
                         "a flagged synthesis must be retried once, like any "
                         "other reason this path rejects")
        self.assertIsNone(
            resp, "twice-flagged synthesis must return None so the caller "
                  "serves the tool's own result")

    def test_flagged_then_clean_serves_the_retry_with_its_own_flags(self):
        calls, attempt = self._attempts((GOOD, [FLAG_CHARSET]), (GOOD, []))
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertEqual(calls["n"], 2)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.semantic_flags, [],
                         "the served generation's own verdict is what ships")
        self.assertTrue(resp.quality_passed)

    def test_a_flagged_reply_is_never_served_with_its_flags_attached(self):
        # The whole point: on this path a flagged completion must not reach the
        # user at all. If it ever does, the flags must at least be visible.
        calls, attempt = self._attempts((GOOD, [FLAG_CHARSET]),
                                        (GOOD, [FLAG_CHARSET]))
        with mock.patch.object(self.llm, "_synthesis_attempt", attempt):
            resp = self._synthesize()
        self.assertIsNone(resp)


class OneScreenNotTwoTests(unittest.TestCase):
    """Both serving paths must reach the same corruption predicate."""

    def setUp(self):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"

    def test_both_paths_call_the_same_screen(self):
        seen = []

        def _spy(response_text, *, system_prompt="", conversation_texts=None):
            seen.append(response_text)
            return assess_semantic_health(response_text)

        with mock.patch("intergen.semantic_health.assess_semantic_health", _spy):
            # conversational path
            with mock.patch.object(self.llm, "_parse_sse_stream",
                                   lambda resp: iter([GOOD])):
                with mock.patch("urllib.request.urlopen",
                                mock.MagicMock(return_value=mock.MagicMock())):
                    list(self.llm.stream(
                        [Message(role=MessageRole.USER, content="hi")]))
                    # agentic path
                    self.llm._synthesis_attempt({"max_tokens": 400})
        self.assertEqual(seen, [GOOD, GOOD],
                         "one screen, reached from both serving paths")


if __name__ == "__main__":
    unittest.main()
