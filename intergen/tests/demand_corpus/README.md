# InterGen Demand Corpus — schema, format, and merge contract (M8-6)

**Status:** ACTIVE. **Arc:** the DOER bar — evaluating InterGen's task-completion quality
against a broad, realistic question bank (tracked in project planning docs, not in this
repo). **Owner of this schema:** the demand-corpus lane. **Consumers:** the test
runner (`intergen/tests/runner.py`), the mass-run autopilot harness, the surface-flex
generator, and the corpus tooling below.

This file is the SINGLE SOURCE OF TRUTH for the JSONL entry shape. Both generator halves
(the demand-distribution half + the surface-flex half) author lines to this exact
schema so the merge tool unifies them and the runner drives them WITHOUT translation.

## 1. Why this exists

M8-6 requirement (decided 2026-07-08): generate a large bank of questions that flex every
aspect of InterGen, mass-run it through the reset-enabled autopilot harness against the live
9B, and record every turn via glass. The FIRST run is DISCOVERY — record what InterGen
actually does under the real user distribution — not pass/fail. Routing gets tweaked first,
response quality comes after.

Two halves, deliberately different lenses so the bank is not a monoculture:
- **The demand-distribution half** (`demand_distribution.jsonl`):
  internet-grounded, mirrors what real users actually ask assistants and Linux help forums.
- **The surface-flex half** (`surface_flex.jsonl`): code-grounded, walks
  the full InterGen tool/route surface from the router and tool registry.

Both merge into one `bank.jsonl` via `intergen/tests/corpus_merge.py`.

## 2. Directory home

```
intergen/tests/demand_corpus/
  README.md                       # this file — the schema contract
  grounding_sources.md            # provenance registry: every grounding key -> real source
  demand_distribution.jsonl       # the demand-distribution half
  surface_flex.jsonl              # the surface-flex half (lands here)
  bank.jsonl                      # merged, deduped bank (emitted by corpus_merge.py)
  bank.report.json                # distribution report (emitted at merge)
```

## 3. The JSONL entry schema

One JSON object per line. UTF-8. No trailing commas. Fields:

| field | type | required | meaning |
|---|---|:--:|---|
| `id` | string | yes | Globally-unique, kebab-case. Prefix names the half: `dd-` = demand-distribution, `sf-` = surface-flex. Duplicate ids across halves are a HARD merge error (a generation bug, not a near-duplicate). |
| `category` | string | yes | Demand category (§4). Free-form string, but pick from the registry in §4 so the distribution report aggregates cleanly. |
| `intent` | string | yes | One short human-readable phrase naming what the user wants ("look up current weather", "install a package"). |
| `turns` | array | yes | 1+ turn objects, in order. `len > 1` = a multi-turn flow; the loader marks these persistent so the runner keeps memory/session state across the turns. |
| `turns[].user` | string | yes | The user's message for that turn (may be empty string for the empty-input edge cell). |
| `turns[].assertions` | array | no | OPTIONAL. For the discovery run leave EMPTY — glass records everything; there is no pass/fail to assert yet. A later quality phase may add assertions. Shape matches `conversations.Assertion`: `{"type","value","description"}`. |
| `expected_behavior_class` | string\|null | no | The behavior CLASS this cell should exhibit where derivable — one of `route-shape`, `should-dispatch`, `should-gate`, `should-teach`, or `null`/omitted when not derivable. This is a CLASS for analysis, NOT a scripted output. |
| `provenance` | object | yes | How the entry was generated (§5). |

### Minimal example (single turn, discovery — no assertions)

```json
{"id":"dd-web-0001","category":"web_search","intent":"look up current weather","turns":[{"user":"what's the weather in Chicago today"}],"expected_behavior_class":"should-dispatch","provenance":{"generator":"demand","lens":"demand-distribution","grounding":["openai-howpeopleuse-2025","voice-assistant-tasks"],"method":"internet-grounded-authored"}}
```

### Multi-turn example (offer -> affirmative -> follow-up)

