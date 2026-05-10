# Audit Review — §2 Installer + §6 Docs/Config/Gates

**Reviewer:** InterGenOS bare-metal contributor
**Date:** 2026-05-07 ~15:30Z UTC
**Master:** 6750fc1
**Lens:** bare-metal install + ship-gate
**Sections reviewed:** §2 (Installer, 12 findings), §6 (Docs/config/gates, 6 findings) — 18 total

---

## §2 Installer Review

### Findings verdicts

- **I1 (MEDIUM, packages.py:134-159)**: **PRIORITIZE** — confirmed accurate. `db = PackageDB(...)` opened on 134, used in loop 149-157, closed on 159 with no try/finally. `installer.install()` raising mid-loop leaks the SQLite handle. Fix matches stated convention used elsewhere in the same module group (e.g., `users.set_root_password` already uses `mount/try/finally/unmount`); making PackageDB a context manager is the cleaner refactor and lets the `with PackageDB(...) as db:` shape match install-time mount/unmount discipline.
- **I2 (MEDIUM, hooks.py:20-22)**: **PRIORITIZE** — confirmed accurate. `mount_virtual_fs` runs 5 mounts with `check=True` and zero rollback. The orchestrator's `_PHASES_NEEDING_VIRTFS_UNMOUNT` covers the case where `mount_virtual_fs` *finishes* and a later phase raises, but a partial mount (e.g., #3 fails) escapes BEFORE the caller's `try:` is entered (e.g., `users.set_root_password:12-19` calls `mount_virtual_fs` outside its try block — finally won't run). Real bare-metal failure mode on hardware where /proc or /sys mount races udev. Fix: track completed mounts inside the function and unmount-in-reverse on failure.
- **I3 (MEDIUM, hooks.py:29-31)**: **PRIORITIZE** — confirmed accurate. `unmount_virtual_fs` calls umount without checking returncode; `capture_output=True` swallows stderr. Silent umount-busy → "already mounted" on retry. Symmetric with I2; fix together as a mount-lifecycle hardening commit.
- **I4 (LOW, install.py:404-412)**: **PRIORITIZE** — bare `except Exception: pass` on the audit-log copy is the wrong shape for a trust-trail primitive. The audit log is the post-incident forensic substrate; silent loss of the copy step (disk full / permissions / target unmounted) is an anti-supply-chain observability hit. Auditor's proposed fix (`_emit warning`) is the minimum; consider also surfacing on `InstallResult.warnings` so frontends can render to the user explicitly. Severity could arguably be MEDIUM given the trust-chain role.
- **I5 (LOW, integrity.py:159-177)**: **DEFER** — auditor's proposed fix (Optional[str] return) improves diagnostics but doesn't change security outcome. Current behavior is fail-closed (any error → False → orchestrator halts install with "manifest signature verification failed"), which is the correct conservative posture for the trust chain. Diagnostic improvement is real but post-v1.0 polish; install-media-with-broken-gpg is a fundamental media problem that re-write-from-trusted-source already remediates.
- **I6 (LOW, config.py:39-55)**: **PRIORITIZE** — `generate_hostname` writes hostname directly into `/etc/hosts` with no validation. `validate_install_inputs` (install.py:164-194) only checks PRESENCE of `cfg["hostname"]`, not contents. A yaml file with `hostname: "foo\n0.0.0.0 evil.com"` survives validation and writes additional /etc/hosts lines. Real injection vector at the install boundary even if frontends sanitize. Severity should arguably be MEDIUM — yaml-driven install is a writable-by-attacker surface (e.g., custom kickstart scenarios). Defense-in-depth fix at backend matters even when frontends validate.
- **I7 (LOW, disks.py:57-63)**: **PRIORITIZE** — no timeout on lsblk. Bare-metal hangs on edge hardware (broken NFS mount, half-detected USB-stuck-IO) wedge the installer at disk-detection with no progress signal. Auditor's `timeout=30` is the right shape; pair with similar audit on other subprocess.run calls in disks.py (e.g., `blkid` at line 175, `ntfsresize` at 211, `partprobe` via `_run`).
- **I8 (LOW, hooks.py:148-151)**: **PRIORITIZE** — `cp -a` packages-into-target without returncode check. Subsequent chroot loops fail silently per-hook; `executed` count drifts from intent. Real bare-metal failure mode if target disk fills mid-install. Pair with I2/I3 in mount-lifecycle hardening commit.
- **I9 (LOW, install.py:393-399)**: **PRIORITIZE** — code-design mismatch is real. The comment at line 391 explicitly states "MOK enrollment last — failure here leaves system bootable; user can re-enroll via mokutil from running install if needed" but the code path lets `mok.queue_mok_enrollment` raise into the outer `except Exception` at line 419 → install reported FAILED. User reformats a system that was actually installable. Fix per auditor: catch+surface-as-warning + complete the install with explicit guidance in the warning. UX impact is high — user perception of failure on success.
- **I10 (LOW, users.py:50-57)**: **PRIORITIZE** — sudoers string-replace is brittle to comment formatting changes. /etc/sudoers shipped by InterGenOS's sudo package today has the canonical `# %wheel ALL=(ALL:ALL) ALL` shape, but a sudo-package update or local edit could change spacing/format and silently disable wheel-group sudo. High-stakes outcome (user locks themselves out of sudo with no error). Auditor's regex-sed alternative is the right shape.
- **I11 (LOW, mok.py:79-88)**: **DEFER** — auditor explicitly notes "this is NOT a security issue"; the regex `_COMMON_NAME_RE` at line 41 (`^[A-Za-z0-9 _.\-]{1,64}$`) already rejects every shell metacharacter. `shlex.quote` on a pre-regex-validated string is a no-op. Defense-in-depth value is theoretical and a future regex weakening would be caught in code review. Polish, not v1.0-blocking.
- **I12 (LOW, __main__.py:48)**: **DEFER** — `(FileNotFoundError, PermissionError)` covers /proc/cmdline missing or unreadable, which are the realistic failure modes on bare-metal install. EIO on /proc is a container-environment edge case that doesn't apply to Forge's install context (Forge runs against `/proc/1/root`, never inside a container). Auditor's one-word fix (add OSError) is harmless but the realistic blast radius is near-zero.

### Cross-cutting concerns SPOC may have missed

- **Mount-lifecycle hardening as a coherent fix** — I2 + I3 + I8 all touch `hooks.py` mount/unmount lifecycle. A single fix-wave commit refactoring `mount_virtual_fs`/`unmount_virtual_fs` into a context-manager (`with virtual_fs(target): ...`) plus returncode-checked unmount + cp-with-check at line 148 closes all three with consistent error handling and matches the convention already present in `users.py` and `mok.py`. Worth packaging as one commit, not three.
- **I6 hostname validation is also a frontend gap** — `validate_install_inputs` only checks presence, not contents. Backend defense-in-depth (I6) is the right v1.0 fix, BUT also worth surfacing as a follow-up: do tui.py + gui state validate hostname value-contents before yaml-emit? If not, that's a parallel finding that the audit might have missed because §2's scope was backend. Recommend SPOC fold this into a frontend-validation review post-fix-wave (either as part of fix-wave or post-§7).
- **I1 + the mount-lifecycle pattern: codify the convention** — `users.py` and `mok.py` use the `setup; try: ...; finally: cleanup` pattern correctly. `packages.py` does not (I1). Fix-wave commit message should explicitly cite the convention so the pattern is visible for future modules. Non-trivial but worth one short paragraph in `installer/backend/__init__.py` or `docs/installer/conventions.md`.
- **Audit log copy failure (I4) — surface, don't just emit** — beyond auditor's `_emit warning`, recommend adding `warnings: list[str]` field on `InstallResult` so frontends can render explicit user-facing notices ("integrity audit log was not copied to target — review manually before retiring install media"). Pure-emit is observability-only; surfacing affects user action.
- **§2 §C (frontend) was light** — auditor notes 38 tests for tui.py and 191+336 lines of GUI state tests, declared "all passing", but didn't enumerate frontend-specific findings. Worth confirming with the auditor: were `installer/frontend/tui.py` (603 lines) and `installer/frontend/gui/{state,window,style}.py` audited at the same depth as backend, or was time-budget bound to backend? If frontend was lighter, may need a follow-up pass before fix-wave merges (defer or extend §7 scope).

### Filter check

- **User-control posture ("user controls / understands their machine"):** OK with one nudge — I9 (MOK enrollment marked as install-fail) actively works against this; user receives a misleading "install failed" message on a system they CAN boot and CAN re-enroll. Prioritizing I9 directly serves the user-control posture.
- **Anti-supply-chain / Mythos ("security-only alignment"):** OK with reservation on I4 vs I5. I4 (audit-log silent loss) is an active erosion of the trust trail — PRIORITIZE matches Mythos posture. I5 (manifest-sig diagnostic loss) is fail-safe and DEFER is consistent with security-only alignment (better diagnostics is convenience-leaning, not security-leaning).
- **Owner-approved baselines:** OK — none of these findings touch text-only-no-voice (Rule 4), Forge-not-Calamares (no third-party scaffolding rule), or signing-key custody decisions. All findings stay inside the InterGenOS-native Forge surface area.

---

## §6 Docs + Config + Gates Review

### Findings verdicts

- **C1 (MEDIUM, .githooks/pre-push:13)**: **PRIORITIZE** — adding `set -e` is defensible defense-in-depth for the trust-chain enforcement layer. **HOWEVER:** the auditor's specific scenario (`git fetch` failing → false-negative PASS) is NOT actually addressed by `set -e` because the existing `git fetch ... || true` (line 90) explicitly neutralizes the failure code regardless of `set -e`. The real underlying concern is that fetch failure leaves the gate operating on stale `origin/master` with no warning. Recommendation for fix-wave: include `set -e` (harmless, defensive), AND separately harden the fetch path to fail-closed-with-warning when `git fetch` exits non-zero. Existing patterns (`[ -z "$f" ] && continue` at lines 117 + 129, command substitutions at 154 + 180, pipelines with awk/grep) are all `set -e`-compatible per the bash short-circuit exemption rules — verified by trace.
- **C2 (LOW, .githooks/pre-push not activated)**: **DISAGREE** — this is a per-worktree configuration state, not a code defect. The pre-push hook itself is correct; the auditor's worktree (DS-v2 on ubuntu.hplt) didn't have `core.hooksPath` set. On my IGOSC bare-metal worktree the gate IS active (verified live during my Step 4 push when stale-master gate fired correctly). Auditor's proposed fix ("run setup-githooks.sh") is an operational action for the auditor, not a code change. **Substitute recommendation:** if SPOC wants this hardened structurally, fold gate-activation check into `scripts/pre-orient.sh` (which the fleet already runs at session-start per `feedback_git_hygiene_gates_2026-05-06.md`). That converts a per-worktree config drift into a self-healing convention. Worth a separate ticket post-fix-wave; not v1.0-blocking.
- **C3 (LOW, docs/ no top-level README)**: **DEFER** — legitimate contributor onboarding gap (203 markdown files, no entry point) but not v1.0-blocking. Fold into v1.0-polish backlog.
- **C4 (LOW, config/kernel/fragments/archive/ no README)**: **DEFER** — minor archive-policy doc nicety. Polish.
- **C5 (LOW, docs/research/branding/ binary asset bloat)**: **DEFER** — auditor explicitly self-classified as "post-v1.0" ("the PR-open target for branding is post-v1.0"). Honor that classification. Branding repo split or git-lfs is a substantial repo restructure decision, not a fix-wave item.
- **C6 (LOW, config/systemd/sshd.service no README)**: **DEFER** — single-file directory README is documentation polish.

### Cross-cutting concerns SPOC may have missed

- **Pre-push gate has no automated test coverage** — auditor's §6 Detailed Analysis notes "No automated test for pre-push gate. Manual verification: push a branch and observe gate output." For the layer that enforces trust-chain hygiene on every push, this is a notable gap. Worth opening as a separate v1.0-polish item: `tests/githooks/test_pre_push.sh` exercising each gate's BLOCK/PASS path with synthetic commit ranges. Out of scope for fix-wave but worth flagging.
- **C2 + C1 together suggest a structural improvement** — fold gate-activation verification + `set -e` hardening into one pre-push-gate-hygiene commit, with the activation check happening in `pre-orient.sh` and the `set -e` change happening in `.githooks/pre-push`. Two-file commit, single coherent intent.
- **`scripts/check-public-content.py` Python detection chain is duplicated** — the same `python3 → py → python` detection logic appears in pre-push (lines 67-76) AND likely elsewhere. Worth confirming via grep that this isn't duplicated across multiple shell scripts; if so, factor into a shared helper. Minor; v1.0-polish.

### Filter check

- **User-control posture:** OK — none of these findings touch user-facing controls. Doc/config improvements serve transparency and contributor onboarding, both posture-aligned.
- **Anti-supply-chain / Mythos:** OK with the C1 nuance. The pre-push gate is the trust-chain enforcement layer; hardening it (C1 PRIORITIZE) directly serves Mythos posture. C2 is per-worktree drift, which a self-healing convention fix (folded into pre-orient.sh) would address structurally.
- **Owner-approved baselines:** OK — no PD-conflict on doc additions, kernel-fragment archive policy is internal-build-only (no end-user surface), branding deferral honors owner's "post-v1.0" classification.

---

## Verdict Summary

**§2 Installer:** 9 PRIORITIZE / 3 DEFER / 0 DISAGREE
- PRIORITIZE: I1, I2, I3, I4, I6, I7, I8, I9, I10
- DEFER: I5, I11, I12

**§6 Docs/Config/Gates:** 1 PRIORITIZE / 4 DEFER / 1 DISAGREE
- PRIORITIZE: C1
- DEFER: C3, C4, C5, C6
- DISAGREE: C2

**Total reviewed:** 18 findings — 10 PRIORITIZE / 7 DEFER / 1 DISAGREE.

**Severity revisions suggested for SPOC consolidation:**
- I4 (currently LOW): consider MEDIUM — trust-trail observability hit
- I6 (currently LOW): consider MEDIUM — yaml-driven hostname-injection vector

**Fix-wave packaging recommendations:**
- Group 1 (mount lifecycle): I2 + I3 + I8 → single `hooks.py` refactor
- Group 2 (orchestrator UX): I4 + I9 → install.py exception handling + InstallResult.warnings field
- Group 3 (validation defense-in-depth): I6 + I10 → backend validators
- Group 4 (resource-hygiene): I1 + I7 → context-manager + timeout
- Group 5 (gate hardening): C1 → pre-push set -e plus fetch-failure fail-closed

DS-v2's audit was thorough and well-calibrated for §2 + §6. No outright misclassifications beyond C2 (which is misclassified as a code finding rather than per-worktree state). Severity calls were on the low side for I4 + I6, both of which touch the trust chain or install-boundary attack surface.

— Reviewer
