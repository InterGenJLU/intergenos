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


FREE_H = (
    "               total        used        free      shared  buff/cache   available\n"
    "Mem:            15Gi       7.5Gi       2.1Gi       4.5Gi        10Gi       7.8Gi\n"
    "Swap:          4.0Gi        19Mi       4.0Gi\n"
)
MEMORY_Q = wb.Question("q07", "elliptical", "And memory?", model_required=False,
                       truth="memory_matches_the_machine", code_required=True)
FIRST_Q = wb.Question("q14", "prior-turn-context",
                      "What was my first question to you?",
                      model_required=False, truth="names_the_first_question",
                      code_required=True)


def _code_turn(text, turn_id="t1", source="direct_answer"):
    frames = [{"type": "turn_ack", "turn_id": turn_id},
              {"type": "response", "turn_id": turn_id, "source": source,
               "text": text}]
    return _turn(frames, text=text)


class CorrectnessPredicateTests(unittest.TestCase):
    """A SERVED answer is not an answer. These predicates read what was said.

    Measured 2026-09-16 against the tree at a51039c4e: this battery scored
    14 PASS / 0 FAIL while the assistant told the person they had "12.1G of
    free RAM out of 16.0G" on a machine with 15Gi total and 7.8Gi available,
    and named the FOURTH question as their first. Every case below is pinned
    from that record, in both directions — a predicate that never passes a
    true answer measures nothing either.
    """

    def _grade(self, q, turn, truth=FREE_H):
        return wb._grade(q, turn, _glass_model(kind="dispatch"),
                         {"memory": truth})

    def test_the_memory_figures_the_machine_never_reported_fail(self):
        v = self._grade(MEMORY_Q,
                        _code_turn("You have 12.1G of free RAM out of 16.0G "
                                   "available."))
        self.assertEqual(v.verdict, "FAIL")
        joined = " | ".join(v.reasons)
        self.assertIn("16.0GiB of memory; it has 15.0GiB", joined)
        self.assertIn("states 12.1GiB", joined)

    def test_a_right_total_with_a_wrong_figure_still_fails(self):
        v = self._grade(MEMORY_Q,
                        _code_turn("You have 12.1Gi of memory available "
                                   "(of 15Gi total)."))
        self.assertEqual(v.verdict, "FAIL")
        self.assertIn("states 12.1GiB", " | ".join(v.reasons))

    def test_the_machines_own_figures_pass(self):
        v = self._grade(MEMORY_Q,
                        _code_turn("You have 7.8Gi of memory available "
                                   "(of 15Gi total)."))
        self.assertEqual(v.verdict, "PASS", v.reasons)

    def test_free_h_rounding_is_tolerated_but_a_whole_gibibyte_is_not(self):
        self.assertEqual(
            self._grade(MEMORY_Q,
                        _code_turn("You have 7.8Gi available of 15.1Gi total.")
                        ).verdict, "PASS")
        self.assertEqual(
            self._grade(MEMORY_Q,
                        _code_turn("You have 7.8Gi available of 16Gi total.")
                        ).verdict, "FAIL")

    def test_an_unreadable_reading_reports_the_question_unchecked(self):
        v = self._grade(MEMORY_Q,
                        _code_turn("You have 7.8Gi of memory available "
                                   "(of 15Gi total)."),
                        truth="")
        self.assertEqual(v.verdict, "FAIL")
        self.assertIn("ground truth unreadable", " | ".join(v.reasons))

    def test_an_answer_with_no_figure_at_all_fails(self):
        v = self._grade(MEMORY_Q, _code_turn("Memory looks fine right now."))
        self.assertEqual(v.verdict, "FAIL")
        self.assertIn("no memory figure", " | ".join(v.reasons))

    def test_naming_a_later_question_as_the_first_fails(self):
        v = self._grade(FIRST_Q,
                        _code_turn('Your first question was: "What year was '
                                   'Linux first released?"'))
        self.assertEqual(v.verdict, "FAIL")
        joined = " | ".join(v.reasons)
        self.assertIn("does not name the first question", joined)
        self.assertIn("names a question that was not the first", joined)

    def test_naming_the_first_question_passes(self):
        v = self._grade(FIRST_Q, _code_turn('Your first question was: "hi"'))
        self.assertEqual(v.verdict, "PASS", v.reasons)

    def test_a_live_machine_fact_composed_by_the_model_fails(self):
        """The disk question is answered from a code-owned probe; the memory
        question must be too. A model does not hold the numbers."""
        turn = _model_turn(text="You have 7.8Gi of memory available "
                                "(of 15Gi total).", tokens=8)
        v = wb._grade(MEMORY_Q, turn, _glass_model(kind="model"),
                      {"memory": FREE_H})
        self.assertEqual(v.verdict, "FAIL")
        self.assertIn("must be code-owned", " | ".join(v.reasons))

    def test_the_battery_marks_its_two_correctness_questions(self):
        by_key = {q.key: q for q in wb.QUESTIONS}
        self.assertEqual(by_key["q07"].truth, "memory_matches_the_machine")
        self.assertEqual(by_key["q14"].truth, "names_the_first_question")
        self.assertTrue(by_key["q07"].code_required)
        self.assertTrue(by_key["q14"].code_required)
        # The reading each one is checked against has a command that takes it.
        for q in wb.QUESTIONS:
            if q.truth == "memory_matches_the_machine":
                self.assertIn(q.truth, wb.TRUTH_PROBES)

    def test_an_unknown_predicate_name_is_a_failure_not_a_silent_pass(self):
        q = wb.Question("qx", "shape", "text", model_required=False,
                        truth="no_such_predicate")
        self.assertIn("unknown correctness predicate",
                      " | ".join(wb._truth_reasons(q, "anything", {})))

    def test_the_mem_row_is_read_by_column_name(self):
        row = wb._mem_row(FREE_H)
        self.assertAlmostEqual(row["total"], 15.0, places=3)
        self.assertAlmostEqual(row["available"], 7.8, places=3)
        self.assertAlmostEqual(row["free"], 2.1, places=3)
        # A reading whose columns do not line up is refused, not guessed at.
        self.assertEqual(wb._mem_row("total used\nMem: 1Gi 2Gi 3Gi\n"), {})
        self.assertEqual(wb._mem_row(""), {})


if __name__ == "__main__":
    unittest.main()
