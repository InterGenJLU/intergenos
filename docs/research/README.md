# Research records — preserved as written

This directory holds **dated research records from the design and development of
InterGenOS**. They are kept in the public tree deliberately, as the reasoning
behind the decisions the project made.

**Decided 2026-08-16: label and keep.** These documents are preserved as-is
rather than edited or removed, so that the record of how a decision was reached
stays available to anyone reading the source.

## What these documents are, and what they are not

- **They are historical.** Each one is a snapshot from the date in its header.
  They are not maintained, and details in them may no longer match the code.
- **The living truth is the tree itself and the current `docs/`.** Where a
  research record and the current tree disagree, the tree is correct.
- **Quoted material is reproduced unmodified.** Several records — most of
  `ai_integration/` — contain verbatim transcripts of evaluation runs against
  language models, including the models' own wording, mistakes, and refusals.
  Those quotations are left exactly as they were captured. Editing them would
  destroy the thing that makes an evaluation record worth keeping.
- **They are not documentation.** For how to build, install, or use the system,
  see the current `docs/` tree and the wiki.

Every document carries a header banner repeating this, so a reader who arrives
at a single file by search or by link is told the same thing.

## Contents

`INDEX.md` is the catalogue: it lists the records by topic with a one-line
summary of what each one decided.

The largest group, `ai_integration/`, records the evaluation rounds used to
develop the assistant integration — each round's prompts, the answers given,
and the assessment of those answers. The rounds are numbered in the order they
were run.

Other groups cover the build system, kernel configuration, the installer,
packaging, theming and branding, security review, and hardware testing.
