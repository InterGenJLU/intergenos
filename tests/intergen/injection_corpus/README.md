# InterGen Injection-Pattern Corpus

Canonical prompt-injection detection corpus + test fixtures for InterGen's
provenance gate (RFC v0.1 §5.2 advisory-pattern scanner).

## Purpose

Per `docs/architecture/intergen-provenance-gate-design.md` §5.2 + line 274:

> The dispatcher scans recent ingress content for known instruction-injection
> patterns (e.g. *"ignore previous instructions"*, *"then run the following
> command"*, *"as part of this task also execute"*). Pattern hits are logged +
> surfaced in the review modal but do not by themselves block. False-positive
> rate is high; the watermark in §5.1 is the load-bearing mechanism.
>
> The injection-test corpus is its own deliverable — a set of webpages + files
> + search results designed to attempt every documented prompt-injection
> technique. Stored at `tests/intergen/injection_corpus/`. Expanded as new
> techniques are documented in the literature.

The corpus is **advisory** — pattern hits feed the §5.2 advisory tagging in
the review modal but do not gate execution. The §5.1 ingress watermark
(`intergen/interfaces/provenance.py` IngressTracker) is the load-bearing
mechanism that escalates the effective provenance label.

## Layout

```
tests/intergen/injection_corpus/
├── README.md           — this file
├── patterns.json       — canonical pattern manifest (regex + metadata + fixture refs)
├── verify-corpus.py    — sanity check; run before committing changes
└── fixtures/
    ├── instruction_override_*.txt   — 5 fixtures (instruction-override category)
    ├── tool_coercion_*.txt          — 4 fixtures (tool-coercion category)
    ├── exfil_prefix_*.txt           — 3 fixtures (exfil-prefix category)
    ├── system_prompt_leak_*.txt     — 2 fixtures (system-prompt-leak category)
    ├── role_confusion_*.txt         — 3 fixtures (role-confusion category)
    ├── authority_spoof_*.txt        — 2 fixtures (authority-spoof category)
    ├── delimiter_attack_*.txt       — 3 fixtures (delimiter-attack category)
    ├── encoding_attack_*.txt        — 2 fixtures (encoding-attack category)
    └── benign_*.txt                 — 3 benign negatives (false-positive checks)
```

Eight categories × ~3 patterns each = 24 positive patterns + 3 benign
negatives. Each pattern names a fixture file under `fixtures/`.

## `patterns.json` schema

```json
{
  "version": "0.1",
  "spec_reference": "...",
  "description": "...",
  "consumed_by": ["..."],
  "categories": {
    "<category-id>": {
      "description": "human-readable category description",
      "advisory_only": true,
      "false_positive_risk": "low|medium|high",
      "patterns": [
        {
          "id": "stable-pattern-id-kebab-case",
          "regex": "Python re module regex (use double-escaped \\\\ in JSON)",
          "fixture": "filename relative to fixtures/"
        }
      ]
    }
  },
  "benign_negatives": [
    {
      "id": "stable-negative-id",
      "description": "...",
      "fixture": "filename relative to fixtures/",
      "should_not_match_any_pattern": true
    }
  ]
}
```

### Field notes

- **`regex`** — uses Python `re` module syntax. Common inline flags:
  `(?i)` case-insensitive, `(?s)` DOTALL (`.` matches newlines too — required
  for multi-line payloads like `curl -X POST .. \\n  --data-binary`).
  JSON requires backslash-escaping: `\\b` → word boundary, `\\s` → whitespace.
- **`fixture`** — path is relative to `fixtures/`. Every pattern must reference
  exactly one fixture that exercises it. Multiple patterns may reference the
  same fixture if the fixture contains multiple distinct injection attempts.
- **`advisory_only`** — currently always `true` for v1.0. v1.x may introduce
  blocking patterns; the schema stays forward-compatible.
- **`false_positive_risk`** — informational; informs how aggressively the §5.2
  scanner should surface the match in the review modal (high-FP patterns get
  shorter advisory text; low-FP patterns get more prominent treatment).
- **`should_not_match_any_pattern`** — load-bearing for benign negatives.
  `verify-corpus.py` asserts the negative fixture is not matched by any
  pattern across any category. Catches false-positive regressions when
  patterns get broadened.

## Consumed by

- **`intergen.provenance`** (RFC §5.2 advisory tagging) — loads
  `patterns.json` at dispatcher init; compiles each regex; on each tool call
  scans the recent ingress content for any match; matches feed the review
  modal's advisory section.
- **`intergen.mcp_client.SentinelGuard.validate_tool_description`** (OWASP
  MCP02 scanning) — uses the substring subset (the simpler patterns from
  `instruction-override` + `delimiter-attack` categories) for MCP tool
  description validation. Existing in-tree implementation predates this
  corpus; migration to consume `patterns.json` is a follow-on cleanup.
- **`intergen.safety.get_blocked_response`** (command-injection short-list)
  — existing 5-pattern short-list; can be replaced by the
  `instruction-override` + `role-confusion` categories from `patterns.json`
  as a follow-on cleanup.
- **`tests/intergen/test_injection_corpus.py`** (integration tests; authored
  by the installed-system coordinator's Step 12) — loads `patterns.json` +
  fixtures, feeds each fixture through the dispatcher, asserts the
  advisory tag fires on positive fixtures + does NOT fire on benign
  negatives.

## Verification

```sh
python3 tests/intergen/injection_corpus/verify-corpus.py
```

The script asserts:
1. Every pattern's regex compiles cleanly.
2. Every pattern's fixture file exists AND the regex matches at least one
   substring in the fixture (positive case).
3. Every benign-negative fixture exists AND NO pattern from ANY category
   matches it (negative case — protects against false-positive regression).

Run before committing any change to `patterns.json` or `fixtures/`.

## Extending the corpus

Adding a new pattern:

1. Pick or create a category in `patterns.json`.
2. Author a fixture file under `fixtures/` that contains a realistic
   example of the pattern in plausible ingress content (webpage, file,
   search result, email).
3. Add the pattern entry to `patterns.json` with stable `id`, the regex,
   and the fixture filename.
4. Run `verify-corpus.py` — must pass.
5. Run against all benign negatives — the new pattern must NOT match any
   benign fixture (`verify-corpus.py` catches this automatically).

Adding a new benign negative:

1. Author the negative fixture under `fixtures/` with content that:
   - Mentions injection-adjacent vocabulary (`ignore`, `override`, `disregard`,
     `system`, `admin`, etc.) in legitimate technical context.
   - Is the kind of content that has tripped a false-positive in production
     or peer review — i.e. real defensive cases, not synthetic.
2. Add the entry to `patterns.json` `benign_negatives` with
   `should_not_match_any_pattern: true`.
3. Run `verify-corpus.py` — must pass. If a pattern matches the negative,
   either tighten the pattern's regex or revisit the negative's plausibility.

## Sources

- OWASP LLM Top 10 — LLM01:2025 Prompt Injection
- Greshake et al. 2023 "Not what you've signed up for" (indirect prompt injection)
- Production observations of delimiter-confusion attacks (`<|im_start|>` and
  variants)
- In-tree pre-existing detection patterns (`intergen/mcp_client.py:236-247` +
  `intergen/safety.py:216-231`) for forward compatibility

The corpus expands as new techniques surface in the literature. PRs that
add categories or patterns should cite the source.
