# B5 — Known Remaining Issues

Three failure categories persist across R17–R20 and show up in every MIXED audit.
These are **fixable in principle** — they're the "our code" 60% of the plateau. The
question for the reviewer is whether our proposed fixes are well-aimed or whether
we're missing a better approach.

---

## Issue 1 — Compound cache bypass

### What happens

User: *"What's my hostname and how much disk free?"*
Router hits cache on "hostname", returns single-line answer, drops the disk half.
Response is correct but incomplete.

### Why it happens

Cache lookup ([router.py:110 area](../../intergen/router.py#L110)) runs before
compound decomposition on some phrasings. Compound decomposer
([decomposer.py](../../intergen/decomposer.py)) fires on conjunctions like "and
also", "and what", "and how" — but certain compound phrasings ("what's my X and
how much Y") slip through.

### What we've tried

- R20 added "and what/how/show/check" to the compound detector. Hit some, missed
  this family.
- Moved compound check before cache in R17. Helped but didn't close the gap —
  the decomposer's heuristics don't catch every conjunction.

### Current hypothesis

The decomposer uses keyword triggers and a clause-count heuristic. The cleaner fix
is probably to run the decomposer on a dependency parse or to give the cache
awareness of which queries it's *eligible* for (right now any single-line cache
answer wins). We haven't tried either.

### What we'd ask

Is there a lightweight way to make the cache "compound-aware" without parsing?
E.g., reject the cache answer if the original query contains a conjunction that
the decomposer didn't catch?

---

## Issue 2 — Identity confusion on ultra-short and emotionally-framed queries

### What happens

- `bnd_single_char`: user sends `?`, model responds with a paragraph identifying
  itself as InterGenOS (the OS) rather than InterGen (the assistant).
- `emo_frustrated_generic`: user says "nothing works!", response mixes diagnostic
  advice with "As InterGenOS, I cannot..."

### Why it happens

The classifier ([B4](./B4_adaptive_prompting_design.md)) routes short queries to
the identity modifier, but:

1. **Single-char queries** like `?` satisfy `len(words) <= 2` and get the identity
   modifier correctly — but the modifier tells the model what it *is*, not what
   to *do* when it has nothing to answer. The model falls back to restating its
   nature, badly.
2. **Emotionally-framed queries** like "nothing works!" hit the diagnostic
   keyword `not working` and get the diagnostic modifier. Correct classification,
   but the emotional phrasing still destabilizes the identity layer and the
   response confuses InterGen with InterGenOS.

### What we've tried

- R20 identity classifier fixes: ultra-short (`≤2 words`) always get identity
  modifier. Worked on some (`lex_hostname_terse`, `mem_transparency`) but not
  all.
- Considered a dedicated "emotional" modifier. Didn't ship because we didn't
  have good data on what it should say.

### Current hypothesis

The base prompt's opening sentence ("Your name is InterGen. You are an AI
assistant built into InterGenOS.") is doing the heavy lifting on identity, and
it's *not quite specific enough* for ambiguous inputs. A 2B model on `?` may not
re-read the base prompt with sufficient weight — its attention is elsewhere.

### What we'd ask

Is there a pattern for anchoring identity that survives attention dilution on
ultra-short inputs? System-level assistant-preamble? A canned response for
`len(input.strip()) <= 2`? We considered the latter but it felt hacky.

---

## Issue 3 — Freeform fabrication on diagnostic-shaped queries

### What happens

User: *"Check /dev/sda1 disk usage"* — diagnostic phrasing, but no compelling
tool match. Query falls through to freeform path. Model fabricates plausible-looking
`df` output including a made-up mount point and utilization percentage.

### Why it happens

Two collaborating failures:

1. **P3 skip threshold.** Router has a confidence threshold above which it skips
   the tool path and goes freeform. Some diagnostic queries don't match tool
   patterns strongly enough and get sent to freeform.
2. **Synthesis grounding is only on tool-path responses.** When the model goes
   freeform, the "use ONLY tool data" grounding instruction isn't applied — the
   model is invited to answer from training data, which includes lots of example
   `df` output.

### What we've tried

- R20 lowered the P3 skip threshold so more diagnostic-phrased queries go to
  tools. `lex_disk_technical` moved from MIXED to PASS. Net helpful.
- R18 added the "use ONLY tool data" grounding prompt to the synthesis stage.
  Effective on the tool path, not applied to freeform.

### Current hypothesis

Freeform path needs its own grounding instruction — something like "if answering
a system-state query without tool results, say you don't have the data instead
of guessing." We haven't shipped this because it risks regressing general
knowledge queries (which should answer from training data).

### What we'd ask

Is there a clean way to say "answer from training data *except* when the question
looks like system state"? Our classifier has `diagnostic` — do we just gate the
freeform grounding prompt on `query_type == "diagnostic"`? That's probably the
right move but we haven't tested it.

---

## What these issues have in common

- All three are **edge cases in classifier-driven logic** — not model failures.
- All three are probably **one commit away** from being fixed.
- The reason we haven't is **Rule #10: one variable per test.** Each targeted fix
  risks regressing ~7 other conversations (as R20 did), so we only change one
  thing at a time. Four more rounds would probably close all three.

## What we explicitly did *not* pursue

- **A larger model on the CPU tier.** 2B is the tier target. 9B is a separate
  tier we'll retest after this review.
- **Test-suite revision.** The variance floor is real but we don't want to move
  the goalposts.
- **Grader leniency.** We've tightened grader twice; loosening it to make
  numbers look better would be self-deception.
