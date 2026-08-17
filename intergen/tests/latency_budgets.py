# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Latency budgets as harness asserts (blueprint §5 "Quick"; work-plan 5.1 Leg B).

The "Quick" rubric axis, made a measurable: each routing path has a warm p50
latency ceiling, and a battery/dyno run turns a breach into a NAMED FAIL instead
of a silent regression. This is an EVAL-LANE checker — pure functions over a
turn's trace fields (route/decided source + dur_ms, route_completed used_llm +
tool_count + latency_ms, prompt/assembled the r36 budget fields). It never runs
in, and imports nothing from, the runtime serving path.

Two independent assert families:

  * PER-PATH LATENCY (warm p50). classify_path() maps a turn's (source, used_llm,
    tool_count) to a path class; check_turn_latency() flags a warm turn whose
    latency exceeds that class's ceiling. COLD-START IS EXEMPT (blueprint §5: the
    warm-on-start check M6 proved) — a cold turn never fails. A class with NO
    ceiling yet (tool-routed model+exec) is reported, never failed — the ceiling
    is set from a 9B-GPU-box measurement, NOT invented here.

  * PROMPT BUDGET. check_prompt_budget() flags a turn whose assembled prompt
    breached its per-path system-prompt budget (r36 glass
    prompt/assembled.system_prompt_over_budget) — a prefill regression is a named
    FAIL, the exact silent-growth class M6 leg 1 added the meter for.

BOX-AWARE (dispatch): the ceilings are the 9B-GPU reference box warm p50s. On a box
that is not the 9B GPU box (e.g. the CPU-served 2B .192), the absolute GPU
ceilings do not apply and would false-fail, so ENFORCEMENT is opt-in per
`budgets_from_env()` — the 9B profile enforces, every other profile records
report-only (budgets=None => no path-latency FAIL). The CHECKER LOGIC is pure and
budget-injected, so the daemon-free tests exercise it deterministically on any box.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# Path classes (dispatch's named paths).
FAST_PATH = "fast_path"                     # no model call (deterministic route)
MODEL_CONVERSATIONAL = "model_conversational"   # model, no tools
SYSTEM_MAP = "system_map"                   # grounded system-state answer
DECOMPOSED_COMPOUND = "decomposed_compound"     # multi-clause decomposition
TOOL_ROUTED_EXEC = "tool_routed_exec"       # model + tool dispatch + execution


# Warm p50 ceilings in ms — the 9B-GPU reference box measurements banked from
# the M6 leg-4 latency wave (2026-07-08). TOOL_ROUTED_EXEC is deliberately None:
# no ceiling has been measured yet, so it is REPORTED, never FAILED — set it from
# a 9B-GPU-box measurement after the 4.3 wave frees the box, never invent it.
WARM_BUDGETS_MS_9B_GPU: dict[str, int | None] = {
    FAST_PATH: 100,
    MODEL_CONVERSATIONAL: 1200,     # measured ~650-750 warm
    SYSTEM_MAP: 1200,
    DECOMPOSED_COMPOUND: 2500,
    TOOL_ROUTED_EXEC: None,         # UNMEASURED — do not invent
}

# The M2b embed hot-path add ceiling (design §3 budget; measured 12ms). Asserted
# separately from the path budgets because it is an ADD to a model turn, not a
# path total.
M2B_EMBED_ADD_CEILING_MS = 50


def classify_path(source: str, used_llm: bool, tool_count: int = 0) -> str:
    """Map a turn's route outcome to its latency path class.

    used_llm is the primary discriminant: a route that never called the model is
    a fast path regardless of its source name (cache/identity/keyword/semantic/
    memory/ip/explain-decline/... all resolve deterministically). Among model
    turns, system_map and decomposed are named by their source; a turn that
    dispatched a tool (tool_count>0 or the llm_tools source) is the tool-routed
    exec class; everything else model is conversational.
    """
    if not used_llm:
        return FAST_PATH
    if source == "system_map":
        return SYSTEM_MAP
    if source == "decomposed":
        return DECOMPOSED_COMPOUND
    if source == "llm_tools" or tool_count > 0:
        return TOOL_ROUTED_EXEC
    return MODEL_CONVERSATIONAL


@dataclass
class LatencyVerdict:
    path_class: str
    latency_ms: float
    ceiling_ms: int | None
    warm: bool
    ok: bool
    reason: str


