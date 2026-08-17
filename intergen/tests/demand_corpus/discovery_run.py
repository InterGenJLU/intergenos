# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Headless mass-discovery runner (M8-6 leg B/C).

Drives a demand-corpus JSONL bank through the existing reset-enabled harness
(InterGenTestClient, direct mode) against the live 9B, HEADLESS and governed:

  1. POLICY GATE RESPONDER — the in-process daemon's review callback is replaced
     with the discovery policy (discovery_policy.make_policy_review_callback):
     read-only executes (never reaches the callback), mutating/privileged is
     DENIED-AND-RECORDED (staged tool+args -> dispatch ledger, zero side effects,
     zero prompts). Fully headless: no OS/GTK modal can ever pop.
  2. NO TIMEOUT DEATHS — every turn runs under a SIGALRM wall-clock ceiling; a
     wedged/slow turn is SKIPPED-AND-RECORDED as its own finding and the run
     CONTINUES. The battery never wedges on one turn.
  3. DURATION GOVERNANCE — the whole-run budget is computed at launch
     (pending × measured warm p50 × safety factor); the duration-budget tripwire
     applies (3x per-turn budget = alarm-and-characterize, 5x = the hard skip
     ceiling); progress is CHECKPOINTED so a crash RESUMES, never restarts; ONE
     tailable log (<run_dir>/discovery-run.log) carries stage transitions.
  4. ARTIFACTS — per-run-id banking: XDG_STATE_HOME=<run_dir>/state co-locates
     glass.jsonl + decisions.jsonl + the dispatch ledger; results.jsonl +
     checkpoint.jsonl + run-meta.json + the runner log complete the record. The
     analysis phase reads ONLY banked artifacts.

The run is DISCOVERY, not pass/fail — it records everything InterGen does.

Prereq (leg C): direct mode spawns its OWN llama-server, so the production
daemon must be stopped first (frees GPU + :8080); restart it after. This runner
does NOT manage the production service — the run sequence does (see paste-back).
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import statistics
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("discovery_run")

# Warmup turns to measure a REPRESENTATIVE warm p50. Deliberately a MIX: LLM
# knowledge turns (the dominant per-turn cost, ~seconds) plus one cached state
# read — a state-only warmup measures the StateCache (~0.04s) and makes the
# duration budget wildly tight (the 29-false-alarm calibration bug, fixed here).
_WARMUP_TURNS = ["what is a kernel?", "explain what DNS does in one sentence",
                 "what is my hostname?"]


# Schema-aware slice predicate (README.md): expected_behavior_class is the
# 4-value hyphenated set; the surface half's finer class rides a `flex-ebc:` tag.
# `_is_readonly_safe(e)` is True for a turn that neither gates, mutates, nor writes
# memory. It is the SINGLE partition predicate: --readonly-safe keeps it True,
# --gated-only keeps its complement (the policy-live gated/mutating/memory slice).
_DROP_FINE = {"should_gate", "offer_affirmative", "offer_prefixed",
              "offer_decline", "should_recall", "decompose"}


def _flex_ebc(e: dict) -> str:
    for t in e.get("tags", []):
        if t.startswith("flex-ebc:"):
            return t.split(":", 1)[1]
    return ""


def _is_readonly_safe(e: dict) -> bool:
    if e.get("expected_behavior_class") == "should-gate":
        return False                  # mutating/privileged / offer-affirmative
    if _flex_ebc(e) in _DROP_FINE:
        return False                  # surface half's finer gated/memory classes
    if e.get("category") == "memory_personal":
        return False                  # avoid writing the live daemon's memory
    tags = e.get("tags", [])
    return "mutating" not in tags and "gated" not in tags


class TurnTimeout(Exception):
    """Raised by the SIGALRM handler when a turn exceeds its wall-clock ceiling."""


# Armed only for the duration of a governed turn. A SIGALRM whose itimer expired
# while a blocking C call (a wedged dbus call holding the GIL) was in flight is
# delivered to Python only AFTER that call returns — possibly after the turn's
# `finally` already cleared the timer. Without this guard that late-arriving
# signal raises TurnTimeout OUTSIDE _ask_governed's try/except and crashes the
# whole run (it killed the dbus full run at 617/1073 on the sf-cap review-modal
# wall, where each capability turn blocked the daemon for the full 120s dbus
# timeout). Gating the raise on the armed flag makes a stale alarm a harmless
# no-op — the run keeps going.
_ALARM_ARMED = False


def _on_alarm(signum, frame):  # noqa: ANN001
    if _ALARM_ARMED:
        raise TurnTimeout()


