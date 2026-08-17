#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Rigorous warm-vs-cold TTFT / prefill probe for the InterGen local LLM.

Measures time-to-first-token and the authoritative prefill cost for the live
llama-server, separating the COLD case (full prefix prefill — the cost paid ONCE by
the daemon's startup prompt-cache warmup, or by a first turn before it completes)
from the WARM case (system+tools prefix already in the KV cache, only a fresh user
tail prefilled — every steady-state user turn). This is the rigorous
warm[cached]-vs-cold[full prefill] number the post-install eval doc wants; it
supersedes an earlier felt-latency-floor draft of this file that drove
LLMRouter.stream but could not separate warm from cold or reach prompt_ms.

WHY THIS SHAPE
- The model-level truth is ``timings.prompt_ms`` / ``timings.prompt_n`` that
  llama.cpp reports in the final SSE chunk: prompt_n is the number of prompt tokens
  actually evaluated and prompt_ms is the time for them. On a cache hit the server
  skips the cached tokens, so prompt_ms collapses to the new tail. We report both:
  prompt_ms (authoritative, server-measured) and wall TTFT (what the user feels).
- The prefix is built from the SAME production code the daemon sends
  (``build_system_prompt`` + the discovered built-in tool schemas), so the prefill
  matches real serving rather than a synthetic proxy. (Honest boundary: only the
  built-in tools are included — MCP tools attached at runtime would make the real
  prefix LARGER, so the tool-scenario cold prefill here is a lower bound.)

METHOD (the deterministic llama.cpp control)
- COLD: send the request with ``cache_prompt: false`` — llama-server ignores its
  prompt cache and re-prefills the ENTIRE system+tools+user prompt every time, so
  each cold trial is genuinely cold and low-variance. (A leading-nonce and a
  foreign-prefill eviction were both tried first and rejected: the server keeps a
  multi-entry prompt cache that restores the prefix KV across requests AND across
  probe runs, so only ``cache_prompt: false`` reliably forces a full prefill.)
- WARM: prime the prefix with ``cache_prompt: true``, then send it with
  ``cache_prompt: true`` and a FRESH user query each trial — the server reuses the
  cached prefix and prefills only the short new tail. That is exactly a production
  turn-2 (and every turn after the daemon's startup warmup).

WHEN TO RUN: on the host that serves :8080, when the port is otherwise idle (do not
contend with a live dyno/eval — the 2B server is single-instance on the port). The
live InterGen daemon serving is fine; the probe just adds turns. stdlib-only, talks
to the server over HTTP; imports intergen only to build the faithful prefix.

USAGE:
    python3 scripts/ttft-probe.py                       # both scenarios, 3 trials each
    python3 scripts/ttft-probe.py --trials 5 --out /path/results.json
    python3 scripts/ttft-probe.py --scenarios tool      # tool prefix only
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, asdict

DEFAULT_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"

# Surfaced in the output and the JSON so a reader of only the numbers sees the bounds.
CAVEATS = [
    "WARM measures a FRESH user query over the cached prefix (production turn-2). "
    "Because the cold trials run immediately before the warm ones, the first warm "
    "trial also carries KV cache-restore overhead, so the warm prefill is a "
    "CONSERVATIVE UPPER BOUND on steady-state — the safe direction for a latency gate. "
    "An identical repeated query (tail also cached) measures lower (~0.23s floor).",
    "Built-in tools only — runtime MCP tools would enlarge the tool prefix, so the "
    "cold tool-prefill here is a LOWER BOUND.",
    "query_type is fixed to 'general'; the daemon varies it per route, which may shift "
    "the conversational-prefix size marginally (the tool-schema bulk dominates regardless).",
]

# Representative InterGen turns — short, realistic system queries. Each WARM/COLD
# trial draws a fresh one so the user tail is never identical (faithful new-turn).
USER_QUERIES = [
    "what time is it",
    "how much disk space is free",
    "is networkmanager running",
    "what's my hostname",
    "how much memory is in use",
    "list the running services",
    "what's the system load right now",
    "show me the kernel version",
]


@dataclass
class Sample:
    phase: str            # "cold" | "warm"
    scenario: str         # "conversational" | "tool"
    wall_ttft_ms: float | None  # send -> first delta; None if no first token was seen
    total_ms: float       # send -> stream end
    prompt_n: int         # prefix tokens evaluated (llama.cpp)
    prompt_ms: float      # prefix EVAL time (skips cached tokens) — the warm signal
    predicted_n: int      # generated tokens
    predicted_ms: float   # generation time


def _build_prefix(with_tools: bool) -> tuple[str, list[dict]]:
    """Build the real production system prompt (+ built-in tool schemas).

    Imports the live intergen package so the measured prefix is what the daemon
    actually sends. discover_tools() registers the local built-in tools only — no
    MCP network discovery — which is the reproducible bulk of the tools array.
    """
    from intergen.llm import build_system_prompt
    system = build_system_prompt("general", with_tools=with_tools)
    tools: list[dict] = []
    if with_tools:
        from intergen.tool_registry import ToolRegistry
        reg = ToolRegistry()
        reg.discover_tools()
        tools = [s.to_openai() for s in reg.get_tool_schemas()]
    return system, tools


def _one_request(endpoint: str, system: str, user: str, tools: list[dict],
                 max_tokens: int, timeout: float, cache_prompt: bool) -> Sample | None:
    payload: dict = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.6,
        "top_p": 0.8,
        "top_k": 20,
        "max_tokens": max_tokens,
        "stream": True,
        "cache_prompt": cache_prompt,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    first_delta: float | None = None
    prompt_n = predicted_n = 0
    prompt_ms = predicted_ms = 0.0
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"    ! request failed: {e}", file=sys.stderr)
        return None
    try:
        for raw in resp:
            if not raw:
                continue
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            timings = chunk.get("timings")
            if timings:
                prompt_n = timings.get("prompt_n", prompt_n)
                predicted_n = timings.get("predicted_n", predicted_n)
                prompt_ms = timings.get("prompt_ms", prompt_ms)
                predicted_ms = timings.get("predicted_ms", predicted_ms)
            if first_delta is None:
                try:
                    delta = chunk["choices"][0].get("delta", {})
                except (KeyError, IndexError):
                    delta = {}
                if delta.get("content") or delta.get("tool_calls") \
                        or delta.get("reasoning_content"):
                    first_delta = time.monotonic() - t0
    finally:
        resp.close()
    total = (time.monotonic() - t0) * 1000.0
    # first_delta is None when no content/tool_call/reasoning delta was ever seen.
    # Record None (not 0.0) so a missing measurement is excluded from the wall-TTFT
    # distribution rather than masquerading as an impossibly-fast 0 ms (which would
    # drag median/min down). prompt_ms/prompt_n stay valid and still count.
    return Sample(
        phase="", scenario="",
        wall_ttft_ms=(round(first_delta * 1000.0, 1) if first_delta is not None else None),
        total_ms=round(total, 1),
        prompt_n=prompt_n, prompt_ms=round(prompt_ms, 1),
        predicted_n=predicted_n, predicted_ms=round(predicted_ms, 1),
    )


def run_scenario(endpoint: str, scenario: str, with_tools: bool,
                 trials: int, max_tokens: int, timeout: float) -> list[Sample]:
    system, tools = _build_prefix(with_tools)
    samples: list[Sample] = []

    # COLD — cache_prompt=false forces a full system+tools+user prefill every trial.
    print(f"  [{scenario}] COLD x{trials} (cache_prompt=false → full prefill each)…")
    for i in range(trials):
        s = _one_request(endpoint, system, USER_QUERIES[i % len(USER_QUERIES)],
                         tools, max_tokens, timeout, cache_prompt=False)
        if s:
            s.phase, s.scenario = "cold", scenario
            samples.append(s)
            ttft = f"{s.wall_ttft_ms:>9.1f}ms" if s.wall_ttft_ms is not None else " MISSING (no first token — excluded)"
            print(f"    cold[{i}] prefill={s.prompt_ms:>9.1f}ms/{s.prompt_n}tok  ttft={ttft}")

    # WARM — prime the prefix, then reuse it with a fresh user tail each trial.
    print(f"  [{scenario}] priming prefix, then WARM x{trials} (cached prefix, new tail)…")
    _one_request(endpoint, system, "warm up", tools, 1, timeout, cache_prompt=True)
    for i in range(trials):
        s = _one_request(endpoint, system, USER_QUERIES[i % len(USER_QUERIES)],
                         tools, max_tokens, timeout, cache_prompt=True)
        if s:
            s.phase, s.scenario = "warm", scenario
            samples.append(s)
            ttft = f"{s.wall_ttft_ms:>9.1f}ms" if s.wall_ttft_ms is not None else " MISSING (no first token — excluded)"
            print(f"    warm[{i}] prefill={s.prompt_ms:>9.1f}ms/{s.prompt_n}tok  ttft={ttft}")
    return samples


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 1),
        "median": round(statistics.median(vals), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
        "stdev": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
    }


