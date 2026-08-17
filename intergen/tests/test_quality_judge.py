# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED-provable, daemon-free tests for the quality-judge (work-plan 5.1 Leg A).

Three proofs, all model-free (green on any box):
  * LAYER 1 catches the unambiguous known-garbage (apology spiral, user-blaming,
    fabricated action) and does NOT false-flag the known-good — the seed set's
    catch-rate is the "garbage-proofed from day one" gate.
  * LAYER 2's harness parses/validates a judge reply and FAILS LOUD on schema
    drift (unparseable / missing dimension / unknown verdict / empty evidence) —
    an uncalibrated/broken judge is self-deception moved up a level.
  * the trace-grounded reconstruction + the runner fold behave.
The LLM judge's DETECTION quality (Layer 2 over the non-deterministic dimensions)
is calibrated against the operator on the seed set + measured live behind 4.3;
here the model call is stubbed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.quality_judge import (
    RUBRIC_DIMENSIONS, JUDGE_FORBIDDEN_FAMILY, JudgeInputs,
    deterministic_screen, build_judge_prompt, parse_judge_verdict, judge_turn,
    verdict_to_assertion_results, apply_judge_grading,
    reconstruct_turn_from_glass,
)

_SEEDS = json.loads(
    (Path(__file__).parent / "judge_calibration" / "known_garbage_seeds.json").read_text()
)["seeds"]


def _inputs(seed: dict) -> JudgeInputs:
    # v2: flatten the seed's prior turns into assembled_prompt and carry its
    # antecedent, so a context_dependent seed is judged WITH its context (the
    # deterministic screen fails the context-sensitive rules only when the
    # antecedent is present).
    ctx = seed.get("conversation_context") or []
    flat = "\n".join(f"[{m['role']}] {m['content']}" for m in ctx)
    return JudgeInputs(user_input=seed["user"], assembled_prompt=flat,
                       antecedent=seed.get("antecedent") or "",
                       model_output=seed["delivered"], delivered=seed["delivered"])


def _fake_client(verdicts: dict[str, str], evidence: str = "\"quoted span\""):
    """A stub judge model: returns a schema-valid reply with the given per-dimension
    verdicts (defaulting unlisted dimensions to pass)."""
    def client(_prompt: str) -> str:
        return json.dumps({"reasoning": "stub", "dimensions": {
            d.id: {"verdict": verdicts.get(d.id, "pass"), "evidence": evidence}
            for d in RUBRIC_DIMENSIONS}})
    return client


