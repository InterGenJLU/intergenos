# InterGen Tool Author Guide

**Audience:** developers writing or modifying tools that InterGen can call
on the user's behalf.

**Companion:** [intergen-provenance-gate-design.md](intergen-provenance-gate-design.md)
is the canonical D-008 RFC. This guide is the implementation-side
companion. Read the RFC first if you want the rationale; read this guide
if you want to ship a tool.

## 1. What you get for free

The dispatcher gate runs in front of every tool call. As a tool author
you do not have to:

- Detect prompt injection — the gate's ingress-tool watermark
  (RFC §5.1) handles provenance escalation when injection-bearing content
  was read earlier in the conversation, whether in the current turn or a
  prior turn of the same conversation.
- Implement a confirmation modal — the gate displays the review modal
  (RFC §7) when your tool is held for user review.
- Track cross-turn user trust decisions — `ConversationTrustState`
  records symmetric allow/deny choices and the router resets them at
  conversation end.
- Write to the audit log — the registry writes one `AuditRecord` per
  dispatch decision to the user's
  `$XDG_STATE_HOME/intergen/tool-dispatch.jsonl`.
- Inject the `source_of_request` field — the system prompt directive
  and `ToolSchema.to_openai()` already require it on every call.

You do have to:

- Declare a `SafetyTier` (and override `classify_safety()` if the tier
  is per-invocation, e.g., `run_command`).
- If your tool reads or fetches ingress content, wrap that content with
  `intergen.spotlighting.wrap_ingress_content()` before returning it to
  the LLM.
- If your tool name belongs in the ingress set
  (`INGRESS_TOOLS_V1` in `intergen.interfaces.provenance`), add it there
  so the watermark fires correctly.
- Write the standard unit tests, plus — if you are an ingress-class
  tool — an injection-corpus integration entry.

## 2. The minimum viable tool

```python
# intergen/tools/echo.py

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema


class EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo a string back to the user verbatim."

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "what to echo"},
                },
                "required": ["text"],
            },
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments: dict) -> ToolResult:
        return ToolResult(content=arguments["text"], success=True)
```

That is the entire tool. The registry discovers `EchoTool` automatically
because the module lives in `intergen/tools/` and the class subclasses
`BaseTool`. The gate gives it `READ_ONLY` risk tier (because
`SafetyTier.AUTO` maps to `READ_ONLY` in `_classify_risk_tier`) and
executes it without modal interruption for any provenance category.

## 3. The BaseTool contract

`intergen/interfaces/tool.py` defines the abstract base. Every concrete
method:

- `name: str` — unique identifier the LLM uses to invoke the tool. Use
  snake_case. Must be stable across releases (renaming breaks the
  schema pin in `/var/lib/intergen/mcp-pins/`).
- `description: str` — one or two sentences explaining what the tool
  does. The LLM reads this when deciding whether to call you.
- `schema: ToolSchema` — OpenAI-style function-calling schema, plus the
  default `safety_tier`. Argument names in `parameters` are user-facing
  contracts; do not rename without a major version bump.
- `execute(arguments)` — the actual work. Returns `ToolResult`. The
  registry has already passed the gate by the time you are called.
- `classify_safety(arguments)` — override when safety depends on
  arguments (`run_command` for example: a `query`-mode invocation may be
  read-only while a `start`-mode invocation is state-changing).
  Default returns `self.schema.safety_tier`.
- `validate_arguments(arguments)` — defaults to checking `required`
  fields. Override if you need richer validation; return `None` on
  success or an error string on failure.

## 4. Safety tier vs. risk tier

Two related classifications:

- `SafetyTier` (per-tool, declared by the tool) — `AUTO`, `CONFIRM`,
  `BLOCKED`. Authored on the tool. `BLOCKED` is a hard refusal
  independent of provenance.
- `ToolRiskTier` (computed by the registry, fed to the gate) —
  `READ_ONLY`, `USER_SCOPE_STATE_CHANGING`,
  `PRIVILEGED_STATE_CHANGING`. The dispatcher's
  `_classify_risk_tier(tool, arguments, tool_name)` derives this from
  the safety tier plus the privileged-tools allowlist.

Mapping in `intergen/tool_registry.py:_classify_risk_tier`:

| Tool state | ToolRiskTier |
|---|---|
| Name in `_PRIVILEGED_TOOLS` | `PRIVILEGED_STATE_CHANGING` |
| External handler (no class) | `USER_SCOPE_STATE_CHANGING` (safe default) |
| `SafetyTier.CONFIRM` | `USER_SCOPE_STATE_CHANGING` |
| `SafetyTier.AUTO` | `READ_ONLY` |
| `SafetyTier.BLOCKED` | rejected before the gate (no `ToolRiskTier`) |

The `_PRIVILEGED_TOOLS` set lives at the top of `tool_registry.py`.
Currently: `manage_services`, `manage_packages`, `run_command`,
`write_file`. Add your tool to that set if it can escalate via
`pkexec` per D-007 Option A; do not declare privilege through
`SafetyTier` alone, because `CONFIRM` is reserved for user-scope
state changes.

