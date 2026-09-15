# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The web battery's grading is strict, and its refusals fire before a record.

The battery (``intergen/tests/web_battery.py``) is the RED for the web-chat
banner defect and the proof for every later assistant fix, so its verdicts
must be exact: each predicate the module docstring names is pinned here on a
synthetic turn shaped like the real frames, with no daemon, no socket and no
model. The checkout refusal is pinned by running the entry point from this
very checkout — the case it exists to refuse.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from intergen.tests import web_battery as wb


def _turn(messages, *, text="", terminal=True, closed_by="client",
          elapsed=1.0):
    return SimpleNamespace(messages=messages, text=text, terminal=terminal,
                           closed_by=closed_by, elapsed_s=elapsed,
                           events=[(0.0, m.get("type")) for m in messages])


def _model_turn(turn_id="t1", source="llm_freeform", tokens=3,
                text="Linux was first released in 1991."):
    frames = [{"type": "turn_ack", "turn_id": turn_id},
              {"type": "stream_start", "turn_id": turn_id, "source": source,
               "model_name": "local"}]
    frames += [{"type": "stream_token", "turn_id": turn_id, "token": tok}
               for tok in text.split(" ")[:tokens]]
    frames.append({"type": "stream_end", "turn_id": turn_id,
                   "full_response": text, "source": source, "used_llm": True,
                   "escalated": False, "confidence": None,
                   "escalation_offer": None,
                   "stats": {"total_ms": 900.0, "tokens": tokens,
                             "tool_calls_count": 0}})
    return _turn(frames, text=text)


def _glass_model(turn_id="t1", kind="model", streamed=True):
    return {turn_id: [{"turn_id": turn_id, "phase": "delivery",
                       "event": "final",
                       "detail": {"iface": "web", "streamed": streamed,
                                  "answer_linkage": {"kind": kind}}}]}


FACT = wb.Question("q", "fact", "What year was Linux first released?",
                   model_required=True)
GREETING = wb.Question("q", "greeting", "hi", model_required=False)
ROW22 = wb.Question("q", "row-22-verbatim", wb.ROW22_SENTENCE,
                    model_required=True, no_action=True)


