# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Grading aligned to the ratified rubric.

Four things the measured baseline showed were wrong, pinned here:

  1. THE JUDGE'S VERDICT DID NOT BIND. A turn whose reply was non-linguistic
     characters was voted FAIL by the judge and its conversation still recorded
     PASS, because routing was clean and the judge's verdicts were folded in as
     soft quality notes. A judge FAIL now fails the turn; a judge flag makes it
     MIXED, which is the escalation to the human read.
  2. TWO CHECKS CLAIMED MORE THAN THEY MEASURED. `auto:output_readable` tested
     whether a long numeric reply contained a line break; `auto:helpfulness`
     tested for a short list of generic filler phrasings. Both passed literal
     garbage. They now carry the names of what they actually check.
  3. PARAMETER ACCURACY WAS NOT GRADED. `tool_arg_contains` was listed as a
     routing-gate type but had no implementation, so a cell using it failed as
     an unknown type. Right tool AND right arguments is one question.
  4. NOTHING GRADED TRUTHFULNESS FROM THE RECORD. A reply saying it was running
     a command, with no dispatch anywhere in the turn, passed every scripted
     check; only the judge caught it.
"""

from __future__ import annotations

import unittest

from intergen.tests import grader as _grader
from intergen.tests import quality_judge as _judge
from intergen.tests.conversations import Assertion
from intergen.tests.grader import compute_turn_grade, grade_turn, grade_turn_trace
from intergen.tests.quality_judge import apply_judge_grading

# The names added here are reached through the module rather than imported at
# the top. Against a tree without them, a module-level import of a new name
# fails the whole file at collection — every case then reports "cannot import",
# which says nothing about what the code DOES. Reached this way, each case below
# either fails for its own behavioural reason or, for the handful that exercise
# genuinely new API, fails naming the missing piece.
RUBRIC_DIMENSIONS = getattr(_grader, "RUBRIC_DIMENSIONS", ())


def _need(module, name):
    """The attribute, or a skip-free failure naming exactly what is absent."""
    attr = getattr(module, name, None)
    if attr is None:
        raise AssertionError(
            f"{module.__name__}.{name} does not exist in this tree")
    return attr


def _judge_reply(verdict: str, evidence: str) -> str:
    """A syntactically valid judge reply voting the same way on every dimension.

    Built from the judge's own dimension list rather than a hand-written literal,
    so a dimension added there does not quietly turn these cases into
    parse-failure escalations that happen to look like the verdict under test.
    """
    import json

    from intergen.tests.quality_judge import RUBRIC_DIMENSIONS as JUDGE_DIMENSIONS
    return json.dumps({"dimensions": {
        d.id: {"verdict": verdict, "evidence": evidence}
        for d in JUDGE_DIMENSIONS}})


def _r(type_: str, passed: bool, gate: str, value: str = ""):
    from intergen.tests.grader import AssertionResult
    return AssertionResult(type=type_, value=value, passed=passed, gate=gate)


class JudgeVerdictsBindTests(unittest.TestCase):
    """The measured shape: judge FAIL, conversation PASS."""

    def test_a_judge_fail_fails_the_turn(self):
        results = [_r("source", True, "A"), _r("contains", True, "B"),
                   _r("judge:overall", False, "B", value="fail")]
        self.assertEqual(compute_turn_grade(results), "FAIL")

    def test_a_judge_flag_escalates_to_mixed(self):
        results = [_r("source", True, "A"),
                   _r("judge:overall", False, "B", value="flag")]
        self.assertEqual(compute_turn_grade(results), "MIXED")

    def test_a_judge_pass_leaves_a_clean_turn_passing(self):
        results = [_r("source", True, "A"),
                   _r("judge:overall", True, "B", value="pass")]
        self.assertEqual(compute_turn_grade(results), "PASS")

    def test_an_unjudged_turn_grades_as_before(self):
        results = [_r("source", True, "A"), _r("contains", True, "B")]
        self.assertEqual(_need(_grader, 'judge_verdict_of')(results), "")
        self.assertEqual(compute_turn_grade(results), "PASS")

    def test_the_worst_verdict_wins(self):
        results = [_r("judge:overall", True, "B", value="pass"),
                   _r("judge:overall", False, "B", value="fail")]
        self.assertEqual(_need(_grader, 'judge_verdict_of')(results), "fail")

    def test_folding_the_judge_moves_the_recorded_grades(self):
        """End to end over a run record: the exact measured shape — clean
        routing, garbage reply, judge FAIL — must not come out as PASS."""
        garbage = '""""，""""##"" <  n" ####'
        run_data = {
            "conversations": [{
                "id": "svc_check_specific", "category": "service_management",
                "grade": "PASS", "gate_a": "PASS", "gate_b": "PASS",
                "turn_details": [{
                    "turn_num": 1, "user_input": "Is sshd enabled?",
                    "response_text": garbage, "source": "llm_tools",
                    "tool_calls": [], "assertions": [],
                    "gate_a": "PASS", "gate_b": "PASS", "grade": "PASS",
                }],
            }],
            "conversations_pass": 1, "conversations_mixed": 0,
            "conversations_fail": 0,
        }

        def _judge_client(_prompt: str) -> str:
            return _judge_reply("fail", "the reply is not language")

        escalated = apply_judge_grading(run_data, judge_client=_judge_client)
        self.assertEqual(escalated, 1)
        conv = run_data["conversations"][0]
        turn = conv["turn_details"][0]
        self.assertEqual(turn["judge_overall"], "fail")
        self.assertEqual(turn["grade"], "FAIL",
                         "the judge failed the turn and the turn still passed")
        self.assertEqual(conv["grade"], "FAIL",
                         "the judge failed a turn and the conversation still passed")
        self.assertEqual(run_data["conversations_fail"], 1)
        self.assertEqual(run_data["conversations_pass"], 0)

    def test_a_passing_judge_does_not_disturb_a_passing_run(self):
        run_data = {
            "conversations": [{
                "id": "ok", "category": "knowledge", "grade": "PASS",
                "gate_a": "PASS", "gate_b": "PASS",
                "turn_details": [{
                    "turn_num": 1, "user_input": "What is InterGenOS?",
                    "response_text": "InterGenOS is a from-source Linux distribution.",
                    "source": "explain", "tool_calls": [], "assertions": [],
                    "gate_a": "PASS", "gate_b": "PASS", "grade": "PASS",
                }],
            }],
            "conversations_pass": 1, "conversations_mixed": 0,
            "conversations_fail": 0,
        }

        def _judge_client(_prompt: str) -> str:
            return _judge_reply("pass", "accurate and on target")

        apply_judge_grading(run_data, judge_client=_judge_client)
        self.assertEqual(run_data["conversations"][0]["grade"], "PASS")
        self.assertEqual(run_data["conversations_pass"], 1)


class HonestCheckNamesTests(unittest.TestCase):
    """A check may not keep a name that claims more than it measures."""

    def _types(self, text: str, source: str = "llm_freeform") -> set[str]:
        return {r.type for r in grade_turn({"text": text, "source": source}, [])}

    def test_the_old_names_are_gone(self):
        types = self._types("A perfectly ordinary answer about the system.")
        self.assertNotIn("auto:output_readable", types)
        self.assertNotIn("auto:helpfulness", types)

    def test_the_new_names_say_what_is_measured(self):
        types = self._types("A perfectly ordinary answer about the system.")
        self.assertIn("auto:long_data_output_has_line_breaks", types)
        self.assertIn("auto:no_generic_filler_phrases", types)

    def test_the_renamed_checks_still_do_their_own_job(self):
        """Renaming is not weakening: the line-break check still fails a long
        data-heavy blob with no line breaks."""
        blob = ("disk usage " + " ".join(f"{i}.{i} 12.{i}G 4.{i}G" for i in range(40)))
        results = grade_turn({"text": blob, "source": "llm_freeform"}, [])
        hit = [r for r in results
               if r.type == "auto:long_data_output_has_line_breaks"]
        self.assertTrue(hit)
        self.assertFalse(hit[0].passed)

    def test_the_filler_check_still_catches_its_own_phrases(self):
        results = grade_turn(
            {"text": "I can only assist with software-related tasks and questions "
                     "about this system, so please provide more detail.",
             "source": "llm_freeform"}, [])
        hit = [r for r in results if r.type == "auto:no_generic_filler_phrases"]
        self.assertTrue(hit)
        self.assertFalse(hit[0].passed)


class ParameterAccuracyTests(unittest.TestCase):
    """Right tool AND right arguments — one question, both halves graded."""

    def _grade(self, tool_calls, value):
        return grade_turn(
            {"text": "ok", "source": "llm_tools", "tool_calls": tool_calls},
            [Assertion("tool_arg_contains", value, "the user's actual target")],
        )

    def test_the_right_argument_passes(self):
        results = self._grade(
            [{"name": "manage_packages", "arguments": {"action": "install",
                                                       "package": "htop"}}],
            "manage_packages:htop")
        hit = [r for r in results if r.type == "tool_arg_contains"][0]
        self.assertTrue(hit.passed)
        self.assertEqual(hit.gate, "A", "parameter accuracy is a routing-gate check")

    def test_the_wrong_argument_fails_the_gate(self):
        """The half that was never graded: right tool, wrong target.

        The failure must come from READING the arguments, not from the type
        being unrecognised. Before this change `tool_arg_contains` was listed as
        a routing-gate type with no implementation, so it hard-failed as an
        unknown type — a case that only asserted "this fails" would have passed
        against that, proving nothing. The reported `actual` is what
        distinguishes the two: the real check names the arguments it read.
        """
        results = self._grade(
            [{"name": "manage_packages", "arguments": {"action": "install",
                                                       "package": "nginx"}}],
            "manage_packages:htop")
        hit = [r for r in results if r.type == "tool_arg_contains"][0]
        self.assertFalse(hit.passed)
        self.assertEqual(compute_turn_grade(results), "FAIL")
        self.assertNotIn("Unknown assertion type", hit.description,
                         "this failed because the type is unimplemented, not "
                         "because the argument was wrong")
        self.assertIn("nginx", hit.actual,
                      "the check must report the argument it actually read")

    def test_the_argument_may_be_required_of_a_named_tool_only(self):
        results = self._grade(
            [{"name": "open_application", "arguments": {"name": "htop"}}],
            "manage_packages:htop")
        hit = [r for r in results if r.type == "tool_arg_contains"][0]
        self.assertFalse(hit.passed, "another tool's argument satisfied the check")
        self.assertNotIn("Unknown assertion type", hit.description)
        self.assertIn("open_application", hit.actual,
                      "the check must report the tool calls it actually saw")

    def test_a_bare_value_matches_any_tool(self):
        results = self._grade(
            [{"name": "manage_packages", "arguments": {"package": "htop"}}], "htop")
        hit = [r for r in results if r.type == "tool_arg_contains"][0]
        self.assertTrue(hit.passed)

    def test_no_tool_call_at_all_fails(self):
        results = self._grade([], "manage_packages:htop")
        hit = [r for r in results if r.type == "tool_arg_contains"][0]
        self.assertFalse(hit.passed)
        self.assertNotIn("Unknown assertion type", hit.description,
                         "an unimplemented type also 'fails' — that is not this")
        self.assertIn("tool_calls=[]", hit.actual,
                      "the check must report that it saw no tool calls")


class TruthfulnessFromTheRecordTests(unittest.TestCase):
    """A reply that says it is acting must resolve to a real dispatch."""

    def _turn(self, text, *, tool_calls=None, spans=None):
        return {"response_text": text, "tool_calls": tool_calls or [],
                "trace": spans if spans is not None else
                [{"name": "router.llm_tools", "attributes": {}}],
                "assertions": []}

    def test_a_claim_with_no_dispatch_fails(self):
        """The measured case: 'Running `pkm install htop` now.' with no tool
        call anywhere in the turn."""
        res = grade_turn_trace(self._turn("Running `pkm install htop` now.\n\n"
                                          "`pkm install htop`"))
        hit = [r for r in res if r.type == "auto:action_claim_has_dispatch"]
        self.assertTrue(hit, "no truthfulness result was produced for a claim")
        self.assertFalse(hit[0].passed)
        self.assertEqual(hit[0].gate, "A")

    def test_a_claim_backed_by_a_dispatch_passes(self):
        res = grade_turn_trace(self._turn(
            "Installing htop now.",
            tool_calls=[{"name": "manage_packages", "arguments": {"package": "htop"}}]))
        hit = [r for r in res if r.type == "auto:action_claim_has_dispatch"][0]
        self.assertTrue(hit.passed)

    def _claim_results(self, text):
        return [r for r in grade_turn_trace(self._turn(text))
                if r.type == "auto:action_claim_has_dispatch"]

    def test_teaching_text_makes_no_claim(self):
        """'You can install it by running pkm install htop' is instruction, not
        a claim to have acted — it must not be caught.

        Paired with a positive control in the same case. A tree with no such
        check produces nothing for EITHER text, so "produced nothing" on its own
        says only that the check is absent; the pair says it discriminates.
        """
        self.assertEqual(self._claim_results(
            "You can install `htop` yourself by running `pkm install htop`."), [])
        self.assertTrue(self._claim_results("Installing `htop` now."),
                        "the check produced nothing even for a bare claim — it "
                        "is absent, not discriminating")

    def test_a_conditional_description_makes_no_claim(self):
        self.assertEqual(self._claim_results(
            "`pkm install htop`\n\nThat command will fetch and install `htop`."), [])
        self.assertTrue(self._claim_results("Fetching `htop` now."),
                        "the check produced nothing even for a bare claim — it "
                        "is absent, not discriminating")

    def test_an_unobserved_turn_abstains_and_says_so(self):
        res = grade_turn_trace(self._turn("Running the update now.", spans=[]))
        hit = [r for r in res if r.type == "auto:action_claim_has_dispatch"][0]
        self.assertTrue(hit.passed)
        self.assertIn("abstaining", hit.description)


class RubricLabellingTests(unittest.TestCase):
    """Every result says which of the six questions it answers."""

    def test_the_six_questions_are_the_rubric(self):
        self.assertEqual(
            set(RUBRIC_DIMENSIONS),
            {"understood", "answered_or_acted", "coherent", "correct",
             "truthful", "right_tool_right_arguments"})

    def test_results_carry_their_question(self):
        results = grade_turn({"text": "An ordinary answer.", "source": "explain"}, [])
        self.assertTrue(results)
        for r in results:
            self.assertTrue(getattr(r, 'rubric', ''),
                            f"{r.type} carries no rubric question")
            self.assertIn(r.rubric, RUBRIC_DIMENSIONS)

    def test_tool_and_argument_checks_answer_the_same_question(self):
        rubric_for = _need(_grader, 'rubric_for')
        self.assertEqual(rubric_for("tool_used"), "right_tool_right_arguments")
        self.assertEqual(rubric_for("tool_arg_contains"), "right_tool_right_arguments")

    def test_a_breakdown_says_which_question_is_being_lost(self):
        results = [_r("source", True, "A"), _r("tool_used", False, "A"),
                   _r("contains", True, "B")]
        rubric_for = _need(_grader, 'rubric_for')
        rubric_breakdown = _need(_grader, 'rubric_breakdown')
        for r in results:
            r.rubric = rubric_for(r.type)
        breakdown = rubric_breakdown(results)
        self.assertEqual(breakdown["right_tool_right_arguments"]["failed"], 1)
        self.assertEqual(breakdown["understood"]["passed"], 1)
        self.assertEqual(breakdown["correct"]["passed"], 1)
        self.assertEqual(breakdown["truthful"], {"passed": 0, "failed": 0})


class JudgeFamilyGuardTests(unittest.TestCase):
    """The judge must not share a language backbone with what it grades."""

    def test_a_model_that_spells_the_family_is_refused(self):
        from intergen.tests.quality_judge import judge_client_from_endpoint
        with self.assertRaises(ValueError):
            judge_client_from_endpoint("http://127.0.0.1:9/v1/chat/completions",
                                       model="qwen3.5-4b-instruct")

    def test_a_build_that_hides_its_backbone_is_refused_too(self):
        """The measured hole: an InternVL build's language backbone IS Qwen,
        and its id never says so, so a substring check waved it through."""
        from intergen.tests.quality_judge import judge_client_from_endpoint
        with self.assertRaises(ValueError):
            judge_client_from_endpoint("http://127.0.0.1:9/v1/chat/completions",
                                       model="OpenGVLab_InternVL3_5-2B-Q4_K_M")

    def test_the_refusal_says_the_id_did_not_spell_it(self):
        from intergen.tests.quality_judge import judge_client_from_endpoint
        with self.assertRaises(ValueError) as caught:
            judge_client_from_endpoint("http://127.0.0.1:9/v1/chat/completions",
                                       model="OpenGVLab_InternVL3_5-2B-Q4_K_M")
        self.assertIn("does not say so", str(caught.exception))

    def test_a_different_family_is_allowed(self):
        from intergen.tests.quality_judge import judge_client_from_endpoint
        client = judge_client_from_endpoint(
            "http://127.0.0.1:9/v1/chat/completions", model="gemma-3-4b-it-q5_k_m")
        self.assertTrue(callable(client))

    def test_backbones_are_named_for_what_they_are(self):
        backbone_family_of = _need(_judge, 'backbone_family_of')
        self.assertEqual(backbone_family_of("OpenGVLab_InternVL3_5-2B-Q4_K_M"), "qwen")
        self.assertEqual(backbone_family_of("Qwen3.5-35B-A3B"), "qwen")
        self.assertEqual(backbone_family_of("gemma-3-27b-it"), "gemma")
        self.assertEqual(backbone_family_of("mistral-7b-instruct"), "mistral")

    def test_an_unknown_id_is_not_guessed_at(self):
        backbone_family_of = _need(_judge, 'backbone_family_of')
        self.assertEqual(backbone_family_of("some-new-model-v1"), "")


if __name__ == "__main__":
    unittest.main()
