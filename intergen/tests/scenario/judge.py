# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-3.2 — judge annotation layer for the scenario harness.

The model judge is an opt-in TRIAGE layer that ANNOTATES a scenario run; it is
never folded into Gate A. Structural-first stays the rule: a scenario's PASS/FAIL
is decided by the deterministic structural grader; the judge's per-dimension
pass/flag/fail sits ALONGSIDE it as advisory triage, catching reworded
fabrications and tone/apology-spiral defects the structural checks miss.

Two things live here:

* **The bridge** — turn a scenario turn (user text + the transport reply, plus
  the joined trace when present) into the JudgeInputs the shared judge consumes,
  and run its **Layer-1 deterministic screen** (daemon-free: apology spirals,
  user-blaming, first-person fabricated-action). Layer 1 needs no model, so it
  runs in the harness's own self-tests; the LLM Layer 2 (a model family that is
  NOT the assistant's — never the forbidden family — behind the opt-in judge
  flag) is a separate, live-only escalation and is not invoked here.
* **The calibration floor** — run the deterministic screen over the RED
  known-garbage fixture through the SAME bridge, so the pre-RC gate ("the judge
  must flag known garbage") is provable in the scenario lane, not just in the
  judge's own unit tests.

The judge annotates; it never changes a grade. That separation is the whole point
of keeping honesty structural rather than model-decided.
"""

from __future__ import annotations

from intergen.tests.quality_judge import (
    JUDGE_FORBIDDEN_FAMILY,
    JUDGE_MODEL_DEFAULT,
    DimensionVerdict,
    JudgeInputs,
    deterministic_screen,
)
from intergen.tests.scenario.runner import ScenarioRun
from intergen.tests.scenario.schema import Scenario
from intergen.tests.scenario.transport import TurnResult

# Triage severity for the worst-of rollup.
_VERDICT_RANK = {"pass": 0, "flag": 1, "fail": 2}


def judge_inputs_for_turn(user: str, result: TurnResult, trace=None,
                          antecedent: str = "") -> JudgeInputs:
    """Build the shared judge's inputs from one scenario turn.

    ``user`` is the turn's input; ``result.text`` is both the raw generation and
    the delivered bytes at this layer (the scenario transport returns the final
    reply). ``trace`` supplies the assembled prompt when a trace was captured;
    ``antecedent`` is the prior turn's reply, which the context-dependent tone
    rules (an apology re-offer) need to judge against.
    """
    # Only a captured trace supplies the assembled prompt. Never fall back to the
    # user text: the judge treats a non-empty assembled_prompt as "prior context
    # present", which flips the context-dependent tone rules (an apology re-offer)
    # from flag to fail — so a fabricated antecedent would over-punish. Prior
    # context arrives explicitly via ``antecedent`` (the previous turn's reply).
    assembled = ""
    if trace is not None:
        assembled = getattr(trace, "assembled_prompt", "") or getattr(trace, "prompt", "") or ""
    text = result.text or ""
    return JudgeInputs(
        user_input=user,
        assembled_prompt=assembled,
        model_output=text,
        delivered=text,
        source=getattr(result, "source", "") or "",
        antecedent=antecedent,
    )


def annotate_turn(user: str, result: TurnResult, trace=None,
                  antecedent: str = "") -> list[DimensionVerdict]:
    """Layer-1 deterministic judge annotation for one turn — model-free.

    ANNOTATION only: it never changes the turn's grade. Returns the verdicts
    Layer 1 can decide (an empty list means Layer 1 abstained — that dimension is
    left to the opt-in LLM layer, never silently passed).
    """
    return deterministic_screen(judge_inputs_for_turn(user, result, trace, antecedent))


def annotate_run(run: ScenarioRun, scenario: Scenario) -> list[list[DimensionVerdict]]:
    """Per-turn Layer-1 annotations for a completed run.

    Threads each turn's reply forward as the next turn's antecedent, since the
    context-dependent tone rules judge a turn against what came before. The run's
    grade is untouched — these annotations are triage beside it, not a re-grade.
    """
    annotations: list[list[DimensionVerdict]] = []
    antecedent = ""
    traces = run.traces or [None] * len(run.turn_results)
    for turn, result, trace in zip(scenario.turns, run.turn_results, traces):
        annotations.append(annotate_turn(turn.user, result, trace, antecedent))
        antecedent = result.text or ""
    return annotations


def worst_verdict(verdicts: list[DimensionVerdict]) -> str:
    """The worst-of the per-dimension verdicts (fail > flag > pass) — the triage
    rollup. 'pass' when Layer 1 abstained on every dimension (empty)."""
    if not verdicts:
        return "pass"
    return max((v.verdict for v in verdicts), key=lambda v: _VERDICT_RANK.get(v, 0))


def run_worst_verdict(annotations: list[list[DimensionVerdict]]) -> str:
    """Worst-of across every turn of a run."""
    return max((worst_verdict(a) for a in annotations),
               key=lambda v: _VERDICT_RANK.get(v, 0), default="pass")


def annotations_to_dict(annotations: list[list[DimensionVerdict]]) -> list[list[dict]]:
    """Serialize per-turn annotations for the run artifact (opt-in judge field)."""
    return [[{"dimension": v.dimension, "verdict": v.verdict, "evidence": v.evidence}
             for v in per_turn] for per_turn in annotations]


def screen_calibration_seed(seed: dict) -> dict[str, str]:
    """Run the Layer-1 screen over a RED calibration seed via the SAME JudgeInputs
    the scenario bridge builds — the pre-RC floor, provable in this lane.

    Mirrors the fixture's flatten-context-and-carry-antecedent shape so a
    context-dependent seed is judged WITH its context. Returns the per-dimension
    verdict map ({} when Layer 1 abstains entirely).
    """
    ctx = seed.get("conversation_context") or []
    flat = "\n".join(f"[{m['role']}] {m['content']}" for m in ctx)
    inputs = JudgeInputs(
        user_input=seed["user"], assembled_prompt=flat,
        antecedent=seed.get("antecedent") or "",
        model_output=seed["delivered"], delivered=seed["delivered"])
    return {dv.dimension: dv.verdict for dv in deterministic_screen(inputs)}


def calibration_catches(seed: dict) -> bool:
    """True iff the deterministic screen surfaces a non-pass verdict for a seed —
    i.e. the known-garbage is caught by the model-free floor."""
    return any(v != "pass" for v in screen_calibration_seed(seed).values())


# Re-exported so a scenario-lane reader sees the anti-self-preference constraint
# without reaching into the judge module: the judge family must differ from the
# assistant's (the assistant is that forbidden family), default a distinct tier.
FORBIDDEN_JUDGE_FAMILY = JUDGE_FORBIDDEN_FAMILY
DEFAULT_JUDGE_MODEL = JUDGE_MODEL_DEFAULT
