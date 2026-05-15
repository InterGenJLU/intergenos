# InterGenOS Build Development Rulebook

**The first filter for every build-development decision.**

This rulebook gates **build decisions** — package authoring, chroot wiring, halt handling, deferral choices. It is consulted ahead of all other guidance:

1. **Project security alignment** — supreme directive (hardened-against-AI-class-attackers stance)
2. **PRIME DIRECTIVE** — supreme directive (user-control-first stance)
3. **This rulebook** — first operational filter
4. Conventions, individual feedback notes — backup detail

Read top-to-bottom **at every halt** and **at every multi-option proposal to the maintainer**.

If a rule and a memory entry conflict, this rulebook wins. The rulebook is the single source of truth for build-development discipline; pre-existing memory entries on these topics become one-line pointers to this file.

---

## Section 1 — The hard rules

Every rule has the same shape: the rule, the failure it prevents (with concrete example), the explicit STOP condition.

### Rule 1 — No retiering as a fix

**Rule.** Tier reflects what a package IS, not what's convenient to make the topo-sort happy. A missing build-dep, a silent-skip, or a stub build.sh is **NEVER** fixed by changing `tier:` in `package.yml`.

**Failure prevented.** Build #6: `pkm`, `btrfs-progs`, `lzo` were tier:core but not in any chroot-build script's run_package list. Retiering core→desktop|extra "made the build green" but produced semantically wrong manifests — pkm (THE package manager) and lzo (low-level compression) ended up tagged as optional user-facing apps. Real fix is wiring them into the right chroot-build script.

**STOP condition.** If you are about to edit `tier:` in a package.yml because a build halt or audit found it isn't being built — STOP. The fix is in `scripts/chroot-build-*.sh`, not in the package.yml. Ask the maintainer before touching tier.

---

### Rule 2 — No silent-skip leaves

**Rule.** Every package with `tier: X` MUST be reachable from `phase_X`'s build invocation, either by an explicit `run_package` call in `scripts/chroot-build-<phase>.sh` OR by topological closure from a request root that IS in that script.

**Failure prevented.** Build #6 audit: `efitools, gnu-efi, mokutil, sbsigntool, shim-signed, rpm` were all `tier: core` but had zero references in any chroot-build script and zero external consumers. Topo-sort couldn't see them — they're graph leaves with no inbound edges. They were silently never built. The bootloader was about to be signed against a rootfs missing the entire user-facing Secure Boot toolchain.

**STOP condition.** Authoring a new package or moving an existing one — if you cannot point to either (a) the `run_package` line that builds it or (b) the request-root package that pulls it in transitively, the package is not in the build. STOP and wire it before the next build run.

**Mechanical enforcement.** Pre-flight check (Rule 17) makes this halt-on-detect.

---

### Rule 3 — No disabling features to bypass missing deps

**Rule.** A missing dependency is NEVER fixed with `-Dfoo=false`, `--disable-X`, or removing a feature flag. Missing dep means: stop, identify the dep, package it, wire it.

**Failure prevented.** Recurring class. The temptation is to make the configure error go away by disabling the feature that needs the missing lib. That ships a silently-degraded ISO — direct PRIME DIRECTIVE violation ("nothing degraded unexplainedly").

**STOP condition.** You are about to add `-D<something>=false` or `--disable-<feature>` to a package's configure flags because configure fails without it — STOP. Identify what's missing, propose adding it.

---

### Rule 4 — No pattern-match disables

**Rule.** "N other packages already do X" is NEVER authorization to do X. Read the policy. If no policy exists, stop and propose one.

**Failure prevented.** Recurring: a partial precedent gets generalized into a rule that wasn't actually approved. Tests-disabled, build-flag-disabled, dependency-removed — each one is supposed to be a per-finding decision. Pattern-matching across packages skips the decision step.

**STOP condition.** You catch yourself thinking "well, package X already does this, so it's fine for package Y" — STOP. Each case stands on its own.

---

### Rule 5 — Vendor tarballs must be extracted explicitly

