# 11 — Resolving the validate-phase gates

**Audience:** maintainers whose build halted in the `validate` phase — the battery of reproducibility/correctness gates the orchestrator runs *before* any compilation. This topic explains each gate, what its failure means, and the **correct** way to clear it, with emphasis on the distinction between a real fix, a tier reclassification, and an acknowledged-divergence.

## Why a validate-phase failure is cheap (and safe to iterate on)

`validate` is the first phase and runs in ~1 second. It executes entirely on the host side of the build — it reads `package.yml` files, the audit database, and the source tarballs; it does **not** touch the chroot at `/mnt/igos` (that's created later, in `phase_chroot_prep`). So a validate failure:

- Halts the orchestrator with exit 1 *before* any toolchain work.
- Leaves `/mnt/igos` and `/tmp/igos-build` untouched (they don't exist yet on a clean baseline).
- Means you can fix the finding and **relaunch without re-reverting the VM** — there is no chroot contamination to undo (contrast with the `-lgcc_s` class that motivates [the revert-before-rebuild rule](10-iteration-resume-builds.md); that only applies once the toolchain has run).

A ~0.1–1s "fast fail" is almost always a validate-phase gate, not a build bug.

## The gates, in order

The orchestrator's `validate` phase runs these in sequence ([`scripts/build-intergenos.sh`](../../scripts/build-intergenos.sh), `phase_validate`):

| # | Gate | Script | Blocks build? |
|---|------|--------|:---:|
| 1 | Host requirements | `host-check.py` | yes |
| 2 | Tier reachability (Rulebook Rule 17) | `preflight-tier-coverage.py` | yes |
| 3 | **Audit-coverage** (reproducibility) | `preflight-audit-coverage.py` | yes |
| 4 | **Tier validation** (Rule 1 + cross-tier-dep) | `validate-package-tiers.py` | yes |
| 5 | **Source-tree coverage** (source-aware change detection) | `check-source-tree-coverage.py` | yes |
| 6 | **Reboot-required activation semantics** | `check-reboot-required-declared.py` | yes |
| 7 | ISO-closure (effective-`iso_include` runtime edges) | `preflight-iso-closure.py` | yes |
| 8 | Scan A — build-order | `preflight-build-order.py` | yes |
| 9 | Licence identifiers (SPDX list membership) | `preflight-license-identifiers.py` | yes (exit 2 — never a quiet pass — when the bundled identifier file is missing/malformed, when the package tree is empty, or when the selected scope leaves nothing to check) |
| 10 | Scan B — silent feature loss | `preflight-silent-loss.py` | yes (self-skips to exit 0 when no prior chroot — first build / post-revert; under `--require-audit` a skip AND any coverage gap — empty inventory, missing/unreadable log, missing package.yml — fail closed, exit 3) |
| 11 | Scan A.2 — undeclared build deps | `preflight-undeclared-deps.py` | yes (first run extracts source tarballs into `build/scan-cache/`, ~12 min; cached ~10s after; gate mode fails closed, exit 3, when a DECLARED source cannot be located/extracted/scanned or the sources dir is absent — `--advisory` for exploratory runs; unknown `--only` names are exit 2. `source: []` and non-archive payloads are named by-design classes, never failures) |
| 12 | Staged-kernel exclusivity | `preflight-single-kernel.sh` | yes (`--allow-none` here: passes on an absent/empty chroot; more than one staged kernel fails in every mode) |

One further fail-closed gate runs later in the pipeline, at the start of the
archive-manifest phase (immediately before the first signing pause):
`check-wiki-manifest.py`, which verifies the shipped wiki book against its
signed page manifest — see its own section below. It lives at that phase
rather than in `phase_validate` because it verifies the *built chroot's*
installed documentation, which does not exist at validate time.

All twelve halt the build on a non-zero exit — the orchestrator runs under `set -euo pipefail`, so a gate failure propagates out of `phase_validate` (gates 4, 5 and 6 also carry explicit `return 1` handlers). The one nuance is Gate 10 (silent-loss), which self-skips to exit 0 when there is no prior-build chroot to audit, so it only blocks on a real regression against a previous run. Gate 1 (`host-check.py`) validates the build *host*, not the package tree; the rest are pure host-side static analysis of `package.yml` / `build.sh` / the source tarballs.

Gate 6 (reboot-required activation semantics) asserts that a package which ships a payload the running system **cannot activate until reboot** declares `reboot_required: true` in its `package.yml`. Without that declaration the package manager cannot tell the user the payload is on disk but inactive, and the install completes silently while the old driver stays loaded — the failure class where an out-of-tree graphics driver lands behind an in-tree blacklist and nothing says a reboot is what makes it take effect. Detection reads the **actual install commands** in each `build.sh`, with comments stripped so prose about modules never false-positives, and flags three shapes: a kernel image copied to `/boot/vmlinuz*`; a write to `/etc/modprobe.d/` in a recipe that also emits a `blacklist <module>` line; and a `*.ko` file installed into `/lib/modules`. Exit 1 lists the offending packages on stderr; exit 2 means the arguments or the packages tree were wrong. Resolve by adding the field — the gate is a regression guard, so a new driver or kernel package that forgets it is caught here rather than in the field.

Gate 7 (ISO-closure) computes every package's **effective** `iso_include` via the build parser itself (explicit override wins, otherwise `tier: extra` defaults to mirror-only) and halts on three classes: a shipped package declaring a `dependencies.runtime` edge on a mirror-only one (the class `pkm iso-prep` would otherwise abort on at squashfs time), a runtime-dep name resolving to no package in the tree, and an explicit `iso_include:` whose YAML value is not a real boolean (the parser coerces with `bool()`, and `bool("false")` is `True` — a quoted value would silently ship a mirror-only package). Resolve by fixing the edge, the name, or the value — the gate's output names each finding; there is no acknowledge/override path.

Gate 9 (licence identifiers) asserts that every recipe's `license:` is a real SPDX licence expression whose identifiers are on the SPDX licence list. That field is not decorative: the ISO SBOM publishes it as `licenseDeclared` and the mirror index carries it, so a declaration like `Public-Domain`, `MIT-style` or `Various (redistributable)` propagates into both and resolves for no SPDX consumer. Checking the *shape* of an expression is not enough — each of those is a well-formed token — so this gate checks list membership, and checks the right operand of `WITH` against the separate licence-**exception** list (which is why `MIT WITH MIT` fails). Resolve it in the recipe, three ways and no others: use the SPDX identifier for the licence the package actually carries; or, when SPDX has no identifier for that licence, declare `LicenseRef-<Name>`, which is SPDX's own mechanism for exactly that case and is how `linux-firmware`'s mixed vendor blobs and the public-domain packages are declared; or, if the identifier is genuinely newer than the bundled list, refresh `config/spdx-license-list.json` per the `_regenerate` note inside that file. There is no acknowledge/override path, and `EXEMPT_PACKAGES` in the gate is empty by design — `LicenseRef-` already covers everything SPDX does not carry. **Deprecated-but-listed identifiers (`GPL-2.0`, `LGPL-2.1`) PASS and are printed as warnings**: replacing one with `-only` or `-or-later` resolves an ambiguity that only the package's own licence text can settle, so the gate reports them and leaves the determination to a human. The identifier sets are bundled rather than fetched, because a gate that reaches the network makes its verdict depend on a third party being reachable and honest at that moment; the run prints the licence-list version and release date every time, so a stale list is stated rather than assumed.

Gate 12 (staged-kernel exclusivity) asserts the chroot holds exactly one staged kernel — one `/usr/lib/modules/<kver>` tree and one `/boot/vmlinuz-<kver>`, mutually agreeing. The kernel's module dir and image are *release*-named (`CONFIG_LOCALVERSION=-igos-<release>`), so a release-bumped rebuild on a populated chroot orphans the prior release's tree instead of overwriting it, and downstream consumers pick ambiguously (the image phase's kernel symlink, the squashfs's wholesale `/usr/lib/modules` copy). Beyond `phase_validate` the same script also runs at every orchestrator phase entry from `kernel` through `iso`, inside the kernel phase driver (which additionally *prunes* superseded staged kernels before building), at the bootloader driver's prerequisites, and as squashfs Step 4.45 — so every entry point into the build asserts it. Resolve a failure by pruning the superseded release's module tree and vmlinuz; never ship a root with two staged kernels.

The aggregate **reconciliation report** (`build/audit-reconciliation-<ts>.tsv`, produced by `aggregate-package-audits.py`) is **advisory** — it is not itself a build gate — but its `mismatch` rows should be triaged, not ignored (see the Tier-validation section below).

### One more host-side gate, one phase later: tarball membership

`validate-tarball-membership.py` is not in the table above because it does **not** run in `validate`. It runs in the next phase, `verify-sources`, and it has to: it reads the generated source tarballs, and those do not exist until the three generator scripts run at the top of that phase. Running it any earlier would check a stale set or no set at all. It is described in full in [its own section below](#gate--tarball-membership-a-generated-tarball-is-missing-a-file-its-recipe-installs) and shares every property that makes a validate failure cheap — host-side, ~1.5s, no chroot, halts before any compilation.

The two gates that most often fire when you add or change a package are **audit-coverage** and **tier validation**; the **source-tree-coverage** gate fires when a first-party package's `build.sh` reads an external in-tree source dir it doesn't declare. The rest of this doc walks each of these three; Gates 1–2 and 6–11 are structural/environmental and rarely need hand-resolution beyond reading their output (the resolution paths for Gates 6, 7, 9 and 12 are fully described above).

---

## Gate — Audit-coverage: "N packages need audit work"

**Symptom** (build log):

```
[audit-preflight] missing audit:     3
[audit-preflight] stale (version):   1
[audit-preflight] FAIL: 4 packages need audit work.
  - cups-pk-helper
  - zenity
  - zram-generator
  - llama-cpp: audit=b5545, yml=b8796
```

**What it checks.** Every in-scope package (tiers `core`/`base`/`desktop`/`extra`/`ai`; LFS Ch 8 is sacrosanct and excluded) must have a **current** audit record in the `package_audit` table of `build/blfs-packages.db`:

- **missing** — no audit record at all. Cause: a newly-added package.
- **stale** — the audit record's `version` ≠ the `package.yml` version. Cause: a version bump (e.g. `llama-cpp` b5545→b8796).
- **drift** — the audit's `our_deps_build_json` ≠ the `package.yml` `dependencies.build`. Cause: deps edited without re-auditing.

**The data flow (understand this or you'll re-aggregate to no effect):**

```
audit-package.py <name> --save
    reads  packages/<tier>/<name>/package.yml + the source tarball
    writes <private-repo>/audits/per-package/<name>.json     ← per-package audit truth

aggregate-package-audits.py
    reads  all audits/per-package/*.json
    writes build/blfs-packages.db  (package_audit table)     ← what the gate reads
           build/audit-reconciliation-<ts>.tsv               ← advisory report

preflight-audit-coverage.py  (the gate)
    reads  build/blfs-packages.db  ← so you MUST re-aggregate after auditing
```

The audit JSONs are held outside this repository, in a separate audit store, under `audits/per-package/`. The scripts resolve its location via `$INTERGENOS_PRIVATE_REPO` / `$INTERGENOS_AUDITS_DIR`, falling back to a sibling checkout and finally to `build/audits/` with a warning. If your audit store is not a sibling of this checkout, set the environment variable explicitly.

**Resolve:**

```bash
export INTERGENOS_PRIVATE_REPO=/path/to/audit-store

# 1. Generate / refresh the audit record for each flagged package:
python3 scripts/audit-package.py cups-pk-helper --save
python3 scripts/audit-package.py zenity         --save
python3 scripts/audit-package.py zram-generator --save
python3 scripts/audit-package.py llama-cpp      --save   # stale → re-audit refreshes the version

# 2. Ingest into the DB + regenerate the reconciliation report:
python3 scripts/aggregate-package-audits.py

# 3. Confirm green:
python3 scripts/preflight-audit-coverage.py
#   → [audit-preflight] PASS: every in-scope package has a current, reconciled audit record

# 4. Commit the new/updated JSONs to the audit store.
```

The `build/blfs-packages.db` is a host-side build artifact on the shared volume; the build VM reads the same file over virtiofs, so re-aggregating on the host is sufficient — no rebuild of the DB inside the VM is needed.

---

## Gate — Tier validation: "MOVE→\<tier\>" or "UNCLEAR"

**Symptom:**

```
package          current_tier   verdict       notes
cups-pk-helper   desktop        MOVE→extra
zenity           desktop        UNCLEAR
# total non-OK rows: 2
ERROR: validator found tier violations requiring correction
```

**What it checks.** `validate-package-tiers.py` encodes the decision tree in [`docs/package-tiers.md`](../package-tiers.md) (the SSoT). For each package it computes a canonical "natural tier" and compares it to the declared tier. Verdicts:

- **MOVE→X** — natural tier differs from declared tier.
- **UNCLEAR** — no natural tier could be determined.
- **CROSS-TIER-DEP** — a `build`/`host` dep resolves to a *later* tier (a genuine Rule 1 violation — fix the dependency, never demote the consumer).

**How the natural tier is derived** (three layers, in order):

1. **Hard category lists** — explicit curated sets (`GUI_SUBSTRATE_DESKTOP`, the print stack, `USER_FACING_APPS`, etc.) plus prefix/suffix patterns (e.g. `*-helper` → `extra`, `gnome-*` → `desktop`).
2. **Consumer inference** — the reverse-dep graph: a package takes the *earliest* tier that consumes it. **Critical subtlety: the graph is built from `build` + `host` deps only.** A package consumed *only* as a `runtime` dep has no graph consumer, so inference cannot place it → **UNCLEAR**.
3. If neither resolves it → **UNCLEAR**.

**Resolve — evaluate against the SSoT first; do not blindly obey the verdict.** There are two correct outcomes:

**(a) The package really is in the wrong tier → move it.**

```bash
git mv packages/<old-tier>/<name> packages/<new-tier>/<name>
# edit package.yml:  tier: <new-tier>
```
Then fix builder reachability:
- `core` / `base`: move the explicit `run_package "<name>" …` line to the correct `scripts/chroot-build-*.sh` (in dependency order). See [Topic 08](08-adding-packages.md).
- `desktop` / `extra` / `ai`: nothing to move — these tiers are built by the Python tier-driver walking the topo closure of all `tier: <X>` packages, so the `package.yml` `tier:` field is the only switch.

Finally **re-audit + re-aggregate** (the audit record carries the tier).

**(b) The package is in the CORRECT tier but the heuristic misfires → encode the SSoT.**

When `docs/package-tiers.md` clearly classifies the package as its declared tier but the validator's heuristic doesn't recognize it, add an explicit entry to the appropriate category list in `validate-package-tiers.py`. Worked examples from 2026-06-11:

- **`cups-pk-helper`** matched the crude `*-helper → extra` fallback, but it's a desktop **print-integration service** (a PolicyKit mechanism for the GNOME Printers panel, belongs with `cups`) — `desktop` per the doc. Fix: add it to the print-stack desktop list.
- **`zenity`** was **UNCLEAR** because its only consumer (`intergen`, tier `ai`) declares it as a **runtime** dep — invisible to the build/host reverse-dep graph. It's a core GNOME GUI utility (GUI substrate) — `desktop` per the doc. Fix: add it to `GUI_SUBSTRATE_DESKTOP_EXTRA`.

> **Editing the validator to pass a verdict is legitimate ONLY when the SSoT already classifies the package that way** — you are encoding `docs/package-tiers.md`, not gaming the gate. If the doc is genuinely ambiguous about the package, that's a judgment call for the operator, not a self-serve list edit.

---

## Gate — Reconciliation mismatches (advisory): "BLFS required dep X not in our dependencies.build"

**Symptom** (in `build/audit-reconciliation-<ts>.tsv`, category `mismatch`):

```
maturin   mismatch   deps_build   BLFS required dep 'setuptools_rust' (name=setuptools_rust-1.12.0) not in our dependencies.build
shadow    mismatch   deps_build   BLFS required dep 'linux-pam' (name=Linux-PAM-1.7.2) not in our dependencies.build
```

This is produced by `aggregate-package-audits.py` and is **not a build-blocking gate**, but a mismatch is a real signal: `audit-package.py` compared the BLFS book's *required* deps for the package against our `dependencies.build` and found one we don't declare. **Triage every mismatch — real gap vs intentional divergence.**

**(a) Real gap** — we genuinely need the dep and forgot to declare it → add it to `dependencies.build`, re-audit + re-aggregate.

**(b) Intentional divergence** — we build differently than BLFS, so the BLFS dep does not apply to our build. Record it at the comparison site in `audit-package.py` by adding a `(package, blfs_anchor)` tuple to the correct skip-set, **with an inline justification comment**:

- **`KNOWN_CYCLES`** — bootstrap-ordering cycles satisfied by a 2-pass variant. (e.g. `("newt","slang")` — satisfied via `slang-pass1`.)
- **`DELIBERATE_DIVERGENCES`** — the BLFS dep is genuinely not used by our build method.

Worked examples from 2026-06-11 (both triaged from an open 2026-05-31 audit item):

- **`shadow` → `linux-pam`**: `shadow` is our pre-PAM **first pass** (LFS Ch 8 order — built before `linux-pam` exists). This is the documented **SCC-3** auth cycle `{libpwquality, linux-pam, shadow, systemd}`, dissolved by a two-pass: the PAM-enabled rebuild is the separate `shadow-pam` package, which *does* declare `linux-pam`. BLFS has a single shadow page covering both passes → false positive.
- **`maturin` → `setuptools_rust`**: we build the `maturin` binary directly via `cargo build` (vendored, `--offline --frozen`) and hand-install the PEP517 shim. We do not use the pip/PEP517 build-isolation path, so the upstream `setuptools-rust` build-backend requirement does not apply.

Then re-audit the package (`_mismatches` clears) and re-aggregate.

> **Divergence skips encode a permanent deviation from upstream and require operator authorization.** Propose with evidence; do not self-authorize. The same applies to `.audit-override` (below).

### The `.audit-override` mechanism — and when NOT to reach for it

A package directory can carry a `.audit-override` JSON:

```json
{"reason": "...", "approved_by": "...", "expires_at": "YYYY-MM-DD"}
```

`preflight-audit-coverage.py` then **skips that package's missing/stale/drift checks entirely**. It is a broad, *temporary* acknowledgment "while the maintainer addresses the gap" — note the `expires_at`. For a permanent, single-dep divergence, prefer the `DELIBERATE_DIVERGENCES` / `KNOWN_CYCLES` skip-set: it is precise (one dep, not the whole package), self-documenting (the comment lives at the comparison site), and version-controlled in the auditor. Reserve `.audit-override` for a genuinely temporary, whole-package gap.

---

## Gate — Source-tree coverage: "package reads external source X not in source_tree"

**What it checks.** `check-source-tree-coverage.py` is the self-policing gate for
**source-aware change detection**. A first-party package whose `build.sh` reads from an
**external in-tree source dir** — one of the top-level first-party roots `intergen/`,
`pkm/`, `installer/`, `assets/` (the dirs the build rsyncs into the chroot via
`ensure_sources_staged`) — **must declare that dir in its `source_tree:`** so the content
is folded into the skip-built fingerprint (`igos-build/content_hash.py`).

**Why it blocks.** If the external read is *not* declared, an edit to that external source
does **not** flip the package's template fingerprint, so a targeted `--skip-built` build
silently ships the **STALE** binary — the exact class that bit `intergen-welcome` and cost
days. The gate keeps the fix *complete as the tree grows*: it fails `validate` the moment
any package reads an undeclared external root. A package's own package-dir content is
hashed automatically (`content_hash` arm (b)) and never needs listing.

**Symptom** (build log): a `validate`-phase fail naming the package + the external path it
reads but does not cover in `source_tree`.

**Resolve — the remedy is always harmless: declare the dir.** Add the external source
root(s) the `build.sh` reads to that package's `source_tree:` list in `package.yml`:

```yaml
source_tree:
  - packages/<tier>/<name>/patches   # (example — any in-tree dir the build.sh reads)
  - intergen                          # declare the EXTERNAL root the build.sh reads from
```

Over-declaring only hashes a bit more — it can **never** ship stale — so when in doubt,
declare it. Detection is conservative (it matches real repo-root-prefixed source reads on
non-comment lines), so a flagged read is a genuine undeclared dependency, not noise.

> **Maintainer note (the gate cannot enforce this on itself):** the external-root list
> (`_EXTERNAL_TOPS` in the gate) **must stay in sync** with the first-party source roots the
> build rsyncs into the chroot (`build-intergenos.sh` `ensure_sources_staged`). A *new*
> first-party top-level source root added to the tree has to be added to `_EXTERNAL_TOPS`
> too, or reads from it silently escape this gate.

---

## Gate — Tarball membership: "a generated tarball is missing a file its recipe installs"

**What it checks.** `validate-tarball-membership.py` asserts that every path a generated
package's install step takes from its extracted source tree is a member of that package's
generated tarball. It runs in `verify-sources`, immediately after the three generator
scripts (`build-forge-tarball.sh`, `build-intergenos-source-tarballs.sh`,
`build-intergenos-wiki-tarball.sh`), so it always reads the artifacts the build is about to
use rather than a stale set. The same call is repeated in the source-staging sweep that a
`--start-at` resume takes, because a resume skips `verify-sources` entirely.

**Why it exists.** A generated tarball carries no committed sha256 pin — the generator is
trusted to stage what the recipe consumes, and nothing checked that the two agreed. On
2026-07-30 they stopped agreeing: `intergen-welcome`'s `do_install` had installed
`org.intergenos.Wiki.svg` since release 19, but the generator never staged the file, so
every build from a freshly generated tarball failed at `install: cannot stat` — the package
could not build **at all** — for four days, while its release note claimed the mark ships.
The recipe and the generator are separate files that are only wrong *together*, so no
check that read either one alone could catch it.

**What it enumerates.** The checked set comes from the recipes, not from a glob over
`build/sources/`: every package whose `package.yml` declares a `generated: true` source.
Two of the nine tarballs `build-intergenos-source-tarballs.sh` produces —
`bibata-cursor-theme` and `catppuccin-mocha-blue` — do not begin with `intergen`, so a
filename-shaped glob would cover seven of nine and read as complete. Coverage that shrinks
silently is the class this gate exists to prevent.

**Member paths are compared after stripping.** The builder extracts with
`tar --strip-components=1` ([`igos-build/builder.py`](../../igos-build/builder.py) lines 586
and 605; [`scripts/pkg-functions.sh`](../../scripts/pkg-functions.sh) lines 210 and 224), so
a member stored as `iw-pkg/foo` is what the recipe sees as `foo`. A leading `./` component —
what `det_tar ... .` produces for the cursor and extension bundles — is a real component to
`tar` and is stripped the same way.

**Symptom** (build log), naming the package and the exact path:

```
[tarball-membership] HALT: 1 package(s) install files their tarball does not carry; ...
  intergen-welcome — installs 1 path(s) absent from intergen-welcome-1.0.tar.xz:
    org.intergenos.Wiki.svg
```

**Resolve** either way, but resolve it: stage the named path in the generator script, or
stop installing it in the recipe. There is no acknowledge/override path.

**"COULD NOT DETERMINE" is a failure, not a skip.** An install step is shell, and this gate
understands a deliberately small subset of it. When it meets an unrecognised command, a
variable it cannot resolve, a sourced helper it cannot read, or a `generated: true` package
whose tarball is absent from the sources dir **and which has not declared why**, it reports
that package as *could not determine* and exits 1. That is the same posture `pkm verify` was
given after a check that could not read a file counted it as verified: a check that cannot
check must say so, because a silent skip would let the build read as covered while nothing
was checked. Resolve by simplifying the install step, or by widening the gate's understood
subset deliberately — the command allowlist is `NON_CONSUMING` in the script, and every
widening gets a test in
[`tests/preflight/test_validate_tarball_membership.py`](../../tests/preflight/test_validate_tarball_membership.py).

An absent tarball reaches the same verdict the builder would reach later on its own —
`Source not found` at [`igos-build/builder.py`](../../igos-build/builder.py) line 521, which
returns `None` and fails the package — so this gate moves that halt to the start of the
build and says which package and which artifact.

**A source staged at RELEASE time is a declared state of its own, not a parse failure.**
One generated tarball cannot be produced outside a release build: `intergenos-wiki`'s source
is the rendered mdBook tree, which is staged into `build/wiki-book` from a separate
repository at release time and is not carried in git. Without it,
`build-intergenos-wiki-tarball.sh` SKIPs by design rather than fabricating content, so every
by-hand firing of this gate found that tarball absent and exited 1. Nothing had failed to
parse — the input legitimately did not exist yet at that call site — and a standing
known-noise failure that everyone reads around is how a real failure eventually hides.

The package states the condition in its own recipe, as a top-level non-empty string:

```yaml
release_staged_source: >-
  build/wiki-book — the rendered mdBook HTML is built in the separate wiki repository and
  staged here at release time (IGOS_WIKI_BOOK_DIR overrides), so it is absent from an
  ordinary checkout; the generator SKIPs rather than fabricating content.
```

The gate quotes that declaration back in its output, so the state cannot be claimed without
saying which input is staged and why:

```
[tarball-membership] PASS: 11 generated package(s), 10 verified against their tarballs
(69 consumed path(s)); 1 NOT VERIFIED — release-staged source absent (0.78s)
  intergenos-wiki — RELEASE-STAGED SOURCE ABSENT: intergenos-wiki-1.0.0.tar.xz is not in
    build/sources, and its recipe declares: build/wiki-book — …
    3 consumed path(s) parsed cleanly and stay UNVERIFIED until a build stages that input.
```

It is **declared, never inferred** — no filename shape and no directory guess, so the class
cannot widen silently to a package that did not ask for it. The key is registered in
`KNOWN_FIELDS` ([`igos-build/parser.py`](../../igos-build/parser.py)), so a misspelled
declaration fails the build loudly at parse time instead of quietly returning the package to
the halting behaviour. What the declaration changes is exactly one verdict — an absent
tarball — and nothing else:

| Situation | Verdict | Exit |
|---|---|---|
| Declared, tarball **present** | checked in full, like any other package | 0 / 1 on a real miss |
| Declared, tarball absent, recipe parses | `RELEASE-STAGED SOURCE ABSENT`, named and counted | 0 |
| Declared, tarball absent, recipe **does not** parse | `COULD NOT DETERMINE` | 1 |
| **Un**declared, tarball absent | `COULD NOT DETERMINE` | 1 |
| Declaration present but not a non-empty string | `SETUP ERROR` | 2 |
| Every generated package release-staged and absent | `SETUP ERROR` — the run verified nothing | 2 |

Exit 0 on the second row does not mask anything: an absent generated tarball is still fatal
where it matters, because the builder refuses to build a package whose declared source is
not on disk. Declining to halt here cannot let such a build through, and the package is
reported as unverified in both the passing and the halting output rather than folded into
the clean count.

## Gate — Wiki page manifest: "shipped book != signed manifest"

Fires at the start of the archive-manifest phase (before the first signing
pause), and can be fired standalone at any time:

```sh
python3 scripts/check-wiki-manifest.py --root /mnt/igos     # build chroot
python3 scripts/check-wiki-manifest.py --root /             # installed system
```

The shipped documentation book (`usr/share/doc/intergenos/wiki/`) must be
exactly the page set its signed manifest covers: the detached signature on
`pages-manifest.json` is verified with `gpgv` against the root's own trust
keyring (`etc/pkm/trusted.gpg`) and a pinned release-key fingerprint, then
every listed page must hash to its pinned value and every rendered `*.html`
on disk must be listed. This is the same chain the installed system's
citation layer enforces at cite time — the gate exists so a stale signature
or drifted page refuses at build time, where it is fixable, instead of
surfacing as a citation refusal on the installed system.

Resolve a refusal one way: regenerate the manifest from the book about to
ship (`scripts/build-wiki-page-manifest.py`), have the release key holder
re-sign it (`scripts/sign-with-gpg.sh`), land both in the tree, and let the
wiki source tarball regenerate. Never edit the manifest by hand — its value
is that the signature covers exactly the shipped bytes. A present book with
a failing signature, a wrong key, or any page drift refuses in every mode;
only a wholly absent wiki (a from-source dev image with no rendered book
staged) downgrades to a warning, and only under `UNSIGNED_TEST=1`.

## Gate — Build backend: "a recipe cannot supply the backend its pinned source demands"

**What it checks.** `preflight-build-backend.py` asserts that every recipe which builds a
Python source distribution with `pip --no-build-isolation` supplies the build backend that
source's own `[build-system]` table names. It runs at the END of `verify-sources`, after the
source SHAs are verified — it reads the staged sources themselves, and reading a build
backend out of bytes that have not been authenticated would be trusting an attacker's
`pyproject.toml`. The same call is repeated in the source-staging sweep a `--start-at`
resume takes, because a resume skips `verify-sources` entirely.

**Why it exists.** `--no-build-isolation` installs nothing for the build: whatever
`[build-system].requires` names must already be present. The `timm` recipe declared
`dependencies.build: [setuptools]` while its pinned 1.0.28 source declares
`requires = ["pdm-backend"]` with `build-backend = "pdm.backend"`. Setuptools cannot provide
`pdm.backend`, so the declared set could never have built the package — and because no check
read a recipe and its own pinned source together, the only way to find it was to spend a
build cycle failing on it.

**What "supply" means.** `dependencies.build` and `dependencies.host` are build-ORDER edges:
[`igos-build/graph.py`](../../igos-build/graph.py) unions them and topologically orders the
build. Declaring the backend is what guarantees it is installed before this package builds.
A recipe that omits it may still build today, but only because some other tier happened to
install the backend first — an assumption, not a guarantee. Ordering is transitive, so the
gate credits a recipe's whole transitive closure, exactly as the builder's own order does.

**Three shapes are satisfied without any declaration**, and they are passes rather than
exemptions: a source with no `pyproject.toml` or no `[build-system]` table (PEP 518 falls
back to the setuptools legacy backend, so setuptools in the closure satisfies it); a source
setting `backend-path`, whose backend ships inside the source tree; and a source declaring
an empty `requires` alongside a backend, which is the self-hosting bootstrap shape that
`setuptools`, `flit_core` and `hatchling` all use to build themselves.

**No module-to-distribution table.** A backend is named as a module (`pdm.backend`,
`mesonpy`) while a dependency is a distribution (`pdm-backend`, `meson-python`), and the two
are not derivable from each other. The gate carries no hand-maintained mapping — such a
table goes stale silently. It resolves the provider out of the source's own `requires` list,
which is where PEP 518 says the provider must appear: a single normalised name match, or a
single-entry `requires`. When neither identifies the provider, the gate does not guess — it
requires that *every* entry of `requires` be supplied, which is a superset of whatever
provides the backend. That is how `mesonpy` resolves with no table and no assumption.

**Symptom** (build log), naming the recipe, what it declared, and what the source demands:

```
  REFUSED  timm
    declared dependencies.build : ['setuptools']
    declared dependencies.host  : []
    the pinned source demands   : backend 'pdm.backend' requires 'pdm-backend', which is not supplied
    correction: add the missing distribution to dependencies.build in
    packages/*/timm/package.yml, so the build order guarantees it is installed
    before this package builds.
```

**Resolve** by adding the named distribution to `dependencies.build`. There is no
acknowledge/override path. If the distribution genuinely is not packaged, that is a missing
package, not a gate to suppress.

**"Could not determine" is a failure, not a skip** — the same posture as the tarball-
membership gate above, for the same reason: a build that reads as covered while nothing was
checked is worse than a build that stops. The exit codes separate the cases so the log says
which happened: `1` a recipe cannot supply its backend, `2` a verdict could not be
determined (unreadable source, unparseable `pyproject.toml`, unresolvable source filename),
`3` an in-class recipe's pinned source is not staged and so was not checked. Exit 3 cannot
occur in the pipeline, where `verify-sources` has already proven every pinned source is on
disk; it exists so that firing the gate by hand against a partially-staged tree names what
it could not read instead of reporting a clean sweep over half the population.

**Fire it by hand** on a targeted resume the same way as the other preflights:

```sh
python3 /mnt/intergenos/scripts/preflight-build-backend.py \
    --packages-dir /mnt/intergenos/packages \
    --sources-dir /mnt/intergenos/build/sources
```

Add `--verbose` to print each satisfied recipe with *how* it is satisfied, which is the fast
way to check that a shape you expected to be exempt is being exempted for the reason you
think.

---

## End-to-end checklist

1. Read the failing gate's output; identify the packages and the failure class.
2. **Audit-coverage** (missing/stale/drift): `audit-package.py <name> --save` for each → `aggregate-package-audits.py` → `preflight-audit-coverage.py` confirms PASS. Commit the JSONs to the audit store.
3. **Tier** (MOVE/UNCLEAR): evaluate against `docs/package-tiers.md`. Either move the package (dir + `tier:` + builder wiring + re-audit) **or** add the SSoT-encoding entry to `validate-package-tiers.py`. Re-run `validate-package-tiers.py` → `# total non-OK rows: 0`.
4. **Reconciliation mismatches**: triage each. Real gap → add the dep. Intentional → propose a divergence skip for operator authorization; once approved, add the `(name, anchor)` tuple, re-audit, re-aggregate. Confirm `mismatch` count is 0 in the new TSV.
5. **Source-tree coverage** (undeclared external read): add the external source root the
   `build.sh` reads to the package's `source_tree:`. Re-run `check-source-tree-coverage.py`.
6. Re-run locally until green:
   ```bash
   python3 scripts/preflight-audit-coverage.py         # PASS
   python3 scripts/validate-package-tiers.py | tail -2 # total non-OK rows: 0
   python3 scripts/check-source-tree-coverage.py       # PASS
   grep -c $'\tmismatch\t' build/audit-reconciliation-*.tsv | tail -1  # 0
   ```
7. Commit (in this repository: any `validate-package-tiers.py` / `audit-package.py` / `source_tree:` edits; in the audit store: the audit JSONs), push, then relaunch the build per [Topic 02](02-running-the-builder.md). No VM revert is needed — validate dies before the chroot is created.

## A note on packages changed after a successful build

When a from-scratch build has succeeded (a captured reference snapshot — see [Topic 07](07-golden-builder-snapshot.md) and the [GBC methodology](09-gbc-iteration-methodology.md)), any package **added or changed after** that build is where unrecorded-but-intentional decisions hide. The maturin/shadow divergences above sat as an un-triaged note for ~11 days precisely because they were correct-but-unrecorded. Before resolving a gate on such a package, check its history since the last good build (`git log --since=<snapshot-date> -- packages/<tier>/<name>/`) so you preserve, rather than silently undo, an intentional choice.

## Where everything lives

| Concern | Script | Reads | Writes |
|---------|--------|-------|--------|
| Per-package audit truth | `scripts/audit-package.py` | `package.yml`, source tarball, BLFS db | `<audit-store>/audits/per-package/<name>.json` |
| Ingest + reconcile | `scripts/aggregate-package-audits.py` | `audits/per-package/*.json` | `build/blfs-packages.db`, `build/audit-reconciliation-<ts>.tsv` |
| Audit-coverage gate | `scripts/preflight-audit-coverage.py` | `build/blfs-packages.db`, `package.yml` | (verdict) |
| Tier gate (Rule 1) | `scripts/validate-package-tiers.py` | `package.yml` tiers, its own category lists | (verdict) |
| Tier SSoT | `docs/package-tiers.md` | — | — |

## Related topics

- [08 — Adding a package](08-adding-packages.md) — the recipe-authoring side; these gates are what catch a half-wired addition.
- [02 — Running the builder](02-running-the-builder.md) — launching/relaunching after a fix.
- [09 — Release-candidate iteration methodology](09-gbc-iteration-methodology.md) — where a from-scratch build (and thus these gates) sits in the candidate lifecycle.
