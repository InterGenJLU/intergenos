# Round-1 training bank

The first supervised-fine-tuning corpus for the assistant's dispatch behavior:
six behavior classes plus a retention lane, authored from the shipped tool
registry and serving flow, deduplicated, and emitted through the fail-closed
SFT emitter (`intergen/tests/corpus_to_sft.py`).

## Files

| File | What it is |
|---|---|
| `gen_class1_bank.py` | Generator: class 1 — imperative → dispatch targets + contrastive/informational twins |
| `gen_class23_bank.py` | Generator: class 2 (approval flow, both endings + deny-then-approve-later) and class 3 (deny recovery with real alternatives) |
| `gen_class465_bank.py` | Generator: class 4 (terse fragments), class 5 (teach-then-offer, both continuations), class 6 (frustration intent) |
| `gen_retention_bank.py` | Generator: retention lane — identity, tool-not-called knowledge answers, off-system scope shape |
| `class1_bank.jsonl` | 289 entries (212 dispatch targets + 77 twins) |
| `class23_bank.jsonl` | 128 entries (class 2: 94, class 3: 34) |
| `class465_bank.jsonl` | 97 entries (class 4: 55, class 5: 20, class 6: 22) |
| `retention_bank.jsonl` | 47 entries |
| `dedup_pass.py` | Embedding dedup (local embedding endpoint): pairs over ~0.85 cosine drop only when the tool target is identical AND the turn structure matches; prose entries drop on bare 0.85 within class. The qualified rule exists because bare 0.85 measurably conflates opposite-intent same-subject pairs (install vs. is-installed 0.92; install vs. remove 0.862) — exactly the contrasts the corpus teaches. Decided 2026-08-11. |
| `round1_bank_deduped.jsonl` | The 517-entry training input (561 authored − 44 true duplicates) |
| `excluded-user-texts.txt` | Held-out + validation eval user texts the generators hard-fail on (normalized exact match), plus subject bans |
| `gen_round2_bank.py` | Generator: round-2 lanes — class 7 (honesty contrastives: read-only report / action claim / failure arms), class 8 (assembled multi-turn context, 4–6 turns), class 9 (long tool-output synthesis) — each aimed at a defect measured in the honest rounds 3–4 (2026-08-12) |
| `round2_bank.jsonl` | 68 entries (41 honesty-contrastive, 16 assembled-context, 11 long-output synthesis) |
| `round2_combined_deduped.jsonl` | The 585-entry round-2 training input: round-1's 517 + the 68 new, deduplicated under the qualified rule (0 qualified drops; 54 over-0.85 pairs all spared as opposite-intent/different-structure) |

## Contracts

- Banks use the demand-corpus schema with per-turn `gold` and
  `training_provenance`; `corpus_to_sft.py` validates every entry fail-closed
  against the live tool registry before emitting.
- Held-out discipline is mechanical: the family split is computed with
  `families.split_for_family`, and every held-out/validation user text plus
  banned subjects are hard-checked by the generators — a hit aborts generation.
- Gold replies never contain internal implementation vocabulary; the
  generators hard-fail on it.
- **A dispatch gold is emitted as a STRUCTURED tool call** — an assistant
  message with `content: null` and a `tool_calls` entry whose `arguments` are a
  **mapping**, key-sorted. That is the only form the chat template renders as a
  tool call: it emits the `<parameter=…>` blocks only under its own
  `{%- if tool_call.arguments is mapping %}` guard. A call written as a JSON
  blob into the assistant's content renders as literal text with no
  `<function=` block at all, and a call whose `arguments` are a JSON *string*
  renders as the function name with every argument dropped. Both shapes were
  emitted here until 2026-08-13 and both are now refused by a fail-closed
  post-condition on every emitted sample (`assert_renderable_tool_calls`).
  The render itself is proven in
  `tests/intergen/test_corpus_to_sft_template_render.py`.
- Emission (deterministic, sorted keys):

```
python3 -m intergen.tests.corpus_to_sft \
  --bank intergen/tests/training_bank/round1_bank_deduped.jsonl \
  --system-prompt-file <extracted-serving-system-prompt.txt> \
  --out round1_sft.jsonl
```

The system prompt is extracted from the shipped composer
(`intergen.llm.build_system_prompt("general", True)`) at the tree being
trained — it is not stored here, so it cannot drift from the composer. The
training step must apply the same chat template the server uses
(`intergen/data/internvl-tool-template.jinja`) with the real tool-schema
array, so training-time and serving-time bytes match by construction.