```json
{"id":"dd-script-0007","category":"do_for_me","intent":"write then save then run a script","turns":[{"user":"write me a script that lists my biggest files"},{"user":"yes, save it"},{"user":"now run it"}],"expected_behavior_class":"should-gate","provenance":{"generator":"demand","lens":"demand-distribution","grounding":["openai-howpeopleuse-2025"],"method":"internet-grounded-authored"}}
```

## 4. Category registry (demand-distribution lens)

Grounded in the real distribution (`grounding_sources.md`). Use these strings verbatim for
the `category` field so the report aggregates; a new category is fine but name it here first.

- `practical_guidance` — advice, planning, recommendations, life how-to (the single largest real class).
- `seeking_information` / `knowledge` — general knowledge, explain-a-concept, definitions.
- `writing_help` — draft / rewrite / summarize / translate text.
- `web_search` — news, prices, weather, sports scores, current-info lookups.
- `do_for_me` — write/save/run a script, make a file, generate content, schedule/remind.
- `device_peripheral` — printers, wifi, bluetooth, audio, displays, webcam, USB.
- `software_mgmt` — install / remove / update software and packages.
- `file_management` — find / move / rename / delete / organize files; disk usage.
- `troubleshooting` — "my X is slow / broken / won't start / crashes".
- `howto_teach` — "how do I X" teaching asks (answer + teach, not do).
- `capability_question` — "can you X?", "are you able to Y?".
- `memory_personal` — remember / forget / recall a user fact or preference.
- `system_info` — read-only hardware / OS / status questions.
- `math` — arithmetic, unit conversion, percentages, word problems.
- `conversational` — greetings, thanks, chit-chat, emotional/expressive turns.

Phrasing dimensions (applied ACROSS categories, not their own category): typos, run-ons,
ALL-CAPS/emotional framing, terse fragments, vague antecedents, over-polite verbosity.

## 5. Provenance object

```json
"provenance": {
  "generator": "demand",                // demand | surface  (which half authored the line)
  "lens": "demand-distribution",        // demand-distribution | surface-flex
  "grounding": ["<key>", ...],          // keys resolvable in grounding_sources.md
  "method": "internet-grounded-authored"// how it was produced
}
```

Every `grounding` key MUST resolve to a real, cited source in `grounding_sources.md`. The
distribution report cross-checks that no entry cites an unregistered key.

## 6. How the runner consumes it (no translation)

`intergen/tests/corpus_loader.py::load_corpus(path)` reads a JSONL bank and returns a list of
`conversations.Conversation` objects — the exact dataclass the runner already drives. Each
line becomes one `Conversation` (`id`, `category`, `turns=[Turn(user, assertions)]`), with
`persist_state=True` for multi-turn entries and `expected_behavior_class` carried through for
downstream analysis. The runner grows a `--corpus PATH` option that swaps its registry for the
loaded bank; everything else (grading, glass, reset hygiene, results emission) is unchanged.

## 7. The merge/dedup contract

`intergen/tests/corpus_merge.py` merges any set of half-files into `bank.jsonl`:
- **Schema-validates** every line (required fields, id prefix, resolvable grounding keys).
- **Hard-errors** on duplicate ids across halves.
- **Near-duplicate detection** is DETERMINISTIC (no embedder, no network, no daemon — so the
  RED/GREEN tests are reproducible): each entry gets a normalized signature (lowercase, strip
  punctuation, collapse whitespace, drop a small filler set, join multi-turn text). Two
  entries collide if signatures are equal OR token-set Jaccard >= the threshold (default 0.9).
  On collision the FIRST-seen entry (stable order: input-file order, then line order) survives;
  the drop is logged with `dropped_id -> kept_id`.
- **Distribution report** (`bank.report.json` + stdout): total, per-category, per-generator,
  single- vs multi-turn counts, per-expected-behavior-class counts, and the dedup log — so the
  bank's shape is visible at merge.

Run: `python3 -m intergen.tests.corpus_merge demand_distribution.jsonl surface_flex.jsonl -o bank.jsonl`
(paths resolve under `intergen/tests/demand_corpus/` by default).
