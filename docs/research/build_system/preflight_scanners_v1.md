# Preflight Scanners v1 — Build-Order + Silent-Feature-Loss Gates

**Status:** v1 (promoted from prototypes used during the Build #8 → Build #9 remediation arc).
**Last updated:** 2026-05-12
**Source:** [scripts/preflight-build-order.py](../../../scripts/preflight-build-order.py), [scripts/preflight-silent-loss.py](../../../scripts/preflight-silent-loss.py)
**Integration:** Both gates run in [scripts/build-intergenos.sh:phase_validate](../../../scripts/build-intergenos.sh) after the existing tier-coverage, audit-coverage, and tier-validator checks.

## Overview

Two new gates extend the `phase_validate` chain. They run before any package compile burns CPU, blocking build kickoff when either:

- A `chroot-build-<phase>.sh` script invokes a package before its declared `dependencies.build` are guaranteed to be built (**build-order**), or
- A previously-installed package's configure log shows the package was missing a declared or BLFS-canonical build dep at probe time (**silent feature loss**).

Both classes of bug were caught manually during the Build #8 → Build #9 remediation that landed at master `55b4da4a`. The prototypes that surfaced those findings now ship as repository-resident, test-covered, gate-wired infrastructure.

## Why this matters

The cost of catching these classes of bug mid-build is large:

- **Build-order violations** typically present as configure-time hard failures hundreds of packages into a multi-hour build. The fix is mechanical (reorder), but the round-trip — halt → research → fix → revert chroot → restart from VM snapshot — eats one full build cycle each time.
- **Silent feature losses** are worse: the build "succeeds" but the produced binaries ship without the features the package declares it needs. The systemd-without-15-security-deps finding (Build #8) was that exact class — systemd built without libseccomp, libapparmor, libcryptsetup, libfido2, libgcrypt, gnutls, ukify, homed, man, sysupdate, all silently. The build was passing; the resulting OS was wrong.

The principle (ratified 2026-05-12): pre-build gates **are** the reproducibility infrastructure. Owner's framing during the ratification arc: *"that's the type of thing that adds to our reproducibility effort."* The gates are infrastructure, not friction.

## Architecture — two-script split

The two scans have fundamentally different inputs and run-time profiles, which is why they ship as two scripts rather than a merged binary.

### Scan A — `preflight-build-order.py`

- **Input:** repo source only — `scripts/chroot-build-*.sh` + `packages/<tier>/<pkg>/package.yml`.
- **Output:** ordering violations against declared deps.
- **Runtime:** sub-second pure-static analysis.
- **Chroot dependency:** none. Runs on any host with the repo checked out.

### Scan B — `preflight-silent-loss.py`

- **Input:** repo source + chroot install-state + chroot per-package configure logs.
- **Output:** declared-and-failed + BLFS-required-and-failed + summary-disabled findings.
- **Runtime:** seconds-to-minutes depending on installed-package count.
- **Chroot dependency:** required — but gracefully SKIPS when chroot data is absent (e.g., first build, post-VM-revert state) so the gate doesn't block bootstrap scenarios.

## Scan A — preflight-build-order

### What it catches

For every `run_package "consumer" "dir" "version"` line across all `scripts/chroot-build-<phase>.sh` files, the scanner reads `packages/<tier>/<consumer>/package.yml`'s `dependencies.build` list and confirms each dep is built earlier (same phase, lower line; or strictly earlier phase).

Phase order (per `scripts/build-intergenos.sh`):

```
ch8 → core-extra → base → ch10 → desktop → ai → extra → bootloader
```

### Five finding types

| Type | Meaning |
|---|---|
| `SAME-SCRIPT-VIOLATION` | dep wired AFTER consumer in same chroot-build-*.sh script |
| `CROSS-PHASE-VIOLATION` | dep is in a strictly LATER phase (via `run_package` line OR tier-default-phase mapping) |
| `DEP-NOT-FOUND` | declared dep has no `package.yml` anywhere in tree |
| `DEP-TIER-UNKNOWN` | dep's tier has no entry in `TIER_TO_DEFAULT_PHASE` |
| `PACKAGE-YML-MISSING` | consumer has a `run_package` line but no `package.yml` |

### Exit semantics

- `0` — clean (no findings)
- `1` — findings present (build kickoff should halt)
- `2` — environment problem (repo root missing scripts/ or packages/)

### Usage

```bash
scripts/preflight-build-order.py              # gate mode — terse pass/fail
scripts/preflight-build-order.py --report     # also emit JSON + TSV to build/
scripts/preflight-build-order.py --verbose    # show duplicate-package details
scripts/preflight-build-order.py --root /alt/repo
```

### Tier-default-phase mapping

When a declared dep does NOT appear in any `run_package` line, the scanner falls back to the dep's tier-default phase (the Python DAG builder runs it in that phase):

| Tier | Default Phase |
|---|---|
| `toolchain` | `ch8` |
| `core` | `core-extra` |
| `base` | `base` |
| `desktop` | `desktop` |
| `ai` | `ai` |
| `extra` | `extra` |

A consumer in `core-extra` declaring a dep that resolves to `desktop` via the tier-default mapping is a `CROSS-PHASE-VIOLATION`.

## Scan B — preflight-silent-loss

### What it catches

For every package installed in the prior-build chroot (`<chroot>/var/lib/igos/packages/`), the scanner reads the latest per-package configure log from `<chroot>/mnt/intergenos/build/logs/` and cross-references against:

1. The package's declared `dependencies.build` (from `packages/<tier>/<pkg>/package.yml`)
2. BLFS truth (from `build/blfs-packages.db` — required/recommended/optional deps per upstream BLFS book)

### Three classification passes

| Pass | Type | Triggered when |
|---|---|---|
| 1 | `DECLARED-FAILED` | A declared dep matches a failure pattern in the consumer's configure log |
| 2 | `BLFS-REQUIRED-UNDECLARED-FAILED` | A BLFS-required dep we did NOT declare matches a failure pattern |
| 2 | `BLFS-RECOMMENDED-UNDECLARED-FAILED` | A BLFS-recommended dep we did NOT declare matches a failure pattern |
| 3 | `BLFS-OPTIONAL-INTREE-FAILED` | A BLFS-optional dep that exists in our tree (but we didn't declare) matches a failure pattern |

Deduplication: when a `(pkg, dep-base)` has a `DECLARED-FAILED` finding, the BLFS-* findings for the same `(pkg, dep-base)` are suppressed (same root cause; one classification path is enough).

### Supplemental scan

Two additional surfaces are scanned per-package:

- **`summary_disabled`:** end-of-configure summary lines matching `<feature>: disabled|no|None|FALSE|off`, with a noise-filter for cross-compile probes (windows.h, kqueue), benign build options (debug, ipv6, tests, docs), and toolchain probes (alloca, snprintf).
- **`meson_not_found_intree`:** `Run-time dependency X found: NO` / `Dependency X found: NO` / `Library X found: NO` / `Program X found: NO` lines where target X exists in our tree but configure didn't see it.

### Detection patterns

Patterns match against autotools, meson, cmake, and pkg-config conventions:

- autotools: `checking for X... no`, `checking for X.h... no`, `checking for X-config... no`, summary-line variants (`X: no/disabled/None`)
- meson: `Run-time dependency X found: NO`, `Dependency X found: NO`, `Library X found: NO`, `Program X found: NO`
- cmake: `Could NOT find X`, `X_FOUND ... FALSE`
- pkg-config: `Package X was not found`, `Package 'X', required by ..., not found`
- explicit configure-error: `configure: error: X (not found|is required|required)`

### Name aliasing

Each dep name generates a search-variant set: bare name, version-stripped, `-pass1`/`-bootstrap`/`-core` stripped, with/without `lib` prefix, trailing digit cluster stripped (`openssl3` → `openssl`, `gtk3` → `gtk`). Variants shorter than 3 chars are filtered to avoid false positives on common 2-char substrings.

### Exit semantics

- `0` — clean OR skipped (chroot data absent — intentional first-build skip)
- `1` — findings present
- `2` — environment problem (repo source missing — distinct from chroot absent)

### Skip-with-info semantics

If `<chroot>/var/lib/igos/packages/` or `<chroot>/mnt/intergenos/build/logs/` are absent (no prior build, VM just reverted to snapshot, scanning on a workstation without a chroot), the gate emits a skip-with-info message and exits `0`. This prevents the gate from blocking first-build scenarios while preserving its value for regression-catching against post-install state from a previous build.

### Usage

```bash
scripts/preflight-silent-loss.py              # gate mode — terse pass/fail
scripts/preflight-silent-loss.py --report     # also emit JSON + TSV to build/
scripts/preflight-silent-loss.py --chroot /mnt/alt-chroot
scripts/preflight-silent-loss.py --root /alt/repo
```

## phase_validate integration

Both gates wire into `scripts/build-intergenos.sh:phase_validate` after the existing checks. The full pre-build gate chain at master:

1. `host-check.py` — host requirements
2. `preflight-tier-coverage.py` — Rulebook Rule 17 (every tier-declared pkg reachable from its phase)
3. `preflight-audit-coverage.py` — every in-scope pkg has a current reconciled audit record
4. `validate-package-tiers.py` — Rule 1 + cross-tier-dep audit
5. **`preflight-build-order.py` — Scan A — ordering violations** (new)
6. **`preflight-silent-loss.py` — Scan B — silent feature loss** (new)

Each gate's non-zero exit halts build kickoff via shell errexit propagation. Errors include a `Resolve:` line suggesting the fix shape.

## Operator runbook — interpreting findings

### SAME-SCRIPT-VIOLATION
Move the dep's `run_package` line earlier in the same `chroot-build-<phase>.sh` script.

### CROSS-PHASE-VIOLATION
Two options:
- Move the dep to an earlier phase (retier `desktop → core`, etc.) — only if the dep's *nature* justifies the tier per Rule 1 carve-outs.
- Author a `-pass1` / `-bootstrap` variant of the dep in the consumer's earlier phase. Standard pattern in our tree (see gobject-introspection-pass1, libpcap-pass1, slang-pass1, networkmanager-pass1, pinentry-pass1, vala-pass1).

Do NOT add `--without-<dep>` or `--disable-<feature>` to the consumer's build.sh to make the build green. That's a Rule 3 violation (silent feature loss).

### DEP-NOT-FOUND
Either:
- The dep was never added to the tree (real missing package) — add it.
- The dep name is a typo or stale reference — fix the consumer's `package.yml` `dependencies.build` entry.

### DECLARED-FAILED
The consumer declared the dep, but configure didn't see it at probe time. Common causes:
- Build order was wrong at the time of the build (re-run Scan A to confirm).
- The dep's installed shape isn't what configure expected (`pkg-config` file location, header path).
- The dep was installed but the consumer's build environment didn't include the right `PKG_CONFIG_PATH` / `CFLAGS` / `LDFLAGS`.

### BLFS-REQUIRED-UNDECLARED-FAILED
BLFS says the dep is required for this package but our `package.yml` doesn't declare it AND configure tried to find it. Add the declared dep + ensure it's built earlier.

### BLFS-RECOMMENDED-UNDECLARED-FAILED
Same as above but BLFS classifies the dep as "recommended." Resolve unless there's a documented rationale for the recommendation skip — in which case add a comment to `package.yml` explaining the deliberate omission.

### BLFS-OPTIONAL-INTREE-FAILED
We have the package, but the consumer didn't declare or wire it. Adding the declared dep is usually correct (we have the dep, we should use it).

### summary_disabled / meson_not_found_intree
End-of-configure summary feature disabled, or a meson-probed in-tree dep not found. Investigate which dep would enable the feature and add it. The `systemd → ukify/homed/man/sysupdate=disabled` pattern surfaced here is the canonical case.

## Tests

Tests at [tests/preflight/](../../../tests/preflight/):

- `test_preflight_build_order.py` — 12 tests covering all 5 finding types, exit codes, indent-style parsing, duplicate detection, clean-tree
- `test_preflight_silent_loss.py` — 20 tests covering name-variants, pattern matching for all detection-failure types, summary-block scanning, noise filtering, in-tree detection, skip-when-chroot-absent

Run: `python3 -m unittest tests.preflight.test_preflight_build_order tests.preflight.test_preflight_silent_loss`

## Limitations + future work

- **Scan B requires chroot data.** A first-build always SKIPs Scan B. This is intentional (no prior state to audit) but means Scan B never catches a build-#1 issue. Mitigation: run Scan B against the chroot post-build as a sanity check before the publish phase.
- **Pattern coverage is heuristic.** The detection-failure patterns cover the common autotools/meson/cmake/pkg-config conventions but don't exhaustively cover every build system in the tree (e.g., custom Makefile-only builds, Go's `go.mod` resolution, Rust's `cargo` checks). Adding new patterns is an additive change — append to `_DETECTION_PATTERNS`.
- **Name aliasing can over-match.** A short variant of a real dep name could match a coincidental log substring. Variants shorter than 3 chars are filtered; longer variants rely on regex word boundaries + line anchors. False positives surface as findings against packages that legitimately don't use the dep — investigate before "fixing."
- **The BLFS-truth db can drift.** If a BLFS upstream changes its required/recommended/optional classification, our DB is stale until next sync. The `package_audit` table reconciliation gate (`preflight-audit-coverage.py`) is the broader mechanism that catches DB drift; Scan B inherits whatever the DB asserts at scan time.

## Provenance

- Prototypes authored 2026-05-11 ~02:00Z during the Build #8 → Build #9 remediation:
  - `scan_build_order.py` — surfaced 4 ordering violations (rpm/libgcrypt, nghttp2/libxml2, newt/slang-pass1, libgudev/vala)
  - `scan_silent_loss.py` — surfaced 9 Tier-1 silent losses including the systemd-without-15-security-deps finding
  - `scan_summary_disables.py` — surfaced the ukify/homed/man/sysupdate=disabled tier of findings
- Promoted to `scripts/preflight-*.py` 2026-05-12 per dispatch ratified by owner.
- Reproducibility-infrastructure rationale ratified 2026-05-12: pre-build validate-time gates are infrastructure, not friction.
