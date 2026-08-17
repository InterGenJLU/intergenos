# The InterGen decision trace — schema, reconstruction, and the observe-first workflow

InterGen routes every user turn through a deterministic cascade with a model as
a fallback, then (on the tool path) executes tools through a provenance gate and
synthesizes an answer. The **decision trace** records *why* each of those steps
went the way it did, so the test harness can assert against the real decision
path instead of guessing it from the answer text.

This note covers the trace schema, how to reconstruct a turn's full decision
path from the trace alone, the `--observe` workflow, and the standing rule the
trace exists to enforce.

## The standing rule

> **Never guess at a routing bug — the trace must show it. If it doesn't, fix
> the trace.**

A test that asserts the *shape* of an answer has not tested the *path* that
produced it. When a route is wrong, the fix is grounded in what the trace shows,
not in a hypothesis about what the model "probably" did. If the trace can't
answer "why did this route win?", the trace is the thing to improve first.

## Two logs, one purpose

| | `decisions.jsonl` (`trace.py`) | `glass.jsonl` (`glass.py`) |
|---|---|---|
| Default | **off** (`INTERGEN_TRACE=1` to enable) | **on** (`INTERGEN_GLASS=0` to disable, loudly) |
| Content | routing/scoring metadata; raw content is a **separate** opt-in (`INTERGEN_TRACE_CONTENT=1`) | full-fidelity content by default |
| Shape | nested OpenTelemetry/OpenInference-style **spans** | flat per-turn event rows |
| Consumer | the **test harness** (this note) | the user-facing "show me everything it did" view |

Both are local-only, 0600, and redact credential-shaped keys. This note is about
`decisions.jsonl` — the harness's view.

## Span schema

Each span is one JSON line with OTel/OpenInference-aligned fields: `trace_id`,
`span_id`, `parent_span_id`, `seq` (a process-wide monotonic counter giving a
total decision order even within one millisecond), `name`, `kind`, `start_ms`,
`duration_ms`, `status`, and an `attributes` dict. Spans nest via
`parent_span_id`; every span of one turn shares its `trace_id`, which is also
stamped onto the turn's `RouteResult.trace_id` as the join key.

`kind` ∈ `request | router | gate | llm | tool | internal`.

### The spans a turn emits

| Span `name` | `kind` | Emitted at | Carries |
|---|---|---|---|
| `router.route` | request | the turn root | input (chars + gated text); classification (`query_type`, `semantic_score`, `semantic_runner_up`, `semantic_gap`, `semantic_intent_id`, `needs_decomposition`); route choice (`routed_via`, `route_trail`, `eligible_for_tools`, `eligibility_reason`); final output (`source`, `handled`, `used_llm`, `escalated`, output chars + gated text) |
| `router.llm_tools` | llm | the tool-decision model call | proposed `tool_calls`, `dispatch_any_failed/blocked/denied`, tokens |
| `tool.execute` | tool | one per tool invocation | `tool_name`, gated `tool_args`, `success`, `executed`, `blocked` |
| `tool.gate` | gate | child of a `tool.execute` | `gate_action` (execute/hold_for_review/reject), `risk_tier`, `effective_provenance`, `needs_pkexec`, gated `gate_reason` |
| `llm.synth` | llm | tool-result synthesis | `synthesis_tool`, `tool_results_in`, `used_model_summary`, `input_len`, `synthesis_ok`, tokens |
| `router.llm_freeform` | llm | the no-tool synthesis call | `grounding_present`, `message_count`, `synthesis_query_type`, tokens |

### `route_trail` — the alternatives considered

The root span's `route_trail` is an ordered list of the **scored decision tiers**
the cascade evaluated on the turn, ending in the winner:

```
[ {"stage": "classify",    "outcome": "info",     "query_type": "diagnostic"},
  {"stage": "decompose",   "outcome": "info",     "needs_decomposition": false},
  {"stage": "keyword",     "outcome": "rejected", "matched": false},
  {"stage": "semantic",    "outcome": "rejected", "score": 0.42, "gap": 0.11},
  {"stage": "eligibility", "outcome": "info",     "eligible": true},
  {"stage": "llm_tools",   "outcome": "won"} ]
```

`outcome` ∈ `info | rejected | won`. The trail records the tiers that compute a
routing signal (classify, decompose, keyword, semantic, eligibility) plus the
terminal winner — so a reader can see *why the winning route beat the earlier
ones*, not just which one won. Exhaustive notes for every deterministic
fast-path guard are a straightforward follow-on: the `_trail_note` helper makes
each one a one-line addition at its seam.

## Reconstructing a turn's decision path

`intergen/tests/trace_reconstruct.py` reassembles the ordered **six-element**
decision path from a turn's spans:

```
input → classification (+why) → route choice (+alternatives) →
tool calls (fired? gate verdict?) → synthesis (its inputs) → final output
```

```python
from intergen.tests import trace_reconstruct as tr

path = tr.load_trace("<run_dir>/intergen/decisions.jsonl", trace_id)
print(path.render())
assert path.is_complete(require_tools=True, require_synthesis=True)
```

`reconstruct(spans)` is pure (it consumes span dicts), so it also runs on an
in-memory span list. `DecisionPath.elements_present()` / `missing_elements()`
report exactly which of the six elements the trace carried; a tool-firing turn
requires all six, a deterministic fast-path turn legitimately has neither
`tool_calls` nor `synthesis`. Each tool call carries a **counterfactual** read
derived from its gate verdict + outcome: `fired` / `blocked_by_gate` (proposed
but refused/held) / `safety_blocked` / `failed`.

## The observe-first workflow

```
python3 -m intergen.tests.runner --mode direct --observe --ids <id>
```

`--observe` enables `INTERGEN_TRACE` for the run and isolates the sink at
`<run_dir>/intergen/decisions.jsonl`. The runner joins each turn's spans to the
turn by `trace_id` (`attach_traces`), writes a consolidated `trace.jsonl` beside
the results, and runs the trace-aware Gate-A pass (`apply_trace_grading`). The
intent: **watch the real decision path first, decide what to codify, then add
assertions** — never hard-fail quality on an observe run.

## Cost when off

With `INTERGEN_TRACE` unset every span is a no-op that allocates nothing and
writes nothing; the tool/gate/synthesis wrappers only observe and change no
dispatch or routing behavior. `test_decision_trace_reconstruction.py`'s
`test_tracing_off_is_transparent` pins that: the same turn routes identically,
writes no trace, and leaves `RouteResult.trace_id` empty.

## Verifying it end to end

`intergen/tests/test_decision_trace_reconstruction.py` drives a real tool-firing
turn ("list the printers") through `router.route()` with a mocked model and a
real tool registry, then reconstructs the six-element path from the emitted
`decisions.jsonl` alone and asserts it is complete — the visible-path
requirement, proven mechanically. On a live install, set `INTERGEN_TRACE=1`, ask
a question through the panel or D-Bus, and read `decisions.jsonl` to see the same
path for a real turn.
