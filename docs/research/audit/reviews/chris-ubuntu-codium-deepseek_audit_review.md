# Audit Review — DS-workstation

**Reviewer:** DS-workstation (chris-ubuntu)  
**Date:** 2026-05-07  
**Sections assigned:** §1 (Build system), §3 (Package recipes), §7 (Gap closure)  
**Audit source:** DS-v2 (HP laptop), 7-section codebase audit filed at `docs/research/audit/`

---

## §1 Build System Review

### Findings verdicts

- **B1 (HIGH): PRIORITIZE** — `scripts/chroot-enter.sh` missing `set -e`. Confirmed: `#!/bin/bash` with no error-guard. This is the gateway for all chroot-phase builds. Correct and blocking.

- **B2 (HIGH): PRIORITIZE** — `scripts/chroot-health-check.sh` missing `set -e`. Confirmed: 348 lines of validation with no `set -e`. A broken package could report PASS. Correct.

- **B3 (MEDIUM): PRIORITIZE** — `scripts/chroot-teardown.sh` missing `set -e`. Confirmed. Even though individual umount calls are `||`-guarded, adding `set -e` is defense-in-depth for unexpected failure modes.

- **B4 (MEDIUM): DEFER** — Dead-code `[ -z "$IGOS" ]` guard. Confirmed that `-z` is unreachable after the hardcoded `IGOS=/mnt/igos`. However: the `|| [ "$IGOS" = "/" ]` leg IS live and load-bearing safety — unmounting `/` would be catastrophic. The suggested `IGOS="${IGOS:-/mnt/igos}"` fix would make both checks live. Not v1.0-blocking; queue for v1.0-polish.

- **B5 (HIGH): PRIORITIZE** — igos-build test gap. Confirmed: 2 test files (`test_parser_supersedes.py`, `test_tracker_b4_staging.py`) for 8 Python modules (~2100 LOC). Zero build-execution tests. The proposed test plan is realistic and scoped well.

- **B6 (MEDIUM): DEFER** — `sha256sum` subprocess timeout. Confirmed no timeout on line 280. However: sha256sum is CPU-bound on local files, not network-bound. NFS edge case is valid but not v1.0-blocking (builds run on local NVMe). Queue for polish.

- **B7 (MEDIUM): PRIORITIZE** — `sync_chroot_scripts()` missing rsync check. Confirmed: no `command -v rsync` guard. Low-hanging diagnostic improvement that saves hours of debugging.

- **B8 (MEDIUM): DEFER** — Hardcoded `/opt/rustc/bin`. Confirmed. Valid finding but Rust toolchain is installed at that path by the build system itself; an external Rust installation is an unlikely dev workflow. Queue for v1.0-polish.

- **B9 (LOW): PRIORITIZE** — `PKG_VERSION=""` export in chroot-enter.sh:82. Confirmed. The empty-string export is a silent-failure trap: any `build.sh` referencing `$PKG_VERSION` before the builder sets it gets an empty string, not an unset-error. Example from audit: `curl "https://.../pkg-${PKG_VERSION}.tar.gz"` → `curl "https://.../pkg-.tar.gz"`. I'm upgrading this to HIGH because (a) the blast radius is ALL 701 packages that reference PKG_VERSION, (b) the failure mode is wrong download URL (not just a crash), (c) the fix is trivial: remove the export line. The builder sets PKG_VERSION per-package at `builder.py:126`; the chroot-entry script should not pre-set it.

- **B10 (LOW): DEFER** — All chroot scripts hardcode `IGOS=/mnt/igos`. Valid organizational issue but not v1.0-blocking — builds target a fixed LFS root by design.

- **B11 (LOW): DEFER** — CustomStyle source-check in one-liner. The `&&` chain already short-circuits on source failure; the error ends up in stderr. Valid polish item but low impact.

- **B12 (LOW): DEFER** — Inline Python YAML error handling. Valid but verify-sources phase is a build-time gate run by developers, not end-users. Stack trace is acceptable for a developer-facing tool.

- **B13 (LOW): DISAGREE** — The finding itself self-debunks: the audit text acknowledges `source` is in `REQUIRED_FIELDS`, so the `raw.get("source", [])` default on line 296 is unreachable dead code. Not a finding — just a comment-marker item. However: the dead-code observation is worth a comment in parser.py for readability. Not a PRIORITIZE/DEFER candidate.

- **B14 (LOW): DEFER** — Manifest empty-archives warning. The current behavior (WARN but return 0) is correct for partial builds. `MANIFEST_STRICT` flag is a useful polish addition but not v1.0-blocking.

### Cross-cutting concerns SPOC may have missed

