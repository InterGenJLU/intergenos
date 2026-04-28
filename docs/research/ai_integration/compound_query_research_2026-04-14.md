# InterGen Compound Query Decomposition Research
**Date:** April 14, 2026
**Purpose:** Prior art on multi-step task handling for local LLM agents

---

## Sources

### ADaPT — As-Needed Decomposition and Planning
**URL:** https://arxiv.org/html/2311.05772v2
**Conference:** NAACL 2024

**Key findings:**
- Decompose only when executor FAILS — try monolithic first
- Recursive decomposition: if sub-task fails, decompose further
- Generates shorter plans than upfront decomposition
- Better for dynamic accommodation to task complexity

### DAAO — Difficulty-Aware Agentic Orchestration
**URL:** https://arxiv.org/abs/2509.11079

**Key findings:**
- Estimates query complexity BEFORE execution using lightweight classifier (VAE)
- Routes to appropriate workflow based on difficulty
- Combines: query-level difficulty estimation, modular operator allocation, heterogeneous LLM routing

### Amazon — Task Decomposition + Smaller LLMs
**URL:** https://www.amazon.science/blog/how-task-decomposition-and-smaller-llms-can-make-ai-more-affordable

**Key findings:**
- Small models + decomposition outperform large models on monolithic tasks
- Each sub-task gets focused context instead of juggling everything
- More affordable than scaling model size

### Prior internal TaskPlanner implementation (internal prior art)
**Location:** <prior-project>/core/task_planner.py

**Key patterns ported:**
- Fast compound detection via regex (conjunctive phrases: "and then", "after that")
- One-shot LLM plan generation (JSON output, max 4 steps)
- Sequential execution with context passing between steps
- Post-step evaluation (CONTINUE/ADJUST/STOP)
- Voice interrupt handling (stop/skip/pause)

---

## Applied in InterGen

- Tier-aware thresholds: Tier 1=1 action, Tier 2=3, Tier 3=5
- Fast regex detection (microseconds, no LLM cost)
- Smart splitting on conjunctions with cleanup
- User-facing message: competent not apologetic
- False positive handling: "disk space and usage" = single intent, don't split