**Rule.** When `package.yml` declares multiple `source:` entries, the orchestrator's auto-extract handles `source[0]` only. Every secondary tarball requires an explicit `tar xf "${IGOS_SOURCES}/<name>.tar.gz"` in `configure()` BEFORE the build system touches the tree.

**Failure prevented.** Build #6 hit this 4 times (apparmor, go-md2man, aardvark-dns, netavark, plus transmission's bundled libs). Symptom is configure or build phase failing in 0.x seconds with `cannot find module providing package` (Go) or `failed to read root of directory source: vendor` (Cargo) or missing header file (C/C++ bundled lib).

**STOP condition.** Authoring or reviewing a package with `source: [..., vendor.tar.gz]` — verify `configure()` extracts every entry beyond source[0]. If it doesn't, that's the bug.

---

### Rule 6 — Three retries triggers FULL HALT — surface to maintainer, never defer

**Rule.** If a package build has been retried `>= 3` times — regardless of whether the errors are the same or different across retries — the build is in **FULL HALT** state. The following MUST happen immediately, in order, before anything else:

1. **The error is noted with full context** — package name, phase, every distinct error from each retry, what was changed between retries, what's still unknown.
2. **All necessary research is conducted** — read the build log in full, check the LFS/BLFS book, check `build_003`, dispatch an Explore subagent if the cause isn't clear, identify the necessary correction.
3. **ONLY AFTER 1 and 2 are complete** — surface the issue to the maintainer and **REQUEST permission** to implement the proposed correction.

**There is NEVER "defer and move on." Deferral is not a default, not a fallback, not a tool in the toolbox.** The maintainer decides every time.

**You MUST NEVER continue past a FULL HALT without explicit maintainer PERMISSION.** Not a 4th retry, not a workaround, not a different package, not a "while we're waiting let me try X." Stop and wait.

**Failure prevented.** Self-directed scope reduction. Build #6 transmission was deferred at retry 5 per a "rule" that was itself a self-directed shortcut — the maintainer never approved deferral as a build-development practice. The correct path was FULL HALT at retry 3, surface, request permission. The maintainer may choose deferral, may choose a sixth-retry attempt with a specific approach, may choose to switch generators or repackage upstream deps as standalone system libs — but it is the maintainer's call, not the build agent's.

**STOP condition.** A package has been retried 3 or more times — STOP. No further attempts of any kind. Note → research → surface → request permission. Wait.

**Implication for outstanding deferrals.** Any package currently marked `package.yml.deferred` that was deferred without explicit maintainer permission must be re-surfaced for the maintainer's decision. This includes `packages/extra/transmission/package.yml.deferred` (44335af).

---

### Rule 7 — Stop, examine, identify, correct, proceed

**Rule.** No knee-jerk fixes. Read the actual error message. Read the build log around it. Check the LFS book and `build_003`. THEN propose.

**Failure prevented.** Acting on the first plausible reading of an error wastes commits and obscures real causes.

**STOP condition.** You catch yourself writing a fix before you've actually read the failing log line and surrounding context — STOP. Read first.

---

### Rule 8 — Research first for hard-to-reverse decisions AND obstacles

**Rule.** Anything affecting toolchain order, package layout, signing chain, or build-graph shape gets background research BEFORE proposal. Obstacles get the same treatment — don't act on the first hypothesis.

**Failure prevented.** Acting on guesses then writing follow-up commits to undo them. Wastes time, dirties git history.

**STOP condition.** Multi-step or hard-to-undo change on the table — STOP and research first. For obstacles, dispatch an Explore subagent if the cause isn't obvious from the immediate log.

---

### Rule 9 — Audit ALL consumers on cross-package changes

**Rule.** Renaming a package, moving a package between tiers, changing a public function signature, retiring a build flag — every such change requires a `git grep` over the entire repo, not just the obvious dispatch checklist.

**Failure prevented.** RFC v1 missed 3 consumers; cost was hours of build retries.

**STOP condition.** About to commit a cross-package change and you have not run `git grep` for the symbol/name across the repo — STOP. Run it first.

---

### Rule 10 — `tests:enabled:false` is the LAST resort, not the first

