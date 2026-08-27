# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Which assertions mean the same thing on every tier.

Why this exists
---------------
A scenario declares the tiers it applies to in its ``postures`` list, and the
grader skips a scenario whose declared postures do not include the tier being
driven. Most of the corpus declares ``["2B-locked"]`` alone, so a 9B or 35B run
skips it — the tiers that ship to most machines are measured by a fraction of
the scenarios that exist. Widening a declaration is only safe where the
assertions in it mean the same thing on the other tiers, so this module decides
that question once, by assertion type, with a reason for each.

The test the classification comes from
--------------------------------------
Not the assertion's NAME — what its evaluator READS. The evaluators are
registered in ``grader.py`` at ``_EXPLICIT_EVALUATORS`` (grader.py:790-820), and
each is handed a specific part of the turn:

* ``ctx.trace`` and ``ctx.called`` — decisions the machine RECORDED: which tools
  it dispatched, what a gate returned, how it split a compound request. The
  machine either did the thing or it did not, and that is not a matter of
  phrasing. TIER-INDEPENDENT.
* ``ctx.text`` compared against a literal the scenario supplies — the model's
  WORDING. Wording is exactly what differs between a 2B and a 35B, and it is
  what a larger model is entitled to change. TIER-DEPENDENT.
* ``ctx.text`` checked for a property rather than a phrase — self-contradiction,
  invention, a claim the trace does not support. Every tier owes these equally;
  a bigger model is not allowed to fabricate more. TIER-INDEPENDENT.

Positive and negative wording are NOT symmetric
-----------------------------------------------
A positive wording assertion ("the reply must contain X") requires a tier to
produce particular words, so running it on an undeclared tier invents failures
the moment that tier phrases the same correct answer differently. A negative one
("the reply must NOT contain X") requires only that a tier avoid a phrase, and
in this corpus those are safety constraints — must not falsely deny a
capability, must not claim a thing was done. Running those wider can only catch
a tier saying something it should never say. So the negative forms are
tier-independent and the positive forms are not.

Two deliberate exclusions
-------------------------
``routes_via`` / ``routes_via_any`` are TIER-DEPENDENT even though they read a
recorded decision. Which route answers a question is a genuine tier difference:
the locked floor answers from code where a native tier is allowed to decide
tools. A memory recall question, measured 2026-08-26, routes ``memory`` on every
tier — but that is a property of that scenario's route, not of the assertion
type, so it stays where the scenario author put it and is widened case by case.

