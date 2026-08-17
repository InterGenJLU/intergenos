# `build/` — Build artifacts and curated source pins

This directory holds a mix of generated build artifacts (gitignored) and
a small set of source-class assets that are tracked in the public repo.

## What lives here

### Tracked

- **`build/patches/`** — upstream-modifying patches applied during package
  builds. Source-class; tracked.
- **`build/sources/`** — narrowly tracked vendored source archives that
  have no single upstream URL: `ca-certificates-*.tar.gz` (wrapped from
  `curl.se`'s `cacert.pem`) and `lego-*-vendor.tar.xz` (locally produced
  via `go mod vendor`). See each package's README for refresh procedure.

### Gitignored (transient or moved)

- **`build/sources/*`** (with the two exceptions above) — source tarballs
  fetched by `scripts/download-sources.py`. Not committed.
- **`build/work/`, `build/system/`, `build/packages/`, `build/logs/`,
  `build/checkpoints/`, `build/archive/`, `build/scan-cache/`,
  `build/sources-pax-regen/`** — build-time working state.
- **`build/audits/`** — per-package classifier output (audit JSONs).
  Not tracked here; the tooling writes it outside the public tree by
  default. See "Audit-output relocation" below.
- **`build/rule5-sweep-results.tsv`** — rule5 classifier output. Written
  to the same location, alongside the per-package output.
- **Operational scripts** (`build*-kickoff.sh`, `build*-rebuild-*.sh`,
  `option-*.sh`) — per-build resume wrappers; generated, not source.
- **Timestamped scanner output** (`*-YYYYMMDDTHHMMSSZ.{tsv,json}`).

## Audit-output relocation

The audit-tooling scripts at `scripts/audit-package.py` +
`scripts/audit-rule5-sweep.py` + `scripts/aggregate-package-audits.py`
generate per-package classifier output. Until this commit, that output
lived under `build/audits/` in the public repo. Per the docs-posture
rubric (USE/FUNCTIONALITY + TRANSPARENCY-COMMITTED + OPERATIONS) the
output qualifies as internal operations content — not load-bearing for
external contributors to build or audit the source — so it no longer
ships in this repository.

The **scripts stay public**. Anyone running them against a clean clone
gets the same classification output for their own audit purposes — the
methodology is fully transparent. Only InterGenJLU's specific classification
output ships privately.

### Where scripts write output

The three audit-tooling scripts resolve their output location in this
order:

1. `$INTERGENOS_AUDITS_DIR` — explicit override path.
2. `$INTERGENOS_PRIVATE_REPO/audits/per-package` — the canonical audits
   subpath under whatever that variable points at.
3. A sibling-directory default beside this repository, used when it
   exists. The exact directory name the scripts probe is defined in
   `scripts/audit-package.py`, which is the authority — it matches the
   layout `scripts/anchor-tracker.sh` uses.
4. `build/audits/` (public-tree fallback) — with a stderr warning naming
   the override env vars. Output is gitignored so it stays local-only
   even if the public tree is committed against.

External contributors without that sibling directory can run the audit
scripts and get output at `build/audits/` (with the warning) for their
own purposes. Setting `INTERGENOS_AUDITS_DIR` to any path overrides
all four.

**Decided 2026-05-29:** this is a forward-going correction.
Historical commits before this one still contain the `build/audits/`
content (`git log --all -- build/audits/`). InterGenOS does not rewrite
git history to scrub past mistakes — the principle is learn, improve,
move on.