**Rule.** When tests fail in chroot:
1. **First**: investigate whether the failure is environmental (no kernel, no loop devices, no FIDO2 hw, no TPM2 simulator, locale/FHS, root-bypass via CAP_DAC_OVERRIDE). If yes → `failure_policy: known_failures` with a reason field. Tests still run; failures are tagged as expected.
2. **Only if** the test infrastructure itself is hazardous (leaves loop mounts, requires ginkgo, runs live container ops) → `tests:enabled:false`.
3. NEVER: disable tests because they're flaky or to make the build green faster.

**Failure prevented.** Cumulative coverage loss without record. `known_failures` is auditable; `tests:enabled:false` is opaque.

**STOP condition.** You are about to add `tests:\n  enabled: false` to a package.yml — STOP. Run through the ladder. Default to `known_failures` with a reason, escalate to `enabled:false` only when there's a cleanup or infrastructure hazard. Document why in-file.

---

### Rule 11 — Stub build.sh is a halt-class bug

**Rule.** A `configure()` / `build()` / `do_install()` that's a no-op (`:`) when the package's purpose is to compile something is **not "ok for now"**. It's a silent ship of a missing component.

**Failure prevented.** Build #6 apparmor: stub build.sh from a "profile-only" earlier era survived into a build where systemd-pass2 declared libapparmor as a dep. libapparmor.so was never compiled. Halt #8.

**STOP condition.** Reviewing a build.sh whose phases are no-ops — STOP. Either the package is a metapackage (declare it explicitly) or the build.sh is incomplete.

---

### Rule 12 — Latest stable versions, unless a known issue dictates otherwise

**Rule.** Default to upstream's latest stable release. Pin to an earlier version ONLY when a documented build issue requires it; record the reason in `package.yml` as a comment.

**Failure prevented.** Drift into stale unsupported versions; missing security fixes.

**STOP condition.** Pinning to a non-latest version without a comment explaining why — STOP. Add the reason or update the version.

---

### Rule 13 — LFS exact compliance for the core build path

**Rule.** Toolchain (Ch. 5-7) and core LFS chapter 8 follow the LFS book **exactly**. No "improvements," no version skips, no sequence changes without explicit maintainer approval.

**Failure prevented.** Toolchain divergence cascades unpredictably.

**STOP condition.** Tempted to deviate from LFS for the core path — STOP and propose. The LFS book is local; cite it.

---

### Rule 14 — No correctness-vs-speed tradeoffs

**Rule.** When the PRIME DIRECTIVE picks one option, the menu does NOT include "faster but wrong." Don't frame proposals in terms of speed.

**Failure prevented.** Recurring offense. Speed-framing creates a false equivalence between a correct slower path and an incorrect faster one.

**STOP condition.** A proposal mentions "fastest", "shortest", "unblocks fastest", or any speed framing — STOP. Reframe in trust/correctness terms or remove the framing.

---

### Rule 15 — Every authored package ships a man page

**Rule.** Anything we author (`pkm`, `forge`, `igos-build`, etc.) ships a man page in section 1 (commands), 5 (file formats), or 8 (admin tools) as appropriate.

**Failure prevented.** Tools without docs are tools without users.

**STOP condition.** Committing a new authored tool without a man page — STOP. Write it first.

---

### Rule 16 — Always run with `--checkpoint`

**Rule.** Every build invocation passes `--checkpoint`. Never run without one.

**Failure prevented.** Loss of progress on long-running builds when something halts.

**STOP condition.** About to invoke `build-intergenos.sh` without `--checkpoint` — STOP. Add it.

---

### Rule 17 — Pre-flight tier-coverage check is mandatory and code-enforced

**Rule.** A `phase_validate` step runs BEFORE `phase_setup` on every build. It walks `packages/*/*/package.yml`, collects every tier declaration, and asserts that every package is reachable from its phase's build invocation. Halt with package list if any are unreachable.

**Failure prevented.** Silent-skip leaves (Rule 2). This is the mechanical guard that prevents the manual discipline from drifting.

**STOP condition.** Build run that DOESN'T include phase_validate, or a phase_validate that's been bypassed — STOP. Restore it before the build proceeds.

