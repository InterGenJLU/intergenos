# 09 — The cost of deferring things we find missing, broken, or unfinished

**Audience:** maintainers and reviewers tempted to track an issue for "later" instead of fixing it during the current arc.

## Goal

This is a case-study document, not a runbook. It tallies the operational cost of deferred work in this project so the trade-off "fix now vs track and move on" can be made with eyes open. The conclusion at the bottom isn't a rule per se; it's a calibration.

The pattern under examination: someone notices a regression, a stub, a missing-but-claimed feature, an unfinished refactor. The temptation is to capture it as a tracker entry, ticket, or `TODO.md` line and move on. The cost-of-deferral case studies below show what happens when that temptation wins.

Each case study cites the canonical incident name + a one-line description; operational details live in the project's archived memory and incident notes.

## Case studies

### Case 1 — The TLS-trust verification bug (9 days lost)

**Symptom that surfaced:** signed pkm repository fetches were accepting forged signatures during a multi-day window in mid-build #5. The verifier was using OpenSSL's `X509_verify_cert` with lenient defaults that treated a self-signed cert in the chain as trusted-by-presence, defeating the chain-of-trust property the repo signing flow exists to provide.

**When deferral happened:** during the original implementation of the verifier, a reviewer noted the `X509_V_FLAG_PARTIAL_CHAIN` flag's exact semantics weren't load-bearing for the happy-path test case and the strict-vs-lenient question was tracked as "follow-up" instead of resolved.

**Cost:** nine calendar days between the bug landing and being surfaced. During that window any malicious actor with a forged sig could have substituted a poisoned package and the verifier would have approved. We didn't have evidence of exploitation but we also didn't have evidence of NON-exploitation. The remediation included a full audit of every signature-verifying call site in the codebase, retroactive verification of every signed-repo state-transition that had happened during the window, and a hardening of the verifier with explicit flag-set assertions.

**Lesson:** "this verifier flag's exact semantics" is exactly the kind of question that LOOKS safe to defer but is load-bearing for the trust contract. The operational note covering the OpenSSL-verify-self-signed-lenient incident is the canonical reference for this class.

### Case 2 — `objcopy --dump-section` mutates its input (~12 hours)