The behavior matrix the gate enforces is in RFC §6 Table:

| Tool tier | `user_direct` | `user_implied` | `ingress_derived` |
|---|---|---|---|
| read-only | execute | execute | execute (logged) |
| user-scope state-changing | execute | execute | HOLD + review |
| privileged state-changing | execute + pkexec | HOLD + review + pkexec on allow | HOLD + review + pkexec on allow |

## 5. The `source_of_request` field

Every `ToolCall` carries a `source_of_request` string declared by the
LLM. The dispatcher rejects any call without one — RFC §5.3 no-fallback
policy.

You do not have to handle this directly. The system prompt directive
`_PROVENANCE_DIRECTIVE` (in `intergen/llm.py`) instructs the model to
declare; `ToolSchema.to_openai()` injects the field into every tool's
argument schema as a required enum:
`user_direct` / `user_implied` / `ingress_derived`.
`ToolCall.__post_init__` raises if the field is absent or invalid.

What this means for your tool:

- The `arguments` dict your `execute()` sees does NOT contain
  `source_of_request` — the registry strips it before dispatching to
  you. Do not list it in `parameters.required`; the schema layer adds
  it transparently.
- If your tool is invoked from CLI test code (bypassing the LLM), you
  must still build a `ToolCall` with a valid label, or the dispatcher
  will reject the call. Tests typically use `user_direct` for
  fixture-driven invocations.

## 6. When is your tool in the ingress set?

A tool belongs in the ingress set if its result delivers free-text
content that could carry instructions targeted at the LLM. The current
set (RFC §5.1, defined as `INGRESS_TOOLS_V1` in
`intergen/interfaces/provenance.py`) is:

- `read_file` — file bodies are arbitrary text and can carry injection.
- `web_search` — fetched web result content.
- `analyze_file` — reads and summarizes file content.
- `take_screenshot` — screen capture surfaced back to the LLM via
  OCR or description.

Every name in the set must resolve to a registered tool; a regression
guard (`intergen/tests/test_ingress_watchlist.py`) asserts this so the
set cannot silently drift out of sync with the tools that actually ship.

`manage_services(action=query)` is read-only but is NOT in the ingress
set per RFC §14.1 — it returns structured machine data, not free-text.
The dividing line is "could the result text contain an instruction the
LLM might try to follow."

If you author an ingress-class tool, add its name to `INGRESS_TOOLS_V1`
in `intergen/interfaces/provenance.py`. The dispatcher's watermark
escalates the effective provenance label of any subsequent tool call
once an ingress tool has fired earlier in the conversation. The
`IngressTracker` keeps two windows over the set: a per-turn window (the
original §5.1 same-turn watermark) and a per-conversation window that
survives across turns, so a fetch-poison-then-act-next-turn attack is
still caught even when the motivating ingress was read in a prior turn
(see RFC §5.1 table).

## 7. Spotlighting ingress content

If your tool returns ingress content, wrap it before handing it back to
the LLM:

```python
from intergen.spotlighting import wrap_ingress_content

class ReadUrlTool(BaseTool):
    def execute(self, arguments: dict) -> ToolResult:
        url = arguments["url"]
        body = _fetch(url)  # raw page text
        wrapped = wrap_ingress_content(
            body,
            source=url,
            source_type="url",
        )
        return ToolResult(content=wrapped, success=True)
```

Why:

- The LLM sees an explicit `<UNTRUSTED-INGRESS source="...">...</UNTRUSTED-INGRESS>`
  region in its context, so it is structurally aware of the trust
  boundary (RFC §10 v1.x item 2, pulled into v1.0 by the 2026-05-19
  D-008 amendment).
- The review modal extracts the `source` attribute when a downstream
  action triggers a hold, so the user sees the exact URL or file path
  that motivated the action.
- `wrap_ingress_content()` automatically escapes any embedded
  `</UNTRUSTED-INGRESS>` literal so an adversary writing the closing
  marker into a page cannot break out of the wrapper.

Use the canonical `source_type` values: `"url"`, `"file"`,
`"web_search"`, `"clipboard"`, `"directory_listing"`, or
`"untrusted"` as a fallback. Anything else is preserved verbatim, but
the review modal renders the canonical values with friendlier labels.

## 8. Test expectations

For any tool, write the standard unit tests:

- Happy path — `execute()` returns success on a valid invocation.
- Validation — `validate_arguments()` rejects each malformed input.
- Safety classification — `classify_safety()` returns the expected tier
  for representative argument shapes (especially for tools that
  override the default).

For ingress-class tools, add two more:

- Spotlighting — verify the result content matches
  `intergen.spotlighting.is_wrapped()` and that
  `extract_first_wrapped_region()` returns the expected
  `(source, source_type, body)` triple.
- Injection corpus — see §9.

For privileged tools, add the registry-side gate tests:

- Dispatch as `user_direct` — execute path fires (still through
  pkexec per D-007).
- Dispatch as `user_implied` or `ingress_derived` — `hold_for_review`
  fires; the review callback is invoked; the audit log records the
  decision.