``uses_tool`` / ``uses_any_tool`` / ``no_tool`` ARE tier-independent, and this
was measured rather than assumed. On the 2B-locked tier the model is not offered
tool schemas (``eligible_for_tools: false``, ``locked_floor_code_owned``), which
reads like "this tier dispatches nothing" and is not what it means — dispatch on
that tier is owned by code. In the 2026-08-26 re-drive, six of ten
``WRT-do-for-me`` scenarios PASSED their ``uses_any_tool`` assertion at
``2B-locked``. Tool dispatch is not a tier property, so a tool assertion that
fails must be allowed to fail loudly on every tier rather than be explained away
by a posture.
"""
from __future__ import annotations

# Reads a recorded decision, or a property every tier owes. Safe on any tier.
TIER_INDEPENDENT: frozenset[str] = frozenset({
    # decisions the machine recorded (ctx.trace / ctx.called)
    "decomposes_into",
    "uses_tool",
    "uses_any_tool",
    "no_tool",
    # Which clause of a compound request dispatched what, read from the
    # router's own per-clause rows. Dispatch is code-owned on every tier, and
    # the clause split is the decomposer's recorded verdict, so neither half of
    # this assertion reads the model's wording.
    "uses_tool_for_clause",
    "tool_arg_contains",
    "tool_result_nonempty",
    "tool_output_contains",
    "dispatch_outcome",
    "gate_outcome",
    # grounding and honesty, owed equally by every tier
    "answer_consistent_with_tool",
    "no_fabricated_success",
    "no_fabricated_state",
    "no_fabricated_citation",
    "no_invented_artifact",
    "self_consistent",
    # Stating a fact and its negation about one subject is not a matter of
    # wording or of tier capability: no tier is permitted to do it, and a
    # smaller model doing it more often is a reason to measure it everywhere,
    # not to excuse it on the tier where it happens.
    "no_self_contradiction",
    # negative wording: a phrase no tier may emit
    "not_contains",
    "not_contains_any",
    # the offer is a router decision, not a phrase the model chose
    "escalation_offered",
    "no_escalation_offer",
})

# Reads the model's wording, or a route that legitimately differs by tier.
TIER_DEPENDENT: frozenset[str] = frozenset({
    "contains",
    "contains_any",
    "no_negation",
    "source",
    "source_any",
    "not_repeat_of_previous",
    "routes_via",
    "routes_via_any",
})

ALL_POSTURES: tuple[str, ...] = ("2B-locked", "9B-native", "35B-native")

# A value written as "capability:<tool>" is not a phrase the scenario chose. The
# grader resolves it at grade time from capability_registry.phrase(tool)
# (grader.py:75-97), which is the same table the router builds its capability
# answer from — and that answer is returned by code, with used_llm false, so it
# is identical on every tier. An assertion carrying such a reference therefore
# tests a registry phrase, not the model's wording, and is tier-independent
# whatever its type would otherwise say.
CAPABILITY_REF_PREFIX = "capability:"


def assertion_type(a) -> str | None:
    """A corpus assertion is either a ``[type, value, description]`` list or a
    dict carrying ``type``. Auto-assertions carry no entry at all."""
    if isinstance(a, list):
        return a[0] if a else None
    if isinstance(a, dict):
        return a.get("type")
    return None


def assertion_postures(a):
    """The postures an assertion is gated to, or None when it is ungated."""
    return a.get("postures") if isinstance(a, dict) else None


def assertion_value(a) -> str:
    """The assertion's value, or the empty string when it carries none."""
    if isinstance(a, list):
        return a[1] if len(a) > 1 and isinstance(a[1], str) else ""
    if isinstance(a, dict):
        v = a.get("value", "")
        return v if isinstance(v, str) else ""
    return ""


def classify(a) -> str:
    """``independent``, ``dependent``, or ``unknown`` for one assertion.

    ``unknown`` is deliberate and is not folded into either bucket: an assertion
    type this module has not classified must not be silently treated as safe to
    widen. It is surfaced so the type gets a decision, with a reason, here.
    """
    # Checked before the type, because a capability reference makes even a
    # wording assertion tier-independent — the wording comes from the registry,
    # not from the model.
    if assertion_value(a).startswith(CAPABILITY_REF_PREFIX):
        return "independent"
    ty = assertion_type(a)
    if ty in TIER_INDEPENDENT:
        return "independent"
    if ty in TIER_DEPENDENT:
        return "dependent"
    return "unknown"


def scenario_is_tier_independent(scenario) -> bool:
    """True when every UNGATED assertion in the scenario is tier-independent.

    An assertion the author already gated to particular postures is excluded
    from the question: the author has said which tiers it speaks for, and this
    module does not overrule that. A scenario with no classified assertions at
    all returns False — nothing was established, so nothing is widened.
    """
    seen_any = False
    for turn in scenario.get("turns") or []:
        for a in turn.get("assertions") or []:
            if assertion_postures(a):
                continue
            kind = classify(a)
            if kind != "independent":
                return False
            seen_any = True
    return seen_any


def unknown_assertion_types(scenario) -> set[str]:
    """Assertion types in this scenario that this module has not classified."""
    out = set()
    for turn in scenario.get("turns") or []:
        for a in turn.get("assertions") or []:
            if classify(a) == "unknown":
                ty = assertion_type(a)
                if ty:
                    out.add(ty)
    return out
