# B4 — Adaptive Prompting: Classify-Then-Compose

## The problem we had

By R12 the 6-rule prompt was working but identity confusion persisted. On queries
like "name?" the model would answer "I am InterGenOS" instead of "I am InterGen,
the assistant in InterGenOS." Adding an always-on identity rule to the base
prompt (R12b) seemed like the obvious fix.

## What went wrong (R12b)

Always-on identity reinforcement caused **14 regressions** while fixing **9** of
the items it targeted. Net: **−5 PASS** vs R12.

The regressions clustered in categories where the extra identity sentence pulled
attention away from the actual answer:
- General knowledge queries became shorter and more confused
- Tool-path queries sometimes re-introduced identity into the synthesis
- Safety refusals got wordier because the identity preamble kicked in first

## What we shipped (commit a85ad60, R13)

Two-stage prompt: a universal base + exactly one per-query modifier selected by a
keyword classifier.

### Classifier ([router.py:761-787](../../intergen/router.py#L761-L787))

```python
def _classify_query_type(self, user_input: str) -> str:
    lower = user_input.lower()
    if any(t in lower for t in self._SAFETY_TRIGGER_WORDS):
        return "safety"
    words = lower.split()
    for kw in self._IDENTITY_KEYWORDS:
        if kw in lower:
            return "identity"
    if len(words) <= 2:
        return "identity"
    for kw in self._DIAGNOSTIC_KEYWORDS:
        if kw in lower:
            return "diagnostic"
    return "general"
```

- **Cost:** zero LLM calls, ~microseconds per classification. Pure string matching.
- **Order:** safety → identity (keywords, then short-query heuristic) → diagnostic → general.
  Safety wins everything — if the query contains `dd`, `mkfs`, `rm -rf`, we want the
  safety modifier regardless of phrasing.

### Keyword lists ([router.py:743-759](../../intergen/router.py#L743-L759))

| Classification | Triggers (frozenset) |
|---|---|
| identity | name, who, what are you, hostname, host, box, machine, computer, yourself, your name |
| diagnostic | slow, crash, broke, error, fail, down, full, running out, can't reach, not working, check, diagnose, fix, install, remove, restart, status, show me, `df `, `free `, `find `, `cat `, top, htop |
| safety | format, delete, remove, wipe, destroy, erase, ignore, bypass, override, hack, inject, mkfs, fdisk, parted, shutdown, reboot, `rm -rf`, `rm -f`, `dd if=`, `dd of=` |

### Composition ([llm.py:57-72](../../intergen/llm.py#L57-L72))

Base prompt + modifier + date/time suffix. Date/time is kept at the *end* so the
prefix stays stable and llama-server's KV cache hits across queries (R-era
optimization — commit d8e0c82).

## Prior art we borrowed from

- **Rasa CALM** — dialog policies selected by intent classification.
- **LangChain LLMRouterChain** — same pattern: cheap classifier → prompt selection.
- **Prior assistant's `_get_domain_rules()`** — a prior internal AI assistant project used the same approach
  for domain rules.
- **RAG-MCP** — reported 3.2× accuracy improvement when only-relevant tools are
  surfaced. The principle generalizes to rules.
- **NLT ("No Long Text")** — +26.1pp accuracy on open-weight models when
  irrelevant context is removed.

Common finding: **small models lose accuracy when asked to ignore irrelevant
context.** Adaptive composition is "don't ask them to ignore it — don't include
it."

## Round-over-round data

| Round | Prompt strategy | PASS | Δ vs R12 | Notes |
|---|---|---|---|---|
| R12 | 6 rules, no identity | 90 | — | Baseline |
| R12b | 6 rules + always-on identity | 85 | **−5** | 14 regressions, 9 fixes |
| R13 | 3 base + 1 modifier | 92 | **+2** | Adaptive — only identity queries get identity content |
| R14 | Same, grader v2 | 96 | **+6** | Grader calibration; same prompt |

**R12b → R13 is the core evidence.** Same content, two different delivery
mechanisms. Always-on harmed; conditional helped.

## Known limitations of the classifier

These are the items B5 covers in detail, tied back to classifier gaps:

1. **Ultra-short edge cases.** "?" alone, emoji-only, or `<=2` words with no
   identity keywords and no diagnostic keywords — the current rule (`len(words)
   <= 2` → identity) covers most but not all. `bnd_single_char` still fails.
2. **Emotional framing.** "nothing is working!" contains `not working` which
   correctly routes to `diagnostic`, but the emotional context confuses identity
   in the response. Emotional framing has no dedicated modifier.
3. **Compound queries.** Multi-intent queries pick exactly one classification,
   but the two clauses may need different modifiers. We don't split the
   classification per clause.

## What we'd ask a reviewer about

- **Classifier strategy.** Keyword matching is cheap but brittle. Would a small
  embedding-based classifier (e.g. MiniLM) pay for itself here, or does the extra
  latency and complexity cost more than it saves?
- **Modifier structure.** Is sentence-level modification the right grain, or
  should modifiers replace whole sections of the base prompt (e.g., swap the
  concision rule on safety queries where refusals may need more words)?
- **Multi-modifier.** Should we ever stack modifiers (e.g., identity + diagnostic
  for "what's my hostname" which is both an identity and a state query)? We
  decided no, but we're not sure.