The existing dispatcher gate tests in `intergen/tests/` (notably
`test_privileged_dispatch_gate.py`, `test_router_classification.py`, and
`test_temporal_watermark.py`) exercise the matrix at the framework
level; you do not need to re-derive them per tool. You do need to verify
your tool composes correctly with the gate — that the registry can find
it, classify it, and dispatch it without error.

## 9. Injection corpus integration

If your tool is in the ingress set, exercise it against the injection
corpus at `tests/intergen/injection_corpus/`. The corpus contains
fixture files in eight injection categories (instruction-override,
tool-coercion, exfil-prefix, system-prompt-leak, role-confusion,
authority-spoof, delimiter-attack, encoding-attack) plus benign
negatives. Schema and add-pattern instructions live in
`tests/intergen/injection_corpus/README.md`.

The parametrized harness `tests/intergen/test_injection_corpus.py`
sweeps each fixture against the advisory pattern scanner
(`intergen/pattern_detect.py`). If your tool transforms content (for
example, a sanitizing reader that strips obvious markup), confirm the
canonical fixtures still trigger the scanner after your transform —
sanitization that defeats the corpus pattern is a regression even when
it is well-intentioned.

If a fixture exposes a gap (a real-world injection pattern that
neither the scanner nor your tool catches), extend
`patterns.json` and add a fixture file under `fixtures/`. Run
`tests/intergen/injection_corpus/verify-corpus.py` before committing.

## 10. The audit log

Every dispatch decision lands as one JSON line in
`$XDG_STATE_HOME/intergen/tool-dispatch.jsonl` (mode 0600, user-owned).
The record schema is `intergen.interfaces.provenance.AuditRecord`. Users
read it via `intergen tool-log` (see
[intergen(1)](../../packages/ai/intergen/intergen.1) — `tool-log`
subcommand).

The writer is best-effort: a failure to record does not crash the
dispatcher, and the gate continues to operate even if the log
filesystem is full. You do not call it directly; the registry writes
the record after every dispatch decision (including refusals).

User-data-deletion (the GDPR right-to-erasure path documented in
[PRIVACY.md](../../PRIVACY.md)) runs via
`intergen tool-log --clear` — the writer preserves the inode and
permissions across truncation cycles. Do not write anything to the
audit log that the user cannot meaningfully erase.

## 11. Common pitfalls

- **Forgetting `_PRIVILEGED_TOOLS`** — A new state-changing tool that
  needs pkexec must be added to that allowlist. If you only declare
  `SafetyTier.CONFIRM`, the gate routes you to
  `USER_SCOPE_STATE_CHANGING` — no pkexec, no privileged escalation.
- **Treating `SafetyTier.BLOCKED` as a soft refusal** — `BLOCKED` is a
  hard reject before the gate even sees the call. Use it for actions
  that should never run. Do not use it as a placeholder during
  development.
- **Returning unwrapped ingress content** — A `read_file` that returns
  raw bytes is a regression. Always wrap with
  `intergen.spotlighting.wrap_ingress_content()`. The wrapper is cheap
  (one regex pass + one format-string concatenation) and the LLM's
  structural awareness is the load-bearing benefit.
- **Side effects in `validate_arguments()` or `classify_safety()`** —
  Both are called speculatively, sometimes more than once per dispatch
  (the gate may invoke them before the review modal, and again after
  user approval). Keep them pure functions of `arguments`.
- **Writing to the audit log directly** — Do not. The registry handles
  it. Tools that write to the audit log bypass the canonical schema
  and the user-data-wipe path.
- **Bypassing the gate in test code** — `ToolRegistry.execute()` is the
  one entry point. Tests that need to bypass the gate should construct
  a tool instance and call `execute()` on it directly (skipping the
  registry); this also skips the audit log and is appropriate for unit
  tests that need to isolate tool behavior.

## 12. References

- [intergen-provenance-gate-design.md](intergen-provenance-gate-design.md)
  — D-008 RFC, the canonical design source.
- `intergen/interfaces/tool.py` — `BaseTool` abstract class.
- `intergen/interfaces/types.py` — `SafetyTier`, `ToolSchema`, `ToolCall`,
  `ToolResult`.
- `intergen/interfaces/provenance.py` — `INGRESS_TOOLS_V1`,
  `IngressTracker`, `ConversationTrustState`, `ToolRiskTier`,
  `DispatchDecision`, `AuditRecord`, `escalate_provenance`.
- `intergen/tool_registry.py` — `_PRIVILEGED_TOOLS`,
  `_classify_risk_tier`, `ToolRegistry.execute()`.
- `intergen/spotlighting.py` — `wrap_ingress_content`,
  `extract_first_wrapped_region`.
- `intergen/audit_log.py` — `write_record`, `read_records`, `clear_log`.
- `tests/intergen/injection_corpus/README.md` — corpus schema and
  add-pattern instructions.
- [intergen(1)](../../packages/ai/intergen/intergen.1) — user-facing man
  page; `tool-log` subcommand reference.
- [PRIVACY.md](../../PRIVACY.md) — user-facing privacy policy; GDPR
  right-to-erasure path via `intergen tool-log --clear`.
