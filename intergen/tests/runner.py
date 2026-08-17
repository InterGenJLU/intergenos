# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen test runner — CLI orchestrator for behavioral tests.

Every run produces a timestamped output directory:
  intergen/tests/results/run_YYYYMMDD_HHMMSS/
    results.json  — full run metrics (conversations, assertions, timing)
    log.jsonl     — per-turn structured logs with assertions
    summary.txt   — human-readable report

Ported from a prior internal AI assistant project. Adapted for D-Bus/direct mode.

Usage:
    python3 -m intergen.tests.runner --mode direct
    python3 -m intergen.tests.runner --mode dbus --category system_info
    python3 -m intergen.tests.runner --ids sys_hostname,know_history
    python3 -m intergen.tests.runner --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from intergen.tests.conversations import (
    Conversation, Turn, Assertion, get_all_conversations as _get_all,
)
from intergen.tests.grader import (
    grade_turn, grade_turn_trace, compute_turn_grade,
    compute_conversation_grade, compute_gate_grades, AssertionResult,
    TRACE_RESOLVED_TYPES,
)
from intergen.tests.quality_judge import apply_judge_grading, judge_client_from_endpoint
from intergen.tests import latency_budgets as _lb
from intergen.tests.families import (
    expand_paraphrase_families, grade_families, family_variance,
    split_of_conversation, family_id_of,
)
from intergen.tests.measurement import bootstrap_interval, summarize_rate

# ── Console colors ──

_COLORS = {
    "PASS": "\033[92m",
    "MIXED": "\033[93m",
    "FAIL": "\033[91m",
    "ERROR": "\033[91m",
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
}


def _color(grade: str) -> str:
    return f"{_COLORS.get(grade, '')}{grade}{_COLORS['RESET']}"


# ── Conversation registry ──

def get_all_conversations() -> list[Conversation]:
    """Return all registered test conversations."""
    return _get_all()


def filter_conversations(conversations: list[Conversation], *,
                         ids: set[str] | None = None,
                         category: str | None = None) -> list[Conversation]:
    """Filter conversations by ID or category.

    An id selects the whole FAMILY, not just the cell that spells it. Expanded
    wordings carry ids of the form ``<base>#<label>``, so an exact-match filter
    silently dropped every sibling and a run asked for one cell by name graded
    its family as a family of one — which reads as a clean unanimous verdict
    while measuring nothing. Naming a single wording explicitly still selects
    just that wording, because its own id is matched first.
    """
    if ids:
        return [c for c in conversations
                if c.id in ids or family_id_of(c.id) in ids]
    if category:
        return [c for c in conversations if c.category == category]
    return conversations


# ── Test execution ──

def run_turn(client, user_input: str) -> dict:
    """Execute a single turn and return the response dict."""
    response = client.ask(user_input)
    if isinstance(response, str):
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            result = {"text": response, "source": "raw", "tool_calls": [],
                      "handled": True}
    elif hasattr(response, "text"):
        # TestResponse dataclass — convert to dict
        result = {
            "text": response.text,
            "response": response.text,
            "source": response.source,
            "handled": response.handled,
            "tool_calls": response.tool_calls,
            "tool_results": getattr(response, "tool_results", []),
            "used_llm": response.used_llm,
            "escalated": getattr(response, "escalated", False),
            "trace_id": getattr(response, "trace_id", ""),
        }
    else:
        result = response
    # Readiness tripwire (fail-closed): a 'startup' source means the daemon has
    # no router for this turn — it never came up, or it was lost mid-run. The
    # init-time _await_ready gate makes this nearly impossible at turn 1, but a
    # mid-run daemon loss would otherwise feed the grader a wall of identical
    # stubs scored as real pass/mixed/fail. Abort loudly rather than grade lies.
    if isinstance(result, dict) and result.get("source") == "startup":
        raise RuntimeError(
            f"InterGen returned the 'starting up' stub for input {user_input!r} "
            "— daemon not ready mid-run. Aborting rather than grading stub "
            "responses as real results.")
    return result


# Categories whose tests deliberately build up state across their own turns —
# memory facts (offer→confirm→store, recall, forget) and session continuity.
# Everything else gets memory wiped per turn so one test can't poison the next.
_PERSISTENCE_CATEGORIES = frozenset({"memory", "session_awareness"})


def _clear_test_memory(client) -> None:
    """Soft-clear stored FACTS on the isolated test memory DB.

    Only facts (clear_all leaves the sessions table intact, so the
    session_awareness pre-seed survives). Runs against the temp DB that
    client._isolate_memory_db() swapped in — never the user's real memory.
    """
    try:
        daemon = getattr(client, "_daemon", None)
        memory = getattr(daemon, "_memory", None) if daemon else None
        if memory is not None and hasattr(memory, "clear_all"):
            memory.clear_all()
    except Exception:
        pass


def _preseed_session(memory) -> None:
    """Seed a completed prior session so session_awareness tests have context
    to recall ("what were we working on?"). The caller clears the sessions
    table first when re-seeding between --repeat runs."""
    memory.record_turn("checked disk space", ["run_command"])
    memory.record_turn("checked hostname", ["run_command"])
    memory.end_session("checking disk space and system info")
    memory.start_session()


