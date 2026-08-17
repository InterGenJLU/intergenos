<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2015-2016, 2026 InterGenJLU -->

# InterGen Perceived-Latency Architecture

**Status:** design (phased build in progress)
**Created:** 2026-06-10
**Related:** [intergen-structured-tool-returns-design.md](intergen-structured-tool-returns-design.md) (the `model_summary` contract this builds on)

## Problem

InterGen's smallest local model tier (the 2B, which is what a machine without a
discrete GPU always runs) has real, unavoidable latency: warm synthesis runs ~10–20 s on low-end hardware, and some system calls
(for example `manage_packages list` shelling out to `pkm`) take seconds on their
own before the model ever sees the data. A user staring at a silent, motionless
UI for 15 s concludes the assistant is broken, even when it is working perfectly.

The 2B model's inference time is fixed. What we control is the standard
assistant-UX pattern of **managing perception**: acknowledge instantly, reassure
during the wait, and let the model own the final synthesis so the answer still
reads naturally. Real latency is unchanged; perceived responsiveness is
transformed.

This document is informative. It describes the architecture so it is not
reinvented.

## The three latency bands

Every tool-bound turn is classified into one of three bands by how long the
**system call** (the `tool.execute()` step) takes to produce data. The band
decides the *display* behavior; the model owns synthesis in all three.

| Band | System-call time | Behavior |
|------|------------------|----------|
| **Instant** | < ~1.5 s | No filler. Stream the answer directly; an acknowledgment would land on top of the result and read as noise. |
| **Medium** | ~1.5–7 s | **Hop-1 acknowledgment** fires the instant the query is classified tool-bound, then the model handles the turn end to end with natural synthesis. |
| **Slow** | > ~7 s | Hop-1 acknowledgment **plus Hop-2 progress nudge(s)** while the call runs, cache consulted first, then model synthesis on the results. |

The 1.5 s and 7 s thresholds are starting points, tuned from the matrix (below)
and adjustable per hardware tier.

## Timer-driven, not just matrix-driven (the key robustness rule)

A static per-call threshold is fragile: the same call is fast warm and slow
cold, fast on newer hardware and slow on older hardware, fast cached and slow
uncached. So:

- The **matrix is a prediction.** It decides which band to pre-arm: whether to
  set up the slow lane (cache lookup plus a scheduled hop-2 nudge) before the
  call starts.
- A **wall-clock timer is the guarantee.** Every tool call starts a timer
  regardless of band. If a call the matrix predicted "fast" crosses ~5 s anyway
  (cold disk, busy machine, a degraded mirror), the hop-2 nudge fires as a
  **backstop**. The user is never left waiting on a misclassified call, which a
  pure-matrix design will eventually allow.

In short: the matrix optimizes the common case; the timer enforces correctness.

## The three artifacts

These are *distinct* build artifacts. "Harness" refers only to the first.

### 1. Timing harness (measurement)

A benchmark tool (`intergen/tools/latency_harness.py`, run via
`python3 -m intergen.tools.latency_harness`) that exercises every
`(tool, action, representative-arg)` over the **safe, read-only (AUTO-tier)**
surface, cold and warm, N samples each, and emits the **latency matrix** as a
checked-in JSON artifact.

- **Safe surface only.** State-changing or side-effectful actions
  (`install`, `remove`, `restart`, `write_file`, `run_command`,
  `take_screenshot`, `open_application`) are **never** auto-run by the harness.
  Those are CONFIRM-tier: they show a permission card first, so their
  perceived latency is the dialog, not a filler. The harness benchmarks:
  `manage_packages {list, search, info, verify, status, query}`,
  `manage_services {status, is-active, is-enabled, is-failed, list-units}`,
  `read_file`, `analyze_file`, and `web_search` (flagged network-variable).
- **Cold vs warm.** The first sample of each call is recorded separately
  (cold), then M warm samples → p50 / p99. Cold-start cost is exactly what the
  cache later removes.
- **Reusable as a regression guard.** Re-run it to catch a tool that silently
  got slow.
- **Runs on the target, not the build host.** `pkm`, `systemctl`, and the like
  only exist on a real InterGenOS install, so the matrix is captured on
  representative hardware (with a low-end reference machine setting the slow
  floor).

**Matrix format** (`intergen/data/latency-matrix.json`):

```json
{
  "version": 1,
  "captured_on": "<hostname / hardware tier>",
  "thresholds_ms": { "instant": 1500, "slow": 7000, "timer_backstop": 5000 },
  "calls": [
    { "tool": "manage_packages", "action": "list",
      "cold_ms": 0, "warm_p50_ms": 0, "warm_p99_ms": 0, "band": "slow",
      "cacheable": true, "cache_ttl_s": 30 }
  ]
}
```

#### Empirical finding (low-end reference hardware, 2026-06-10 — first capture)

The first matrix run (`intergen/data/latency-matrix.json`, captured on the slow
floor) overturned the starting assumption. **10 of 12 safe read-only system
calls execute in under 120 ms**: `read_file` 0.1 ms, the `manage_services`
queries 3–8 ms, and every `manage_packages` action ~118 ms. The system call
itself is almost never the bottleneck. The two calls above 120 ms are outliers
for different reasons: `web_search` at **~824 ms** is network-bound, and
`analyze_file` at **~12.7 s** is model-bound.

`analyze_file` is the slowest because that tool invokes the model internally
(`_call_llm`). It is slow not as a system call but as an inference. This splits
the latency-heavy surface into **two classes**:

1. **System-call-bound** (`manage_packages`, `manage_services`, `read_file`):
   fast execution; the perceived cost is the subsequent synthesis hop, which
   scales with **output size**. `manage_packages list` is 42 KB; `manage_services
   list-units` is **64 KB**. The harness times execution only, so for these the
   matrix under-counts true perceived latency; the real term is
   `synthesis(content_len, warm/cold)`. The `model_summary` contract is exactly
   what collapses that term (`manage_packages list` already sets one).
2. **Model-bound tools** (`analyze_file`, and any future tool that calls the
   model): slow at the tool level itself, and the clearest candidates for the
   slow lane.

**Consequences for the design:**

- The band classifier keys on `(tool_exec_p50, content_len, llm_backed)`, not
  execution time alone. A fast-execution, large-output call is "instant
  execution, slow synthesis" and still warrants hop-1, because the total turn is
  slow.
- **`manage_services list-units` (64 KB) has no `model_summary` yet.** Folding it
  into the structured-returns work (`manage_packages list` is already done) is
  the highest-leverage synthesis win.
- Caching's value is **narrower than first assumed.** The system-call data it
  caches is already fast, so its win is repeated identical reads: skip the
  execution and reuse the cached `model_summary` to skip re-synthesis. The bigger
  lever for model-bound slowness is keeping the model warm via prompt caching, a
  separate performance effort.

### 2. Caching subsystem (runtime, in the request path)

This is a real component, not a `dict.get()`, because a stale cache that
confidently reports a wrong fact is a **fabrication-class failure**, which
InterGen's security-first posture does not tolerate.

- **Key:** `(tool, normalized-args, user)`. Per-user scoping is mandatory: a
  privileged read cached for user A must never be served to user B (the same
  rule that moved `memory.db` to per-user XDG state).
- **TTL:** short, per-call, from the matrix (`cache_ttl_s`). Read-only system
  state goes stale quickly.
- **Invalidation hooks (the part that makes it safe):** any **successful
  state-changing call flushes the related read cache.** A `manage_packages
  install/remove/update` flushes the package-list, info, and verify entries; a
  `manage_services start/stop/restart/enable/disable` flushes service-status
  entries. Event-based invalidation beats TTL-only for correctness.
- **Trust boundary:** the cache is trusted InterGen substrate (Zone 3). There is
  no untrusted write path; only the tool layer populates it.
- **Scope:** only AUTO-tier read-only calls are cacheable. CONFIRM-tier calls
  execute fresh every time.

### 3. Filler / router layer (the voice)

- **Hop-1 acknowledgment** fires from the **semantic fast-path classifier** the
  instant a query is recognized as tool-bound, before the model/tool round-trip,
  so it is genuinely under 100 ms. The server emits a `tool_ack` event; the
  client renders it immediately. **Hop-1 asserts nothing about the result.** The
  pool is written so every line composes with success, a gate prompt, or a
  refusal, avoiding the "I'm on it!" followed by a permission-card whiplash.
- **Hop-2 progress** fires when the timer crosses the slow threshold (either
  matrix-armed or via the ~5 s backstop). The server emits `tool_progress`; the
  client keeps the **thinking pill** alive and shows the nudge. Hop-2 **implies
  delivery**, so it only fires once the system is committed to answering, and the
  slow path must always produce a terminal message (even "that took longer than
  expected" on timeout). There is never silence after a nudge.
- **Pools:** `intergen/data/voice/fillers.json`, installed to
  `/usr/share/intergen/voice/`. 24 hop-1 lines and 24 hop-2 lines (20 generic
  plus 4 with a `{what}` slot filled by a noun-phrase mapper, for example "the
  package list" or "the cups service"). Selection is **random with no repeat
  within the last 5** (a small per-pool ring buffer). The asset is loaded as data
  so the voice is tunable without a code change. The voice target is a helpful,
  current-generation assistant: warmth and precision with a light touch.

## Anti-fabrication and gate composition (security-first)

- A filler is **never** a claim. Hop-1 promises nothing; hop-2 promises only that
  an answer is coming, which the terminal-message rule guarantees the system
  honors.
- Fillers must **compose with refusals.** If a call turns out to be gate-blocked
  or forbidden by the trust boundary, the neutral acknowledgment still fits and
  the permission or refusal card follows naturally.
- The cache must **never** serve a stale fact as current. Invalidation hooks plus
  TTL are load-bearing, not optional.

## Telemetry: the matrix self-tunes

`MetricsTracker` already records per-call latency (average and p99, surfaced in
the `/metrics` card). That gives the matrix **two feeds**: the active harness for
the initial table, and passive production telemetry so the bands can adapt to the
real machine over time. The harness is built once; production telemetry keeps it
honest.

## Build phasing

1. **Voice asset** (`fillers.json`) — shipped. ✅
2. **This design document.** ✅
3. **Timing harness** plus capture of the **matrix** on reference hardware. ✅
   (finding: the system call is rarely the bottleneck; the model is. See the
   empirical finding above.)
4. **Filler runtime** — `tool_ack` and `tool_progress` WebSocket events; the
   thinking pill speaks. ✅ (validated on test hardware by driving the WebSocket)
5. **Caching subsystem** — `intergen/tool_cache.py`: per-user keyed store, TTL,
   and event-based invalidation, wired at `ToolRegistry.execute()`. ✅ (validated
   on test hardware: a repeated `manage_packages list` served in 0.24 ms versus
   119.7 ms, with the `model_summary` preserved so re-synthesis is skipped too)
6. **Tune** thresholds from the matrix and production telemetry. ← next

Each phase is independently shippable and observable.