**Implementation.** `scripts/preflight-tier-coverage.py` (to be written): exit 0 if all tier-declared packages are reachable; exit 1 with the orphan list otherwise. Wired as the first phase in `build-intergenos.sh`'s PHASES array.

---

### Rule 18 — Manifest reconciliation at end-of-build

**Rule.** After `phase_image`, the build emits `build/manifest-reconciliation-<ts>.txt` comparing:
- The set of packages declared in `packages/*/*/package.yml`
- The set of packages installed in the chroot's `/var/lib/igos/packages/`
- Any deferred (`*.deferred`) entries

Halt with diff if the YAML-promise set ≠ chroot-reality set ∪ deferred-set.

**Failure prevented.** Authoring/wiring drift that pre-flight (Rule 17) didn't catch — e.g., a package whose build silently produced empty `do_install()`.

**STOP condition.** Build completes, manifest reconciliation reports diffs not accounted for by `*.deferred` — STOP. Investigate every entry on the diff before signing.

---

### Rule 19 — Bulk audit-recommended hardening is forbidden

**Rule.** `set -euo pipefail`, `subprocess check=True`, broad-except cleanup, and similar "general hardening" recommendations from audits or subagents get **per-finding review only**. Bulk-applying them has broken builds.

**Failure prevented.** Owner-direct 2026-05-08 — these patterns have caused real regressions. Each finding is its own decision.

**STOP condition.** Audit returns N similar findings and you're about to apply them as a batch — STOP. One at a time, each justified.

---

### Rule 20 — Every package declares its load-bearing files via `verify_paths:`

**Rule.** Every `packages/<tier>/<name>/package.yml` declares a `verify_paths:` field listing 2-3 load-bearing files the package produces on disk. The pre-squashfs audit (`scripts/pre-squashfs-audit.py`, run from `scripts/build-squashfs.sh` step 4.5) checks each declared path exists on the chroot; missing paths halt the squashfs build.

```yaml
# packages/core/bzip2/package.yml
name: bzip2
version: "1.0.8"
# ... rest of the package definition ...
verify_paths:
  - /usr/bin/bzip2
  - /usr/bin/bunzip2
  - /usr/lib/libbz2.so
```

