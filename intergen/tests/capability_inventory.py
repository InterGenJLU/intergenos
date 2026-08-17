# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Capability inventory for the eval harness — the canonical coverage axis.

Why this exists
---------------
The eval harness grades whether a turn routed and recovered correctly, but a
green run says nothing about WHAT it covered. A capability that quietly loses
all of its cells — a tool no conversation exercises any more, a gate-branch that
stopped being driven — reads as "all clean" while coverage silently erodes.
This module makes coverage a first-class, enumerable thing: the canonical set of
user-facing tools, each classified by whether it has a consent-gated dispatch
branch, plus the machinery to extract which cells a run actually covered and
which capabilities are covered by nothing at all.

The comparator (compare_runs.py) consumes the coverage SET so a vanished cell
registers as a regression in its own right — missing-cell=fail applied across
runs, not only within one.

Classification is DERIVED from each tool's own declared safety posture in
intergen/tools/, never guessed:
  * a tool whose default safety_tier is SafetyTier.AUTO is read/query — it never
    reaches the consent gate (read_file, analyze_file, web_search,
    open_application);
  * a tool that declares SafetyTier.CONFIRM (and a classify_safety that can
    return CONFIRM/BLOCKED for state-changing arguments) HAS a gated branch
    (manage_packages, manage_services, write_file, run_command, take_screenshot).
    manage_packages / manage_services are conditionally gated — their read-only
    sub-actions (list/search/info/verify/status) stay AUTO — but the capability
    HAS a gated branch, which is what coverage tracks.