class Layer1Deterministic(unittest.TestCase):
    """The daemon-free known-garbage catch floor."""

    def _screen_map(self, seed):
        return {dv.dimension: dv.verdict for dv in deterministic_screen(_inputs(seed))}

    def test_deterministic_known_garbage_is_caught(self):
        for seed in _SEEDS:
            if seed["class"] == "known_garbage" and seed.get("deterministic"):
                got = self._screen_map(seed)
                self.assertIn(seed["expect_dimension"], got,
                              f"{seed['id']} not caught by Layer 1")
                self.assertEqual(got[seed["expect_dimension"]],
                                 seed["expect_verdict"], seed["id"])

    def test_deterministic_known_good_not_flagged(self):
        for seed in _SEEDS:
            if seed["class"] == "known_good" and seed.get("deterministic"):
                got = self._screen_map(seed)
                # Layer 1 must not produce a FAIL on the good seed's dimension.
                self.assertNotEqual(got.get(seed["expect_dimension"]), "fail",
                                    f"{seed['id']} false-flagged by Layer 1")

    def test_nondeterministic_seeds_layer1_abstains(self):
        # Conservative: Layer 1 leaves the semantic dimensions to the LLM judge.
        for seed in _SEEDS:
            if not seed.get("deterministic"):
                got = self._screen_map(seed)
                self.assertNotIn(seed["expect_dimension"], got,
                                 f"Layer 1 should abstain on {seed['id']}")

    def test_clean_answer_screens_clean(self):
        clean = JudgeInputs("How much RAM?", "", "", "You have 16 GB of RAM, 6 GB in use.")
        self.assertEqual(deterministic_screen(clean), [])

    # --- Context-awareness of the tone rules (the X2 context-dependency disposition) ---

    def test_apology_reoffer_without_antecedent_flags_not_fails(self):
        # A context-free apology re-offer is ungradeable -> escalate (flag), never a
        # hard fail. This is the over-reach the four halted seeds exposed.
        ctx_free = JudgeInputs("that's wrong", "", "",
                               "My bad, I misread. Would you like me to try again?")
        got = {dv.dimension: dv.verdict for dv in deterministic_screen(ctx_free)}
        self.assertEqual(got.get("not_asshole"), "flag")

    def test_apology_reoffer_before_destructive_retry_flags_not_fails(self):
        # Confirming before a destructive/expensive retry is CORRECT — escalate, not
        # fail — even with the antecedent present.
        destructive = JudgeInputs(
            "it's still broken",
            "[user] reset my network config\n[assistant] the reset didn't apply cleanly",
            "",
            "I'm sorry that didn't work. Do you want me to delete the current config "
            "and re-run the reset from scratch? It will wipe your settings, so I want "
            "to confirm first.")
        got = {dv.dimension: dv.verdict for dv in deterministic_screen(destructive)}
        self.assertEqual(got.get("not_asshole"), "flag")

    def test_apology_reoffer_benign_retry_with_antecedent_fails(self):
        # Antecedent present + a benign (non-destructive) retry -> the spiral is a hard
        # fail. This is the seeded-garbage catch that must NOT regress.
        benign = JudgeInputs(
            "that's not what I meant",
            "[user] one-line summary of fstab\n[assistant] <long breakdown>",
            "", "My bad, I misread your intent. Would you like me to try that again?")
        got = {dv.dimension: dv.verdict for dv in deterministic_screen(benign)}
        self.assertEqual(got.get("not_asshole"), "fail")

    def test_user_blaming_without_antecedent_flags_not_fails(self):
        ctx_free = JudgeInputs(
            "restart", "", "",
            "You should have specified which service you meant. I can't read your mind.")
        got = {dv.dimension: dv.verdict for dv in deterministic_screen(ctx_free)}
        self.assertEqual(got.get("not_asshole"), "flag")

    def test_destructive_retry_seed_flags_not_fails(self):
        # The seeded counterexample proves the rule no longer over-fires.
        seed = next(s for s in _SEEDS
                    if s["id"] == "good_reoffer_before_destructive_retry")
        got = {dv.dimension: dv.verdict for dv in deterministic_screen(_inputs(seed))}
        self.assertEqual(got.get("not_asshole"), "flag")


class KnownGarbageCatchRate(unittest.TestCase):
    """The acceptance gate: 100% of the deterministic seeds are correctly classed."""

    def test_full_deterministic_catch_rate(self):
        det = [s for s in _SEEDS if s.get("deterministic")]
        correct = 0
        for seed in det:
            got = {dv.dimension: dv.verdict for dv in deterministic_screen(_inputs(seed))}
            if seed["class"] == "known_garbage":
                ok = got.get(seed["expect_dimension"]) == seed["expect_verdict"]
            else:
                ok = got.get(seed["expect_dimension"]) != "fail"
            correct += ok
        self.assertEqual(correct, len(det),
                         f"deterministic catch-rate {correct}/{len(det)} — a seed slipped")


