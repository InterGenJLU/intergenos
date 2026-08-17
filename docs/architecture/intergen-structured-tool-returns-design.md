# InterGen Structured Tool-Result Returns — Research and Design

**Status:** Implemented. InterGen tools return a concise, model-facing summary
alongside the full user-facing payload, and InterGen Sentinel scans and isolates
that summary at the model trust boundary. This document surveys the prior art,
defines the return-shape contract, and describes the security wiring that scans
and spotlights tool results before they re-enter the model. A universal
character cap remains underneath as a safety net for any tool that has not yet
adopted a summary.

**Companion documents:**
[intergen-tool-author-guide.md](intergen-tool-author-guide.md) — the contract
below extends the tool-author surface ·
[intergen-provenance-gate-design.md](intergen-provenance-gate-design.md) — the
InterGen Sentinel ingress-scan and spotlight design referenced in Section 7.

---

## 1 — Problem statement

On a machine without a discrete GPU InterGen serves its smallest tier — a 2B
model at Q4_K_M — through `llama-server`, invoked from a per-user D-Bus daemon.
When a tool returns a large raw payload, that 2B chokes on the synthesis step
(`continue_after_tool_call`). The measurements below were taken on that tier and
are what motivate the contract; the contract itself is tier-independent.

> Measured on a test install: `manage_packages(list)` returned a full
> dump of roughly 42 KB for several hundred installed packages.
> `continue_after_tool_call` fed all 42 KB back to the 2B model, which spent
> 319 seconds, generated zero tokens, and produced no answer. The user saw only
> the tool-executed line and never received a reply.

The root cause is **not** the model and **not** the tool — it is the
**return-shape contract.** `ToolResult.content` is a single string consumed by
two very different audiences with opposite needs:

| Consumer | Wants | Prior behavior |
| --- | --- | --- |
| **The model** (`continue_after_tool_call`) | a concise, salient representation it can synthesize from in seconds | received the full raw dump |
| **The user** (web `tool_executed` line, audit log) | the complete result, on demand | received only a short head slice of the payload |

One field, two masters. The model was force-fed a payload it could not ingest;
the user was shown only the first hundred-odd characters of it. Neither audience
was served by the raw string, and head-truncation only accidentally works for
tools whose answer happens to sit at the front (counts, headlines). A tool whose
salient datum is at the *tail* — a `grep` whose match is on the last line, a log
read where the error is at the bottom, a `run_command` whose exit summary lands
last — loses its answer entirely under head-truncation.

**The fix is to split the contract:** a tool hands the model a concise
*structured summary* and hands the user the *full payload*, separately.

---

## 2 — Prior art

The table below is a grounded synthesis from full reads of the primary sources.
Quotes are kept to 125 characters or fewer per source.

