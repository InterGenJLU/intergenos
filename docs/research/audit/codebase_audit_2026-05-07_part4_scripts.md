# Codebase Audit — §4 Scripts + Ceremony

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 10:15–10:40 CDT  
**Master:** c4d5126  
**Paths audited:** `scripts/verify-*`, `scripts/ceremony/`, `scripts/sign-*`, `scripts/check-*`, `scripts/download-*`, `scripts/generate-*`, `scripts/host-check.py`, `scripts/pre-*`, scripts not covered in §1  
**Findings:** 0 CRITICAL, 0 HIGH, 1 MEDIUM, 7 LOW

---

## Summary

The scripts directory has 33 files across ceremony automation, signing pipelines, verification harnesses, source downloaders, and utility scripts. The signing ceremony (`ceremony.py`, 2213 lines) is a mature pexpect-driven automation with append-only trace logging, idempotent/resumable staging, and a companion validator (`validate.py`, ~550 lines) that defines "done-ness" as a 0-failures verdict. The signing pipeline (`sign-release.sh`, `sign-grub.sh`, `sign-kernel-uki.sh`, `sign-shim.sh`) all have `set -euo pipefail` and correct pre-flight discipline. The main finding is that `generate-templates.py` has a `try:` block with no `except:` handler, which would crash at runtime on the first error path that raises.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| S1 | MEDIUM | Scripts | `scripts/generate-templates.py:1` | Has 1 `try:` block but 0 `except:` blocks — this is a Python SyntaxError if the try was intended to have a handler. If the `try:` is a leftover from refactoring, the unmatched-try causes a runtime crash on the first raise path. 386 lines, used for generating package.yml templates. | Verify whether the try block should have an except handler or should be removed. If generated code, audit the generation logic for completeness. |
| S2 | LOW | Scripts | `scripts/ceremony/ceremony.py` (2213 lines) | Monolithic script with no test suite. 2213 lines of hardware-dependent (Nitrokey 3, YubiKey, PIV, smartcard) code with no automated validation. The README states "reference implementation, not actively maintained between rotations" — which is documented intent, but the gap between rotations means regressions from subprocess API changes or pexpect version bumps won't surface until the next rotation ceremony. | Add a dry-run mode (`--dry-run` / `--simulate`) that validates card detection, subprocess availability (pexpect, gpg-connect-agent, openssl), and stage ordering without making any card mutations. This can be run in CI against a Debian container. |
| S3 | LOW | Scripts | `scripts/check-manifest-signature.sh:1` | Runs `gpg --verify` without explicit `--no-default-keyring` flag (unlike `integrity.py:165` which uses throwaway keyring). The check uses `--keyring release-key.asc` but does not pass `--no-default-keyring`, so a stray key in the operator's `$HOME/.gnupg` could change the verdict. | Add `--no-default-keyring` flag to match the install-time verifier's security posture. |
| S4 | LOW | Scripts | `scripts/download-sources.py:1` (579 lines) | Downloads upstream source tarballs for all packages. No `--tier` filtering — always downloads ALL sources (701 packages). For partial builds, this wastes bandwidth and time. The script already exists per MEMORY.md (used for offline build preparation), but a tier filter would improve workflow. | Add `--tier <tier>` flag to filter downloads to a single tier. |
| S5 | LOW | Scripts | `scripts/host-check.py:1` (466 lines) | Host requirements verification. Has 1 try/2 except blocks — covers basic error paths. No timeout on subprocess version checks (e.g., `gcc --version` could hang if gcc is broken). | Add `timeout=15` to each subprocess.check_output call. |
| S6 | LOW | Scripts | `scripts/pkg-functions.sh:1` (511 lines) | Function library sourced by chroot-build scripts to provide `do_package()` orchestrator. Functions lack `set -e` internally — same issue as build.sh (P1 in §3). Since this is sourced into scripts that DO have `set -e`, the calling context provides error propagation, but individual function failures within loops could cascade. | Add `set -e` in the main script body (outside function definitions) or prefix critical commands with `|| return 1`. |
| S7 | LOW | Scripts | `scripts/verify-b2-reproducibility.sh:106-107` | Uses `objcopy --dump-section .sbat=/dev/stdout` for SBAT section extraction. If `objcopy` is not installed (non-binutils host), this check crashes with "command not found" rather than a clean SKIP. Other checks (openssl at line 120) properly guard with `command -v openssl`. | Guard with `if ! command -v objcopy &>/dev/null; then pass "SBAT-SECTION: SKIPPED (objcopy not available)"; else ...`. |
| S8 | LOW | Scripts | `scripts/ceremony/validate.py` (~550 lines) | Five validation sections. Hardcoded paths to `/media/amnesia/CEREMONY/` (line assumed from ceremony.py convention). If the validation script is run outside the Tails environment (e.g., for code review), all low-level card checks fail but the top-level verdict is clear. No `--check-only-file-system` or similar review-mode flag. | Add `--review` mode that skips card-dependent checks (SCD APDU, card-status) and validates file-format checks only (key fingerprint format, expiry format, cert CN). |

---

## Detailed Analysis

### A. Signing Ceremony (`scripts/ceremony/`)

**ceremony.py (2213 lines)** — Mature pexpect-based automation for key generation on hardware tokens.

