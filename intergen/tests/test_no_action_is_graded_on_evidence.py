# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A no-action question is graded on evidence of action, not on a route label.

WHAT WAS MEASURED. The battery asks one question whose own text forbids doing
anything — arithmetic, "Reply with the number only. Do not use tools, run
commands, access files, or contact external services." On 2026-09-18, on two
installed machines, that turn was answered "391" with:

  * no action frame on the wire at all (no gate prompt, no tool acknowledgement,
    no tool execution),
  * the target's own delivery row reading tool_calls 0,
  * and answer_linkage.tool empty — the answer came from the model.

and it was FAILED, because the route carried the label "llm_tools". Being
OFFERED tool descriptions is not using one. On a tier-2 machine in native
posture (dispatch unlocked) the web path labels an ordinary model answer
llm_tools, and llm_freeform is not reachable there at all, so the rule was
failing a turn for the machine's posture rather than for anything the turn did.
A grader that disagrees with its own evidence teaches a reader to distrust the
evidence, not the grader.

WHAT THE RULE IS NOW. Three checks, every one of them evidence of an action:
an ACTION FRAME on the wire; the route "decomposed", which IS an action plan
the person did not ask for; and the target's own delivery row, where any
counted tool call or an answer linked to a named tool fails the turn. Nothing
asks what the turn was allowed to do.

THE FIXTURE BELOW IS A REAL RECORD, not an invented one: the q08 frames and
delivery row from the sealed battery record taken on this project's own
workstation against installed intergen 0.1.0-269 on 2026-09-18. The session
listings are reduced to their type, and per-frame client and timestamp fields
are dropped, because the grader reads neither; everything the grader reads is
verbatim.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from intergen.tests.web_battery import QUESTIONS, _grade

Q08_FRAMES = [
    {
        "type": "session_list"
    },
    {
        "type": "turn_ack",
        "turn_id": "a0347efa49bec74f"
    },
    {
        "type": "stream_start",
        "turn_id": "a0347efa49bec74f",
        "source": "llm_tools",
        "model_name": "local"
    },
    {
        "type": "stream_token",
        "turn_id": "a0347efa49bec74f",
        "token": "3"
    },
    {
        "type": "stream_token",
        "turn_id": "a0347efa49bec74f",
        "token": "9"
    },
    {
        "type": "stream_token",
        "turn_id": "a0347efa49bec74f",
        "token": "1"
    },
    {
        "type": "stream_end",
        "turn_id": "a0347efa49bec74f",
        "full_response": "391",
        "source": "llm_tools",
        "used_llm": True,
        "escalated": False,
        "confidence": 0.0,
        "escalation_offer": None,
        "stats": {
            "total_ms": 451.9,
            "tokens": 3,
            "tool_calls_count": 0
        }
    },
    {
        "type": "session_list"
    }
]

Q08_DELIVERY_ROW = {
    "phase": "delivery",
    "event": "final",
    "turn_id": "a0347efa49bec74f",
    "detail": {
        "iface": "web",
        "text": "391",
        "source": "llm_tools",
        "streamed": True,
        "stream_chunks": 3,
        "tool_calls": 0,
        "answer_linkage": {
            "kind": "model",
            "tool": "",
            "call_id": "",
            "renderer": "llm_stream"
        }
    }
}


def _q08():
    q = next(q for q in QUESTIONS if q.key == "q08")
    assert q.no_action, "q08 is the battery's no-action question"
    return q


@dataclass
class FakeTurn:
    """The shape _grade reads off a collected turn."""
    messages: list[dict]
    text: str = "391"
    terminal: bool = True
    closed_by: str = "client"
    elapsed_s: float = 2.38
    # What the harness measures about the end of the turn. A double that
    # leaves these out makes the grader read a measurement nobody took, so
    # they are part of the shape rather than optional extras.
    terminal_at: float | None = 2.31
    late_frames: int = 0


def _turn(frames=None) -> FakeTurn:
    import copy
    return FakeTurn(messages=copy.deepcopy(frames or Q08_FRAMES))


def _glass(row=None) -> dict[str, list[dict]]:
    import copy
    r = copy.deepcopy(row or Q08_DELIVERY_ROW)
    return {r["turn_id"]: [r]}


