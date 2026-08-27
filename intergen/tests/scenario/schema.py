# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Scenario / Turn / Assertion data model + the harness registries.

The test unit is a scripted multi-turn Scenario of ordered Turns; turn ordering
IS the intra-session context dependency (a turn that depends on a referent is
placed after the turn that establishes it). Cross-session dependency is
expressed structurally via linked pairs (a producer with cleanup=False and a
consumer naming it in cleanup_for).

This module owns the DATA and the fixed vocabularies (axes, postures, the
assertion-type registry, the auto-assertion set). It deliberately does NOT
evaluate assertions — the grader does that in a later phase. Keeping evaluation
out of the schema means the loader can validate a scenario file with no daemon
and no model in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The six grading axes. Every scenario declares at least one; this drives the
# coverage map (a capability/axis with no asserting scenario is a finding).
AXES: frozenset[str] = frozenset({
    "context_persistence",   # a referent from turn k honored in turn k+n (one session)
    "memory_persistence",    # a fact from session A recalled in session B (across restart)
    "decomposer",            # compound split correctly; atomic not decomposed
    "routing",               # reaches the correct handler / route source
    "capability_recall",     # knows + offers/uses a capability it has
    "fabrication",           # live-state answers are backed by a check and consistent with it
})

# The model tiers a scenario validates. The locked-down 2B is the coverage
# floor; the 9B adds native-dispatch cells. A 9B-only scenario is not counted
# against 2B coverage.
POSTURES: frozenset[str] = frozenset({"2B-locked", "9B-native", "35B-native"})

# --- Posture semantics (the tier-expectation rubric, Decided 2026-07-25) ------
# A posture is a TIER EXPECTATION, not a model size label: it names what the tier
# is gated on, so a scenario can bind different assertions to different tiers.
#
#   2B-locked   — the coverage FLOOR. Locked-down behaviour: the code-owned fast
#                 paths answer, declines carry polite-steering language, no
#                 best-effort negotiation at the tier limit.
#   9B-native   — the workhorse. The model may satisfy an ask NATIVELY where the
#                 2B is gated to a fast path, and it is expected to negotiate at
#                 the tier limit (best-effort tone that invites push-back).
#   35B-native  — OWN-GATED (added 2026-07-25). The top tier is not graded by
#                 inheriting the 9B's expectations: it carries its own gated set.
#
# GATING RULE (unchanged mechanism, stated here because the 35B tier depends on
# it): an assertion whose `postures` list is NON-EMPTY is evaluated ONLY under a
# listed posture. An assertion with an EMPTY `postures` list is posture-agnostic
# and applies under every tier — it is the shared baseline, NOT an inherited
# tier expectation.
#
# OWN-GATED means the consequence of that rule is deliberate and load-bearing for
# 35B-native: an assertion marked ["9B-native"] does NOT reach the 35B tier. The
# 35B tier inherits a 9B expectation only where a scenario says so EXPLICITLY, by
# listing "35B-native" on that assertion. There is no default inheritance, and
# adding the posture to a scenario never silently re-points another tier's gates.
#
# ALLOWED ASSERTION CLASSES PER POSTURE. Which classes a posture may gate:
#
#   class                 2B-locked  9B-native  35B-native   notes
#   behaviour/content         yes       yes        yes       contains*/not_contains*
#   grounding/fabrication     yes       yes        yes       never tier-relaxed
#   tool discipline           yes       yes        yes       no_tool/uses_tool
#   routing (routes_via)      see below
#
# ROUTING IS NOT POSTURE-GATED (the claim-contract convention). A routing
# assertion states whether the CODE-OWNED FAST PATH CLAIMS the query — a property
# of the request and the routing code, not of the model tier. Posture-gated
# routes_via pairs predate this convention and are being retired under their own
# change; NO NEW posture-gated routes_via is authored, and 35B-native gates none.
#
# WHAT 35B-NATIVE IS GATED ON TODAY (and what is deliberately still open):
#   Gated now — the posture-agnostic baseline of every scenario that declares the
#   tier (behaviour/content, grounding/fabrication and tool-discipline assertions
#   all bind), plus the tier-limit negotiation tone, which is inherited from the
#   9B EXPLICITLY per assertion rather than by default.
#   NOT gated yet — the tier-specific STRICTER thresholds the rubric implies for
#   the top tier. Those are a measurement, not a judgement call: they ride the
#   per-tier assertion-config skeleton earned by ablation plus the judge, and the
#   run data that would set them is not in this tree. They are therefore left
#   OPEN rather than guessed — an invented threshold would grade the tier against
#   an author's assumption, which is the failure the calibration work exists to
#   prevent. The vocabulary and gating below are ready to carry them unchanged.
POSTURE_OWN_GATED: frozenset[str] = frozenset({"35B-native"})