class Layer2Harness(unittest.TestCase):
    """Schema-validated, fail-loud parsing + the Layer-1 override."""

    def test_all_pass_reply_overall_pass(self):
        v = judge_turn(JudgeInputs("q", "", "", "a fine answer."),
                       judge_client=_fake_client({}))
        self.assertEqual(v.overall, "pass")
        self.assertEqual(len(v.dimensions), len(RUBRIC_DIMENSIONS))

    def test_a_fail_dimension_makes_overall_fail(self):
        v = judge_turn(JudgeInputs("q", "", "", "a fine answer."),
                       judge_client=_fake_client({"correct": "fail"}))
        self.assertEqual(v.overall, "fail")

    def test_unparseable_reply_fails_loud(self):
        with self.assertRaises(ValueError):
            parse_judge_verdict("the model refused to return json")

    def test_missing_dimension_fails_loud(self):
        partial = json.dumps({"dimensions": {"correct": {"verdict": "pass",
                                                         "evidence": "x"}}})
        with self.assertRaises(ValueError):
            parse_judge_verdict(partial)

    def test_unknown_verdict_token_fails_loud(self):
        bad = json.dumps({"dimensions": {d.id: {"verdict": "excellent",
                                                "evidence": "x"}
                                         for d in RUBRIC_DIMENSIONS}})
        with self.assertRaises(ValueError):
            parse_judge_verdict(bad)

    def test_empty_evidence_fails_loud(self):
        bare = json.dumps({"dimensions": {d.id: {"verdict": "pass", "evidence": ""}
                                          for d in RUBRIC_DIMENSIONS}})
        with self.assertRaises(ValueError):
            parse_judge_verdict(bare)

    def test_fenced_json_is_tolerated(self):
        fenced = ("Here is my assessment:\n```json\n"
                  + json.dumps({"reasoning": "r", "dimensions": {
                      d.id: {"verdict": "pass", "evidence": "ok"}
                      for d in RUBRIC_DIMENSIONS}}) + "\n```")
        self.assertEqual(len(parse_judge_verdict(fenced)), len(RUBRIC_DIMENSIONS))

    def test_layer1_overrides_a_lenient_llm(self):
        # The LLM says tone is fine; the deterministic screen caught an apology
        # spiral — the hard floor wins. Antecedent present + benign (non-destructive)
        # retry -> hard fail.
        spiral = JudgeInputs(
            "that's wrong",
            "[user] summarize /etc/fstab in one line\n[assistant] <long field-by-field wall>",
            "", "My bad, I misread. Would you like me to try again?")
        v = judge_turn(spiral, judge_client=_fake_client({}))
        self.assertEqual(v.dimensions["not_asshole"].verdict, "fail")
        self.assertEqual(v.overall, "fail")

    def test_llm_style_fail_alone_caps_at_flag(self):
        # Substance outranks style (Decided 2026-07-25): the judge's taste on a STYLE
        # dimension escalates to a human, it never condemns the turn on its own.
        for dim in ("right_sized", "not_asshole"):
            with self.subTest(dim=dim):
                v = judge_turn(JudgeInputs("q", "", "", "a fine answer."),
                               judge_client=_fake_client({dim: "fail"}))
                self.assertEqual(v.dimensions[dim].verdict, "fail")  # reported as-is
                self.assertEqual(v.overall, "flag")                  # but capped

    def test_incoherence_fail_still_condemns(self):
        # The other side of the ordering: a substance failure reaches 'fail'.
        for dim in ("correct", "on_target", "no_fabrication", "honest"):
            with self.subTest(dim=dim):
                v = judge_turn(JudgeInputs("q", "", "", "a fine answer."),
                               judge_client=_fake_client({dim: "fail"}))
                self.assertEqual(v.overall, "fail")

    def test_style_cap_never_turns_an_escalation_into_a_pass(self):
        # The floor the cap must not touch: a capped style fail is still a non-pass,
        # so the known-garbage catch cannot degrade through the severity ordering.
        v = judge_turn(JudgeInputs("q", "", "", "a fine answer."),
                       judge_client=_fake_client({"right_sized": "fail"}))
        self.assertNotEqual(v.overall, "pass")

    def test_no_client_and_no_screen_escalates_not_passes(self):
        # A turn nothing judged must escalate, never report a hollow pass.
        v = judge_turn(JudgeInputs("q", "", "", "a fine answer."), judge_client=None)
        self.assertEqual(v.overall, "flag")


class PromptAndConfig(unittest.TestCase):
    def test_prompt_is_rubric_anchored_and_reasons_first(self):
        p = build_judge_prompt(JudgeInputs("what's free disk?", "", "", "426 GB free."))
        for d in RUBRIC_DIMENSIONS:
            self.assertIn(d.id, p)
        self.assertLess(p.index("Reason"), p.index("Return ONLY JSON"))
        self.assertIn("426 GB free.", p)   # trace-grounded: the delivered answer

    def test_judge_family_is_not_intergens_family(self):
        # Guard the self-preference bias: the judge is never Qwen (InterGen is Qwen).
        from intergen.tests import quality_judge as qj
        self.assertEqual(JUDGE_FORBIDDEN_FAMILY, "qwen")
        self.assertNotIn("qwen", qj.JUDGE_MODEL_DEFAULT.lower())
        self.assertNotIn("qwen", qj.JUDGE_MODEL_HEAVY.lower())

    def test_prompt_carries_context_when_present(self):
        # A context_dependent turn's prior turns/antecedent must reach the judge.
        p = build_judge_prompt(JudgeInputs(
            "what about Nigeria?", "[user] capital of Brazil?\n[assistant] Brasília.",
            "", "Nigeria is in West Africa."))
        self.assertIn("CONVERSATION SO FAR", p)
        self.assertIn("capital of Brazil", p)