class GradingPredicates(unittest.TestCase):

    def test_a_model_answer_with_its_trace_row_passes(self):
        v = wb._grade(FACT, _model_turn(), _glass_model())
        self.assertEqual(v.verdict, "PASS", v.reasons)
        self.assertEqual(v.origin, "model")
        self.assertEqual(v.glass_corroborated, "yes")

    def test_an_error_frame_fails(self):
        frames = [{"type": "turn_ack", "turn_id": "t1"},
                  {"type": "error", "code": "internal_error",
                   "message": wb.BANNER_TEXT + " Could you try again?"}]
        r = _turn(frames, text="[error] {...}")
        v = wb._grade(FACT, r, {})
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any(s.startswith("error frame") for s in v.reasons),
                        v.reasons)
        self.assertEqual(v.origin, "error")

    def test_the_banner_text_in_a_delivered_answer_fails(self):
        r = _model_turn(text=wb.BANNER_TEXT + " Could you try again?")
        v = wb._grade(GREETING, r, _glass_model())
        self.assertEqual(v.verdict, "FAIL")
        self.assertIn("red-banner text delivered", v.reasons)

    def test_a_refusal_source_fails_even_for_a_greeting(self):
        frames = [{"type": "turn_ack", "turn_id": "t1"},
                  {"type": "response", "turn_id": "t1",
                   "content": "Something went wrong on my side: I could not "
                              "tell which conversation this message belongs "
                              "to, so I have not answered it.",
                   "source": "conversation_unbound", "handled": False}]
        r = _turn(frames, text=frames[1]["content"])
        v = wb._grade(GREETING, r, {})
        self.assertEqual(v.verdict, "FAIL")
        self.assertEqual(v.origin, "refusal")

    def test_the_empty_completion_nudge_is_not_an_answer(self):
        r = _model_turn(text="Sorry — I didn't quite catch that. Could you "
                             "rephrase it for me?")
        v = wb._grade(FACT, r, _glass_model())
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("canned non-answer" in s for s in v.reasons))

    def test_a_code_owned_answer_is_fine_for_a_greeting_but_not_a_fact(self):
        frames = [{"type": "turn_ack", "turn_id": "t1"},
                  {"type": "response", "turn_id": "t1",
                   "content": "Hi! I'm InterGen.", "source": "identity",
                   "handled": True}]
        r = _turn(frames, text="Hi! I'm InterGen.")
        self.assertEqual(wb._grade(GREETING, r, {}).verdict, "PASS")
        v = wb._grade(FACT, r, {})
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("model-required" in s for s in v.reasons))

    def test_row_22_fails_on_a_gate_prompt_and_on_a_tool_route(self):
        r = _model_turn(source="llm_tools")
        r.messages.insert(2, {"type": "tool_ack", "turn_id": "t1",
                              "text": "On it."})
        r.messages.insert(3, {"type": "gate_prompt", "turn_id": "t1",
                              "tool_call_id": "c1"})
        v = wb._grade(ROW22, r, _glass_model())
        self.assertEqual(v.verdict, "FAIL")
        joined = " ".join(v.reasons)
        self.assertIn("action frames", joined)
        self.assertIn("llm_tools", joined)

    def test_row_22_fails_on_a_decomposition(self):
        frames = [{"type": "turn_ack", "turn_id": "t1"},
                  {"type": "response", "turn_id": "t1",
                   "content": "I see two things you'd like done.",
                   "source": "decomposed", "handled": True}]
        r = _turn(frames, text=frames[1]["content"])
        v = wb._grade(ROW22, r, {})
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("decomposition" in s for s in v.reasons))

    def test_frames_alone_do_not_prove_model_origin_when_the_trace_disagrees(self):
        # The frames say model; the target's trace has no streamed delivery for
        # that turn id. The second witness wins: origin is not "model".
        v = wb._grade(FACT, _model_turn(), {"t1": []})
        self.assertEqual(v.verdict, "FAIL")
        self.assertEqual(v.glass_corroborated, "no")
        self.assertNotEqual(v.origin, "model")

    def test_an_unreadable_trace_grades_on_the_frames_and_says_so(self):
        v = wb._grade(FACT, _model_turn(), None)
        self.assertEqual(v.verdict, "PASS")
        self.assertEqual(v.glass_corroborated, "unreadable")

    def test_no_terminal_frame_fails(self):
        r = _turn([{"type": "turn_ack", "turn_id": "t1"}], terminal=False,
                  closed_by="deadline")
        v = wb._grade(GREETING, r, {})
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(any("no terminal frame" in s for s in v.reasons))


class BatteryShape(unittest.TestCase):

    def test_at_least_twelve_questions_and_the_row_22_sentence_verbatim(self):
        self.assertGreaterEqual(len(wb.QUESTIONS), 12)
        texts = [q.text for q in wb.QUESTIONS]
        self.assertIn(
            "Calculate 17 times 23 mentally. Reply with the number only. Do "
            "not use tools, run commands, access files, or contact external "
            "services.", texts)
        shapes = {q.shape for q in wb.QUESTIONS}
        for needed in ("greeting", "capability", "fact",
                       "follow-up-prior-turn", "live-data-machine",
                       "elliptical", "row-22-verbatim"):
            self.assertIn(needed, shapes)
        row22 = [q for q in wb.QUESTIONS if q.shape == "row-22-verbatim"][0]
        self.assertTrue(row22.no_action and row22.model_required)

    def test_it_refuses_to_run_from_inside_a_checkout(self):
        # This test file lives in a checkout, so the battery's own file does
        # too: the refusal must fire before anything is read or written.
        self.assertIsNotNone(wb._checkout_above(Path(wb.__file__).parent))
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            wb.main(["--target", "nobody@127.0.0.1", "--output",
                     "/nonexistent/web-battery-record", "--tree-sha",
                     "0123456789abcdef"])
        self.assertEqual(cm.exception.code, wb.EXIT_REFUSED)
        self.assertIn("source checkout", err.getvalue())
        self.assertFalse(Path("/nonexistent/web-battery-record").exists())


if __name__ == "__main__":
    unittest.main()
