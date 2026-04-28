# InterGen Code Review Packet — Introduction

**Date:** April 17, 2026
**Author:** InterGenJLU + Claude
**Commit:** 72c041c (master)
**Project:** InterGenOS — AI-Integrated Linux Distribution

---

## What You're Reviewing

InterGen is the built-in AI assistant for InterGenOS, a Linux distribution built entirely from source. It runs a **Qwen3.5-2B local LLM** via llama.cpp on the user's hardware — no cloud dependency. InterGen answers system questions, executes commands, manages packages and services, and diagnoses problems.

This packet covers the three files that form InterGen's behavioral core:

| File | Lines | Purpose |
|------|-------|---------|
| `intergen/router.py` | 868 | Routes user queries through a priority chain to produce responses |
| `intergen/llm.py` | 693 | Manages local LLM interaction, adaptive prompting, agentic synthesis |
| `intergen/tests/grader.py` | 388 | Evaluates behavioral test assertions against actual responses |

These files were the focus of a 20-round quality revolution (R1–R20) that raised InterGen's behavioral accuracy from a lying 99% (grader bugs hiding ~50 failures) to an honest 97–102 PASS out of 112 test conversations across 4 stable rounds (R17–R20).

## Why These Files Matter

**router.py** is the brain. Every user query enters `route()` and exits as a `RouteResult`. The priority chain (P0–P4) determines whether a query gets an instant template response, a tool-backed answer, or an LLM-generated reply. Bad routing = fabrication, hallucination, or capability denial. Good routing = the right path for each query type.

**llm.py** is the voice. It manages all interaction with the local Qwen 2B model: streaming, tool calling, quality gating, cloud escalation, and — critically — the adaptive prompting system that gives each query type only the rules it needs. The agentic loop (`continue_after_tool_call`) sends tool results back to the LLM for human-readable synthesis, with strict grounding to prevent fabrication.

**grader.py** is the conscience. It evaluates 112 test conversations with both explicit assertions (per-test) and 13 auto-assertions (applied to every response). The grader was itself the subject of a calibration effort — R10 showed 99% on a grader that was testing absence of bad words, not presence of correct answers.

## What We Learned (Rule #11)

The single most important finding from 20 rounds of testing:

> **"Check OUR code first — Qwen wants to behave."**

Every behavioral issue we attributed to the 2B model traced back to our code or prompts:

| Symptom | Blamed | Actual Cause |
|---------|--------|--------------|
| "I am InterGenOS" identity confusion | Model confusion | "InterGenOS" appeared 3x more than "InterGen" in LLM-visible text |
| Raw data dumps instead of summaries | Model laziness | Cache intercepted response before LLM could format it |
| "Please run this command" | Model training bias | Diagnostic modifier was missing from adaptive prompting |
| Fabricated system data | Model hallucination | Synthesis prompt said "present as though you know it" — 2B took it literally |
| "dd" safety bypass | Model ignorance | "dd" wasn't in our safety trigger list |
| Compound query failures | Model limitation | "and what/how" not in compound detection patterns |

**Small models take instructions literally.** "Act like you know it" on a 2B model = fabricate. The same instruction works on 9B+ because larger models understand nuance. This insight shaped every design decision in the adaptive prompting system.

## How to Read This Packet

1. **This document** (00_introduction.md) — framing and context
2. **01_architecture.md** — full pipeline diagram, design rationale, data flow
3. **02_router_walkthrough.md** — router.py line-by-line with design decisions
4. **03_llm_walkthrough.md** — llm.py: adaptive prompting, agentic loop, quality gate
5. **04_test_methodology.md** — test suite design, grader calibration
6. **05_variance_band.md** — R17–R20 data, honest vs. headline scores
7. **06_adaptive_prompting_design.md** — prior art, data-backed rationale
8. **07_known_issues.md** — remaining issues with root cause analysis

## Questions for Reviewers

1. **Routing logic:** Is the P0–P4 priority chain sound? Are there ordering issues we're missing?
2. **Adaptive prompting:** Is classify-then-compose the right pattern for a 2B model? Are there simpler/better approaches?
3. **Agentic loop:** The synthesis grounding prompt ("use ONLY tool data") was critical. Is this pattern robust, or fragile?
4. **Grader design:** 13 auto-assertions applied to every response. Too many? Too few? Missing categories?
5. **Safety:** The safety trigger word list is flat (no regex, no context). Is this sufficient for a system-level assistant?
6. **2B ceiling:** We plateau at 97–102/112. Is this expected for a 2B model with tool calling? What would break through?

## Key Constraints

- **CPU-only on Tier 1** — no GPU assumed. Response time budget: <15s per query.
- **No external dependencies at runtime** — llama-server is the only subprocess. No LangChain, no vector DB, no embedding service.
- **Prime Directive** — "InterGenOS exists to put the user in control of their own machine." Every design decision serves user transparency and control.
- **Local first** — cloud escalation exists but is opt-in and off by default.