- **B9 upgrade rationale:** I'm upgrading B9 from LOW to HIGH (see above). The blast-radius across 701 packages makes this more severe than initially assessed. SPOC should review this reclassification.
- **B4 nuance:** The `[ "$IGOS" = "/" ]` check on the same line as the dead `-z` check is live, load-bearing safety code. The audit fix proposal (`IGOS="${IGOS:-/mnt/igos}"`) is correct and makes the whole guard block functional.
- **B5 scope accuracy:** The audit correctly identifies the gap but understates it — the existing 2 test files don't test the build loop at all. This is the single highest-ROI test investment in the codebase.

### Filter check

- **Prime Directive:** OK — no security tradeoffs in findings or verdicts.
- **InterGenOS security posture:** OK — B6 (timeout) and B8 (hardcoded path) are robustness concerns, not security. B1/B2 (missing set -e) are correctness concerns, not security. B9 is a correctness concern but has downstream security implications (wrong download URL).
- **Owner-approved baselines:** OK.

---

## §3 Package Recipes Review

### Findings verdicts

- **P1 (MEDIUM): DEFER** — All ~700 build.sh functions lack `set -e`. Confirmed: spot-checked `packages/core/bash/build.sh` — `configure()` has no `set -e`. However: the builder sources build.sh into a `bash -c` subshell (CustomStyle, line 32), and each phase command is a one-liner. If a function fails, the `&&` chain short-circuits and the builder reports `PHASE_FAILED`. The gap is real but the blast radius is contained by the builder's phase-error detection. Not v1.0-blocking; queue for the package-recipe standardization push.

- **P2 (LOW): DEFER** — `$IGOS_SOURCES` vs `$IGOS_SOURCES_DIR` convention break in librsvg. Valid. Both resolve to the same path (`builder.py:122-123` sets both). Queue for consistency sweep.

- **P3 (LOW): DEFER** — nodejs `post_install()` PKG_VERSION guard. Valid defensive-coding note. Low impact — builder always sets PKG_VERSION.

- **P4 (LOW): DEFER** — Secure Boot packages in `core/` directory with `tier: desktop` YAML. Valid organizational note. The build system uses the YAML `tier:` field correctly, so no functional impact.

- **P5 (LOW): DEFER** — AppArmor empty dep list. Valid observation but AppArmor's build system pulls in Perl/Python via builder's host-system PATH, not the dep graph. Adding explicit deps is good bookkeeping but non-blocking.

- **P6 (LOW): DEFER** — GCC bundled deps variable resolution. Valid parser robustness concern. Queue with `parser.py` improvements.

- **P7 (LOW): DEFER** — PKG_VERSION convention not documented. This is the downstream consequence of B9 (chroot-enter.sh exporting empty PKG_VERSION). The audit correctly identifies the documentation gap. Fixing B9 removes the underlying cause.

- **P8 (LOW): DISAGREE** — The finding itself self-debunks ("Already handled — urlparse path-based extraction works"). Not a finding.

- **P9 (LOW): DEFER** — gnu-efi tier classification. Valid organizational concern but the YAML tier overrides directory location, so build ordering is correct.

### Cross-cutting concerns SPOC may have missed