**Exemption.** Deliberately-deferred packages (waiting on an upstream that hasn't shipped yet — e.g., `shim-signed` pending Microsoft UEFI CA sponsorship) declare `pending_acquisition: "<reason>"` instead. The audit treats those as known-not-installed and skips them.

**Fallback.** If `verify_paths:` is absent, the audit falls back to an `auto-verify-paths.json` sidecar that the builder emits automatically from the build-time filesystem snapshot (see `igos-build/verify_paths_derive.py`). The sidecar is a build-host fallback; the package.yml field is the human-curated source of truth.

**Failure prevented.** The linux-firmware-class regression — a package recipe present in the tree, the build orchestrator reporting success, but the install function silently produced zero files (e.g., `phase_core` bash scripts don't include linux-firmware → entire amdgpu firmware tree absent → DS-v2 GPU init fails at boot). With this rule, the audit halts at squashfs time with `linux-firmware: MISSING /usr/lib/firmware/amdgpu/carrizo_sdma.bin` instead of letting the broken ISO ship.

**Authoring guidance — picking verify_paths.** Pick 2-3 paths that prove the package landed:

1. **Primary binary** at `/usr/bin/<name>` or `/usr/sbin/<name>` — strongest identity signal.
2. **Primary library** at `/usr/lib/lib<name>.so*` — for lib-only packages.
3. **Canonical directory** at `/usr/share/<name>/`, `/usr/lib/<name>/`, `/etc/<name>/`, or `/usr/lib/firmware/<...>/` — for data/firmware/config packages.
4. For Perl/Python module packages, use the site_perl / site-packages path.
5. For the kernel, declare `/boot/vmlinuz-<version>` + `/usr/lib/modules/<version>`.

Each path must start with `/` and have ≥3 segments (e.g., `/usr/bin/x`). Reject descriptive single-word entries that aren't actual filenames.

**Enforced by.** Pre-push hook gate 8 (`.githooks/pre-push`) refuses to push a *new* `package.yml` file without either `verify_paths:` or `pending_acquisition:`. Existing missing-verify_paths packages (the historical backlog) are tracked separately; the gate stops the bleeding for net-new packages.

**STOP condition.** Pre-squashfs audit reports MISSING paths — STOP. Two possible causes: (a) the package wasn't actually built/installed (regression — investigate); (b) the declared verify_paths are wrong (correct the field). Don't ship the build.

---

### Rule 21 — No stubs

> *"We don't want no stubs; a stub is a lie that we'll decode now to see."*
> — Owner, 2026-05-15 (riffing on TLC, 1999)

**Rule.** A **stub** is any code, comment, service file, config, or path-reference that *claims* a feature/file/component exists or works without actually delivering it. Stubs are forbidden across the entire codebase, not just `build.sh`. Rule 11 covers `build.sh` stubs specifically; this rule generalizes.

Concrete stub patterns to reject:

1. **Aspirational path references** — a comment, ExecStart, ConditionPathExists, autostart Exec, polkit rule, or any code-string referencing a file/binary path that nothing in the tree produces. (Tonight's example: init.sh referenced `/usr/lib/systemd/system-generators/igos-mode-generator` — no such generator exists anywhere.)
2. **"Almost-empty" install_func / do_install** — covered by Rule 11 for build.sh; same pattern in other scripted installs (post_install hooks, autostart writers, etc.).
3. **Service files referencing nonexistent binaries** — `forge-tui.service` would have been a stub if `/usr/bin/forge` weren't in tree; this is exactly the kind of bug that surfaced tonight before being fixed.
4. **Documentation claims without backing** — comments / READMEs / CLAUDE.md statements like "X is wired up to Y" when no wiring code exists. Especially common in dispatched-AI-authored chunks.
5. **Missing verify_paths on a new package** — gated by Rule 20 + pre-push hook gate 8. A package without verify_paths is a stub in shape: it claims to produce files but doesn't say which.

**Why "a stub is a lie."** Stubs break the trust contract between intent and reality. Future readers (humans OR audit tools) act on the assumption that a referenced path exists or a documented behavior works. When it doesn't, downstream work is built on quicksand and the regression surfaces far from the original lie (tonight's amdgpu failure on DS-v2 → traced back to linux-firmware never being built → traced back to phase_core bash script that doesn't include it → a stub-shape gap in the orchestrator's coverage).

**Detection.** Three layers:

- **Pre-squashfs audit** (Rule 20) catches stubs whose lie is "this file gets installed" — verifies on the chroot.
- **Code-stub audit** (`scripts/check-aspirational-stubs.py`, scheduled) — greps init.sh / *.service / *.desktop / tmpfiles.d / sysusers.d / polkit rules / dbus configs for path references, cross-checks each against the packages-yml-derived install manifests.
- **AI-dispatch verification protocol** — every dispatched integration claim must include a verification step proving the claim is real (file lands on disk, command resolves, service activates). No claim-without-evidence merges.

**Failure prevented.** Two tonight: (1) linux-firmware silently dropped from chroot, GPU init failed on DS-v2 hardware. (2) init.sh comment about `igos-mode-generator` that doesn't exist — discovered during install-gui debugging, would have caused continued aspirational drift if not surfaced.

**STOP condition.** You catch yourself writing a comment, service file, or doc that references a path/binary/feature you haven't verified exists in the tree — STOP. Verify first. If the thing should exist but doesn't, file it as work-to-do, don't reference it as if it's done.

**Honoring the source material.** Owner's rule statement is preserved verbatim with attribution because the comedic framing IS load-bearing — "a stub is a lie" is the shortest possible mnemonic for the trust-violation this rule prevents. Future readers should feel the weight.

---

## Section 2 — Halt-handler decision tree

A halt fired. Before doing anything else:

**Step 0 — Read.** Read the actual error message. Read the surrounding 30 lines of log. Note the package name and phase.

**Step 1 — Classify.**

| Symptom | Class | Canonical fix | Forbidden workaround |
|---|---|---|---|
| `command -v X` returns nothing; `X.h: No such file`; `cannot find module providing package`; package never appears in build logs | **Silent-skip** (Rule 2) | Wire into the right `chroot-build-<phase>.sh`'s `run_package` list | Retiering (Rule 1) |
| `configure()` is `:` or only does file-drops; `do_install()` produces nothing meaningful | **Stub** (Rule 11) | Rewrite to actually compile | "Mark as metapackage" without verifying it really is one |
| Multi-source pkg, build/configure halts in <5s with module/vendor lookup failure | **Missing extract** (Rule 5) | Add `tar xf "${IGOS_SOURCES}/<name>.tar.gz"` in `configure()` | Disabling vendor mode (Rule 3) |
| Test fails but library/binary built fine; failure depends on env (locale, FHS, hw, kernel, root) | **Chroot-environmental** (Rule 10) | `failure_policy: known_failures` with reason | `tests:enabled:false` as default |
| Test infra leaves loop mounts; needs ginkgo/live runtime; cleanup hazardous | **Test-infra hazard** (Rule 10) | `tests:enabled:false` with reason | `failure_policy: known_failures` (cleanup still hazardous) |
| Upstream CMake option enforcement (`Invalid value "on"`); alias-of-alias; missing source aliasing | **Upstream build-system bug** | Patch in `configure()` mirroring existing pattern | Disabling the feature (Rule 3) |
| Package has been retried `>= 3` times (any errors, same or different) | **FULL HALT** (Rule 6) | Note error + context → research → surface to maintainer → REQUEST permission. Wait. | 4th retry, deferral, workaround, switching to a different package "while we wait" |
| Required by another package as a build-dep but not in build | **Missing build-dep** | Verify dep declaration; verify dep itself is reachable (cascade case); fix root cause | Disabling the consumer's feature flag (Rule 3) |

**Step 2 — Propose, don't act.** Bring the classification + canonical fix to the maintainer per RULE #0 (PROPOSE → WAIT → PERMISSION → CHANGE) unless the change is small, reversible, and matches an existing canonical fix exactly. When in doubt, propose.

**Step 3 — Implement, document the why in the commit message.**

---

## Section 3 — Forbidden-action register

These are the workarounds that *look tempting* during a halt. They are out-of-bounds:

| Forbidden action | Rule | Recurrence count in Build #6 |
|---|---|---|
| Edit `tier:` in package.yml to "fix" a wiring/silent-skip issue | Rule 1 | 3 (pkm, btrfs-progs, lzo) |
| Add `-D<feature>=false` to make a missing dep go away | Rule 3 | 0 (caught at proposal) |
| `tests:enabled:false` as first response to a test failure | Rule 10 | 2 (btrfs-progs, podman — second was justified by infra hazard) |
| Stub `configure() { :; }` for a package that has real code to compile | Rule 11 | 1 (apparmor pre-fix) |
| 4th retry on the same package, OR deferral without explicit maintainer permission, OR any continuation past a FULL HALT | Rule 6 | 1 (transmission was retried 5x then auto-deferred — both violations of the corrected rule) |
| Bulk-apply audit-recommended hardening | Rule 19 | 0 (rule pre-existed) |
| Pin a non-latest version without a comment | Rule 12 | 0 |
| Build invocation without `--checkpoint` | Rule 16 | 0 |
| Skipping pre-flight tier-coverage check | Rule 17 | N/A — rule new |
| Skipping manifest reconciliation | Rule 18 | N/A — rule new |
| Skipping pre-squashfs `verify_paths` audit, or shipping a package.yml without `verify_paths:` (or `pending_acquisition:`) | Rule 20 | 1 (linux-firmware → DS-v2 amdgpu break, 2026-05-15) |
| Writing comments / services / docs that reference paths or features that don't exist in tree (aspirational stubs) | Rule 21 | 2 tonight (linux-firmware miss; init.sh `igos-mode-generator` reference) |

---

## Section 4 — When in doubt

STOP. Ask the maintainer.

The cost of pausing to ask is low. The cost of a "fast" workaround that ships a degraded ISO is the credibility of the entire project.

The maintainer's standing direction at session-start of every build session: **"bootable, working, secureboot capable ISO, all expected functionality present, nothing degraded unexplainedly."** Every rule here exists to keep that promise.