SESSION_POLICIES: frozenset[str] = frozenset({"single-session", "multi-session"})

# Turn-level session boundary markers. None (no marker) is also valid. The
# restart-before marker is the true between-sessions signal; its execution is
# wired by a later phase, but the vocabulary is fixed here so scenarios can be
# authored against it now.
SESSION_MARKERS: frozenset[str] = frozenset({"restart-before", "new-session-before"})

# The terminal states of a held dispatch's review gate (the panel/WS consent
# lifecycle). A dispatch classified hold_for_review is shown for review and must
# reach exactly one of these — a gate that never resolves is a liveness failure,
# not a fifth state. `allow` leads to execution; `deny` refuses before running;
# `timeout` is the review window expiring with no decision (a fail-closed implicit
# deny); `cancel` is the user withdrawing the request.
GATE_OUTCOMES: frozenset[str] = frozenset({"allow", "deny", "timeout", "cancel"})

# Explicit assertion taxonomy. The schema validates only that a type is KNOWN;
# per-type evaluation (and the Gate-A/Gate-B split) belongs to the grader.
ASSERTION_TYPES: frozenset[str] = frozenset({
    # routing / decision
    "routes_via",            # value = route-source tag (keyword/cache/decomposed/llm_tools/…)
    "routes_via_any",        # value = comma-joined route-source tags, any of which
                             #   satisfies. For a query whose handler the
                             #   architecture decides from the query AND the data
                             #   (the single-value state cache claims a query only
                             #   when the cached value is single-line), so no single
                             #   source is the contract. A source outside the set
                             #   still fails hard.
    "uses_tool",             # value = tool name (the fabrication guard)
    "uses_any_tool",         # value = comma-joined tool names
    "uses_tool_for_clause",  # params{index} = the decomposer's 1-based sub-query
                             #   index, value = the tool THAT CLAUSE must
                             #   dispatch. Every other tool assertion reads the
                             #   turn's FLAT dispatch list, which cannot say
                             #   which half of a compound request a dispatch
                             #   served: "find X and use it to Y" was graded as
                             #   served while clause 2 only talked, because
                             #   clause 1's dispatch satisfied the flat check.
                             #   Read from the trace's per-clause attribution
                             #   (the router's prompt/subquery rows), and FAILS
                             #   CLOSED when no source attested that attribution
                             #   — an ordering guess is not a measurement.
    "no_tool",               # value = tool name that must NOT be called
    "tool_arg_contains",     # params{tool,key}, value = substring (the composition guard)
    "tool_result_nonempty",  # value = tool name
    "tool_output_contains",  # params{tool}, value = substring
    "dispatch_outcome",      # params{tool}, value = outcome (executed_success/deny/…)
    "gate_outcome",          # value = a GATE_OUTCOME (allow/deny/timeout/cancel):
                             #   the terminal state of a held dispatch's review
                             #   gate. A gate that was held but never resolved is a
                             #   liveness failure (WP-3.4).
    "decomposes_into",       # value = sub-request COUNT (int) or comma-list of
                             #   substrings each covered by a sub-request — the
                             #   decomposer-tree structural assertion (WP-2.4)
    # grounding / fabrication
    "answer_consistent_with_tool",  # params{tool}: answer polarity must match the tool result
    "no_fabricated_success",        # a completed-action claim needs a matching successful dispatch
    "no_fabricated_state",          # value = kind (printers/disk/services/hours): must be backed by a check
    "no_invented_artifact",         # no fabricated URL shape / phone / device path
    "no_fabricated_citation",       # value = comma-joined citations legitimately in
                                    #   the turn's provided context (empty = none). A
                                    #   citation SHAPE in the reply — DOI, ISBN,
                                    #   page-number cite, external URL, or wiki-path —
                                    #   that is NOT present in the turn's provided
                                    #   context (the user's turn text) or this value
                                    #   allow-list is a fabrication. Stricter than the
                                    #   always-on no_invented_artifact (which allow-lists
                                    #   the system's own doc hosts): here a citation is
                                    #   legitimate ONLY if the scenario actually provided
                                    #   it — the context-grounding half of citation honesty.
    # content
    "contains",
    "contains_any",          # value = comma-joined alternatives
    "not_contains",
    "not_contains_any",      # value = comma-joined alternatives; FAILS if the reply
                             #   contains ANY of them — the negative mirror of
                             #   contains_any, one guard replacing N stacked
                             #   not_contains lines
    "no_negation",           # value = keyword present but NOT inside a can't/unable negation.
                             #   The value may NAME A CAPABILITY instead of
                             #   spelling its wording out — "capability:<tool>"
                             #   resolves through capability_registry at grade
                             #   time, so the product owns the phrase and the
                             #   corpus cannot drift from it. Any text-matching
                             #   assertion type accepts the same reference.
    "escalation_offered",    # the reply carried an offer to consult the frontier
                             #   model. Read from RouteResult.escalation_offer,
                             #   never sniffed out of the answer text: the offer
                             #   is a decision on its own field. An optional value
                             #   must also appear IN the offer, which is how a
                             #   scenario pins the phrase the offer tells the user
                             #   to type.
    "no_escalation_offer",   # the reply carried NO such offer. The half with
                             #   teeth: the offer fires selectively, so a scenario
                             #   has to be able to say "not on this turn".
    "source",                # a citation is present
    "source_any",            # value = comma-joined citation alternatives
    "self_consistent",       # must not enumerate results and also claim none were found
    "no_self_contradiction", # value = the SUBJECT (a name, or comma-joined
                             #   aliases). The reply must not assert both a
                             #   positive and a negative state about it. A
                             #   decomposed answer is where this happens: each
                             #   clause is answered on its own and nothing
                             #   reconciles them, so one reply said a package was
                             #   "not installed" and, two lines later, "already
                             #   installed". self_consistent catches exactly one
                             #   shape (enumerating items while claiming none
                             #   were found) and cannot see this one. A state
                             #   that CHANGED within the turn ("was not
                             #   installed, so I installed it") is a sequence,
                             #   not a contradiction, and does not fail.
    "not_repeat_of_previous",  # the reply must not be a replay of the PREVIOUS turn's
                             #   reply. The only assertion that compares a turn to the
                             #   turn before it. Authored for the elliptical-reply shape
                             #   ("wha?", "huh?"): a person who did not understand an
                             #   answer is asking for a different one, and repeating the
                             #   same words is the one response that cannot help them.
                             #   Measured in the field 2026-08-24: "wha?" returned the
                             #   preceding answer verbatim. `value` is an optional
                             #   similarity ceiling as a percentage (default 90); a reply
                             #   sharing more than that share of the previous reply's
                             #   lines, or repeating its opening, is a replay.
})