- **P1 vs B9 interaction:** P1 (build.sh set -e) and B9 (PKG_VERSION="") interact. If a build.sh function errors because PKG_VERSION is empty (from chroot-enter.sh's export), the builder catches the exit. But the *wrong thing happens* — a wrong download URL — before the exit is caught. Fixing B9 is the acute priority; P1 is the chronic defense-in-depth.
- **No HIGH findings in packages:** DS-v2's §3 verdict (0 HIGH) is accurate. The packages tree is the cleanest surface in the codebase, consistent with the schema migration effort.
- **Post-migration verification:** Audited confirmed 0 local://, 0 placeholder- SHA256 across 701 packages. This independently validates DS Step 3.

### Filter check

- **Prime Directive:** OK.
- **InterGenOS security posture:** OK.
- **Owner-approved baselines:** OK.

---

## §7 Gap Closure Review

### Findings verdicts

- **G1 (LOW): DISAGREE** — The audit claims `cmd.split()[0]` doesn't handle explicit paths like `/usr/bin/python3`. However, `intergen/safety.py:150` actually uses `base_cmd = os.path.basename(parts[0])`. `os.path.basename("/usr/bin/python3")` → `python3`, so explicit-path commands ARE correctly classified. The finding is based on an incomplete code read — the auditor saw `cmd.split()[0]` on line 226 (which IS used elsewhere) but missed the `os.path.basename()` normalization on line 150 in `classify_command()`. The proposed fix (add basename) is already implemented.

- **G2 (LOW): DEFER** — Missing API key guard in `_search_serper()`. Valid defensive-coding note. If the intergen config is missing the API key, the error message from Serper is confusing. Adding an explicit guard is good practice. Not v1.0-blocking — the runtime doesn't call web_search without config.

- **G3 (LOW): DEFER** — No logging of empty-input. Valid observability gap. Low priority — the empty-input return is a graceful fallback, not a failure path.

- **G4 (LOW): DEFER** — Hardcoded `DB_PATH = Path("/var/lib/igos/pkm.db")`. Confirmed on `pkm/database.py:14`. However, line 121 already accepts an optional `db_path` parameter: `self.db_path = Path(db_path) if db_path else DB_PATH`. The testability concern is partially addressed by the constructor override. An env-var fallback would complete the fix.

- **G5 (LOW): DISAGREE** — The audit claims `pkm/installer.py:_extract_archive()` uses Python `tarfile.extractall()` without path-traversal hardening. **This is incorrect.** The pkm installer uses subprocess `tar` for extraction (line ~52 area), NOT Python tarfile. The Python `tarfile` usage at line 169 is for the setuid/setgid restore pass — a second-pass operation that (a) uses `tf.getmembers()` for reads only, (b) validates paths with `deployed.relative_to(self.root.resolve())` on line 176, and (c) explicitly rejects path-escape members with `except ValueError: continue`. The audit missed the `relative_to` validation and the subprocess-tar context. SPOC's earlier note that this is a "partial-FP" is directionally correct but understated — the path-traversal hardening IS present.

- **G6 (LOW): DEFER** — Cloud-init template placeholders. Valid observation. Template files should have `.template` extension or README warning.

- **G7 (LOW): DEFER** — Safety plugin "sketch" naming. Valid — the "sketch" label is misleading for production fleet-roster validation code. Add deployment-status comment.

- **G8 (LOW): DEFER** — `forge-iso.1` missing `--archives` flag. Valid documentation gap. Manpages should match the CLI interface.

- **G9 (LOW): DEFER** — AI package dependencies not cross-referenced. Valid observation. The intergen package.yml should list Python runtime dependencies.

- **G10 (LOW): DISAGREE** — This is a duplicate of B5. The audit correctly notes "Already captured in §1 B5. No additional §7 finding needed." A finding that self-declares as a duplicate is not a finding.

### Cross-cutting concerns SPOC may have missed

- **G1 is a confirmed false-positive:** `os.path.basename()` on line 150 handles the exact case the audit claims is missing. The auditor read line 226 (`cmd = os.path.basename(command.strip().split()[0])`) but not line 150 in `classify_command()`. Note: this is the same pattern as §4 S1 (try/finally misread) — DS-v2's audit methodology has a pattern of incomplete code-path tracing. Recommend DS-v2 use `python3 -m py_compile` as a pre-screen (which they did for §7!) AND cross-reference function definitions before claiming missing code.
- **G5 is a confirmed false-positive:** The pkm tar handling has path-traversal hardening on the Python side AND uses subprocess tar for extraction (not tarfile). DS-v2 missed both the `relative_to()` guard and the subprocess-tar context.
- **Two false-positives in §7:** G1 and G5 are both invalid. This is a 2/10 false-positive rate in the gap closure section. The pattern (incomplete code-path tracing) is now observable across §4 and §7.

### Filter check

- **Prime Directive:** OK — G1 and G5 claims could lead to unnecessary hardening; their rejection is security-positive (existing code is already correct).
- **InterGenOS security posture:** OK.
- **Owner-approved baselines:** OK.

---

## Summary

| Section | PRIORITIZE | DEFER | DISAGREE |
|---------|-----------|-------|----------|
| §1 Build | 3 (B1, B2, B5) + B3, B7, B9 upgraded | B4, B6, B8, B10, B11, B12, B14 | B13 |
| §3 Packages | 0 | P1, P2, P3, P4, P5, P6, P7, P9 | P8 |
| §7 Gap | 0 | G2, G3, G4, G6, G7, G8, G9 | G1, G5, G10 |

**Total: 3 PRIORITIZE (from §1: B1, B2, B5 + B3, B7, B9 upgraded), 22 DEFER, 5 DISAGREE.**

**Key reclassifications:**
- **B9 LOW→HIGH:** blast radius across 701 packages, wrong-download failure mode, trivial fix.
- **G1 DISAGREE:** `os.path.basename()` already handles the claimed gap.
- **G5 DISAGREE:** path-traversal hardening exists via `relative_to()` + subprocess tar extraction.
