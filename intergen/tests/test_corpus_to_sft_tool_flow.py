# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""tool_flow turns: training the post-dispatch composition faithfully.

The approval-flow and deny-recovery training classes target the reply the model
composes AFTER a dispatch resolves — the serving path is
``LLMRouter.continue_after_tool_call``, whose message shape is: the original
user turn, an assistant message carrying the structured tool call, a tool-role
message carrying the result (or the refusal reason), and a user-role synthesis
instruction chosen by (success, executed). Training that behaviour on plain
user/assistant pairs would teach a different distribution than serving — the
exact class the emitter exists to refuse — so the bank gains a ``tool_flow``
turn shape and the emitter renders it through the REAL serving pieces:
``LLMRouter._synthesis_prompt`` for the instruction, never a copy of its text.

Covered here:
  1. render shape for a DENY flow: [system, user, assistant+tool_calls, tool,
     user(the binding NOT-executed instruction), assistant gold] — the
     instruction text is the real function's output;
  2. render shape for a SUCCESS flow uses the plain synthesis instruction;
  3. the tool_call inside tool_flow is validated like dispatch gold (unknown
     tool refused, missing source_of_request refused);
  4. tool_result must be a non-empty string;
  5. a tool_flow turn's gold must be prose content (a tool_call gold there is
     refused — the flow turn trains composition, not emission);
  6. success/executed must both be present booleans.
"""
from __future__ import annotations

import unittest

from intergen.llm import LLMRouter
from intergen.tests.corpus_loader import CorpusError
from intergen.tests import corpus_to_sft as sft

SCHEMAS = {
    "manage_services": {
        "type": "object",
        "properties": {"action": {"type": "string"},
                       "service": {"type": "string"},
                       "user_mode": {"type": "boolean"}},
        "required": ["action"],
    },
}

DENY_RESULT = "Tool call denied by user via review modal."
OK_RESULT = "Restarted cups: active (running) since 17:02"


def entry(turns):
    return {
        "id": "tf-0001", "category": "service_management",
        "intent": "approval flow", "expected_behavior_class": "should-dispatch",
        "turns": turns,
        "provenance": {"generator": "g", "lens": "l", "grounding": ["k"],
                       "method": "m"},
        "training_provenance": {"class": "class2-approval-flow",
                                "origin": "authored"},
    }


def flow_turn(*, result=DENY_RESULT, success=False, executed=False,
              gold=None, call=None):
    return {
        "user": "restart cups for me",
        "tool_flow": {
            "tool_call": call or {"name": "manage_services",
                                  "arguments": {"action": "restart",
                                                "service": "cups",
                                                "source_of_request": "user_direct"}},
            "tool_result": result,
            "success": success,
            "executed": executed,
        },
        "gold": gold or {"content": "I wasn't able to restart cups — it needs "
                                    "your approval. Say the word and I'll do it."},
    }


def emit_one(turn):
    samples = []
    obj = entry([turn])
    sft.validate_training_entry(obj, locator="<t>", tool_schemas=SCHEMAS)
    samples.append(sft.entry_to_sample(obj, system_prompt="SYSPROMPT"))
    return samples[0]


class ToolFlowRenderTests(unittest.TestCase):
    def test_deny_flow_renders_the_real_serving_shape(self):
        s = emit_one(flow_turn())
        roles = [m["role"] for m in s["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool",
                                 "user", "assistant"])
        asst = s["messages"][2]
        self.assertIsNone(asst["content"])
        fn = asst["tool_calls"][0]["function"]
        self.assertEqual(fn["name"], "manage_services")
        # Strengthened 2026-08-13. This test asserted the call's NAME and
        # stopped, so it passed for months while the arguments were emitted as
        # a JSON string — the shape the chat template renders with every
        # argument dropped. A test that cannot fail on its own subject is the
        # reason that defect reached a trained model.
        self.assertIsInstance(fn["arguments"], dict)
        self.assertEqual(fn["arguments"]["service"], "cups")
        self.assertEqual(fn["arguments"]["action"], "restart")
        self.assertEqual(s["messages"][3]["content"], DENY_RESULT)
        # The instruction is the REAL function's output for a deny.
        expect = LLMRouter._synthesis_prompt(
            object.__new__(LLMRouter), success=False, executed=False)
        self.assertEqual(s["messages"][4]["content"], expect)
        self.assertIn("NOT executed", s["messages"][4]["content"])
        self.assertIn("approval", s["messages"][5]["content"])

    def test_success_flow_uses_the_plain_instruction(self):
        s = emit_one(flow_turn(result=OK_RESULT, success=True, executed=True,
                               gold={"content": "Done — `cups` is back up "
                                                "and running."}))
        expect = LLMRouter._synthesis_prompt(
            object.__new__(LLMRouter), success=True, executed=True)
        self.assertEqual(s["messages"][4]["content"], expect)
        self.assertNotIn("NOT executed", s["messages"][4]["content"])

    def test_unknown_tool_in_flow_is_refused(self):
        t = flow_turn(call={"name": "no_such_tool",
                            "arguments": {"source_of_request": "user_direct"}})
        with self.assertRaises(CorpusError):
            emit_one(t)

    def test_missing_source_of_request_in_flow_is_refused(self):
        t = flow_turn(call={"name": "manage_services",
                            "arguments": {"action": "restart",
                                          "service": "cups"}})
        with self.assertRaises(CorpusError):
            emit_one(t)

    def test_empty_tool_result_is_refused(self):
        with self.assertRaises(CorpusError):
            emit_one(flow_turn(result="   "))

    def test_flow_gold_must_be_prose_not_a_tool_call(self):
        t = flow_turn(gold={"tool_call": {
            "name": "manage_services",
            "arguments": {"action": "restart", "service": "cups",
                          "source_of_request": "user_direct"}}})
        with self.assertRaises(CorpusError):
            emit_one(t)

    def test_missing_success_or_executed_is_refused(self):
        t = flow_turn()
        del t["tool_flow"]["executed"]
        with self.assertRaises(CorpusError):
            emit_one(t)

    def test_distribution_report_counts_flow_samples(self):
        # The report walks assistant messages; a tool_flow sample's history
        # assistant carries content None (the structured call), which must be
        # counted as a dispatch-history turn — not crash the report.
        s = emit_one(flow_turn())
        report = sft.distribution_report([s])
        self.assertIn("class class2-approval-flow: 1", report)
        self.assertIn("dispatch-history 1", report)


if __name__ == "__main__":
    unittest.main()