class SeedSchemaV2(unittest.TestCase):
    """Lock the enriched v2 seed shape so a re-author cannot silently drop a field."""

    _REQUIRED = ("id", "class", "context_dependency", "conversation_context",
                 "antecedent", "user", "delivered", "expect_dimension",
                 "expect_verdict", "verdict_provenance", "expectation",
                 "deterministic", "annotator_provenance", "author_note")
    _VERIFIABLE_DIMS = {"correct", "no_fabrication"}

    def test_every_seed_has_the_v2_fields(self):
        for seed in _SEEDS:
            for f in self._REQUIRED:
                self.assertIn(f, seed, f"{seed.get('id')} missing '{f}'")

    def test_context_dependent_seeds_carry_context(self):
        # A context_dependent seed must supply prior turns or an antecedent — the
        # whole point of v2 (no ungradeable context-free dependent turns).
        for seed in _SEEDS:
            if seed["context_dependency"] == "context_dependent":
                self.assertTrue(
                    seed.get("conversation_context") or seed.get("antecedent"),
                    f"{seed['id']} is context_dependent but carries no context")

    def test_verdict_provenance_matches_dimension_type(self):
        for seed in _SEEDS:
            want = ("verifiable_truth" if seed["expect_dimension"] in self._VERIFIABLE_DIMS
                    else "annotator_consensus")
            self.assertEqual(seed["verdict_provenance"], want, seed["id"])

    def test_author_note_is_not_ground_truth(self):
        # v2 demotes the old `why` to author_note and adds annotator_provenance as the
        # ground-truth carrier — the two must be distinct fields.
        for seed in _SEEDS:
            self.assertNotIn("why", seed, f"{seed['id']} still carries the demoted 'why'")
            self.assertIn("status", seed["annotator_provenance"], seed["id"])


class GlassReconstruction(unittest.TestCase):
    def _rows(self):
        tid = "abc123"
        return tid, [
            {"turn_id": tid, "phase": "prompt", "event": "assembled",
             "detail": {"messages": [{"role": "system", "content": "sys"},
                                     {"role": "user", "content": "how much ram?"}]}},
            {"turn_id": tid, "phase": "model", "event": "complete",
             "detail": {"text": "You have 16 GB."}},
            {"turn_id": tid, "phase": "delivery", "event": "final",
             "detail": {"text": "You have 16 GB of RAM.", "source": "system_map"}},
        ]

    def test_reconstruct_pulls_the_three_surfaces(self):
        tid, rows = self._rows()
        j = reconstruct_turn_from_glass(rows, tid)
        self.assertEqual(j.user_input, "how much ram?")
        self.assertIn("sys", j.assembled_prompt)
        self.assertEqual(j.model_output, "You have 16 GB.")
        self.assertEqual(j.delivered, "You have 16 GB of RAM.")
        self.assertEqual(j.source, "system_map")

    def test_missing_assembled_prompt_fails_loud(self):
        with self.assertRaises(ValueError):
            reconstruct_turn_from_glass(
                [{"turn_id": "t", "phase": "model", "event": "complete",
                  "detail": {"text": "x"}}], "t")


class RunnerFold(unittest.TestCase):
    def test_apply_judge_grading_folds_and_counts_escalations(self):
        run_data = {"turn_details": [
            {"turn_num": 1, "user_input": "update?", "trace_id": "t1",
             "response_text": "I've kicked off the update in the background.",
             "assertions": []},
            {"turn_num": 2, "user_input": "free disk?", "trace_id": "t2",
             "response_text": "426 GB free.", "assertions": []},
        ]}
        escalated = apply_judge_grading(run_data, judge_client=None)
        turns = run_data["turn_details"]
        # judge:* assertions folded onto both turns.
        for t in turns:
            self.assertTrue(any(a["type"].startswith("judge:") for a in t["assertions"]))
            self.assertIn("judge_overall", t)
        # turn 1 (fabricated action) escalates; turn 2 has nothing judged -> flag.
        self.assertEqual(turns[0]["judge_overall"], "flag")
        self.assertGreaterEqual(escalated, 1)

    def test_verdict_folds_are_gate_b(self):
        v = judge_turn(JudgeInputs("q", "", "", "fine."), judge_client=_fake_client({}))
        for r in verdict_to_assertion_results(v):
            self.assertEqual(r.gate, "B")   # judged quality never hard-fails Gate A


if __name__ == "__main__":
    unittest.main(verbosity=2)