# Auto-assertions appended to EVERY turn at grade time unless a named member of
# a turn's skip_auto suppresses that specific one. These guarantee no turn is
# vacuous: an empty explicit assertion list still yields several real checks.
AUTO_ASSERTION_TYPES: frozenset[str] = frozenset({
    "non_empty",
    "no_filler",
    "no_wrong_package_manager",
    "no_hallucinated_device_path",
    "no_capability_denial",
    # answer_responsive is the only auto-assertion that compares the answer to
    # the QUESTION rather than to the trace. Every other check in the harness
    # grades the decision (right route, right tool, backed claim), which is how a
    # turn asking "search for a pdf editor" and answered "Disk usage is
    # available." passed every assertion it carried. Deterministic, no model:
    # see intergen.tests.scenario.responsiveness for the mechanism and for the
    # explicit statement of what it can and cannot catch.
    "answer_responsive",
})

# Some auto-assertions do not apply to certain scenario categories. A refusal /
# safety scenario SHOULD decline, so "no_capability_denial" must not fire there.
# Category matching is case-insensitive and substring-based so "refusals",
# "refusal", and "safety_decline" all match.
_AUTO_SUPPRESSED_BY_CATEGORY: dict[str, frozenset[str]] = {
    "no_capability_denial": frozenset({"safety", "refusal"}),
}


def applicable_auto_assertions(category: str) -> frozenset[str]:
    """The auto-assertions that apply to a scenario in `category`.

    An auto-assertion is dropped for a category when that category is one where
    the assertion would be wrong (e.g. no_capability_denial on a refusal
    scenario, where a decline is the correct behavior).
    """
    cat = (category or "").lower()
    out = set(AUTO_ASSERTION_TYPES)
    for auto, suppressed_in in _AUTO_SUPPRESSED_BY_CATEGORY.items():
        if any(marker in cat for marker in suppressed_in):
            out.discard(auto)
    return frozenset(out)


