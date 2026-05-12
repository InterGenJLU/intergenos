# Build-order + silent-feature-loss audit methodology (Scan A + Scan B)

**Authored:** 2026-05-12 build-system maintainer. Documents the audit methodology that produced the Build #8 → Build #9 fix wave (commits `55b4da4` + `0cadd8c` on master). Companion doc to `preflight_scanners_v1.md` (operator runbook, authored by the harness-layer maintainer during the preflight-scan-promotion dispatch).

## Why this audit existed

Build #8 halted at rpm/libgcrypt configure (`6f18cd8` master, resume-2 unit `igos-build-8-resume-mandoc.service` FAILED 2026-05-11T19:15Z). The fix shape for rpm/libgcrypt was known (reorder libgcrypt before rpm in `chroot-build-core-extra.sh`) — but owner's question went deeper:

> *"How many silent feature losses have we already absorbed into this build?"*

That question splits into two independent classes:

1. **Build-order bugs** — declared dependency wired AFTER its consumer in the same `chroot-build-*.sh` script, or wired in a LATER phase script than its consumer. Easy to detect mechanically given the scripts + per-package `package.yml`. Caught the rpm/libgcrypt halt + 3 more.

2. **Silent feature loss** — configure-time probe failed (so the feature is disabled in the resulting binary) but the build proceeded. The package's manifest looks installed; users see no error. Only the build log knows what was actually probed-and-not-found. Substantially harder to detect mechanically because (a) probe patterns vary across build systems (autotools / meson / cmake / pkg-config), (b) some probes are legitimately negative (Windows-host probes on Linux, alternate-TLS-impl probes when we use OpenSSL only, etc.).

Existing audit infrastructure (`scripts/audit-silent-feature-loss.py`, shipped at `13aa75f`) measures **declared deps vs configure flags** — a static analysis of what we INTENDED. It doesn't read build logs. The audit Phases A-E (2026-05-10..11 morning) measured declared-deps-vs-BLFS-truth + tier classification + Rule 5 bundled-libs — also static. None of these reach the **build log evidence** layer.

Scan A + Scan B were authored to fill that gap: catch what configure ACTUALLY saw, not what we intended.

## Scan A — build-order violation detector

**Source:** `scripts/preflight-build-order.py` (promoted from `/tmp/scan_build_order.py` prototype via the 2026-05-12 the harness-layer maintainer dispatch).

**What it does:** parses `run_package "NAME" "DIR" "VER"` lines across every `chroot-build-{ch8,core-extra,base,desktop,ai,extra,bootloader}.sh` script. For each `run_package` call, looks up the package's `dependencies.build` from `packages/<tier>/<dir>/package.yml`. For each declared build dep, checks whether the dep is wired BEFORE or AFTER the consumer.

**Classification:**
- **SAME-SCRIPT-VIOLATION** — consumer at line N, dep at line N+M (M > 0), same script. Build will halt OR silently disable the feature depending on whether configure is strict or lenient.
- **CROSS-PHASE-VIOLATION** — consumer in phase P, dep is tier:T where T's phase is LATER than P. The Python DAG resolver in phases desktop/ai/extra correctly topo-sorts within a phase, but doesn't cross-phase-resolve. So a tier:desktop dep declared by a tier:core package would be unavailable at build time.
- **DEP-NOT-FOUND** — declared dep doesn't exist in any tier directory. Always a real bug.

**Phase order** (per `scripts/build-intergenos.sh`):
```
ch8 → core-extra → base → ch10 → desktop → ai → extra → bootloader
```

**Name resolution:** packages declare deps by directory name (e.g., `glib2`, `libxml2`). Phase 1 lookup is by direct match against `packages/<tier>/<name>/`. Phase 2 fallback: tier-directory enumeration if direct lookup fails (handles installed-pkg-name → dir-name mismatches like `glibc-core`'s install name "glibc" vs dir "glibc-core" — though the validator's resolution goes the other direction, treating the dir as canonical).

**Variant handling:** `-pass1`, `-bootstrap`, `-core`, `-host` suffixes are stripped before BLFS db lookups (Scan B uses this) but kept as-distinct for build-order purposes (vala-pass1 and vala are different packages in different tiers).

**What it found in Build #8 audit (2026-05-11T20:30Z):**

| # | Consumer | Line | Dep | Dep line | Class |
|---|---|---:|---|---:|---|
| 1 | nghttp2 | 294 | libxml2 | 620 | SAME-SCRIPT (already silently absorbed) |
| 2 | rpm | 473 | libgcrypt | 576 | SAME-SCRIPT (current halt) |
| 3 | newt | 676 | slang-pass1 | 815 | SAME-SCRIPT (would halt next) |
| 4 | libgudev | 580 | vala | (desktop, Python DAG) | CROSS-PHASE (would halt) |

