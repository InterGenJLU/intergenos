"""InterGen test runner — CLI orchestrator for behavioral tests.

Every run produces a timestamped output directory:
  intergen/tests/results/run_YYYYMMDD_HHMMSS/
    results.json  — full run metrics (conversations, assertions, timing)
    log.jsonl     — per-turn structured logs with assertions
    summary.txt   — human-readable report

Ported from JARVIS test_suite_v3/runner.py. Adapted for D-Bus/direct mode.

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
    grade_turn, compute_turn_grade, compute_conversation_grade,
    AssertionResult,
)

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
    """Filter conversations by ID or category."""
    if ids:
        return [c for c in conversations if c.id in ids]
    if category:
        return [c for c in conversations if c.category == category]
    return conversations


# ── Test execution ──

def run_turn(client, user_input: str) -> dict:
    """Execute a single turn and return the response dict."""
    response = client.ask(user_input)
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"text": response, "source": "raw", "tool_calls": [],
                    "handled": True}
    return response


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
        t0 = time.monotonic()
        response = run_turn(client, turn.user)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Normalize response format
        if "response" in response and "text" not in response:
            response["text"] = response["response"]

        assertion_results = grade_turn(response, turn.assertions)
        grade = compute_turn_grade(assertion_results)
        turn_grades.append(grade)

        passed = sum(1 for r in assertion_results if r.passed)
        failed = sum(1 for r in assertion_results if not r.passed)
        total_assertions += len(assertion_results)
        total_passed += passed
        total_failed += failed

        turn_data = {
            "turn_num": i + 1,
            "user_input": turn.user,
            "response_text": response.get("text", ""),
            "source": response.get("source", ""),
            "tool_calls": response.get("tool_calls", []),
            "elapsed_ms": round(elapsed_ms, 1),
            "assertions": [r.to_dict() for r in assertion_results],
            "grade": grade,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        turn_results.append(turn_data)

        if verbose:
            _print_turn(i + 1, turn.user, response, assertion_results,
                        grade, elapsed_ms)

    conv_grade = compute_conversation_grade(turn_grades)
    elapsed_total = (time.monotonic() - conv_start) * 1000

    if verbose:
        print(f"  Result: {_color(conv_grade)} "
              f"({total_passed}/{total_assertions} assertions, "
              f"{elapsed_total:.0f}ms)")

    return {
        "id": conversation.id,
        "name": conversation.name,
        "category": conversation.category,
        "grade": conv_grade,
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
    lines.append("")

    at = run_data["assertions_total"]
    ap = run_data["assertions_passed"]
    af = run_data["assertions_failed"]
    if at:
        lines.append(f"Assertions: {ap}/{at} passed ({ap/at*100:.0f}%)")
        if af:
            lines.append(f"  Failed: {af}")
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
    args = parser.parse_args()

    conversations = get_all_conversations()

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

    # Initialize client
    from intergen.tests.client import InterGenTestClient as TestClient
    client = TestClient(mode=args.mode)

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

    for conv in conversations:
        result = run_conversation(client, conv, verbose=verbose)
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

    # Print final summary
    print("\n" + "=" * 60)
    pct = (total_assertions_passed / total_assertions * 100
           if total_assertions else 0)
    print(f"RESULT: {_color('PASS') if total_fail == 0 and total_mixed == 0 else _color('MIXED') if total_fail == 0 else _color('FAIL')}")
    print(f"  Conversations: {total_pass} PASS / {total_mixed} MIXED / "
          f"{total_fail} FAIL")
    print(f"  Assertions:    {total_assertions_passed}/{total_assertions} "
          f"({pct:.0f}%)")
    print(f"  Duration:      {total_duration/1000:.1f}s")

    # Write output
    write_results(output_dir, run_data)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