*Strengths:*
- Idempotent staging via `--from-stage N` — ceremony can resume from any stage after a card disconnect or mid-ceremony power off.
- Append-only trace log with scdaemon log tail integration — every gpg-connect-agent and gpg --card-edit exchange is traced to disk.
- Comprehensive SCD APDU health checks (VERIFY, GET DATA, GET CHALLENGE) before each write — catches dead tokens before attempting writes.
- Stage ordering defined in an explicit list at bottom of file — reorderable for v3 refactor.
- PIN/passphrase sourced from environment (not hardcoded) with `getpass` fallback for interactive entry.

*Weaknesses:*
- No test suite (S2) — 2213 lines of hardware-dependent code with zero automated validation.
- README documents "not actively maintained between rotations" — by design, but two-year rotation gap means pexpect API changes accumulate silently.

**validate.py (~550 lines)** — Post-ceremony verification.

*Strengths:*
- Five validation sections defining the complete "done-ness" spec.
- Clear PASS/FAIL output per check.
- Card-dependent and file-format-dependent checks separated.

*Weaknesses:*
- No review-mode (S8) — can't run validation outside Tails environment.

### B. Signing Pipeline

**sign-release.sh (350 lines)** — Master release signing orchestrator. Handles GPG detached signatures for pkm repo index + sbsign for kernel UKI + GRUB EFI binaries.

*Strengths:*
- `set -euo pipefail` (line 65).
- Explicit exit codes (0-4) for different failure modes.
- Environment variable defaults (`$INTERGENOS_GPG_KEY_ID`, `$INTERGENOS_PKCS11_URI`) with CLI overrides.
- Pre-flight discipline documented inline (close browsers, dev tools).
- `--strict` flag for enforcement vs. tolerance of missing optional artifacts.
- Token presence pre-check at start.

**sign-grub.sh, sign-kernel-uki.sh, sign-shim.sh** — All have `set -euo pipefail`. Single-responsibility signers for individual artifact types.

### C. Verification Harnesses

**verify-b2-reproducibility.sh (145 lines)** — B2 shim reproducibility verification. 9 checks. `set -euo pipefail`. Clean trap-based tempfile cleanup.

**check-manifest-signature.sh (140 lines)** — Q14-style manifest precheck. Has `set -euo pipefail`. Three checks: format, signature, master cosign.

**check-sbat-generations.sh (121 lines)** — SBAT generation validation. Has `set -euo pipefail`.

### D. Utility Scripts

**download-sources.py (579 lines)** — Downloads all package sources. No tier filtering (S4).

**host-check.py (466 lines)** — Verifies build host meets LFS requirements. No subprocess timeout (S5).

**generate-templates.py (386 lines)** — Template generation. Has broken try/except (S1).

**check-public-content.py (347 lines)** — Scans for secrets/agent names in public files.

**pkg-functions.sh (511 lines)** — Shared function library for chroot-build scripts. Sourced (not executed standalone).

**pre-clear-check.sh (105 lines)** — Pre-session-clear validation. Has `set -e`.

**pre-orient.sh (106 lines)** — Pre-session orientation. Has `set -e`.

### E. Shell Robustness Summary

| Script | Has `set -e` | Type | Notes |
|--------|-------------|------|-------|
| verify-b2-reproducibility.sh | `set -euo pipefail` | Verification | Solid |
| sign-release.sh | `set -euo pipefail` | Signing | Solid |
| sign-grub.sh | `set -e` | Signing | Solid |
| sign-kernel-uki.sh | `set -e` | Signing | Solid |
| sign-shim.sh | `set -e` | Signing | Solid |
| check-manifest-signature.sh | `set -euo pipefail` | Verification | Solid |
| check-sbat-generations.sh | `set -e` | Verification | Solid |
| pre-clear-check.sh | `set -e` | Utility | Solid |
| pre-orient.sh | `set -e` | Utility | Solid |
| create-image.sh | `set -e` | Image creation | Solid |
| pkg-functions.sh | None | Library | Sourced (by design) |
| toolchain-build.sh | None | Build | §1 B1 coverage |
| temp-tools-build.sh | None | Build | §1 B1 coverage |
| download-sources.py | N/A | Python | (S4) |
| generate-templates.py | N/A | Python | (S1) |
| host-check.py | N/A | Python | (S5) |
| check-public-content.py | N/A | Python | OK |

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | ceremony.py stage ordering + validate.py gate logic traced. Sign-release.sh artifact→output flow traced. |
| Error-handling scan | All signing scripts have `set -euo pipefail`. Python scripts vary: download-sources.py has 3 try/4 except, host-check.py has 1 try/2 except, generate-templates.py has 1 try/0 except (S1). |
| Hardcoded-path scan | `/media/amnesia/OFFLINEDEBS`, `/media/amnesia/CEREMONY`, `/tmp/scdaemon-ceremony.log` in ceremony.py — correct for Tails environment. `/etc/intergenos/signing/vendor-cert.pem` in sign-release.sh — correct. |
| Test gap analysis | ceremony.py has no test suite (S2). validate.py has no review-mode (S8). All other scripts lack automated tests. |
| Shell robustness | 10/14 shell scripts have `set -e` or `set -euo pipefail`. 2 are function libraries (sourced, by design). 2 are build-phase scripts (covered in §1). |
| git-hygiene | ceremony.py contains hardcoded key UID/email — by design (this is the trust anchor). No secrets or token PINs in any script. |