def check_turn_latency(source: str, used_llm: bool, latency_ms: float, *,
                       tool_count: int = 0, warm: bool = True,
                       budgets: dict[str, int | None] | None = WARM_BUDGETS_MS_9B_GPU
                       ) -> LatencyVerdict:
    """Verdict for one turn. ok=False is a NAMED battery FAIL.

    - Cold-start (warm=False) is EXEMPT — always ok (blueprint §5).
    - budgets=None (a non-enforcing box profile) is report-only — always ok.
    - A path class whose ceiling is None (unmeasured) is reported, never failed.
    - Otherwise a warm latency strictly greater than the ceiling FAILS.
    """
    path_class = classify_path(source, used_llm, tool_count)
    ceiling = budgets.get(path_class) if budgets is not None else None
    if not warm:
        return LatencyVerdict(path_class, latency_ms, ceiling, warm, True,
                              "cold-start exempt")
    if budgets is None:
        return LatencyVerdict(path_class, latency_ms, ceiling, warm, True,
                              "report-only (no enforcing profile for this box)")
    if ceiling is None:
        return LatencyVerdict(path_class, latency_ms, ceiling, warm, True,
                              f"{path_class}: no ceiling measured yet — reported, not failed")
    over = latency_ms > ceiling
    return LatencyVerdict(
        path_class, latency_ms, ceiling, warm, not over,
        (f"{path_class} {latency_ms:.0f}ms <= {ceiling}ms" if not over
         else f"BUDGET FAIL: {path_class} {latency_ms:.0f}ms > {ceiling}ms warm p50 ceiling"))


@dataclass
class BudgetVerdict:
    system_variant: str
    with_tools: bool
    system_prompt_chars: int
    budget_chars: int
    ok: bool
    reason: str


def check_prompt_budget(assembled_detail: dict) -> BudgetVerdict:
    """Verdict from a prompt/assembled glass detail (r36 fields). A system-prompt
    over-budget is a NAMED FAIL — the prefill-regression class M6 added the meter
    for. Reads the recorded over-budget flag directly (the runtime computed it
    against the per-path budget), so the harness and the runtime never disagree.
    """
    chars = assembled_detail.get("system_prompt_chars", 0)
    budget = assembled_detail.get("system_prompt_budget_chars", 0)
    over = bool(assembled_detail.get("system_prompt_over_budget", False))
    variant = assembled_detail.get("system_variant", "general")
    with_tools = bool(assembled_detail.get("with_tools", False))
    return BudgetVerdict(
        variant, with_tools, chars, budget, not over,
        (f"prompt budget ok: {variant}/tools={with_tools} {chars} <= {budget} chars"
         if not over
         else f"BUDGET FAIL: system prompt {variant}/tools={with_tools} "
              f"{chars} chars > {budget} budget (prefill regression)"))


def check_embed_add(embed_ms: float, *, ceiling_ms: int = M2B_EMBED_ADD_CEILING_MS,
                    warm: bool = True) -> LatencyVerdict:
    """The M2b embed hot-path add (design §3, measured 12ms). Cold-exempt; a warm
    add over the ceiling FAILS."""
    if not warm:
        return LatencyVerdict("m2b_embed_add", embed_ms, ceiling_ms, warm, True,
                              "cold-start exempt")
    over = embed_ms > ceiling_ms
    return LatencyVerdict(
        "m2b_embed_add", embed_ms, ceiling_ms, warm, not over,
        (f"embed add {embed_ms:.0f}ms <= {ceiling_ms}ms" if not over
         else f"BUDGET FAIL: M2b embed add {embed_ms:.0f}ms > {ceiling_ms}ms ceiling"))


def apply_latency_budgets(run_data: dict, *,
                          budgets: dict[str, int | None] | None,
                          warm_after_turn: int = 1) -> int:
    """Fold per-turn latency + prompt-budget verdicts into a completed run's
    records as latency:* / budget:* assertions (Gate B), returning the count of
    warm breaches (named FAILs). Cold-start exemption: the first `warm_after_turn`
    turn(s) of each conversation are treated as cold. Reads the recorded (source,
    used_llm, tool_count, elapsed_ms) — no live daemon. LIVE enforcement is gated
    by budgets (budgets_from_env() is None => report-only on a non-9B box)."""
    breaches = 0
    for conv in run_data.get("conversations", run_data.get("turn_details", [])):
        turns = conv.get("turn_details", conv.get("turns", [conv]))
        for turn in turns:
            warm = turn.get("turn_num", 99) > warm_after_turn
            v = check_turn_latency(
                turn.get("source", ""), bool(turn.get("used_llm", False)),
                float(turn.get("elapsed_ms", 0.0)),
                tool_count=int(turn.get("tool_count", 0)), warm=warm, budgets=budgets)
            turn.setdefault("assertions", []).append({
                "type": f"latency:{v.path_class}", "value": str(v.ceiling_ms),
                "passed": v.ok, "description": v.reason, "actual": f"{v.latency_ms:.0f}ms",
                "gate": "B"})
            if not v.ok:
                breaches += 1
    return breaches


def budgets_from_env() -> dict[str, int | None] | None:
    """The enforcing budget map for THIS box, or None (report-only).

    The absolute ceilings are 9B-GPU warm p50s; they are enforced ONLY when the
    run declares the 9B-GPU profile (INTERGEN_LATENCY_PROFILE=zephyrus-9b-gpu),
    so a battery run on the CPU-served 2B .192 records latencies without
    false-failing GPU ceilings. Unset / any other value => None => report-only.
    """
    if os.environ.get("INTERGEN_LATENCY_PROFILE") == "zephyrus-9b-gpu":
        return WARM_BUDGETS_MS_9B_GPU
    return None