**Symptom that surfaced:** a UKI sign step was producing PE binaries that failed strict UEFI loader verification. The size header (`SizeOfImage`) was diverging from the actual section layout by a few bytes — enough that some firmware implementations (Ubuntu's OVMF) rejected the binary with `EFI_LOAD_ERROR` while others (older AMI firmwares) accepted it. The root cause was traced to `objcopy --dump-section` being used to extract a section for inspection during the sign step — and the `--dump-section` operation silently rewrites the input binary's section table even though the documented intent is a read-only dump.

**When deferral happened:** the unintended-mutation behavior had been noted by a senior contributor in a comment thread on an unrelated PR ("objcopy is more side-effectful than its docs suggest, worth investigating") and tracked as a research item. No one followed up.

**Cost:** twelve hours of debugging across multiple test boots before the mutation behavior was identified as the root cause; the sign step had to be re-architected to use `ukify` (which produces correct `SizeOfImage` by construction) rather than the raw objcopy chain.

**Lesson:** "X is more side-effectful than its docs suggest" is a high-signal early-warning that gets cheap to investigate (a one-hour spike) and expensive to defer (a multi-day debug arc when the side-effect surfaces in production). The operational note covering objcopy-dump-section-mutates-input captures this class.

### Case 3 — The chroot-rsync coverage gap (one shipped ISO with stale init.sh)

**Symptom that surfaced:** Build #3's ISO booted into a live session but the live-session shutdown was missing a feature documented in the commit message. Investigation revealed the chroot at `/mnt/igos/mnt/intergenos/installer/init/init.sh` was older than the host clone's `installer/init/init.sh` by several commits — the orchestrator's chroot-sync step was using rsync with a too-narrow include pattern and silently missing `installer/init/` updates.

**When deferral happened:** the rsync coverage was originally implemented to sync `scripts/` and `packages/`; `installer/` was added later but the rsync include pattern wasn't updated. The discrepancy was noted as a tracker item.

**Cost:** one shipped ISO with stale init.sh that didn't match the source tree. Investigation cost ~6 hours. Beyond the time cost: the discovery undermined the trust contract that "the source tree IS what shipped" — if init.sh can drift silently, what else can? Subsequent reviews had to audit every chroot-staged path against its source-tree counterpart.

**Lesson:** "sync pattern needs to be extended to cover the new directory" tracked-and-deferred turns into "we shipped a binary that doesn't match the source." The operational note covering the chroot-rsync-coverage-gap captures the canonical lesson.

### Case 4 — Greedy-glob silent-skip (`base/at` never built)

**Symptom that surfaced:** the pre-squashfs verify_paths audit (Rule 20 enforcement) halted with `at: MISSING /usr/bin/at` — the `at` daemon binary wasn't installed despite the package recipe existing at `packages/base/at/`. Investigation revealed the bash builder's `run_package "at-*"` glob was greedily matching `at-spi2-core` (a desktop-tier package whose name happens to start with `at-`) and leaving `base/at` un-built.

**When deferral happened:** the glob-vs-exact-match question had come up during an earlier code review of a chroot-build-*.sh edit; the reviewer noted "globs in run_package are a trap waiting to spring" and the issue was tracked for a follow-up cleanup pass.

**Cost:** the greedy-glob behavior didn't surface as a failure until verify_paths was implemented and audited the chroot for declared paths. Without verify_paths, the broken state could have shipped — the live ISO would have booted but `at` jobs would have failed at runtime with "command not found." Hours of debugging averted by the audit catching it pre-squashfs.

**Lesson:** "this is a trap" tracked-and-deferred turned into a regression that was only caught because an unrelated guard rail (Rule 20 audit) happened to be in place. Without that audit, the regression would have shipped to users. The canonical fix shipped at master tip `86109772` — exact-version match form for `run_package`.

### Case 5 — Forge tarball stale-snapshot (caught at the audit)

**Symptom that surfaced:** `packages/desktop/forge/package.yml` referenced `forge-1.0.0.tar.xz` with a declared sha256 but no script in the tree generated the tarball. The tarball had been hand-curated at some earlier time and snapshot-pinned by sha; subsequent edits to `installer/` (which the tarball is meant to bundle) didn't propagate to the chroot because the tarball wasn't being regenerated.

**When deferral happened:** the original forge-packaging commit chose to hand-curate the tarball as a "v1 simplicity" choice, with the regenerator-script tracked as a follow-up. The follow-up never landed until the stub-hunt audit surfaced the gap.

**Cost:** every edit to `installer/data/forge-tui.service`, `installer/init/init.sh` (sometimes), and `installer/frontend/*.py` was silently NOT reaching the chroot via the forge package. Multiple cycles where a change "should be in the build" but wasn't, traced (after substantial confusion) back to the stale tarball. Resolution required authoring `scripts/build-forge-tarball.sh` (landed at master tip `067ecf6d`) and wiring it into `phase_setup` so every build regenerates the tarball from in-tree state.

**Lesson:** "we'll regenerate the tarball later" is the deferral that silently kept changes from reaching the chroot, manifesting as "did this code path actually run?" debug arcs whose root cause was upstream of the code path itself. The fix wasn't hard once identified — the deferral cost was the multi-cycle confusion, not the eventual implementation.

## Pattern across the case studies

In every case:

1. **Someone noticed the issue at write-time.** A reviewer, a co-author, the original implementer themselves — the gap was visible. The decision to defer was conscious.
2. **The deferral was reasonable in isolation.** Each individual "let's track this and move on" was defensible against the immediate priority pressure. None of the deferrals were lazy in the moment.
3. **The cost compounded.** The deferred work didn't sit politely in the tracker. It interacted with downstream changes, masked the root cause of unrelated debugging, and surfaced as production-class failures rather than build-time errors.
4. **The fix was always cheaper than the cost.** The exact-version match in case 4 was a one-line change. The build-forge-tarball.sh script in case 5 was ~100 lines. The verifier flag audit in case 1 was a focused session. The objcopy → ukify migration in case 2 was a half-day refactor. In every case, the "expensive to fix now" framing that drove the deferral was wrong about the actual cost.

## Calibration

This document doesn't argue for never-defer. It argues for the heuristic that defaults should be **fix-now** when:

- The gap is in a trust-bearing surface (signatures, build-time integrity, chroot-staging-vs-source consistency).
- The gap is in a build-orchestration step where downstream surfaces will be silently wrong (greedy globs, hand-curated artifacts, sync pattern coverage).
- The cost-now estimate is "a few hours" rather than "a multi-day refactor."

When defer is the right call, document the deferral with an expiry condition and a measurement plan, not just a tracker entry: *"defer until X event, surface for re-decision then, measure Y in the meantime to confirm the deferral isn't compounding."* Tracker entries without expiry conditions are the shape that drifts.

The standing rule shipping in `docs/build-development-rulebook.md`: when in doubt, STOP and ask the maintainer. The cost of pausing to ask is low. The cost of a "fast" workaround that ships a degraded artifact is the credibility of the project.

## Cross-references

- `docs/build-development-rulebook.md` — Section 2 halt-handler decision tree; Section 3 forbidden-action register
- Topic 10: Recommendations / what's missing — the synthesis layer on top of these case studies
- Internal operational notes referenced above (by incident name, not file path) — read the project's archived memory for verbatim text and dated commit references