| # | Source | Pattern | What it tells us | Applicability here |
| --- | --- | --- | --- | --- |
| 1 | **SLM Agentic Survey** — arXiv [2510.03847](https://arxiv.org/abs/2510.03847) | Schema-first / validator-first tool use; concise structured outputs | "tool-use accuracy is more critically dependent on argument correctness and strict schema adherence than on raw parameter count"; small language models are "inherently more efficient for concise, structured outputs"; format fidelity is a first-class metric; small models match or surpass large ones at far lower token cost *with structured I/O* | **High — the core principle.** Our 2B is exactly the small-language-model class this paper validates. Concise structured returns are the lever it names. |
| 2 | **Observation Masking** — arXiv [2508.21433](https://arxiv.org/html/2508.21433v1) (JetBrains, NeurIPS 2025); [blog](https://blog.jetbrains.com/research/2025/12/efficient-context-management/) | Replace tool observations older than a window with a placeholder; keep most-recent in full | 52.7% cost cut and +2.6% solve rate vs raw; *matches or beats* LLM summarization; LLM-summary causes "trajectory elongation" (13–15% longer runs, masks failure signals); observations are roughly 84% of agent trajectory tokens. A hybrid of masking plus batch summarization performs best | **Medium — transfer the lesson, not the mechanism.** That domain is *multi-turn* software-engineering agents; InterGen makes a single tool call per turn. The transferable finding: the raw observation must never reach the model verbatim, and cheap structural compression beats an extra summarization call. This argues against a summarizer-model middleware for us. |
| 3 | **Context Overflow / Pointer-Store** — arXiv [2511.22729](https://arxiv.org/abs/2511.22729) | Shift "from raw data to memory pointers"; model gets an index plus summary, dereferences full on demand | "truncation or summarization fail to preserve complete outputs"; the pointer approach used roughly 7× fewer tokens and ran a task the raw workflow could not | **Medium — the right model for "full-for-user."** The full payload lives in a store; our `ToolResult.content` and audit log already serve as that store. The model gets the summary. Model-side dereference is unnecessary for single-turn use, but the contract does not preclude it. |
| 4 | **LangChain SummarizationMiddleware** / Deep Agents | Token-threshold-triggered summarization in a `before_model` hook; clip large tool args; offload verbose tool I/O to the filesystem | Production pattern: monitor token count, summarize or clip past a threshold, keep AI/Tool pairs together | **Low–medium — confirms the layering.** Validates "threshold plus clip" as the floor and offloading the full payload elsewhere as the pattern. InterGen already offloads (content stays full) and adds the structured summary above the character cap. |
| 5 | **Model floor** — quantization degradation data | Q4_K_M vs Q3_K | Q4_K retains competitive accuracy; Q3_K loses roughly 40% on the cited evaluation. The Qwen llama.cpp documentation is *non-prescriptive* — it lists Q4_K_M as common but states no floor; the floor conclusion rests on the degradation data plus our own evidence, not an upstream mandate. | **Confirm and keep.** Q4_K_M is the right production floor for tool-calling; do not drop to Q3 to claw back latency. The latency fix is the return shape, not a smaller quantization. |

**Net conclusion from the literature:** the highest-leverage, lowest-risk fix is
**concise structured tool returns** (source 1), with the **full payload preserved
out-of-band** (source 3), and the existing **character cap kept as the floor**
(source 4). InterGen does **not** reach for a summarizer-model middleware
(source 2 shows it is costlier, can elongate trajectories, and adds a second
fragile model call on the exact latency-critical path we are trying to speed up).

---

## 3 — The recommended pattern

**Per-tool deterministic structured summary, full payload preserved, character
cap as floor.**

1. A tool that can produce large or unbounded output computes a **concise
   structured summary** itself — deterministic Python, no extra model call — and
   returns it alongside the full payload.
2. The **model** synthesizes from the summary; the **user** keeps the full
   payload.
3. The existing character cap stays underneath as a universal safety net for any
   tool that has not yet adopted a summary, or whose summary is somehow still
   large.

Why deterministic per-tool summaries rather than a generic summarizer:

- **Salience is tool-specific.** The right summary of `manage_packages(list)` is
  a count plus a sample; of `read_file` it is line and byte counts plus a head;
  of `run_command` it is exit status plus the tail. A generic head-truncator
  cannot know where the answer lives. The tool author does.
- **No second model call.** Source 2's central result is that paying for an extra
  summarization inference is *worse* than cheap structural compression. A
  deterministic summary costs microseconds and cannot time out or hallucinate.
- **Format fidelity (source 1).** A structured, predictable summary shape is
  exactly what raises a small model's synthesis reliability.

---

## 4 — The return-shape contract

`ToolResult` carries one optional field (`intergen/interfaces/types.py`):

```python
@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str                       # FULL payload — user-facing + audit
    success: bool = True
    model_summary: str | None = None   # concise model-facing summary; None = use content
```

**Semantics:**

- `content` is the **full** result. Every user surface (web `tool_executed.summary`,
  audit `result_summary`, CLI) and every existing tool keep working
  byte-for-byte — `model_summary` defaults to `None`.
- `model_summary`, when set, is **the only thing the model sees** for synthesis.
  It is a concise, structured, salient-first string: counts, status, and a sample,
  plus a one-line note telling the model how to talk about it.
- The synthesis path consumes `model_summary if model_summary is not None else
  content`, then applies the character cap. The cap is the *floor*, not the
  primary mechanism.

**The split is opt-in and incremental.** A tool adopts a summary when it has
large or unbounded output; until then it behaves exactly as before. There is no
big-bang migration and no regression for un-migrated tools.

**Touch points (all additive):**

1. `interfaces/types.py` — the optional field above.
2. `llm.py continue_after_tool_call` — accepts the model-facing text from the
   caller (it already takes `tool_result: str`; the router selects which field to
   pass, per point 3). The cap logic is unchanged and stays as the floor.
3. `router.py` — at the three synthesis call sites, pass
   `tr.model_summary or tr.content` instead of `tr.content`. The `tool_results`
   list returned in `RouteResult` is unchanged and carries full `content` to the
   user surface.
4. `web_server.py` — the same one-line selection on the streaming path
   (`tr.model_summary or tr.content` into `continue_after_tool_call`).

No change is required to the gate, the audit log, the user transcript, or any
tool that does not set `model_summary`.

---

## 5 — Per-tool inventory

Which tools produce large or unbounded output, and the right summary for each:

| Tool | Large? | Salient datum | `model_summary` shape |
| --- | --- | --- | --- |
| **`manage_packages`** | **Yes** (`list` produces a multi-kilobyte dump) | count plus whether a named package is present | `"<N> packages installed. Sample: a, b, c, … (+M more). Full list shown to the user."` — for `info`/`search`/`verify` the payload is already small, so no summary (pass-through) |
| `read_file` | Yes (file body) | path, line and byte count, head | `"<path>: 1240 lines, 48 KB. First 30 lines: …"` |
| `run_command` | Yes (arbitrary stdout) | exit code plus head **and** tail | `"exit 0; 612 lines out. Head: … Tail: …"` (the tail matters — errors and summaries land last) |
| `manage_services` | Yes (`list-units` returns all units) | count plus state breakdown | `"218 units: 142 active, 70 inactive, 6 failed. Failed: x, y, … Full list to user."` |
| `web_search` | Yes (results) | top-N titles and snippets | `"5 results. 1) <title> — <snippet> …"` (also an ingress tool — see Section 7) |
| `analyze_file` | Maybe | analysis verdict | summary is the verdict; detail is the body |
| `write_file` | No (confirmation) | — | none (pass-through) |
| `open_application`, `take_screenshot` | No / non-text | — | none |
| **MCP / external** | **Yes, unbounded, unknown shape** | not generically knowable | cannot be summarized per-tool (no author); the character floor is the backstop. A generic "first N plus last N plus length" structural summary could apply here (source 2 style) — see Section 10. |

`manage_packages` is the reference implementation (Section 6). The others follow
the same contract, each a small additive change.

---

## 6 — `manage_packages` reference implementation

The reference implementation (`intergen/tools/manage_packages.py`) sets
`model_summary` **only** for the unbounded `list` action and leaves every other
action — `info`, `search`, `verify`, install, remove, update, all already small or
already salient — as pass-through (`model_summary=None`).

For `list`, the tool parses the pkm output once and builds:

- the **count** (front and center — the most-asked question is "how many?"),
- a small **sample** of names (first ~10),
- a **note** instructing the model to state the count exactly and not enumerate.

The full `content` (the entire package list) is untouched, so the user surface
and audit log are byte-identical to before. Only the model-facing field shrinks —
from tens of kilobytes to a few hundred bytes.

---

## 7 — InterGen Sentinel ingress scan and spotlight on `model_summary`

InterGen Sentinel scans every tool result that crosses an ingress trust boundary.
For ingress tools (`read_file`, `analyze_file`, `web_search`, `take_screenshot`)
and all external/MCP handlers, the registry, after execution, **ingress-scans**
the result (block to withhold; flag to a modal that fails closed) and
**spotlight-wraps** it in untrusted-ingress markers before it re-enters the model.

The principle: **the moment an ingress tool emits a `model_summary`, that summary
becomes a new model-facing trust boundary.** The router and web server feed
`model_summary or content` to the model, so the summary — not `content` — is what
the model synthesizes from. A scan that only read `content` would let an injection
in a derived, possibly non-subset summary bypass Sentinel. As implemented
(`tool_registry.py`):

- **The scan covers both fields.** The ingress-scan input is `content` when there
  is no summary, and `content + "\n" + model_summary` when there is one. The full
  source catches injection *anywhere*, including the middle that a head/tail
  summary drops; the summary portion catches injection in a derived transform that
  is not a strict subset. One scan, one verdict, one modal.
- **A block or flag-deny withholds both.** `content` becomes the withheld notice
  **and** `model_summary` becomes `None`, so the router falls back to the withheld
  notice. A poisoned summary can never reach the model behind a clean `content`,
  or survive when `content` is withheld.
- **The spotlight wraps both.** `content` and `model_summary` (when present) are
  each wrapped in untrusted-ingress markers, with `is_wrapped()` idempotency
  guards.

**Cache interaction.** The `ToolCache` (`tool_cache.py`) stores read-only results
served automatically — including `read_file` and `web_search`, which carry a
`model_summary`. The scan runs **after** the cache lookup on the unified result,
so **a cache hit is scanned on every serve; there is no scan-bypass via the
cache.** One nuance: `put()` stores the result *reference* before the scan, and
the scan and spotlight mutate it, so the cached object can hold the
spotlight-wrapped form, or, on a block, the withheld form. The `is_wrapped()`
guards make re-serving idempotent, and a cached *withheld* result re-serves as
withheld, which is the fail-closed and secure outcome. A further hardening — having
`put()`/`get()` store and return a shallow `dataclasses.replace()` copy so the
cache holds the raw pre-scan result and each serve is scanned fresh — removes the
mutate-the-cached-object class entirely at negligible cost, since strings are
immutable and shared.

**`run_command` is intentionally excluded.** Its output is an ingress vector too,
but its inclusion is recorded in `provenance.py` as a separate,
security-reviewed decision: it is a privileged tool, and adding it changes
turn-taint semantics. This design does not alter that standing decision.

---

## 8 — Token budget

What summary size keeps the 2B comfortably under its latency cliff?

**Empirical anchors:**

- The failure was roughly 42 KB (about 10.5 K tokens) and produced 319 seconds
  with zero tokens generated.
- A 4000-character cap (about 1 K tokens) already resolved the timeout — the
  prior timed-out warning no longer appears. The cliff therefore sits between
  4000 characters (works) and 42 KB (dies).
- Warm normal-query latency on the test install is 2.66 seconds — the budget a
  summary synthesis should stay near.
- The small-model survey's interactive target (source 1) is p95 of 1.5–2.0
  seconds for short JSON/tool hops.

**Recommendation:** target `model_summary` at roughly 1500 characters (about 375
tokens) as a soft design target, with the 4000-character hard cap as the floor.

**Rationale:** 1500 characters is comfortably under the known-working
4000-character point, so synthesis stays in the low-single-digit-seconds band that
matches the 2.66-second warm baseline and the survey's interactive target.
Critically, because the summary is *structured and salient-first* rather than a
truncated dump, those ~375 tokens carry the **answer** (count, status), not just
the head. The cap stays at 4000 so any not-yet-summarized tool still cannot
reproduce the 42 KB cliff. The budget is character-based, not token-based, to stay
consistent with the cap; roughly 4 characters per token for English and
identifiers is a safe planning ratio.

---

## 9 — User-view behavior

**Finding (verified in code):** there is no surface that shows the user the full
tool payload. The web path caps `content` to a short slice (`web_server.py`) and
the front-end slices it further (`app.js`). The user sees a short head of the raw
output plus the model's synthesized prose. The large raw payload never reaches the
user directly.

**This design causes no regression:** `content` is unchanged, so the web
`tool_executed.summary` still shows the same short head, and the audit log still
records the full `content`. If anything, the full payload is now *more* cleanly
available, because it is no longer the value being mangled for the model.

The full payload still does not reach the user transcript verbatim, because no
such surface exists today. A "show full output" expander fed by `content` is a
clean user-experience addition that this contract enables; see Section 10.

---

## 10 — Design notes and open options

- **The `model_summary` contract is the foundation.** One optional field on
  `ToolResult`, additive, incremental, and zero-regression. Everything else in
  this document builds on it.

- **Full-payload user surface.** The user currently sees a short head of a tool
  result, never the full output. Options: leave it as-is (the model's synthesis
  *is* the user's answer, which is defensible for a conversational assistant); add
  a "show full output" expander in the web UI fed by `content`; or show
  `model_summary` (the clean count line) instead of the raw head in
  `tool_executed`. The clean-summary line is a strict improvement and inexpensive;
  the expander is a natural later user-experience addition.

- **Ingress-tool rollout and Sentinel wiring (Section 7).** The ingress scan and
  spotlight operate on `model_summary` for every ingress tool. `read_file`
  (structural head plus tail, overflow-gated) and `web_search` (snippet-trimmed,
  overflow-gated) emit summaries. `analyze_file`'s normal path is already bounded
  (it synthesizes via the model, capped at 1024 tokens); only its
  model-unavailable fallback dumps a raw file, which the generic Section 7 scan
  covers if a summary is ever added, and which is a candidate for the same
  `read_file` structural helper.

- **MCP/external generic fallback.** External tools have no author to write a
  per-tool summary, so they ride the character floor. A generic "first N plus
  last N plus total length" structural summary could cover them (source 2's
  cheap-compression result supports this). Building it speculatively is not
  warranted; the right time to add it is when an external tool actually trips the
  floor in practice.

- **Cache copy-on-put/get hardening.** The `ToolCache` stores the result
  reference before the scan and spotlight run, so the cached object can be mutated
  to its wrapped or withheld form. It is secure today — the scan runs on every
  serve, a withheld result re-serves as withheld, and idempotency guards prevent
  double-wrapping — but a shallow `dataclasses.replace()` copy on put and get
  would make the cache hold the raw pre-scan result and remove the
  mutate-the-cached-object class entirely at negligible cost.

---

## Sources

- SLM Agentic Survey — arXiv [2510.03847](https://arxiv.org/abs/2510.03847)
- Observation Masking ("The Complexity Trap") — arXiv [2508.21433](https://arxiv.org/html/2508.21433v1); JetBrains [blog](https://blog.jetbrains.com/research/2025/12/efficient-context-management/)
- Context-Window Overflow / pointer-store — arXiv [2511.22729](https://arxiv.org/abs/2511.22729)
- LangChain [SummarizationMiddleware](https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware)
- Qwen llama.cpp [quantization documentation](https://qwen.readthedocs.io/en/latest/quantization/llama.cpp.html) (model floor, non-prescriptive)
- In-tree: `intergen/llm.py`, `intergen/router.py`, `intergen/tool_registry.py`, `intergen/interfaces/types.py`, `intergen/tools/manage_packages.py`, `intergen/web_server.py`, `intergen/tool_cache.py`