@dataclass
class Assertion:
    """A single machine-checkable claim about a turn's response.

    `type` is one of ASSERTION_TYPES. `value` is the primary expected string
    (a tool name, a substring, a route-source tag). `params` carries extra
    arguments for the multi-argument types (e.g. tool_arg_contains needs
    params={"tool": ..., "key": ...} with value = the expected substring).
    Evaluation is the grader's job, not the schema's.
    """
    type: str
    value: str = ""
    params: dict = field(default_factory=dict)
    description: str = ""
    # Posture gating (WP-4.1). Empty = the assertion applies under EVERY posture
    # (the common case). When set, the assertion is evaluated ONLY when grading
    # under a listed posture — this is how a routing expectation differs by tier:
    # under 2B-locked a request routes via the code dispatch path, under 9B-native
    # the model decides tools natively, so the two postures assert different
    # route sources on the same turn.
    postures: list[str] = field(default_factory=list)
    # Gate override ("A" | "B"). Empty = the gate the TYPE implies (the common
    # case; the grader owns that mapping). An individual assertion sets "B" when
    # the thing it actually checks is PHRASING rather than a decision — e.g. a
    # contains_any listing connective words the model may or may not use. Such an
    # assertion is still evaluated and still reported; it reports MIXED instead of
    # hard-failing a turn over wording. It is a re-scope, never a suppression: "A"
    # can never be weakened silently, because the override is written in the
    # fixture where a reviewer sees it.
    gate: str = ""


@dataclass
class Phrasing:
    """An alternate wording of a turn's input that must satisfy the SAME
    assertions — the unit of a phrasing family (WP-2.3).

    A class is not a single sentence: the same request arrives colloquial,
    imperative, polite, emotional, or sloppy (typos), and the invariant must hold
    across all of them. A finding that behavior FLIPS on wording (the tool-
    grounding brittleness surfaced in the phase-1 sweep) is exactly what a
    phrasing family makes visible — each wording becomes its own graded sibling.
    `label` names the wording class (colloquial/imperative/…) so the expanded
    variant's id is auditable.
    """
    text: str
    label: str = ""


@dataclass
class Turn:
    """One user input and the assertions its response must satisfy.

    `session_marker` (None | restart-before | new-session-before) requests a
    session boundary BEFORE the turn is sent. `skip_auto` names the specific
    auto-assertions to suppress for this turn — always narrow; a turn can never
    suppress its way to zero effective assertions (the loader rejects that).
    `phrasings` are alternate wordings of THIS turn that expand (via the family
    expander) into sibling scenarios sharing these same assertions.
    """
    user: str
    assertions: list[Assertion] = field(default_factory=list)
    speaker: str = "user"
    description: str = ""
    session_marker: str | None = None
    skip_auto: list[str] = field(default_factory=list)
    phrasings: list[Phrasing] = field(default_factory=list)


@dataclass
class Scenario:
    """A scripted multi-turn conversation graded against the six axes.

    `id` is the stable join key across logs, results, the comparator, and
    cleanup. `axis` is one or more of AXES (required — it drives coverage).
    `postures` names the model tiers this scenario validates. `capabilities`
    names the inventory rows this scenario claims to cover (authoritative when
    set). `cleanup`/`cleanup_for` express artifact isolation and linked pairs.
    """
    id: str
    name: str
    axis: list[str]
    turns: list[Turn]
    category: str = ""
    postures: list[str] = field(default_factory=lambda: ["2B-locked"])
    capabilities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    session_policy: str = "single-session"
    cleanup: bool = True
    cleanup_for: list[str] = field(default_factory=list)


def effective_assertion_count(turn: Turn, category: str) -> int:
    """How many real checks a turn carries once auto-assertions are folded in.

    = explicit assertions + (auto-assertions applicable to the category that the
    turn did not suppress via skip_auto). The loader requires this to be > 0 for
    every turn, which is what makes a vacuous always-pass turn impossible to
    author (the failure mode a prior apparatus rotted into).
    """
    autos = applicable_auto_assertions(category) - set(turn.skip_auto)
    return len(turn.assertions) + len(autos)