When a tool is added under intergen/tools/, add it here in the matching set; the
module asserts at import that every tool module is classified, so a new tool
cannot silently escape the inventory.
"""

from __future__ import annotations

from pathlib import Path

# ── The canonical inventory ──
# Gated: has a consent-gated (SafetyTier.CONFIRM / privileged-review) dispatch
# branch. These are where a gate defect like F2 can hide, so the matrix must
# cover each one's gate-lifecycle (deny / timeout / cancel / reject) explicitly.
GATED_TOOLS = frozenset({
    "manage_packages",
    "manage_services",
    "write_file",
    "run_command",
    "take_screenshot",
})

# Read/query: SafetyTier.AUTO only — never reaches the gate. Covered for routing
# and answer-quality, but they have no gate-branch to drive.
READ_TOOLS = frozenset({
    "read_file",
    "analyze_file",
    "web_search",
    "open_application",
})

ALL_TOOLS = GATED_TOOLS | READ_TOOLS

# The tool-dispatch OUTCOME axis (the 8 outcomes from the PR3 matrix design).
# A gated capability's coverage is only complete when every applicable outcome
# has a cell; missing-cell=fail. Read/query capabilities only reach the
# non-gate outcomes (executed_success / executed_fail).
GATE_OUTCOMES = (
    "executed_success",
    "executed_fail",
    "deny",
    "gate_timeout",
    "cancel",
    "policy_reject",     # missing-provenance / D-008 class
    "safety_decline",    # denylist / destructive-tier block
    "malformed_reject",  # dialect-parsed-but-refused
)
# Outcomes a read/query (never-gated) capability can legitimately reach.
READ_OUTCOMES = ("executed_success", "executed_fail")

# The teaching / route-driver NEGATIVE axis: a cell asserting the model TEACHES a
# capability and never dispatches it (asserts no_tool). It is its OWN coverage axis,
# distinct from the gate outcomes — a teaching cell does NOT cover any gate branch,
# so a gated tool with only a teaching cell still has all its gate outcomes missing.
# (WC PR3 coverage-granularity red-team, 2026-06-29.)
TEACHING_OUTCOME = "teaching"

# Every outcome a cell may legitimately declare.
_VALID_OUTCOMES = (
    frozenset(GATE_OUTCOMES) | frozenset(READ_OUTCOMES)
    | {TEACHING_OUTCOME, "unspecified"}
)

# Category → the capability its conversations are about, for conversations that
# carry no explicit tool signal (a derivation fallback, never an override).
_CATEGORY_CAPABILITY = {
    "package_management": "manage_packages",
    "service_management": "manage_services",
}


def capability_class(name: str) -> str:
    """'gated' | 'read' | 'unknown' for a tool/capability name."""
    if name in GATED_TOOLS:
        return "gated"
    if name in READ_TOOLS:
        return "read"
    return "unknown"


def is_gated(name: str) -> bool:
    return name in GATED_TOOLS


def _tool_modules() -> set[str]:
    """The tool names that actually exist under intergen/tools/ (drift guard)."""
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    names = set()
    for p in tools_dir.glob("*.py"):
        if p.stem in {"__init__", "latency_harness"}:
            continue  # not user-facing tools
        names.add(p.stem)
    return names


def conversation_capabilities(conv_result: dict) -> set[str]:
    """The set of capabilities a single conversation result exercises.

      1. an explicit `capabilities:` tag on the conversation (PR3 cells declare
         theirs) is AUTHORITATIVE — it replaces derivation entirely;
      2. otherwise the UNION of tools actually dispatched in any turn
         (turn_details[].tool_calls) and tools the turns declare via `tool_used`
         assertions — both are real coverage signals for the same conversation;
      3. a category→capability fallback ONLY when there is no tool signal at all.
    Returns an empty set for a pure-knowledge conversation that touches no tool.
    """
    declared = conv_result.get("capabilities")
    if declared:
        return {c for c in declared if c in ALL_TOOLS}

    caps: set[str] = set()
    for turn in conv_result.get("turn_details", []):
        for tc in turn.get("tool_calls", []) or []:
            name = tc.get("name", "")
            if name in ALL_TOOLS:
                caps.add(name)
        for a in turn.get("assertions", []) or []:
            if a.get("type") == "tool_used" and a.get("value") in ALL_TOOLS:
                caps.add(a["value"])
    if caps:
        return caps

    cat = conv_result.get("category", "")
    fallback = _CATEGORY_CAPABILITY.get(cat)
    return {fallback} if fallback else set()


def conversation_outcome(conv_result: dict) -> str:
    """The tool-dispatch OUTCOME a conversation exercises (the GATE_OUTCOMES axis).

      1. an explicit `outcome:` tag on the conversation is AUTHORITATIVE — WS-driven
         cells declare deny / gate_timeout / cancel / policy_reject / safety_decline /
         malformed_reject, outcomes that cannot be derived from a runner result.json;
      2. else derived from the TRACE: a cell asserting no_tool with a declared
         capability is the TEACHING negative; an observed dispatch splits via the
         span — executed_success if no dispatch failed; executed_fail only for a
         genuine RUN-then-error (dispatch_any_failed AND NOT dispatch_any_denied);
         a failed-but-DENIED dispatch (a pre-run reject — executed=False) is
         'unspecified', and no dispatch at all is 'unspecified'.

    Derivation reads the TRACE, NEVER the grade. Coverage and grade are orthogonal: a
    dispatch's executed_success/_fail comes from whether the TOOL errored
    (dispatch_any_failed on the span), not from whether the conversation's assertions
    passed — so a FAIL->PASS grade improvement cannot flip the outcome and falsely read
    as cell erosion. The executed split IS observable from the trace, so it is derived
    (refined 2026-06-29 in the read-tool-matrix review: turn_details tool_calls carry
    only name+args, the per-call success/fail lives in the span). The GATE-branch
    outcomes (deny/timeout/cancel/reject/etc) are NOT observable from a plain runner
    result and stay DECLARED-only — an undeclared gate branch reads as a coverage GAP
    rather than being inferred. executed_fail still needs a DRIVER cell that provokes the
    error (you cannot observe an error you do not cause) AND a run that captured the
    trace (--observe); once provoked + traced it derives with no declare tag, and an
    un-traced dispatch (no span) reads executed_success since no failure is observable.

    SINGLE-OUTCOME-PER-CONVERSATION caveat (coverage authoring constraint): this returns
    ONE outcome for the whole conversation, and coverage_set fans it across EVERY
    capability the conversation exercises. So a multi-dispatch turn where one tool errors
    and another succeeds would label BOTH executed_fail (any_failed is turn-wide, not
    per-call). An executed_fail DRIVER cell must therefore be SINGLE-dispatch — one read
    tool, one provoked error — or it mislabels a co-dispatched success as a fail. (The
    natural driver-cell design anyway; noted so the constraint is legible, not silent.)
    """
    declared = conv_result.get("outcome")
    if declared in _VALID_OUTCOMES:
        return declared

    asserts_no_tool = any(
        a.get("type") == "no_tool"
        for turn in conv_result.get("turn_details", [])
        for a in (turn.get("assertions", []) or [])
    )
    if asserts_no_tool and conv_result.get("capabilities"):
        return TEACHING_OUTCOME

    dispatched = any(
        (turn.get("tool_calls") or [])
        for turn in conv_result.get("turn_details", [])
    )
    if not dispatched:
        return "unspecified"
    any_failed = any(
        s.get("attributes", {}).get("dispatch_any_failed")
        for turn in conv_result.get("turn_details", [])
        for s in (turn.get("trace", []) or [])
    )
    if not any_failed:
        return "executed_success"
    # A failed dispatch is executed_fail ONLY for a genuine RUN-then-error (the tool
    # executed and errored: executed=True, success=False). A pre-run REJECTION
    # (validation/schema reject: executed=False, success=False) ALSO carries
    # dispatch_any_failed, but it shapes as dispatch_any_denied and exercises the
    # reject path, NOT the run-then-error path executed_fail is meant to cover. So a
    # rejected dispatch must NOT false-claim executed_fail coverage — it stays a
    # visible gap ('unspecified'), so the missing run-then-error driver is not masked.
    # This makes the derivation itself ENFORCE the run-then-error requirement, rather
    # than relying on cell-authoring vigilance. (WC pre-run-reject criterion, 2026-06-29.)
    any_denied = any(
        s.get("attributes", {}).get("dispatch_any_denied")
        for turn in conv_result.get("turn_details", [])
        for s in (turn.get("trace", []) or [])
    )
    return "executed_fail" if not any_denied else "unspecified"


def coverage_set(run_data: dict) -> set[tuple[str, str, str]]:
    """The (capability, outcome, conversation_id) cells a run covered.

    A run "covers" a cell when one of its conversations exercises a capability via a
    specific outcome. The comparator diffs this set across runs: a cell present in an
    earlier run and absent now is coverage erosion — a removed conversation, one that
    stopped exercising the capability, OR one that no longer drives that OUTCOME — and
    is flagged at the same severity as a pass→fail. Keying on the outcome (not just the
    capability) is what makes a lost gate-branch cell a visible regression rather than
    being masked by some other still-present cell on the same capability.
    """
    cells: set[tuple[str, str, str]] = set()
    for conv in run_data.get("conversations", []):
        cid = conv.get("id", "")
        outcome = conversation_outcome(conv)
        for cap in conversation_capabilities(conv):
            cells.add((cap, outcome, cid))
    return cells


def covered_capabilities(run_data: dict) -> set[str]:
    """Capabilities exercised by at least one conversation in the run."""
    return {cap for cap, _o, _cid in coverage_set(run_data)}


def covered_capability_outcomes(run_data: dict) -> set[tuple[str, str]]:
    """The (capability, outcome) pairs at least one conversation covers."""
    return {(cap, o) for cap, o, _cid in coverage_set(run_data)}


# Capabilities whose LIVE gate coverage is not corpus-viable on the shipped small
# model: empirically the 2B teaches "create a file" and auto-safes "echo hello"
# instead of dispatching write_file/run_command into the gate, so a live corpus gate
# cell for them is not drivable — their gate MECHANISM is proven deterministically at
# the unit + dbus layer instead. The gap report ANNOTATES (never drops) their
# gate-outcome gaps with this, so a permanent gap reads "mechanism-covered elsewhere,
# not corpus-viable here" rather than "untested mechanism" — a bare un-closable gap is
# its own erosion; an annotated one stays honest + legible. Silent exclusion would
# mask. (convergent review, inventory decision, 2026-06-29.)
_NOT_CORPUS_VIABLE_GATE = {
    "write_file": "2B teaches instead of dispatching; gate mechanism proven at unit "
                  "88de2ec9 + dbus d8bf42cc, not drivable as a live corpus cell",
    "run_command": "2B auto-safes instead of dispatching; gate mechanism proven at "
                   "unit 88de2ec9 + dbus d8bf42cc, not drivable as a live corpus cell",
    # take_screenshot: a consent-gated privacy tool with no established live corpus
    # driver on the 2B (an allow captures the screen/webcam — unit-only). Its gate
    # deny-no-wedge mechanism is REALLY covered at the cross-tool unit deny test
    # (test_deny_no_wedge_across_all_gated_tools), so the note CITES that coverage
    # instead of reading as an untested gate. (WC corpus-complete red-team, 2026-06-29.)
    "take_screenshot": "consent-gated privacy capture is unit-only (an allow captures "
                       "the screen/webcam); gate deny-no-wedge mechanism proven at the "
                       "cross-tool unit deny test, not drivable as a live corpus cell",
}

# Per-gated-tool corpus-REQUIRED gate outcomes — the outcomes that are actually
# drivable as a live corpus cell on the shipped model, so a missing one IS a real gap
# (missing-cell=fail). Every OTHER gate outcome for the tool is annotated, not chased.
# A flat all-8 bar can never reach zero (executed_* mutate the box, gate_timeout is a
# 300s wait, cancel has no corpus driver, etc.), so a per-tool applicable set is what
# makes the bar honest and closable. Grounded in empirical dispatch probing
# (2026-06-29): the 2B reliably dispatches manage_services / manage_packages into the
# gate (deny corpus-viable, both landed); it teaches/auto-safes write_file / run_command
# and has no established corpus driver for take_screenshot. (data-model decision.)
REQUIRED_GATE_OUTCOMES = {
    "manage_services": ("deny",),
    "manage_packages": ("deny",),
    "write_file": (),
    "run_command": (),
    "take_screenshot": (),
}

# Per-outcome reason a NON-required gate outcome is annotated rather than chased.
_OUTCOME_ANNOTATION = {
    "executed_success": "allow-execute mutates the box — unit-only, does not count "
                        "toward corpus coverage (the unit-cells-don't-count call)",
    "executed_fail": "allow-execute error path mutates the box — unit-only",
    "gate_timeout": "the 300s gate wait is not corpus-practical",
    "cancel": "no corpus driver for a mid-gate cancel",
    "policy_reject": "specialized missing-provenance path, not a natural corpus phrasing",
    "safety_decline": "candidate via existing SAFETY cells (taggable); promote to "
                      "required once a SAFETY cell tags outcome=safety_decline",
    "malformed_reject": "specialized dialect-reject path, not a natural corpus phrasing",
    "deny": "no established live corpus driver on this model for this tool",
}


def coverage_note(capability: str, outcome: str) -> str | None:
    """A legibility note for an acceptable / not-corpus-required (capability, outcome)
    gap, or None when the outcome IS corpus-required for the tool (a real chase gap).
    NEVER removes the outcome from the gap set — it annotates it, so a reader sees
    "covered elsewhere / not corpus-viable here" instead of chasing an un-closable gap.
    """
    if capability not in GATED_TOOLS or outcome not in GATE_OUTCOMES:
        return None
    if outcome in REQUIRED_GATE_OUTCOMES.get(capability, ()):
        return None  # required for this tool -> a real gap, no annotation
    # write_file/run_command: the whole gate row is not corpus-viable (tool-level note,
    # cites the unit+dbus mechanism coverage). Everyone else: a per-outcome reason.
    if capability in _NOT_CORPUS_VIABLE_GATE:
        return _NOT_CORPUS_VIABLE_GATE[capability]
    return _OUTCOME_ANNOTATION.get(outcome, "not corpus-required for this tool")


def coverage_gaps(run_data: dict) -> dict[str, dict]:
    """OUTCOME-granular coverage gaps against the canonical inventory.

    A gated capability is COMPLETE only when EVERY gate outcome (GATE_OUTCOMES) has a
    cell AND it has a teaching cell; until then its missing outcomes are reported. This
    wires the GATE_OUTCOMES axis into the signal so missing-cell=fail bites per OUTCOME,
    not per capability — a gated tool no longer reads "covered" off a single
    teaching-negative cell while its deny / timeout / cancel / reject branches sit
    untested (the green-too-early hole WC's red-team caught, 2026-06-29). A read/query
    capability only needs the two executed outcomes.

    This measures HARNESS (Conversation) coverage — the live-surface cells the runner
    drives. Deterministic UNIT cells (the pytest deny tests, the dbus D-cells) are
    complementary defense and intentionally do NOT count here: a gate branch covered
    only by a unit test STILL reads as a harness gap until its live-surface cell exists,
    because the live surface is exactly where the F2 class hid while the unit/synchronous
    path passed. Suppressing the harness-gap signal on the strength of a unit test would
    be the masking this signal exists to prevent. (data-model decision, red-team
    residual, 2026-06-29.)

    A missing outcome carrying a coverage_note (e.g. a gate branch the shipped model
    does not dispatch into) is reported but ANNOTATED, so it reads as "covered
    elsewhere / not corpus-viable" rather than an untested gap. Notes never shrink the
    missing set — silent exclusion would mask.

    Returns {'gated': {tool: {'missing_outcomes': [...], 'required_missing': [...],
                              'teaching_covered': bool, 'corpus_complete': bool,
                              'notes': {outcome: note}}},
             'read':  {tool: {'missing_outcomes': [...]}}} — only tools with a gap.
    `required_missing` is the chase-these set; `corpus_complete` is True when a gated
    tool has every corpus-required gate outcome + a teaching cell (the rest annotated).
    """
    covered = covered_capability_outcomes(run_data)
    gated: dict[str, dict] = {}
    for tool in sorted(GATED_TOOLS):
        missing = [o for o in GATE_OUTCOMES if (tool, o) not in covered]
        required = REQUIRED_GATE_OUTCOMES.get(tool, ())
        required_missing = [o for o in required if (tool, o) not in covered]
        teaching_covered = (tool, TEACHING_OUTCOME) in covered
        # CORPUS-complete = every corpus-required gate outcome has a cell AND a teaching
        # cell exists. The non-required outcomes stay annotated, never block completion.
        corpus_complete = not required_missing and teaching_covered
        if missing or not teaching_covered:
            notes = {o: coverage_note(tool, o) for o in missing
                     if coverage_note(tool, o)}
            gated[tool] = {"missing_outcomes": missing,
                           "required_missing": required_missing,
                           "teaching_covered": teaching_covered,
                           "corpus_complete": corpus_complete,
                           "notes": notes}
    read: dict[str, dict] = {}
    for tool in sorted(READ_TOOLS):
        missing = [o for o in READ_OUTCOMES if (tool, o) not in covered]
        if missing:
            read[tool] = {"missing_outcomes": missing}
    return {"gated": gated, "read": read}


def outcome_consistency(run_data: dict) -> list[dict]:
    """Falsifiability guard on the AUTHORITATIVE declared `outcome` tag: a declared tag
    must not contradict what the run actually did. Decoupled from grade. Returns a list
    of {id, declared, reason} inconsistencies (empty = clean).

    Derivable invariants, from the runner result:
      * declared executed_success / executed_fail REQUIRES an observed tool dispatch;
      * declared teaching REQUIRES NO observed dispatch (it is the never-dispatch axis).

    Declared gate-branch outcomes (deny / gate_timeout / cancel / policy_reject /
    safety_decline / malformed_reject) run over the WS/dyno gate path, whose decision
    the standard runner result does not carry — those are asserted by the harness cells'
    own gate checks (gate_resolved_decision + the deny-content assertion), not here. The
    guard keeps the cheap, declaration-vs-reality contradiction loud without coupling to
    grade. (WC hardening note, 2026-06-29.)
    """
    bad = []
    for conv in run_data.get("conversations", []):
        declared = conv.get("outcome")
        if not declared:
            continue
        dispatched = any((t.get("tool_calls") or [])
                         for t in conv.get("turn_details", []))
        if declared in ("executed_success", "executed_fail") and not dispatched:
            bad.append({"id": conv.get("id", ""), "declared": declared,
                        "reason": "declared executed_* but no tool dispatch observed"})
        elif declared == TEACHING_OUTCOME and dispatched:
            bad.append({"id": conv.get("id", ""), "declared": declared,
                        "reason": "declared teaching but a tool dispatch was observed"})
    return bad


# Import-time drift guard: every real tool module must be classified, and the
# inventory must not name a tool that does not exist. Either drift means the
# inventory has gone stale against intergen/tools/ — fail loudly at import so a
# coverage report can never be quietly computed against a wrong inventory.
def _assert_inventory_matches_tree() -> None:
    on_disk = _tool_modules()
    classified = ALL_TOOLS
    missing = on_disk - classified
    phantom = classified - on_disk
    if missing or phantom:
        raise RuntimeError(
            "capability_inventory is out of sync with intergen/tools/: "
            f"unclassified tool modules={sorted(missing)} "
            f"inventoried-but-absent={sorted(phantom)} — update GATED_TOOLS / "
            "READ_TOOLS to match the tree.")


_assert_inventory_matches_tree()