def summarize(samples: list[Sample]) -> dict:
    out: dict = {}
    for scenario in sorted({s.scenario for s in samples}):
        out[scenario] = {}
        for phase in ("cold", "warm"):
            sub = [s for s in samples if s.scenario == scenario and s.phase == phase]
            wall_ok = [s.wall_ttft_ms for s in sub if s.wall_ttft_ms is not None]
            out[scenario][phase] = {
                "prefill_ms": _stats([s.prompt_ms for s in sub]),
                "wall_ttft_ms": _stats(wall_ok),
                "wall_ttft_excluded": len(sub) - len(wall_ok),  # samples with no first token seen
                "prompt_n": _stats([float(s.prompt_n) for s in sub]),
            }
        cold = out[scenario]["cold"]["prefill_ms"]
        warm = out[scenario]["warm"]["prefill_ms"]
        if cold.get("n") and warm.get("n"):
            saved = round(cold["median"] - warm["median"], 1)
            pct = round(100.0 * saved / cold["median"], 1) if cold["median"] else 0.0
            out[scenario]["prefill_saved_ms_median"] = saved
            out[scenario]["prefill_saved_pct_median"] = pct
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="InterGen warm-vs-cold TTFT/prefill probe.")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--trials", type=int, default=3, help="trials per phase per scenario (n>=3)")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default=None, help="write full JSON results here")
    ap.add_argument("--scenarios", default="conversational,tool",
                    help="comma list: conversational,tool")
    args = ap.parse_args()

    # Health gate — fail loud if the engine is down rather than time out per request.
    health = args.endpoint.rsplit("/v1/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=5) as r:
            json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: llama-server health check failed at {health}: {e}", file=sys.stderr)
        return 2

    print(f"TTFT probe → {args.endpoint}  (trials={args.trials}, max_tokens={args.max_tokens})\n")
    all_samples: list[Sample] = []
    for scenario in [x.strip() for x in args.scenarios.split(",") if x.strip()]:
        with_tools = scenario == "tool"
        all_samples += run_scenario(args.endpoint, scenario, with_tools,
                                    args.trials, args.max_tokens, args.timeout)
        print()

    summary = summarize(all_samples)
    print("=" * 70)
    print("SUMMARY (median prefill = the authoritative warm-vs-cold signal)")
    print("=" * 70)
    for scenario, blk in summary.items():
        if not isinstance(blk, dict) or "cold" not in blk:
            continue
        c = blk["cold"]["prefill_ms"]
        w = blk["warm"]["prefill_ms"]
        ct = blk["cold"]["wall_ttft_ms"]
        wt = blk["warm"]["wall_ttft_ms"]
        n = blk["cold"]["prompt_n"]
        print(f"\n[{scenario}]  prefix≈{int(n.get('median', 0))} tokens")
        print(f"  COLD  prefill median {c.get('median')}ms (min {c.get('min')} / "
              f"max {c.get('max')} / sd {c.get('stdev')})   wall-TTFT median {ct.get('median')}ms")
        print(f"  WARM  prefill median {w.get('median')}ms (min {w.get('min')} / "
              f"max {w.get('max')} / sd {w.get('stdev')})   wall-TTFT median {wt.get('median')}ms")
        print(f"  SAVED prefill {blk.get('prefill_saved_ms_median')}ms "
              f"({blk.get('prefill_saved_pct_median')}%) on a warm prefix")
        missing = (blk["cold"].get("wall_ttft_excluded", 0)
                   + blk["warm"].get("wall_ttft_excluded", 0))
        if missing:
            print(f"  WARN  {missing} trial(s) had no first token and were EXCLUDED "
                  f"from the wall-TTFT distribution")

    print("\nCAVEATS:")
    for c in CAVEATS:
        print(f"  - {c}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"endpoint": args.endpoint, "trials": args.trials,
                       "caveats": CAVEATS,
                       "summary": summary,
                       "samples": [asdict(s) for s in all_samples]}, f, indent=2)
        print(f"\nFull results → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
