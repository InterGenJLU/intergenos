#!/usr/bin/env python3
"""Judge-calibration batch runner (calibration plan steps 2+3; the periodic
drift guard).

Runs every seed in known_garbage_seeds.json (ground truth = each seed's
annotator_provenance.operator_grading) through one or more live judge
endpoints and reports, per endpoint:

  - raw LLM target-dimension agreement vs the operator grading, and the
    Layer-1-composed (system) agreement — the deterministic screen overrides
    the LLM per dimension exactly as quality_judge.judge_turn does;
  - 3-category PABAK on the target dimensions (the sanctioned chance-corrected
    metric for this class skew — Cohen's kappa is recorded INVALID here, see
    README.md);
  - the step-3 hard gate: 100% of class known_garbage must compose to a
    non-pass overall. A schema-parse failure is counted as CAUGHT-BY-ESCALATION
    and listed separately: parse_judge_verdict raises loudly, so an errored
    seed can never be graded pass — but it is not a measured verdict, so it is
    never silently folded into the agreement numbers.

Plus cross-endpoint raw-LLM agreement (identical pinned bytes should behave
as one judge; divergence including intermittent parse failures is expected
across different GPUs — temperature 0 does not guarantee cross-silicon token
identity, first measured 2026-07-24).

Seed -> JudgeInputs composition matches tests/test_quality_judge.py exactly.

Usage:
  python3 run_calibration.py --endpoint name=http://host:port/v1/chat/completions \
      [--endpoint other=...] [--out results.json]

Eval-lane only; never part of the default battery (needs live judge servers).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))

from intergen.tests.quality_judge import (   # noqa: E402
    JUDGE_MODEL_DEFAULT,
    DimensionVerdict,
    JudgeInputs,
    build_judge_prompt,
    compose_overall,
    deterministic_screen,
    judge_client_from_endpoint,
    parse_judge_verdict,
)

SEEDS_PATH = Path(__file__).with_name("known_garbage_seeds.json")
ORDER = {"pass": 0, "flag": 1, "fail": 2}


def seed_inputs(seed: dict) -> JudgeInputs:
    ctx = seed.get("conversation_context") or []
    flat = "\n".join(f"[{m['role']}] {m['content']}" for m in ctx)
    return JudgeInputs(user_input=seed["user"], assembled_prompt=flat,
                       antecedent=seed.get("antecedent") or "",
                       model_output=seed["delivered"], delivered=seed["delivered"])


def run_batch(endpoints: dict[str, str], out_path: Path) -> dict:
    data = json.loads(SEEDS_PATH.read_text())
    seeds = data["seeds"]
    clients = {n: judge_client_from_endpoint(u, model=JUDGE_MODEL_DEFAULT)
               for n, u in endpoints.items()}
    results = []
    for seed in seeds:
        inputs = seed_inputs(seed)
        target = seed["expect_dimension"]
        operator = seed["annotator_provenance"]["operator_grading"]["verdict"]
        screened = {dv.dimension: dv.verdict for dv in deterministic_screen(inputs)}
        row = {"id": seed["id"], "class": seed["class"], "target": target,
               "operator": operator, "layer1": screened, "endpoints": {}}
        prompt = build_judge_prompt(inputs)
        for name, client in clients.items():
            t0 = time.time()
            try:
                raw = client(prompt)
                dims = parse_judge_verdict(raw)
                llm = {d: v.verdict for d, v in dims.items()}
                composed = dict(llm)
                composed.update(screened)   # Layer-1 override, as judge_turn does
                # Compose through the SAME function judge_turn uses, so the batch
                # measures the shipped severity ordering (substance outranks style)
                # rather than a second, drifting copy of the rule.
                overall = compose_overall(
                    {d: DimensionVerdict(d, v, "") for d, v in composed.items()},
                    list(deterministic_screen(inputs)))
                row["endpoints"][name] = {
                    "llm_target": llm.get(target),
                    "composed_target": composed.get(target),
                    "overall": overall, "secs": round(time.time() - t0, 1),
                    "evidence": dims[target].evidence[:160] if target in dims else "",
                    # ALL per-dimension verdicts, not just the target. Without these
                    # the escalate-never-pass failure mode is invisible: a seed whose
                    # target dimension the judge grades correctly can still compose to
                    # flag via some OTHER dimension, and the target-only record cannot
                    # say which one (first hit 2026-07-25).
                    "llm_all": llm,
                    "composed_all": composed,
                    "escalated_by": sorted(d for d, v in composed.items()
                                           if v != "pass"),
                    "evidence_all": {d: dv.evidence[:160]
                                     for d, dv in dims.items()},
                }
            except Exception as e:   # loud per-seed; the batch continues
                row["endpoints"][name] = {"error": f"{type(e).__name__}: {e}"[:300],
                                          "secs": round(time.time() - t0, 1)}
        results.append(row)
        eps = {n: (r.get("llm_target") or r.get("error", "?")[:40])
               for n, r in row["endpoints"].items()}
        print(f"{seed['id']:42s} {target:14s} op={operator:5s} {eps}", flush=True)

    summary: dict = {}
    for name in endpoints:
        judged = [r for r in results if "llm_target" in r["endpoints"][name]]
        errors = [r["id"] for r in results if "error" in r["endpoints"][name]]
        agree = [r for r in judged
                 if r["endpoints"][name]["llm_target"] == r["operator"]]
        composed_agree = [r for r in judged
                          if r["endpoints"][name]["composed_target"] == r["operator"]]
        garbage = [r for r in results if r["class"] == "known_garbage"]
        caught = [r for r in garbage
                  if r["endpoints"][name].get("overall") in ("flag", "fail")]
        escalated = [r["id"] for r in garbage if "error" in r["endpoints"][name]]
        n = len(judged)
        po = len(agree) / n if n else 0.0
        summary[name] = {
            "judged": n, "errors": errors,
            "llm_target_agreement": f"{len(agree)}/{n}",
            "composed_target_agreement": f"{len(composed_agree)}/{n}",
            "pabak_3cat": round((3 * po - 1) / 2, 3) if n else None,
            "garbage_catch_measured": f"{len(caught)}/{len(garbage)}",
            "garbage_caught_by_escalation": escalated,
            "garbage_catch_hard_gate":
                len(caught) + len(escalated) == len(garbage),
        }
    both = [r for r in results
            if all("llm_target" in r["endpoints"][n] for n in endpoints)]
    xagree = [r for r in both
              if len({r["endpoints"][n]["llm_target"] for n in endpoints}) == 1]
    summary["cross_endpoint_llm_agreement"] = f"{len(xagree)}/{len(both)}"

    payload = {"model": JUDGE_MODEL_DEFAULT, "results": results, "summary": summary}
    out_path.write_text(json.dumps(payload, indent=1))
    print("\nSUMMARY:", json.dumps(summary, indent=1))
    print("results ->", out_path)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", action="append", required=True,
                    metavar="NAME=URL",
                    help="Judge endpoint as name=url; repeatable.")
    ap.add_argument("--out", type=Path,
                    default=Path("judge_calibration_results.json"))
    args = ap.parse_args()
    endpoints = {}
    for spec in args.endpoint:
        name, _, url = spec.partition("=")
        if not name or not url:
            ap.error(f"--endpoint needs NAME=URL, got {spec!r}")
        endpoints[name] = url
    summary = run_batch(endpoints, args.out)
    gates_green = all(v["garbage_catch_hard_gate"]
                      for k, v in summary.items() if isinstance(v, dict))
    return 0 if gates_green else 3


if __name__ == "__main__":
    sys.exit(main())
