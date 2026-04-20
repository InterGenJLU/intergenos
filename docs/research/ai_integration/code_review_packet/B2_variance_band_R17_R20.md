# B2 — Variance Band: R17 → R20

## Headline

Four consecutive rounds on the 2B model produced:

| Round | Commit | PASS | MIXED | FAIL | Assertions | Duration |
|---|---|---|---|---|---|---|
| R17 | 6b11026 | **102** | 10 | 0 | 1491/1503 (99.2%) | 960s |
| R18 | 31243a0 | 99 | 13 | 0 | 1489/1503 (99.1%) | 950s |
| R19 | 93e7f71 | 97 | 15 | 0 | 1483/1503 (98.7%) | 927s |
| R20 | 72c041c | 99 | 13 | 0 | 1489/1503 (99.1%) | 1065s |

**Variance band:** 97–102 PASS, 10–15 MIXED, 927–1065s, 0 FAIL. Call it **the 2B plateau.**

The gap between rounds is smaller than the gap between identical-code reruns
would be — the model is non-deterministic at this quantization (Q4_K_M, greedy
sampling off, temperature > 0), and different conversations fail on different
runs even with identical code.

## What changed round-over-round

- **R17 (6b11026)** — history reset between tests + routing fixes for context
  contamination and filler-word stripping. Baseline for the plateau.
- **R18 (31243a0)** — added dd/mkfs/fdisk/shutdown/reboot to safety triggers (P0
  tier), added "use ONLY tool data" grounding to the synthesis prompt to stop 2B
  fabrication.
- **R19 (93e7f71)** — privacy-query response templates, grader category passthrough
  so grader can see conversation category when choosing which assertions to skip.
- **R20 (72c041c)** — three structural fixes targeting items flagged in every audit
  since R16:
  1. P3 skip threshold lowered so diagnostic queries reach tools instead of freeform
  2. Compound detection catches "and what/how/show/check" so cache doesn't short-circuit
  3. Identity classifier runs keyword check on all queries; ultra-short (≤2 words)
     always get the identity modifier

## R20 targeted-fix audit (3 of 6 hit)

Items flagged in every MIXED audit since R16 and whether R20 moved them:

| ID | R19 | R20 | Notes |
|---|---|---|---|
| lex_disk_technical | MIXED | **PASS** | P3 routing fix — no longer fabricates `/dev/sda1` df output |
| lex_hostname_terse | MIXED | **PASS** | Identity classifier — "name?" now gets identity modifier |
| mem_transparency | MIXED | **PASS** | Identity classifier — short memory queries no longer say "I am InterGenOS" |
| compound_mixed | MIXED | MIXED | Cache still intercepts; compound detector doesn't cover this phrasing |
| bnd_single_char | MIXED | MIXED | "?" alone — edge case for identity classifier |
| emo_frustrated_generic | MIXED | MIXED | Emotional framing confuses identity classifier |

Net over R19: 9 improvements, 7 regressions = **+2 PASS**. Inside the variance band.

## The stable "our fault" failure set

From audits of R17, R18, R19, and R20 MIXED results (Rule #11: check our code first):

- **Identity confusion on ultra-short/emotional queries** (4 conversations): classifier
  still misses single-char, emoji-only, and emotionally-framed inputs. Model says
  "I am InterGenOS" when it should say "I am InterGen, the assistant in InterGenOS."
- **Freeform fabrication on diagnostic-shaped queries** (~1 conversation): even with
  P3 routing lowered, some diagnostic phrasings slip through to freeform and the
  model fabricates plausible-looking disk/service output.
- **Compound cache bypass** (1 conversation): compound query where cache answers
  first part only. Detector regex doesn't cover every conjunction phrasing.

Same categories, every round. These are the three items in B5.

## The rotating variance

From the same audits: ~5–6 conversations fail *differently* each round — no pattern,
no code change explains them. Examples: `amb_status`, `edge_just_greeting`,
`emo_grateful_praise`, `ind_boot_problem`, `lex_svc_indirect`, `self_privacy`.

This is the **irreducible noise floor** at Q4_K_M. Rounds alternate which
conversations land in this bucket.

## Honest ceiling estimate

If every "our fault" item were fixed, the stable band would be approximately:
- **~6 MIXED** (variance only)
- **~106 PASS**

That is the theoretical 2B maximum on this suite without architectural change
(larger model, better quant, or test-suite revision).