def _run_id() -> str:
    return "discovery-" + time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _setup_logging(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    handler_file = logging.FileHandler(run_dir / "discovery-run.log")
    handler_stream = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (handler_file, handler_stream):
        h.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler_file, handler_stream]
    log.propagate = False


def _load_completed(checkpoint: Path) -> set[str]:
    if not checkpoint.exists():
        return set()
    done = set()
    for line in checkpoint.read_text().splitlines():
        if line.strip():
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    return done


class DiscoveryRunner:
    def __init__(self, *, bank: list[dict], run_dir: Path,
                 safety_factor: float = 3.0, per_turn_ceiling_factor: float = 8.0,
                 min_ceiling_s: float = 45.0, resume: bool = False,
                 mode: str = "direct") -> None:
        self.bank = bank
        self.run_dir = run_dir
        self.mode = mode
        self.state_dir = run_dir / "state"
        self.safety_factor = safety_factor
        self.per_turn_ceiling_factor = per_turn_ceiling_factor
        self.min_ceiling_s = min_ceiling_s
        self.resume = resume
        self.checkpoint = run_dir / "checkpoint.jsonl"
        self.results = run_dir / "results.jsonl"
        self.ledger_path = self.state_dir / "intergen" / "dispatch-ledger.jsonl"
        self.warm_p50 = 0.0
        self.budget_s = 0.0
        self.ceiling_s = self.min_ceiling_s
        self._client = None
        self._ledger = None
        self._turn_hint = None

    # -- setup ------------------------------------------------------------
    def _bank_env(self) -> None:
        """Co-locate glass/decisions/dispatch-ledger under the run-id BEFORE the
        daemon is constructed (glass resolves its path from XDG at start)."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_STATE_HOME"] = str(self.state_dir)

    def _build_client(self):
        from intergen.tests.client import InterGenTestClient
        from intergen.tests.demand_corpus.discovery_policy import (
            DispatchLedger, make_policy_review_callback, _TurnHint,
        )
        self._ledger = DispatchLedger(path=self.ledger_path)
        self._turn_hint = _TurnHint()
        if self.mode == "direct":
            log.info("STAGE build-client: DIRECT mode — in-process daemon spawns "
                     "its own llama-server (production must be stopped AND the "
                     "com.intergenos.InterGen dbus-activation masked, else the "
                     "single-instance guard aborts the bind).")
            client = InterGenTestClient(mode="direct")
            # Replace the harness auto-approve with the discovery POLICY:
            # record-and-deny every mutating/privileged dispatch (zero side effects).
            cb = make_policy_review_callback(self._ledger, self._turn_hint)
            client._daemon._review_callback_override = cb  # type: ignore[attr-defined]
            log.info("STAGE build-client: direct daemon ready, discovery policy injected")
        else:
            log.info("STAGE build-client: DBUS mode — driving the LIVE production "
                     "daemon over the session bus. The policy responder is NOT "
                     "injected here (it is unit-proven RED/GREEN separately); the "
                     "bank MUST be --readonly-safe filtered so no gated/mutating/"
                     "memory turn reaches the production modal or pollutes memory.")
            client = InterGenTestClient(mode="dbus")
        atexit.register(client.close)
        self._client = client
        return client

    # -- per-turn ---------------------------------------------------------
    def _ask_governed(self, user: str, ceiling_s: float) -> dict[str, Any]:
        """Run one turn under a SIGALRM wall-clock ceiling. Returns an observation
        dict; on timeout returns a skip record and the run continues."""
        global _ALARM_ARMED
        if self._turn_hint is not None:
            self._turn_hint.set(user)
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, ceiling_s)
        _ALARM_ARMED = True
        t0 = time.monotonic()
        try:
            resp = self._client.ask(user)  # type: ignore[union-attr]
            elapsed = time.monotonic() - t0
            return {
                "user": user,
                "text": resp.text,
                "source": resp.source,
                "handled": resp.handled,
                "used_llm": resp.used_llm,
                "escalated": resp.escalated,
                "tool_calls": resp.tool_calls,
                "tool_results_len": sum(len(str(tr.get("content", "")))
                                        for tr in (resp.tool_results or [])),
                "trace_id": resp.trace_id,
                "elapsed_ms": round(elapsed * 1000, 1),
                "skipped": False,
            }
        except TurnTimeout:
            elapsed = time.monotonic() - t0
            log.warning("SKIP turn wedged >%.0fs (skip-and-record): %.60s",
                        ceiling_s, user)
            return {"user": user, "skipped": True, "skip_reason": "liveness_ceiling",
                    "elapsed_ms": round(elapsed * 1000, 1)}
        finally:
            _ALARM_ARMED = False      # disarm FIRST: a late alarm now no-ops
            signal.setitimer(signal.ITIMER_REAL, 0)

    # -- warmup + budget --------------------------------------------------
    def _measure_warm_p50(self) -> float:
        log.info("STAGE warmup: measuring warm p50 over %d read-only turns",
                 len(_WARMUP_TURNS))
        samples = []
        for q in _WARMUP_TURNS:
            obs = self._ask_governed(q, ceiling_s=max(self.min_ceiling_s, 90.0))
            if not obs.get("skipped"):
                samples.append(obs["elapsed_ms"] / 1000.0)
        self._client.reset_conversation()  # type: ignore[union-attr]
        p50 = statistics.median(samples) if samples else self.min_ceiling_s
        self.warm_p50 = p50
        return p50

    # -- the run ----------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self._bank_env()
        _setup_logging(self.run_dir)
        started = time.time()
        log.info("RUN start: bank=%d run_dir=%s resume=%s",
                 len(self.bank), self.run_dir, self.resume)

        completed = _load_completed(self.checkpoint) if self.resume else set()
        pending = [e for e in self.bank if e["id"] not in completed]
        log.info("RUN pending=%d (skipping %d already-completed)",
                 len(pending), len(completed))

        self._build_client()
        self._measure_warm_p50()
        # Floor the budget baseline: latency is bimodal (StateCache ~20ms vs LLM
        # ~1-13s), so a cache-fast warmup yields an absurdly tight budget (the
        # 29-false-alarm calibration bug). Floor at MIN_BASELINE so the tripwire
        # tracks real per-turn cost; warm_p50 is still reported verbatim.
        MIN_BASELINE_S = 1.0
        baseline = max(self.warm_p50, MIN_BASELINE_S)
        self.ceiling_s = max(self.min_ceiling_s,
                             baseline * self.per_turn_ceiling_factor)
        n_turns = sum(len(e["turns"]) for e in pending)
        self.budget_s = n_turns * baseline * self.safety_factor
        alarm_turn_s = baseline * 3.0
        log.info("BUDGET warm_p50=%.2fs turns=%d whole-run budget=%.0fs "
                 "(safety x%.1f) | per-turn: alarm>%.1fs skip-ceiling=%.1fs",
                 self.warm_p50, n_turns, self.budget_s, self.safety_factor,
                 alarm_turn_s, self.ceiling_s)

        meta = {
            "run_id": self.run_dir.name, "bank_size": len(self.bank),
            "pending": len(pending), "warm_p50_s": round(self.warm_p50, 3),
            "budget_s": round(self.budget_s, 1), "safety_factor": self.safety_factor,
            "per_turn_ceiling_s": round(self.ceiling_s, 1),
            "started": started,
        }
        (self.run_dir / "run-meta.json").write_text(json.dumps(meta, indent=1))

        skipped = 0
        alarms = 0
        run_t0 = time.monotonic()
        results_fh = self.results.open("a")
        ck_fh = self.checkpoint.open("a")
        try:
            for i, entry in enumerate(pending):
                # reset per conversation (skip for continuity categories)
                if entry.get("category") not in ("memory", "session_awareness"):
                    try:
                        self._client.reset_conversation()  # type: ignore[union-attr]
                    except Exception as exc:  # fail loud but keep going
                        log.error("reset failed on %s: %s", entry["id"], exc)
                observed_turns = []
                for turn in entry["turns"]:
                    obs = self._ask_governed(turn["user"], self.ceiling_s)
                    if obs.get("skipped"):
                        skipped += 1
                    elif obs["elapsed_ms"] / 1000.0 > alarm_turn_s:
                        alarms += 1
                        log.warning("ALARM turn %.1fs > 3x budget (%.1fs): %.60s",
                                    obs["elapsed_ms"] / 1000.0, alarm_turn_s,
                                    turn["user"])
                    observed_turns.append(obs)
                rec = {
                    "id": entry["id"], "category": entry["category"],
                    "ebc": entry.get("expected_behavior_class", ""),
                    "intent": entry.get("intent", ""),
                    "capabilities": entry.get("capabilities", []),
                    "tags": entry.get("tags", []),
                    "observed": observed_turns,
                }
                results_fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                results_fh.flush()
                ck_fh.write(json.dumps({"id": entry["id"], "ok": True}) + "\n")
                ck_fh.flush()
                if (i + 1) % 25 == 0 or (i + 1) == len(pending):
                    elapsed = time.monotonic() - run_t0
                    log.info("PROGRESS %d/%d entries | elapsed=%.0fs skipped=%d "
                             "alarms=%d staged-denied=%d",
                             i + 1, len(pending), elapsed, skipped, alarms,
                             len(self._ledger) if self._ledger else 0)
        finally:
            results_fh.close()
            ck_fh.close()

        ended = time.time()
        actual_s = time.monotonic() - run_t0
        summary = {
            **meta, "ended": ended, "actual_run_s": round(actual_s, 1),
            "turns_skipped": skipped, "turns_alarmed": alarms,
            "staged_denied": len(self._ledger) if self._ledger else 0,
            "budget_vs_actual": (round(actual_s / self.budget_s, 2)
                                 if self.budget_s else None),
        }
        (self.run_dir / "run-meta.json").write_text(json.dumps(summary, indent=1))
        log.info("RUN done: actual=%.0fs (budget=%.0fs, ratio=%s) skipped=%d "
                 "alarms=%d staged-denied=%d",
                 actual_s, self.budget_s, summary["budget_vs_actual"],
                 skipped, alarms, summary["staged_denied"])
        return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Headless mass-discovery runner (M8-6)")
    default_bank = str(Path(__file__).with_name("surface_flex.jsonl"))
    ap.add_argument("--bank", default=default_bank,
                    help="JSONL bank (or merged bank) to drive")
    ap.add_argument("--run-dir", default=None,
                    help="output dir (default: ./discovery-runs/<run-id>)")
    ap.add_argument("--mode", choices=("direct", "dbus"), default="direct",
                    help="direct = in-process daemon (needs production stopped + "
                         "dbus-activation masked); dbus = drive the live daemon")
    ap.add_argument("--readonly-safe", action="store_true",
                    help="drop gated/mutating/memory turns — REQUIRED for a dbus "
                         "run against the live production daemon (no modal hang, "
                         "no memory pollution)")
    ap.add_argument("--gated-only", action="store_true",
                    help="the COMPLEMENT of --readonly-safe: keep ONLY the "
                         "gated/mutating/memory turns. This is the policy-live "
                         "gated-flow slice — VALID ONLY in --mode direct (the "
                         "in-process daemon denies-and-records every mutating "
                         "dispatch and isolates memory to the run's XDG_STATE_HOME).")
    ap.add_argument("--sample", type=int, default=0,
                    help="smoke mode: run only the first N entries (0=all)")
    ap.add_argument("--stratified", type=int, default=0,
                    help="smoke mode: up to N entries PER category (0=off)")
    ap.add_argument("--category", default=None, help="filter to one category")
    ap.add_argument("--safety-factor", type=float, default=3.0)
    ap.add_argument("--resume", default=None,
                    help="resume an existing run dir (skips completed ids)")
    args = ap.parse_args()

    # Read raw entries via the authoritative loader (corpus_loader) so the run
    # drives exactly what corpus_merge validated; the extra fields (capabilities,
    # tags incl. flex-ebc) ride through as the trace-miner's coverage/EBC signal.
    from intergen.tests.corpus_loader import iter_corpus_records
    bank = iter_corpus_records(args.bank)

    if args.readonly_safe and args.gated_only:
        ap.error("--readonly-safe and --gated-only are mutually exclusive")
    if args.gated_only and args.mode != "direct":
        ap.error("--gated-only is direct-mode only (the live daemon would hang on "
                 "a modal / pollute memory); pass --mode direct")
    if args.readonly_safe:
        before = len(bank)
        bank = [e for e in bank if _is_readonly_safe(e)]
        print(f"readonly-safe filter: {before} -> {len(bank)} entries "
              "(dropped gated/mutating/memory turns for a live-daemon run)")
    if args.gated_only:
        before = len(bank)
        bank = [e for e in bank if not _is_readonly_safe(e)]
        print(f"gated-only filter: {before} -> {len(bank)} entries "
              "(kept ONLY gated/mutating/memory turns — the policy-live slice)")
    if args.category:
        bank = [e for e in bank if e["category"] == args.category]
    if args.stratified:
        by_cat: dict[str, list[dict]] = {}
        for e in bank:
            by_cat.setdefault(e["category"], []).append(e)
        picked: list[dict] = []
        for cat, items in by_cat.items():
            picked.extend(items[:args.stratified])
        bank = sorted(picked, key=lambda e: e["id"])
    if args.sample:
        bank = bank[:args.sample]

    if args.resume:
        run_dir = Path(args.resume)
        resume = True
    else:
        run_dir = Path(args.run_dir) if args.run_dir else (
            Path.cwd() / "discovery-runs" / _run_id())
        resume = False

    runner = DiscoveryRunner(bank=bank, run_dir=run_dir,
                             safety_factor=args.safety_factor, resume=resume,
                             mode=args.mode)
    summary = runner.run()
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
