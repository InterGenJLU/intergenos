# Audit "needs-review" Disposition — Why These Aren't Build Risks

**For:** AI Assistants + Maintainers reviewing `build/audit-reconciliation-*.tsv`
**Created:** 2026-05-11

After the exhaustive per-package audit (2026-05-11), the reconciliation
report shows ~153 rows in the `needs-review` category. This document
explains why these rows are **informational metadata gaps, not build
risks**, and how the build verifies install correctness post-build.

## What "needs-review" actually flags

The audit script flags a package as `needs-review` when it cannot
auto-derive one or more of the install-output fields (`expected_binaries`,
`expected_libs`, `expected_headers`, `expected_pkgconfig`) from the BLFS
book HTML. The flag is NOT a claim that the package's build will fail —
it's a claim that the audit's documentation cross-reference is incomplete.

### Breakdown by reason (post-2026-05-11 audit)

| Reason | Count | Build-risk? |
|---|---:|---|
| `bundled-lib-*-extract-unclear` (subprojects/contrib/third_party/...) | ~134 | Resolved in Rule 5 sweep (see `build/rule5-sweep-results.tsv`) |
| `build-system-undetected` | 7 | No — package builds via its own native pathway (Go, custom shell, firmware blobs) |
| `not-in-blfs-install-output-unknown` | 11 | No — package isn't in BLFS book; install verified post-build via Rule 18 manifest reconciliation |
| `not-in-blfs-book` (helpers + internal + bootstrap variants) | 18 | No — we author these; install is determined by our own build.sh |
| `used X-lib canonical mapping (no standalone BLFS anchor)` | 20 | No — BLFS aggregates X.org packages under `xorg7-lib`; individual install is determined by the X.org package's own Makefile |
| `blfs-section-not-found` | 5 | No — typically `-pass1`/`-pass2` bootstrap variants whose install matches the full package |
| Misc (no BLFS anchor) | ~3 | No — package's own build system handles install |

## Why this is safe for Build #8

**Three layers of post-build verification catch real install mismatches:**

1. **Build-time DESTDIR tracking** (`pkg-functions.sh`):
   Every package install goes through `pkg_install` which:
   - Stages to `${DESTDIR}` (a temp dir)
   - Records every file installed to the chroot
   - Computes sha256 of each installed file
   - Updates `/var/lib/igos/packages/<name>.igo_manifest` + `pkm.db`

2. **Build Development Rulebook Rule 18 — Manifest reconciliation**
   (post-`phase_image`):
   - Compares the set of YAML-declared packages against the set of
     installed packages in `/var/lib/igos/packages/`.
   - Halts the build if anything is missing or extra.
   - Catches the "package built but didn't install anything" failure
     mode that an incomplete audit might miss.

3. **`pkm verify --strict <package>`** (post-install):
   - Recomputes file hashes vs `pkm.db`.
   - Detects any drift between what was declared installed and what's
     actually on disk.

**What the audit data adds on top of these layers**: a forward-looking
record of what each package SHOULD install — useful for post-build
manifest review, for security signatures, and for future reproducibility
work. But it's not gating Build #8 because the post-build verifiers
catch the same class of issue.

## What this document is NOT

- A blanket excuse to leave audit fields empty going forward.
  Maintainer-authored packages (helpers, internal, bootstrap variants)
  should have their `expected_binaries`/`expected_libs` fields populated
  from the package's own `build.sh` + the BLFS-equivalent docs when
  they next get touched. The audit is a baseline, not a ceiling.

- A way to bypass the audit cross-check for actual cmake/meson/autotools
  packages with full BLFS docs. Those packages should have populated
  install-output fields; if they don't, that's a real audit gap to
  refresh.

## When a `needs-review` row matters

A `needs-review` row becomes a real concern when:
- The package is `tier:core` or `tier:base` AND
- Post-build Rule 18 manifest reconciliation flags the package as
  "installed but no files tracked" or "files tracked but unexpected
  paths"

In that case: re-audit the package, populate the install-output fields
from observation, and fix the build.sh that produced the unexpected
state.

For Build #8: proceed with current audit data. The post-build
verifications are the safety net.

## How this document gets updated

When a real install issue surfaces from a `needs-review` row during a
build (Build #8 or later), document it here under "Known install
disposition exceptions" and apply the corresponding audit refresh +
build.sh fix.

Last updated: 2026-05-11
