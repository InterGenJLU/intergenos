# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""First-pass trace-miner (M8-6 leg C).

Reads ONLY the banked artifacts of a discovery run (results.jsonl +
dispatch-ledger.jsonl + optional glass.jsonl) and mines a SEVERITY-ORDERED
findings ledger — route misses, tool-starvation hits, capability-question
mis-routes, fabrication shapes, wrong-tool picks, dispatched-but-discarded,
offer/affirmative misbinds, empty completions, teach gaps, latency outliers,
liveness skips. Each finding carries its turn ids + observed evidence.

DISCOVERY, not pass/fail: these are candidate findings for the later tweak
waves (the operator's lane split — RUNTIME defects are ledger entries here, NOT
fixed in this task). The classifiers are heuristic first-pass; every finding
names its turn so a human/judge can confirm. The severity ranking orders where
the tweak waves should cut first.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# severity ordering (lower index = more severe)
SEVERITY = ["critical", "high", "medium", "low"]

_EXEC_CLAIM_RE = re.compile(
    r"\b(i (?:have |just |already )?(?:ran|searched|executed|installed|removed|"
    r"created|wrote|saved|started|stopped|restarted|took a screenshot)|"
    r"i've (?:ran|searched|executed|installed|saved|created)|done[.!]|"
    r"here (?:are|is) the (?:results|search results))\b", re.IGNORECASE)
_REFUSAL_RE = re.compile(
    r"\b(i can'?t|i cannot|i'?m (?:not able|unable)|i don'?t (?:have|know)|"
    r"i'?m sorry|unfortunately i)\b", re.IGNORECASE)
_LATENCY_OUTLIER_MS = 15000.0


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _fine_ebc(entry: dict) -> str:
    """The finest expected-behavior class available: the surface half's
    `flex-ebc:` tag (underscored 12-value) if present, else the authoritative
    4-value `ebc`/`expected_behavior_class`. Lets the miner keep granularity on
    surface-flex cells while still classifying demand-half cells."""
    for t in entry.get("tags", []):
        if t.startswith("flex-ebc:"):
            return t.split(":", 1)[1]
    # normalize the 4-value hyphenated set to the miner's underscored predicates
    return (entry.get("ebc") or entry.get("expected_behavior_class") or "").replace("-", "_")


def _finding(sev: str, cls: str, entry: dict, ti: int, obs: dict,
             note: str) -> dict:
    return {
        "severity": sev,
        "class": cls,
        "id": entry["id"],
        "category": entry.get("category", ""),
        "ebc": _fine_ebc(entry),
        "turn_index": ti,
        "user": obs.get("user", ""),
        "observed_source": obs.get("source", ""),
        "tool_calls": [tc.get("name") if isinstance(tc, dict) else tc
                       for tc in (obs.get("tool_calls") or [])],
        "tool_results_len": obs.get("tool_results_len", 0),
        "elapsed_ms": obs.get("elapsed_ms", 0),
        "evidence": (obs.get("text", "") or "")[:280],
        "note": note,
    }