def _reset_session_state(client) -> None:
    """Reset session-continuity state to the pre-seeded baseline so each
    --repeat run of a session_awareness test is independent. clear_all keeps the
    sessions table (so the pre-seed survives a per-turn fact wipe), which means
    without this reset a session test's history accumulates across repeats and
    the distribution reads confounded (WC harness-review residual 2b)."""
    daemon = getattr(client, "_daemon", None)
    memory = getattr(daemon, "_memory", None) if daemon else None
    if memory is not None and hasattr(memory, "clear_sessions"):
        try:
            memory.clear_sessions()
            _preseed_session(memory)
        except Exception:
            pass  # Non-critical — session tests degrade, don't crash the run


def run_conversation(client, conversation: Conversation, *,
                     verbose: bool = True) -> dict:
    """Run a full conversation and return graded results."""
    conv_start = time.monotonic()
    turn_results = []
    turn_grades = []
    total_assertions = 0
    total_passed = 0
    total_failed = 0

    if verbose:
        print(f"\n{_COLORS['BOLD']}[{conversation.id}] "
              f"{conversation.name}{_COLORS['RESET']} "
              f"({conversation.category})")

    for i, turn in enumerate(conversation.turns):
        # Per-turn memory hygiene (the state-poisoning lesson): wipe stored
        # facts before each turn UNLESS the test targets persistence, which must
        # keep state across its own turns. A multi-turn demand-corpus flow
        # (persist_state, set by the loader for len(turns) > 1) is persistent for
        # the same reason — its offer/affirmative/antecedent turns depend on prior
        # state, so wiping mid-flow would sever it.
        if (conversation.category not in _PERSISTENCE_CATEGORIES
                and not getattr(conversation, "persist_state", False)):
            _clear_test_memory(client)
        t0 = time.monotonic()
        response = run_turn(client, turn.user)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Normalize response format
        if "response" in response and "text" not in response:
            response["text"] = response["response"]

        # Pass conversation category to grader for category-aware assertions
        response["category"] = conversation.category

        assertion_results = grade_turn(response, turn.assertions)
        grade = compute_turn_grade(assertion_results)
        gates = compute_gate_grades(assertion_results)
        turn_grades.append(grade)

        passed = sum(1 for r in assertion_results if r.passed)
        failed = sum(1 for r in assertion_results if not r.passed)
        total_assertions += len(assertion_results)
        total_passed += passed
        total_failed += failed

        turn_data = {
            "turn_num": i + 1,
            "user_input": turn.user,
            # SCRIPTED-OUTCOME DISCLOSURE. When a cell declares an outcome, the
            # harness is what produces it: on a deny cell the test client refuses
            # the privileged dispatch, so the refusal in the reply is the
            # fixture, not a choice the model made. The fact was recorded at
            # conversation level only, and a review document built from the
            # per-turn log therefore could not state it — a human read of a deny
            # cell was misled by exactly that absence. Carried on the turn as
            # well, so anything reading turns has it.
            "scripted_outcome": conversation.outcome,
            "response_text": response.get("text", ""),
            "source": response.get("source", ""),
            "trace_id": response.get("trace_id", ""),
            "tool_calls": response.get("tool_calls", []),
            "elapsed_ms": round(elapsed_ms, 1),
            # For the Leg-B latency-budget classifier (quality_judge/latency_budgets).
            "used_llm": response.get("used_llm", False),
            "tool_count": len(response.get("tool_results", [])
                              or response.get("tool_calls", [])),
            "assertions": [r.to_dict() for r in assertion_results],
            "grade": grade,
            "gate_a": gates["gate_a"],
            "gate_b": gates["gate_b"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        turn_results.append(turn_data)

        if verbose:
            _print_turn(i + 1, turn.user, response, assertion_results,
                        grade, elapsed_ms)

    conv_grade = compute_conversation_grade(turn_grades)
    # Conversation-level gates: Gate A FAILs if any turn's Gate A FAILs (a wrong
    # routing decision anywhere); Gate B is MIXED if any turn has a quality miss.
    conv_gate_a = "FAIL" if any(t["gate_a"] == "FAIL" for t in turn_results) else "PASS"
    conv_gate_b = "MIXED" if any(t["gate_b"] == "MIXED" for t in turn_results) else "PASS"
    elapsed_total = (time.monotonic() - conv_start) * 1000

    if verbose:
        print(f"  Result: {_color(conv_grade)} "
              f"({total_passed}/{total_assertions} assertions, "
              f"{elapsed_total:.0f}ms)")

    return {
        "id": conversation.id,
        "name": conversation.name,
        "category": conversation.category,
        "capabilities": list(conversation.capabilities),
        "outcome": conversation.outcome,
        # Same disclosure at conversation level, under the name a reader will
        # recognise without having to know what "outcome" means here.
        "scripted_outcome": conversation.outcome,
        # Which wording family this cell belongs to, and which split that family
        # falls in. Derived from the id, so it is the same on every machine and
        # every run without a stored assignment file to drift.
        "paraphrase_of": conversation.paraphrase_of,
        "contrast_of": conversation.contrast_of,
        "split": split_of_conversation(conversation.id),
        "grade": conv_grade,
        "gate_a": conv_gate_a,
        "gate_b": conv_gate_b,
        "turn_count": len(conversation.turns),
        "turn_grades": turn_grades,
        "assertions_total": total_assertions,
        "assertions_passed": total_passed,
        "assertions_failed": total_failed,
        "duration_ms": round(elapsed_total),
        "turn_details": turn_results,
    }


def _print_turn(num: int, user_input: str, response: dict,
                results: list[AssertionResult], grade: str,
                elapsed_ms: float) -> None:
    """Print a single turn result to console."""
    text = response.get("text", "")[:120]
    source = response.get("source", "")
    print(f"  T{num}: {_COLORS['DIM']}\"{user_input}\"{_COLORS['RESET']}")
    print(f"       → {text}")
    print(f"       [{source}, {elapsed_ms:.0f}ms] {_color(grade)}")
    for r in results:
        if not r.passed:
            print(f"       ✗ {r.type}: expected '{r.value}' — "
                  f"{r.description} (got: '{r.actual[:80]}')")


# ── Output writing ──

def write_results(output_dir: Path, run_data: dict) -> None:
    """Write all output files to the timestamped run directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # results.json
    with open(output_dir / "results.json", "w") as f:
        json.dump(run_data, f, indent=2, default=str)

    # distribution.json — per-id grade tallies across the N --repeat runs
    if run_data.get("distribution"):
        repeat = run_data.get("repeat", 1)
        samples = run_data.get("distribution_samples", {})
        dist = {
            cid: {
                "pass": grades.count("PASS"),
                "mixed": grades.count("MIXED"),
                "fail": grades.count("FAIL"),
                "pass_rate": round(grades.count("PASS") / len(grades), 3),
                "grades": grades,
                "samples": samples.get(cid, []),
            }
            for cid, grades in run_data["distribution"].items()
        }
        with open(output_dir / "distribution.json", "w") as f:
            json.dump({"repeat": repeat, "by_id": dist}, f, indent=2)

    # log.jsonl
    with open(output_dir / "log.jsonl", "w") as f:
        for conv in run_data.get("conversations", []):
            for turn in conv.get("turn_details", []):
                entry = {
                    "type": "turn",
                    "conversation_id": conv["id"],
                    **turn,
                }
                f.write(json.dumps(entry, default=str) + "\n")
            summary = {
                "type": "conversation_summary",
                "conversation_id": conv["id"],
                "name": conv["name"],
                "category": conv["category"],
                # See the turn record's note — a review document reading this
                # log must be able to say the outcome was scripted.
                "scripted_outcome": conv.get("scripted_outcome", ""),
                "paraphrase_of": conv.get("paraphrase_of", ""),
                "split": conv.get("split", ""),
                "grade": conv["grade"],
                "turn_grades": conv["turn_grades"],
                "assertions_total": conv["assertions_total"],
                "assertions_passed": conv["assertions_passed"],
                "assertions_failed": conv["assertions_failed"],
                "duration_ms": conv["duration_ms"],
            }
            f.write(json.dumps(summary, default=str) + "\n")

    # summary.txt
    summary_text = generate_summary(run_data)
    with open(output_dir / "summary.txt", "w") as f:
        f.write(summary_text)

    print(f"\nResults saved to: {output_dir}/")
    print(f"  results.json  — full run data")
    print(f"  log.jsonl     — per-turn logs")
    print(f"  summary.txt   — human-readable report")


def generate_summary(run_data: dict) -> str:
    """Generate human-readable summary report."""
    lines = []
    lines.append("InterGen Test Suite — Run Summary")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Run ID:      {run_data['run_id']}")
    lines.append(f"Timestamp:   {run_data['timestamp']}")
    lines.append(f"Mode:        {run_data.get('mode', 'unknown')}")

    duration_s = run_data["total_duration_ms"] / 1000
    lines.append(f"Duration:    {duration_s:.1f}s")
    lines.append("")

    total = run_data["conversations_total"]
    p = run_data["conversations_pass"]
    m = run_data["conversations_mixed"]
    f = run_data["conversations_fail"]
    lines.append(f"Conversations: {total} total")
    if total:
        lines.append(f"  PASS:  {p:3d} ({p/total*100:.0f}%)")
        lines.append(f"  MIXED: {m:3d} ({m/total*100:.0f}%)")
        lines.append(f"  FAIL:  {f:3d} ({f/total*100:.0f}%)")
        # THE STATED NOISE FLOOR, attached to the number it qualifies. A bare
        # percentage invites the reader to set it beside last round's and
        # believe something happened; at this corpus size a few points is
        # variance. Two runs whose intervals overlap are not an improvement.
        convs_for_rate = run_data.get("conversations", [])
        lines.append(summarize_rate(
            [c.get("grade") == "PASS" for c in convs_for_rate],
            unit="conversation"))
    lines.append("")

    at = run_data["assertions_total"]
    ap = run_data["assertions_passed"]
    af = run_data["assertions_failed"]
    if at:
        lines.append(f"Assertions: {ap}/{at} passed ({ap/at*100:.0f}%)")
        if af:
            lines.append(f"  Failed: {af}")
    lines.append("")

    # ── Two-gate summary ──
    # Gate A (routing/structural) is the hard release signal; Gate B (quality)
    # is reported alongside but never hard-fails the run. Surfaced separately so
    # a quality nit can't masquerade as a routing defect (and vice versa).
    convs = run_data.get("conversations", [])
    a_fail = [c for c in convs if c.get("gate_a") == "FAIL"]
    b_mixed = [c for c in convs if c.get("gate_b") == "MIXED"]
    if convs:
        lines.append("Two-Gate Summary:")
        lines.append(f"  Gate A (routing/structural, HARD): "
                     f"{len(convs) - len(a_fail)}/{len(convs)} clean"
                     + (f"  — {len(a_fail)} FAIL" if a_fail else "  — all clean ✓"))
        if a_fail:
            lines.append("    FAIL: " + ", ".join(c["id"] for c in a_fail))
        lines.append(f"  Gate B (quality, SOFT):            "
                     f"{len(convs) - len(b_mixed)}/{len(convs)} clean"
                     + (f"  — {len(b_mixed)} with quality misses" if b_mixed else "  — all clean ✓"))
        if b_mixed:
            lines.append("    MIXED: " + ", ".join(c["id"] for c in b_mixed))
        lines.append("")

    # ── Paraphrase families ──
    # A family is the unit the methodology actually grades: one wording landing
    # by keyword accident is not understanding, and one unlucky wording is not a
    # regression. Families that did not hold across their wordings are named —
    # that list IS the path-luck subset, readable without opening a trace.
    families = run_data.get("families") or []
    if families:
        held = [fam for fam in families if fam.get("grade") == "PASS"]
        split_of = {c.get("id"): c.get("split", "") for c in convs}
        lines.append("Paraphrase Families:")
        lines.append(f"  {len(held)}/{len(families)} families held across their wordings")
        lines.append(summarize_rate(
            [fam.get("grade") == "PASS" for fam in families], unit="family"))
        varied = [fam for fam in families if not fam.get("unanimous")]
        if varied:
            lines.append(f"  Varied within the family ({len(varied)}) — the "
                         "path-luck subset:")
            for fam in varied:
                failing = [mid for mid, g in fam.get("member_grades", {}).items()
                           if g != "PASS"]
                lines.append(f"    {fam['family']}: {fam['passed']}/{fam['total']} "
                             f"passed [{fam.get('grade')}]  split="
                             f"{split_of.get(fam['family'], 'n/a')}")
                lines.append(f"      wordings that did not hold: {', '.join(failing)}")
        else:
            lines.append("  Every family was unanimous across its wordings ✓")
        # The held-out split is what a training round's ship/reject call weighs.
        by_split: dict[str, list[bool]] = {}
        for fam in families:
            by_split.setdefault(split_of.get(fam["family"], "unassigned"), []).append(
                fam.get("grade") == "PASS")
        lines.append("  By split (the held-out delta is what a round is judged on):")
        for split_name in sorted(by_split):
            outcomes = by_split[split_name]
            iv = bootstrap_interval(outcomes, label=split_name)
            lines.append(f"    {split_name:<14} {sum(outcomes)}/{len(outcomes)} "
                         f"families  {iv.rate*100:.1f}% "
                         f"[{iv.low*100:.1f}–{iv.high*100:.1f}%]")
        lines.append("")

    # ── Scripted outcomes ──
    # Say plainly which refusals the harness itself produced. A reader who does
    # not know this reads a scripted deny as the model's own choice.
    scripted = [c for c in convs if c.get("scripted_outcome")]
    if scripted:
        lines.append("Scripted Outcomes (produced by the harness, not by the model):")
        for c in scripted:
            lines.append(f"  {c['id']}: outcome '{c['scripted_outcome']}' is driven "
                         "by the test client, so the refusal in the reply is the "
                         "fixture — what is graded is how the reply handles it")
        lines.append("")

    # Per-category breakdown
    categories: dict[str, dict] = {}
    for conv in run_data.get("conversations", []):
        cat = conv.get("category", "unknown")
        if cat not in categories:
            categories[cat] = {"pass": 0, "mixed": 0, "fail": 0, "total": 0}
        categories[cat]["total"] += 1
        grade = conv.get("grade", "FAIL").lower()
        if grade in categories[cat]:
            categories[cat][grade] += 1

    if categories:
        lines.append("By Category:")
        lines.append(f"  {'Category':<25} {'P':>3} {'M':>3} {'F':>3} {'Total':>5}")
        lines.append(f"  {'-'*25} {'-'*3} {'-'*3} {'-'*3} {'-'*5}")
        for cat in sorted(categories.keys()):
            c = categories[cat]
            lines.append(f"  {cat:<25} {c['pass']:>3} {c['mixed']:>3} "
                         f"{c['fail']:>3} {c['total']:>5}")
        lines.append("")

    # Non-PASS details
    non_pass = [c for c in run_data.get("conversations", [])
                if c.get("grade") != "PASS"]
    if non_pass:
        lines.append("Non-PASS Conversations:")
        for conv in non_pass:
            lines.append(f"  {conv['id']}: {conv['name']} — {conv['grade']}")
            lines.append(f"    Assertions: {conv['assertions_passed']}/"
                         f"{conv['assertions_total']} "
                         f"({conv['assertions_failed']} failed)")
            for ti, td in enumerate(conv.get("turn_details", [])):
                if td.get("grade") != "PASS":
                    lines.append(f"    Turn {ti+1}: {td['grade']}")
                    for a in td.get("assertions", []):
                        if not a.get("passed"):
                            lines.append(f"      ✗ {a['type']}: {a['description']}")
                            if a.get("actual"):
                                lines.append(f"        got: {a['actual'][:100]}")
        lines.append("")

    return "\n".join(lines)


# ── Main ──

def attach_traces(run_data: dict, decisions_path: Path) -> tuple[int, dict]:
    """Group the run's decision spans by trace_id and attach each turn's spans.

    The tracer appends every span to decisions.jsonl; each turn carries the
    trace_id of its router.route root span, so spans join to turns by that id.
    Spans within a trace are ordered by their monotonic seq. Mutates run_data
    (adds turn["trace"]); returns (turns_with_trace, spans_by_trace).
    """
    spans_by_trace: dict[str, list] = {}
    if decisions_path.exists():
        for line in decisions_path.read_text().splitlines():
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            spans_by_trace.setdefault(rec.get("trace_id", ""), []).append(rec)
    for spans in spans_by_trace.values():
        spans.sort(key=lambda s: s.get("seq", 0))
    attached = 0
    for conv in run_data.get("conversations", []):
        for turn in conv.get("turn_details", []):
            spans = spans_by_trace.get(turn.get("trace_id", ""), [])
            turn["trace"] = spans
            if spans:
                attached += 1
    return attached, spans_by_trace


def apply_trace_grading(run_data: dict) -> int:
    """Second grading pass — trace-aware Gate-A assertions, run AFTER attach_traces.

    Some Gate-A truths (no_fabricated_success) can only be judged once the
    decision spans are joined to their turn. Evaluate them here, fold the new
    assertions into each turn, and re-derive the turn / conversation / run grades
    + assertion counters so summary.txt, the console line, and the exit code all
    reflect the trace-aware verdict. Returns the number of new assertions added.
    """
    added = 0
    for conv in run_data.get("conversations", []):
        turn_grades = []
        for turn in conv.get("turn_details", []):
            new = grade_turn_trace(turn)
            if new:
                # A trace-RESOLVED type (gate_action) was declared on the cell
                # and emitted by the first pass as a fail-closed placeholder;
                # grade_turn_trace above just produced its real verdict. Drop the
                # placeholder so the resolved result REPLACES it — otherwise the
                # stale fail-closed placeholder would pin Gate A to FAIL even on a
                # clean deny, and the assertion would be double-counted.
                resolved = {r.type for r in new} & TRACE_RESOLVED_TYPES
                if resolved:
                    turn["assertions"] = [
                        a for a in turn["assertions"]
                        if a["type"] not in resolved
                    ]
                turn["assertions"].extend(r.to_dict() for r in new)
                added += len(new)
            gates = compute_gate_grades(turn["assertions"])
            turn["gate_a"], turn["gate_b"] = gates["gate_a"], gates["gate_b"]
            turn["grade"] = compute_turn_grade(turn["assertions"])
            turn_grades.append(turn["grade"])
        turns = conv.get("turn_details", [])
        conv["grade"] = compute_conversation_grade(turn_grades)
        conv["gate_a"] = "FAIL" if any(t["gate_a"] == "FAIL" for t in turns) else "PASS"
        conv["gate_b"] = "MIXED" if any(t["gate_b"] == "MIXED" for t in turns) else "PASS"
        conv["assertions_total"] = sum(len(t["assertions"]) for t in turns)
        conv["assertions_passed"] = sum(
            1 for t in turns for a in t["assertions"] if a["passed"])
        conv["assertions_failed"] = sum(
            1 for t in turns for a in t["assertions"] if not a["passed"])
    # Re-derive run-level aggregates from the (now trace-graded) conversations.
    convs = run_data.get("conversations", [])
    run_data["conversations_pass"] = sum(1 for c in convs if c["grade"] == "PASS")
    run_data["conversations_mixed"] = sum(1 for c in convs if c["grade"] == "MIXED")
    run_data["conversations_fail"] = sum(1 for c in convs if c["grade"] == "FAIL")
    run_data["assertions_total"] = sum(c["assertions_total"] for c in convs)
    run_data["assertions_passed"] = sum(c["assertions_passed"] for c in convs)
    run_data["assertions_failed"] = sum(c["assertions_failed"] for c in convs)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(
        description="InterGen behavioral test runner"
    )
    parser.add_argument("--mode", choices=["direct", "dbus"], default="direct",
                        help="Test mode: direct (in-process) or dbus (session bus)")
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated conversation IDs to run")
    parser.add_argument("--category", type=str, default=None,
                        help="Run only this category")
    parser.add_argument("--list", action="store_true",
                        help="List all conversations and exit")
    parser.add_argument("--verbose", action="store_true", default=True,
                        help="Show detailed output")
    parser.add_argument("--brief", action="store_true",
                        help="Summary only")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Override output directory (default: auto-timestamped)")
    parser.add_argument("--observe", action="store_true",
                        help="Capture the decision trace beside each turn — enables "
                             "INTERGEN_TRACE for the run and writes trace.jsonl + "
                             "per-turn spans into results.json.")
    parser.add_argument("--judge", action="store_true",
                        help="Fold quality-judge verdicts (judge:* Gate-B) beside "
                             "Gate-A/Gate-B. The deterministic Layer-1 screen runs "
                             "with no model; pass --judge-endpoint to add the live "
                             "LLM judge (sequenced behind the 4.3 wave).")
    parser.add_argument("--judge-endpoint", type=str, default=None,
                        help="OpenAI-compatible /v1/chat/completions URL for the Gemma "
                             "judge (a different family+port than InterGen's :8080).")
    parser.add_argument("--latency-budgets", action="store_true",
                        help="Fold per-path latency + prompt-budget verdicts (latency:* "
                             "Gate-B). Enforced only under the 9B-GPU profile "
                             "(INTERGEN_LATENCY_PROFILE=zephyrus-9b-gpu); report-only else.")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="Run each conversation N times and report a per-conversation "
                             "PASS-rate distribution (small models are nondeterministic — "
                             "reliability is a distribution, not a single pass). One daemon "
                             "startup amortizes across all N runs. results.json holds the "
                             "last run; distribution.json holds the per-id grade tallies.")
    parser.add_argument("--families", action="store_true",
                        help="Expand each cell's alternate wordings into sibling "
                             "conversations and grade them as FAMILIES (a family "
                             "passes when 4 of its 5 wordings do). This is the unit "
                             "of measurement a training round is read on — a wording "
                             "landed by keyword accident shows as family variance. "
                             "OFF by default: the default battery stays the cell "
                             "list every existing gate and count is written against.")
    parser.add_argument("--corpus", type=str, default=None, metavar="PATH",
                        help="Drive a demand-corpus JSONL bank instead of the built-in "
                             "conversation registry (M8-6). The bank loads through "
                             "corpus_loader (no translation) into the same Conversation "
                             "objects; multi-turn entries persist state across their turns. "
                             "The discovery run carries no assertions — glass records "
                             "everything. See intergen/tests/demand_corpus/README.md.")
    parser.add_argument("--demand-bank", action="store_true",
                        help="Run the merged demand-corpus BANK as a named OPT-IN battery "
                             "(M8-6 / M8 doc §7). Regenerates bank.jsonl from BOTH "
                             "half-files via corpus_merge, loads it via corpus_loader, and "
                             "drives it DISCOVERY-grade (entries carry no content "
                             "assertions — the run fires turns and records; the quality "
                             "judge scores it in a later wave). NOT part of the default "
                             "battery run (1300+ entries). Needs a live daemon to drive.")
    args = parser.parse_args()

    # Named OPT-IN battery: the merged four-digit demand bank. Regenerated from
    # both halves via corpus_merge (bank.jsonl stays generated/gitignored) and
    # loaded via corpus_loader — never part of get_all_conversations(), so the
    # default battery never balloons.
    if args.demand_bank:
        from intergen.tests.corpus_loader import load_corpus
        from intergen.tests.corpus_merge import read_grounding_keys, regenerate_bank
        bank_path, report = regenerate_bank()
        print(f"[demand-bank] regenerated {bank_path} — {report['total']} entries "
              f"({report['single_turn']} single-turn, {report['multi_turn']} multi-turn, "
              f"generators {dict(report['by_generator'])})")
        conversations = load_corpus(
            bank_path, known_grounding_keys=read_grounding_keys())
    elif args.corpus:
        from intergen.tests.corpus_loader import load_corpus
        from intergen.tests.corpus_merge import read_grounding_keys
        conversations = load_corpus(
            args.corpus, known_grounding_keys=read_grounding_keys())
    else:
        conversations = get_all_conversations()

    # Expand wordings BEFORE filtering and listing, so --ids and --list see the
    # family members that will actually run.
    if args.families:
        before = len(conversations)
        conversations = expand_paraphrase_families(conversations)
        print(f"[families] expanded {before} cells into {len(conversations)} "
              f"conversations across their wordings")

    if args.list:
        print(f"{'ID':<20} {'Name':<35} {'Category':<20} {'Turns':>5}")
        print("-" * 85)
        for c in conversations:
            print(f"{c.id:<20} {c.name:<35} {c.category:<20} "
                  f"{len(c.turns):>5}")
        print(f"\nTotal: {len(conversations)} conversations, "
              f"{sum(len(c.turns) for c in conversations)} turns")
        return 0

    # Filter
    ids = set(args.ids.split(",")) if args.ids else None
    conversations = filter_conversations(conversations, ids=ids,
                                         category=args.category)

    if not conversations:
        print("No conversations matched the filter.")
        return 1

    verbose = args.verbose and not args.brief

    # Create timestamped output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"run_{timestamp}"
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(__file__).parent / "results" / run_id

    # --observe: enable the decision trace for THIS run and point its sink at the
    # run dir. MUST be set before the client builds the router — the tracer reads
    # the env once, at the first get_tracer(). XDG_STATE_HOME redirects the sink to
    # <run_dir>/intergen/decisions.jsonl, isolated per run.
    if args.observe:
        output_dir.mkdir(parents=True, exist_ok=True)
        os.environ["INTERGEN_TRACE"] = "1"
        os.environ["XDG_STATE_HOME"] = str(output_dir)

    # Initialize client
    from intergen.tests.client import InterGenTestClient as TestClient
    client = TestClient(mode=args.mode)
    # Direct mode runs the daemon in-process and spawns its own llama-server(s);
    # without this they orphan and hold :8080/:8081, conflicting with the next
    # run's bind. atexit covers both the normal return and an unhandled-exception
    # exit; close() is self-guarding so a double call is a no-op.
    import atexit
    atexit.register(client.close)

    # Pre-seed a completed session for session_awareness tests
    has_session_tests = any(c.category == "session_awareness" for c in conversations)
    if has_session_tests and args.mode == "direct" and client._daemon:
        try:
            memory = getattr(client._daemon, "_memory", None)
            if memory and hasattr(memory, "end_session"):
                _preseed_session(memory)
        except Exception:
            pass  # Non-critical — session tests may still fail

    print(f"InterGen Test Suite")
    print(f"Run ID:    {run_id}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Mode:      {args.mode}")
    print(f"Tests:     {len(conversations)} conversations, "
          f"{sum(len(c.turns) for c in conversations)} turns")
    print(f"Output:    {output_dir}")
    print("=" * 60)

    # Run all conversations
    run_start = time.monotonic()
    conv_results = []
    total_pass = 0
    total_mixed = 0
    total_fail = 0

    repeat = max(1, args.repeat)
    distribution = {}  # conv_id -> list of grades across the N runs
    dist_samples = {}  # conv_id -> per-run [{grade, turns:[{source, text}]}]

    for conv in conversations:
        grades = []
        samples = []
        result = None
        for run_i in range(repeat):
            # Reset the daemon's per-conversation router state between tests to
            # prevent cross-conversation contamination (a prior conversation's
            # staged offer / trust posture leaking into the next). This runs for
            # BOTH modes via the client: direct resets the in-process router,
            # dbus calls ResetConversation() on the persistent daemon — the
            # dbus-mode root-cause fix (the old inline reset here only ever
            # reached the direct-mode in-process router, so a dbus run never
            # reset between conversations). Fires before EVERY conversation, so
            # the first iteration is the run-start reset. Skip for
            # session_awareness tests, which depend on prior context.
            if conv.category != "session_awareness":
                client.reset_conversation()
            elif run_i > 0:
                # session_awareness: between --repeat runs, reset to the
                # pre-seeded baseline so each run is independent (WC residual 2b).
                # run_i == 0 already starts from the global pre-seed above.
                _reset_session_state(client)
            # Each run starts with clean stored facts (incl. between --repeat
            # runs of a memory test, so every run is independent). clear_all
            # touches only facts, so the session pre-seed survives — safe for
            # all categories.
            _clear_test_memory(client)
            # Verbose only on the first repeat to keep the N-run output readable.
            result = run_conversation(client, conv, verbose=verbose and run_i == 0)
            grades.append(result["grade"])
            # Per-run question + route + FULL response, so an A/B can see WHICH
            # route and the COMPLETE wording each repeat produced. results.json
            # keeps only the last run, so without this the per-run record is lost.
            # Capture the full response and pair it with its question — gauging
            # quality needs the complete answer next to what was asked; truncating
            # either is a self-inflicted blind spot (a known eval-harness footgun).
            samples.append({
                "grade": result["grade"],
                "turns": [
                    {"user_input": t.get("user_input"),
                     "source": t.get("source"),
                     "text": t.get("response_text") or ""}
                    for t in result.get("turn_details", [])
                ],
            })
        if repeat > 1:
            distribution[conv.id] = grades
            dist_samples[conv.id] = samples
            p = grades.count("PASS")
            print(f"  [{conv.id}] {p}/{repeat} PASS  "
                  f"({grades.count('MIXED')} MIXED, {grades.count('FAIL')} FAIL)")
        # results.json keeps the last run (trace/spans intact); distribution.json
        # carries the per-id tallies.
        conv_results.append(result)
        if result["grade"] == "PASS":
            total_pass += 1
        elif result["grade"] == "MIXED":
            total_mixed += 1
        else:
            total_fail += 1

    total_duration = (time.monotonic() - run_start) * 1000

    # Aggregate
    total_assertions = sum(c["assertions_total"] for c in conv_results)
    total_assertions_passed = sum(c["assertions_passed"] for c in conv_results)
    total_assertions_failed = sum(c["assertions_failed"] for c in conv_results)

    run_data = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "conversations_total": len(conv_results),
        "conversations_pass": total_pass,
        "conversations_mixed": total_mixed,
        "conversations_fail": total_fail,
        "assertions_total": total_assertions,
        "assertions_passed": total_assertions_passed,
        "assertions_failed": total_assertions_failed,
        "total_duration_ms": round(total_duration),
        "conversations": conv_results,
    }
    if distribution:
        run_data["repeat"] = repeat
        run_data["distribution"] = distribution
        run_data["distribution_samples"] = dist_samples

    # --observe: join the captured spans to their turns and write a consolidated
    # trace.jsonl beside the results (the dyno readout — the trace next to each turn).
    if args.observe:
        decisions_path = output_dir / "intergen" / "decisions.jsonl"
        turns_traced, spans_by_trace = attach_traces(run_data, decisions_path)
        spans_total = sum(len(v) for v in spans_by_trace.values())
        with open(output_dir / "trace.jsonl", "w") as f:
            for spans in spans_by_trace.values():
                for s in spans:
                    f.write(json.dumps(s, default=str) + "\n")
        run_data["trace"] = {"spans_total": spans_total, "turns_traced": turns_traced}
        print(f"  Trace:         {spans_total} spans, {turns_traced} turns traced")

        # Trace-aware Gate-A re-grade (no_fabricated_success etc.) now that spans
        # are joined — may flip grades, so refresh the console totals + exit code.
        trace_added = apply_trace_grading(run_data)
        total_pass = run_data["conversations_pass"]
        total_mixed = run_data["conversations_mixed"]
        total_fail = run_data["conversations_fail"]
        total_assertions = run_data["assertions_total"]
        total_assertions_passed = run_data["assertions_passed"]
        total_assertions_failed = run_data["assertions_failed"]
        if trace_added:
            print(f"  Trace grading: +{trace_added} trace-aware assertion(s)")

    # --judge: fold quality-judge verdicts (Gate B, advisory/triage — surfaced for
    # the human read, not folded into the Gate totals until calibrated). Layer 1
    # runs with no model; --judge-endpoint adds the live LLM judge (behind 4.3).
    if getattr(args, "judge", False):
        jc = (judge_client_from_endpoint(args.judge_endpoint)
              if args.judge_endpoint else None)
        escalated = apply_judge_grading(run_data, judge_client=jc)
        tier = "Layer-1 + LLM" if jc else "Layer-1 only"
        print(f"  Judge ({tier}): {escalated} turn(s) flagged/failed for the human read")
        # The judge BINDS, so its fold can move grades — refresh the console
        # totals and the exit code from the re-derived aggregates, exactly as
        # the trace pass above does. Without this the run would print and exit
        # on numbers taken before the judge had spoken.
        total_pass = run_data.get("conversations_pass", total_pass)
        total_mixed = run_data.get("conversations_mixed", total_mixed)
        total_fail = run_data.get("conversations_fail", total_fail)
        total_assertions = run_data.get("assertions_total", total_assertions)
        total_assertions_passed = run_data.get(
            "assertions_passed", total_assertions_passed)
        total_assertions_failed = run_data.get(
            "assertions_failed", total_assertions_failed)

    # --latency-budgets: fold per-path latency + prompt-budget verdicts (Gate B).
    if getattr(args, "latency_budgets", False):
        budgets = _lb.budgets_from_env()
        breaches = _lb.apply_latency_budgets(run_data, budgets=budgets)
        mode = "enforced (9B-GPU profile)" if budgets else "report-only (non-9B box)"
        print(f"  Latency budgets [{mode}]: {breaches} warm breach(es)")

    # Family grading LAST, after every pass that can move a grade (trace, judge,
    # latency) has run — a family verdict computed before the judge bound would
    # be a verdict on numbers that no longer exist.
    if args.families:
        fam_results = grade_families(run_data["conversations"])
        run_data["families"] = [fr.to_dict() for fr in fam_results]
        varied = family_variance(fam_results)
        held = sum(1 for fr in fam_results if fr.grade == "PASS")
        print(f"  Families:      {held}/{len(fam_results)} held across their "
              f"wordings ({len(varied)} varied within the family)")

    # Print final summary
    print("\n" + "=" * 60)
    pct = (total_assertions_passed / total_assertions * 100
           if total_assertions else 0)
    print(f"RESULT: {_color('PASS') if total_fail == 0 and total_mixed == 0 else _color('MIXED') if total_fail == 0 else _color('FAIL')}")
    print(f"  Conversations: {total_pass} PASS / {total_mixed} MIXED / "
          f"{total_fail} FAIL")
    gate_a_fail = sum(1 for c in conv_results if c.get("gate_a") == "FAIL")
    gate_b_mixed = sum(1 for c in conv_results if c.get("gate_b") == "MIXED")
    print(f"  Gate A (route): {len(conv_results) - gate_a_fail}/{len(conv_results)} clean"
          + (f" / {_color('FAIL')} {gate_a_fail}" if gate_a_fail else ""))
    print(f"  Gate B (qual):  {len(conv_results) - gate_b_mixed}/{len(conv_results)} clean"
          + (f" / {gate_b_mixed} quality misses" if gate_b_mixed else ""))
    print(f"  Assertions:    {total_assertions_passed}/{total_assertions} "
          f"({pct:.0f}%)")
    print(f"  Duration:      {total_duration/1000:.1f}s")

    # Write output
    write_results(output_dir, run_data)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