class TheRealRecordPassesTest(unittest.TestCase):
    """RED before this change: FAIL, with reason
    "tool/decomposition route on a no-action question: 'llm_tools'"."""

    def test_the_turn_that_took_no_action_passes(self) -> None:
        v = _grade(_q08(), _turn(), _glass())
        self.assertEqual(v.verdict, "PASS", v.reasons)
        self.assertEqual(v.reasons, [])

    def test_and_it_is_still_credited_to_the_model(self) -> None:
        """The question is also model-required; narrowing the action rule must
        not quietly change where the answer is judged to have come from."""
        v = _grade(_q08(), _turn(), _glass())
        self.assertEqual(v.origin, "model")
        self.assertEqual(v.source, "llm_tools")
        self.assertEqual(v.glass_corroborated, "yes")

    def test_the_answer_is_the_right_number(self) -> None:
        self.assertEqual(_turn().text, "391")


class EvidenceOfActionStillFailsTest(unittest.TestCase):
    """The positive controls. A rule that passes the real record is worth
    nothing unless it still fails a record that shows an action."""

    def test_a_tool_execution_frame_fails(self) -> None:
        frames = [dict(f) for f in Q08_FRAMES]
        frames.insert(-1, {"type": "tool_executed",
                           "turn_id": "a0347efa49bec74f", "tool": "run_command"})
        v = _grade(_q08(), _turn(frames), _glass())
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("action frames" in r for r in v.reasons), v.reasons)

    def test_a_tool_acknowledgement_frame_fails(self) -> None:
        frames = [dict(f) for f in Q08_FRAMES]
        frames.insert(3, {"type": "tool_ack", "turn_id": "a0347efa49bec74f"})
        v = _grade(_q08(), _turn(frames), _glass())
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("action frames" in r for r in v.reasons), v.reasons)

    def test_a_gate_prompt_fails(self) -> None:
        frames = [dict(f) for f in Q08_FRAMES]
        frames.insert(3, {"type": "gate_prompt", "turn_id": "a0347efa49bec74f"})
        v = _grade(_q08(), _turn(frames), _glass())
        self.assertEqual(v.verdict, "FAIL")

    def test_a_counted_tool_call_in_the_delivery_row_fails(self) -> None:
        """No frame shows it, and it still fails — this is the check the frames
        alone could not make."""
        import copy
        row = copy.deepcopy(Q08_DELIVERY_ROW)
        row["detail"]["tool_calls"] = 1
        v = _grade(_q08(), _turn(), _glass(row))
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("tool call(s) recorded" in r for r in v.reasons),
                        v.reasons)

    def test_an_answer_linked_to_a_tool_fails(self) -> None:
        import copy
        row = copy.deepcopy(Q08_DELIVERY_ROW)
        row["detail"]["answer_linkage"]["tool"] = "run_command"
        row["detail"]["answer_linkage"]["kind"] = "dispatch"
        v = _grade(_q08(), _turn(), _glass(row))
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("linked to the tool" in r for r in v.reasons),
                        v.reasons)

    def test_a_decomposed_route_still_fails(self) -> None:
        """A decomposition IS an action plan: the turn was split into clauses to
        carry out, and this question asked for none."""
        frames = [dict(f) for f in Q08_FRAMES]
        for f in frames:
            if f.get("type") in ("stream_start", "stream_end"):
                f["source"] = "decomposed"
        import copy
        row = copy.deepcopy(Q08_DELIVERY_ROW)
        row["detail"]["source"] = "decomposed"
        v = _grade(_q08(), _turn(frames), _glass(row))
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("decomposition route" in r for r in v.reasons),
                        v.reasons)


class TheLabelAloneNoLongerFailsTest(unittest.TestCase):
    """The narrowing, stated on its own so it cannot be lost in a refactor."""

    def test_the_llm_tools_label_is_not_by_itself_a_failure(self) -> None:
        v = _grade(_q08(), _turn(), _glass())
        self.assertEqual(v.source, "llm_tools")
        self.assertEqual(v.verdict, "PASS")
        self.assertFalse(any("llm_tools" in r for r in v.reasons), v.reasons)

    def test_a_question_that_is_not_no_action_is_untouched(self) -> None:
        """Every other question in the battery is graded exactly as before."""
        q12 = next(q for q in QUESTIONS if q.key == "q12")
        self.assertFalse(q12.no_action)
        frames = [dict(f) for f in Q08_FRAMES]
        frames.insert(3, {"type": "tool_ack", "turn_id": "a0347efa49bec74f"})
        v = _grade(q12, _turn(frames), _glass())
        self.assertFalse(any("no-action question" in r for r in v.reasons),
                         v.reasons)


if __name__ == "__main__":
    unittest.main()