**False-positive considerations:** none observed in this run. Every flagged violation was a real build problem. Future runs may surface cases where a declared dep is technically a dependency-of-dependency that gets satisfied transitively without the explicit ordering — these are cases for human judgment, but rare.

## Scan B — silent-feature-loss detector

**Source:** `scripts/preflight-silent-loss.py` (promoted from `/tmp/scan_silent_loss.py` + `/tmp/scan_summary_disables.py` prototypes — merged into one tool per the the harness-layer maintainer dispatch).

**What it does:** walks build logs at `/mnt/igos/mnt/intergenos/build/logs/` (chroot-resident; build-system maintainer's host accesses via SSH+sudo) for every installed package. For each log:

- **Pass 1 — Declared-Failed**: for each declared `build:` dep in the package's `package.yml`, search log for detection-failure patterns. If the package declared the dep, configure should have FOUND it. If it didn't, that's a silent loss.
- **Pass 2 — BLFS-Required-Undeclared-Failed**: for each `required` or `recommended` BLFS dep (per `build/blfs-packages.db`) that the package did NOT declare in package.yml, search log for detection-failure. If failed, the dep was probed-and-missed without being recorded as a real dep — likely a pkg.yml gap, not a build problem.
- **Pass 3 — BLFS-Optional-In-Tree-Failed**: for each BLFS `optional` dep that EXISTS in our package tree but wasn't declared, search log for detection-failure. Per the project's "optional means use-if-have" discipline, in-tree optionals MUST get picked up if we have them; if configure missed them, ordering or pkg-config issue.
- **Supplement — Summary-Disabled**: scan log for end-of-configure summary-table entries (`^[ \t]+<feature>:\s*(no|disabled|None)\b`). These are the high-fidelity "did the build actually use this feature?" signal that primary patterns miss when configure doesn't print a "checking for..." line.

**Detection-failure patterns:**

- autotools: `checking for X... no` / `checking for X.h... no` / `checking for X-config... no` / summary-table `X: no` or `X: disabled`
- meson: `Run-time dependency X found: NO` / `Dependency X found: NO` / `Library X found: NO` / `Program X found: NO`
- cmake: `Could NOT find X` / `X_FOUND.*FALSE`
- pkg-config: `Package X was not found` / `Package 'X', required by ..., not found`
- generic: `configure: error: X (not found|is required)` (hard-fail; cause of halts)

Each pattern variants are generated for the dep name: literal, lib-prefix/unprefix, version-stripped, lowercase. IGNORECASE + MULTILINE flags. False-positives are reduced by anchoring summary-table patterns to `^[ \t]+` (indented summary block).

**Name aliasing:** BLFS db uses different names (e.g., GLib in the book vs glib2 in our tree). The `aliases` table maps igos_name → BLFS anchor. Scan B's BlfsLookup class tries 3 paths: direct `anchor_id` match, then `name` match, then aliased lookup.

**What it found (122 installed packages, Build #8 chroot state):**

| Package | Lost feature / class | Severity |
|---|---|---|
| systemd | 15 in-tree deps not found: libseccomp, libapparmor, libcryptsetup, libfido2, libgcrypt, gnutls, libcurl, libidn2, libqrencode, libarchive, bash-completion, git, rsync, rpm + 7 summary-disabled features (ukify, homed, man, sysupdate, default-dnssec, pamconfdir, rpmmacrosdir) | **HIGH** (HG-blocker — sandbox primitives missing) |
| nghttp2 | libxml2 not found (Scan A consequence; pass 1) | MED |
| glib2 + glib2-bootstrap | bash-completion not found + man-pages/sysprof/glib_debug disabled in summary | MED |
| linux-pam | libtirpc not found (NIS/RPC backend missing) | LOW-MED |
| dbus | doxygen not found (HTML API docs missing) | LOW |
| kmod + p11-kit | bash-completion not found | LOW |
| gobject-introspection-pass1 | cairo + doctool disabled (intentional pass1; resolved by full goi in desktop) | LOW |

Plus the Tier-3 likely-intentional findings (curl: brotli/LDAP/GSS-API; sudo: LDAP/SSSD; wget: Metalink/GPGME; etc.) which are by-design under our security-only stance.

## Why both scans are needed

Scan A catches **structural** ordering bugs. It's mechanical and ~5x cheaper to run than Scan B (parses scripts + ymls; no log walking). It catches the rpm/libgcrypt halt class BEFORE the build runs.

Scan B catches **configuration-time evidence**. It catches the cases where:
- A dep was undeclared (so Scan A has nothing to check)
- A dep was declared and theoretically in-order, but configure's actual probe failed for a runtime reason (pkg-config path issue, missing .pc file, etc.)
- A package's "in-tree optional" got silently dropped because we never told its pkg.yml about it

The two scans intersect on the simplest class (declared-failed) but each catches material the other misses:
- Scan A misses: undeclared deps that should have been declared (the systemd 15-deps-missing case is the canonical example; nothing in package.yml told us about libseccomp/libapparmor/etc. so Scan A had no anchor to check)
- Scan B misses: future-build halts (Scan B requires a log; pre-build it has nothing to walk)

So: Scan A runs at **build-validate time** (pre-kickoff). Scan B runs **per-post-build** (post-N-package-install, in a phase_validate hook on the next kickoff) AND **on-demand** when auditing a specific build's evidence.

## What this surface enables

The 9 Tier-1 silent losses Scan B surfaced WERE silently absorbed in Build #8. Under the project's security-only directive, several of them (systemd's missing hardening libs in particular) made Build #8 non-shippable. The owner-direct: fix ALL findings, discard Build #8, start Build #9 fresh.

Build #9 launched 2026-05-11T21:34Z against master `0cadd8c` (the fix wave). Both audit infrastructures (phase_validate gates + Scan A/B prototypes) ran at kickoff:
1. First kickoff failed at audit-preflight (7 missing audit records + 9 dep-drift — exactly the kind of catch that the audit infrastructure is for)
2. Second kickoff failed at tier-validator (6 packages classified MOVE→desktop — same)
3. Third kickoff clean

Both pre-build catches cost minutes, not hours. Validating that the validate-gates ARE reproducibility infrastructure — they catch input drift at validate time, when fixing is cheap.

## How to use this methodology going forward

**For Build #N+1:**

1. Pre-kickoff: `phase_validate` invokes Scan A (build-order) via `preflight-build-order.py`. Zero violations expected; any new finding is a real bug.
2. Post-install at each phase boundary: `preflight-silent-loss.py` walks logs of newly-installed packages. Findings classified by severity; HIGH = halt; LOW-MED = annotate per `docs/audit-needs-review-disposition.md`.
3. Per-finding owner-decisioning: feature loss in a security-critical path is a HARD HALT. Feature loss in a UX/docs path may be tolerated **only with explicit annotation** in the relevant package.yml + a "why" comment. Tolerated does NOT mean ignored.

**For investigating a specific build:**

1. Run Scan A against current scripts — confirm zero ordering bugs.
2. Run Scan B against the build's log dir — list silent losses.
3. Cross-reference each Scan B finding against package.yml's intent + BLFS-truth + tier directory presence.
4. For each finding, choose: fix-by-reorder, fix-by-declare-dep, fix-by-pass2-rebuild, or document-and-tolerate.

**Anti-pattern reminders** (carved from the 2026-05-11 evening session):

- **Never offer "ship with X disabled as known limitation"** as an alternative to fixing the loss. The build that surfaced the loss is the build that fixes it.
- **Validate gate failures are the system working**, not friction to bypass. Fix the input (yml / audit / tier hard-rule), never the gate.
- **A defer rebrand by version number** (e.g., "fix in v1.0+1") is still a defer. Audit findings get fixed in the same propose-permission-fix cycle that surfaced them.

## File locations

- **Production scanners** (post-the harness-layer maintainer promotion):
  - `scripts/preflight-build-order.py`
  - `scripts/preflight-silent-loss.py`
- **Operator runbook** (the harness-layer maintainer dispatch deliverable): `docs/research/build_system/preflight_scanners_v1.md`
- **Audit database**: `build/blfs-packages.db` (614 audit records as of 2026-05-12)
- **Audit JSON records**: `build/audits/*.json` (one per package; aggregated into the db)
- **Prototypes** (historical reference): staged on the maintainer host at `~/tmp/scan_prototypes/{scan_build_order,scan_silent_loss,scan_summary_disables}.py` during the 2026-05-12 promote-to-scripts hand-off

## Related work

- `scripts/audit-package.py` + `scripts/aggregate-package-audits.py` — per-package audit framework (I1 in TRACKER)
- `scripts/audit-silent-feature-loss.py` — static "declared deps vs configure flags" checker (I4); complements Scan B's evidence-based approach
- `scripts/audit-rule5-sweep.py` — bundled-libs classifier (I3)
- `scripts/validate-package-tiers.py` — Rule 1 tier-classification enforcement (I5)
- `scripts/preflight-tier-coverage.py` — every tier-declared package reachable from chroot-build scripts (I2)
- `scripts/preflight-audit-coverage.py` — every in-scope package has a current audit record (I2)

Six audit-infrastructure scripts now, three pre-build gates wired into `phase_validate`. The next builds inherit all of them.