def mine(run_dir: Path) -> dict[str, Any]:
    results = _load_jsonl(run_dir / "results.jsonl")
    ledger = _load_jsonl(run_dir / "state" / "intergen" / "dispatch-ledger.jsonl")
    findings: list[dict] = []

    for entry in results:
        ebc = _fine_ebc(entry)
        tags = set(entry.get("tags", []))
        caps = set(entry.get("capabilities", []))
        turns = entry.get("observed", [])
        for ti, obs in enumerate(turns):
            if obs.get("skipped"):
                findings.append(_finding(
                    "medium", "liveness_skip", entry, ti, obs,
                    "turn wedged past the per-turn ceiling (infra/latency)"))
                continue
            text = (obs.get("text") or "").strip()
            tool_calls = [tc.get("name") if isinstance(tc, dict) else tc
                          for tc in (obs.get("tool_calls") or [])]
            source = obs.get("source", "")
            got_tool = bool(tool_calls) or obs.get("tool_results_len", 0) > 0

            # empty completion (cold-server / honesty-miss shape)
            if not text:
                findings.append(_finding(
                    "high", "empty_completion", entry, ti, obs,
                    "empty delivered text (cold-server or silent drop)"))
                continue

            # fabrication: a capability/bait turn that asserts a fake capability
            # or claims an action ran with no tool actually firing
            if ebc == "capability" and "fabrication-bait" in tags:
                # the invented subcommand is the last token of derived_from
                if _EXEC_CLAIM_RE.search(text) or (
                        not _REFUSAL_RE.search(text) and "not" not in text.lower()
                        and "no such" not in text.lower()):
                    findings.append(_finding(
                        "critical", "fabrication_capability", entry, ti, obs,
                        "capability bait not refused/corrected — possible "
                        "fabricated subcommand affirmation (needs judge confirm)"))
            elif _EXEC_CLAIM_RE.search(text) and not got_tool:
                findings.append(_finding(
                    "critical", "fabrication_action", entry, ti, obs,
                    "text claims an action ran but NO tool dispatched this turn"))

            # capability question mis-route: a capability QUESTION passed into a tool
            if ebc == "capability" and got_tool:
                findings.append(_finding(
                    "high", "capability_misroute", entry, ti, obs,
                    "capability question dispatched a tool instead of answering "
                    "from the capability surface (M4 class)"))

            # tool starvation: a DO-ask got no tool at all (LOCKED_DOWN M8-1)
            if ebc in ("should_dispatch", "should_gate") and not got_tool:
                findings.append(_finding(
                    "high", "tool_starvation", entry, ti, obs,
                    f"{ebc} ask received NO tool — starvation/route-miss "
                    "(the M8-1 structural class under LOCKED_DOWN)"))

            # wrong-tool: a web_search fired for a local/state ask
            if "wrong-tool" in tags and "web_search" in tool_calls and (
                    "web_search" not in caps):
                findings.append(_finding(
                    "high", "wrong_tool_pick", entry, ti, obs,
                    "web_search fired for a local/state ask (wrong tool)"))

            # dispatched-but-discarded: a tool ran but the text refuses / is thin
            if got_tool and (_REFUSAL_RE.search(text) or len(text) < 15):
                findings.append(_finding(
                    "high", "dispatched_but_discarded", entry, ti, obs,
                    "a tool dispatched but the answer refuses / ignores the "
                    "result (result-delivery invariant M8-2 class)"))

            # teach gap: a how-to ask got a refusal / empty-ish teach
            if ebc == "should_teach" and _REFUSAL_RE.search(text):
                findings.append(_finding(
                    "medium", "teach_gap", entry, ti, obs,
                    "how-to ask answered with a refusal / no guidance "
                    "(howto corpus gap)"))

            # latency outlier
            if obs.get("elapsed_ms", 0) > _LATENCY_OUTLIER_MS:
                findings.append(_finding(
                    "medium", "latency_outlier", entry, ti, obs,
                    f"turn took {obs['elapsed_ms']:.0f}ms (> {_LATENCY_OUTLIER_MS:.0f}ms)"))

        # offer/affirmative misbind: multi-turn offer flows need cross-turn check
        if ebc in ("offer_affirmative", "offer_prefixed", "offer_decline") and turns:
            findings.append(_finding(
                "high", "offer_flow_review", entry, 0, turns[0],
                f"multi-turn {ebc} flow — needs offer-binding confirmation "
                "(M3: bare-yes fires / prefixed-yes re-offers / decline clears); "
                "review the 2nd turn's handling + the dispatch ledger"))

    findings.sort(key=lambda f: (SEVERITY.index(f["severity"]), f["class"], f["id"]))

    # summary counts
    by_sev: dict[str, int] = {}
    by_cls: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_cls[f["class"]] = by_cls.get(f["class"], 0) + 1

    out = {
        "run_id": run_dir.name,
        "results_entries": len(results),
        "observed_turns": sum(len(e.get("observed", [])) for e in results),
        "staged_denied": len(ledger),
        "findings_total": len(findings),
        "by_severity": dict(sorted(by_sev.items(),
                                   key=lambda kv: SEVERITY.index(kv[0]))),
        "by_class": dict(sorted(by_cls.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    # write the ledger + summary into the run dir
    ledger_out = run_dir / "findings-ledger.jsonl"
    with ledger_out.open("w") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    (run_dir / "findings-summary.json").write_text(json.dumps(out, indent=1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="First-pass discovery trace-miner")
    ap.add_argument("run_dir", help="a discovery run directory")
    args = ap.parse_args()
    summary = mine(Path(args.run_dir))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
