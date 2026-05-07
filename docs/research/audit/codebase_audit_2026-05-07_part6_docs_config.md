# Codebase Audit — §6 Docs + Config + Gates

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 10:55–11:10 CDT  
**Master:** c4d5126  
**Paths audited:** `docs/` (203 markdown files, 30+ subdirectories), `.githooks/pre-push` (240 lines), `config/` (kernel fragments, gsettings, systemd)  
**Findings:** 0 CRITICAL, 0 HIGH, 1 MEDIUM, 5 LOW

---

## Summary

The documentation is extensive (203 markdown files across 30+ subdirectories) covering architecture, research, ceremony procedures, branding, security, and hardware test reports. The pre-push gate (`.githooks/pre-push`, 240 lines) has 5 well-structured gates but is missing `set -e` (only `set -uo pipefail`) and has not been activated via `setup-githooks.sh` on this worktree. Configuration files (`config/`) are well-organized with kernel fragments, GSettings overrides, and systemd service files.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| C1 | MEDIUM | Gates | `.githooks/pre-push:13` | `set -uo pipefail` is missing `-e`. Without `set -e`, a failing subcommand continues execution. Example: if `git fetch origin master` (line 90) fails due to network error, `LOCAL` and `REMOTE` may be empty strings, and the stale-master check (line 97) could produce a false-positive BLOCK or a false-negative PASS. | Change to `set -euo pipefail`. |
| C2 | LOW | Gates | `.githooks/pre-push` (not activated) | `git config core.hooksPath` returns empty — `scripts/setup-githooks.sh` has not been run on this worktree. The pre-push gate exists and is well-written, but pushes from this host are NOT gated. The other worktree (`/mnt/intergenos`) has the hook active, but this deepseek worktree does not. | Run `bash scripts/setup-githooks.sh` on this worktree. |
| C3 | LOW | Docs | `docs/` (root) | 203 markdown files with no top-level index or README. New contributors or reviewers arriving at the docs/ directory have no entry point. Key docs like `VISION.md`, `getting-started.md`, `signing-key.md` are at the root but not cross-referenced. | Add `docs/README.md` with a curated index of key documents, grouped by role (contributor, reviewer, security researcher). |
| C4 | LOW | Config | `config/kernel/fragments/archive/` | 15 kernel config fragments in an `archive/` subdirectory. Archive naming suggests these are deprecated/previous versions, but there's no README explaining the archive policy (when do fragments graduate to active vs archive?). | Add `config/kernel/fragments/archive/README.md` explaining the archive policy: "Previous iteration fragments preserved for audit trail. Active fragments live in parent directory." |
| C5 | LOW | Docs | `docs/research/branding/` | Branding directory contains 80+ subdirectories with PNG/SVG assets across 9 rounds of iteration + final marks + proposals. This is ~150MB of binary assets in a documentation tree. Does not affect build or audit, but `git clone` time is inflated for contributors who only need the code. | Consider moving brand assets to a separate repository (`intergenos-branding`) or git-lfs. For now, non-blocking — the PR-open target for branding is post-v1.0. |
| C6 | LOW | Config | `config/systemd/sshd.service` | Single service file but no explanation of which systemd overrides are included. The `config/systemd/` directory has only one file — unclear if this is the complete set or if more services are planned. | Add a `config/systemd/README.md` listing all service overrides and their purpose. |

---

## Detailed Analysis

### A. Pre-Push Gate (`.githooks/pre-push`, 240 lines)

Well-structured gate with 5 sequential checks:

1. **Gate 0 — Force-push hard block on master** (lines 29-49): Parses stdin push refs, checks if remote master is ancestor of local. `ZERO` sentinel for new-branch pushes. Correct.

2. **Gate 1 — Public-content audit** (lines 51-85): Runs `scripts/check-public-content.py` via `git show HEAD:<file>` (NOT working tree — correct for push-content validation). Portable Python detection (python3 → py → python fallback). Correct.

3. **Gate 2 — Stale-master check** (lines 87-102): Fetches origin master, compares local vs remote. Blocks if local is behind. Correct pattern (prevents the "SPOC pushed while I worked" loop).

4. **Gate 3 — Bash syntax check** (lines 104-140): Runs `bash -n` on all modified `.sh` files. Portable bash detection. Skips `.py` files generated during hook execution. Correct.

5. **Gate 4 — Python syntax check** (lines 142-190): Runs `python3 -m py_compile` on all modified `.py` files. Correct.

**Issues:**
- Missing `set -e` (C1) — only `set -uo pipefail`.
- Not activated on this worktree (C2) — `core.hooksPath` not set.

### B. Configuration (`config/`)

**Kernel config fragments:**
- `config/kernel/fragments/00-universal-baseline.config` — cross-distro convergence baseline (Ubuntu/Arch/Fedora/Debian/openSUSE intersection)
- `config/kernel/fragments/99-intergenos-overrides.config` — InterGenOS-specific hardening overrides (applied last, takes precedence)
- `config/kernel/fragments/archive/` — 15 previous-iteration fragments

The two-fragment system (baseline + overrides) with concatenation order enforced in `packages/core/linux-kernel/build.sh:36` is a clean pattern.

**GSettings overrides:**
- `90_intergenos.gschema.override` — base GNOME defaults
- `91_intergenos-extensions.gschema.override` — extension defaults
- `92_intergenos-desktop.gschema.override` — desktop layout defaults

Numbered prefix convention ensures correct application order by glib-compile-schemas.

**Systemd services:**
- `systemd/sshd.service` — SSH server override

### C. Documentation (`docs/`)

**Structure (key categories):**
- Root: `VISION.md`, `getting-started.md`, `signing-key.md`, `signing-procedure.md`, `shim-review-submission.md`, `grub2-cve-audit.md`
- `ceremony/`: signing-key-ceremony-procedure.md
- `governance/`: succession.md, security-policy
- `research/`: 20+ subdirectories covering installer, security, branding, AI, packaging, kernel, networking, virtualization
- `security/`: advisories

**Quality:**
- Key docs (VISION.md, signing-key.md) are well-maintained and up-to-date.
- Research docs have dated filenames (convention: YYYY-MM-DD suffix).
- Shims-review submission is comprehensive (638 lines, all 29 questions answered).

**Issues:**
- No top-level index (C3).
- Branding assets in docs tree (C5) — 80+ subdirectories of binary assets.

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | Pre-push gate: 5 gates in order. Gate 0 (force-push) runs first before any file checks — correct priority. Gate 2 (stale-master) fetches origin mid-hook — could be slow on slow networks, but no timeout issue since it runs `--quiet`. |
| Error-handling scan | Pre-push: missing `set -e` (C1). Individual gate failures cascade silently. Each gate returns exit 1 on BLOCK, but inter-gate failures might not propagate. |
| Hardcoded-path scan | `.githooks/pre-push` uses `$REPO_ROOT` (git rev-parse) — portable. `scripts/check-public-content.py` path is relative — correct. |
| Test gap analysis | No automated test for pre-push gate. Manual verification: push a branch and observe gate output. |
| Shell robustness | Pre-push: `set -uo pipefail` (missing -e, C1). GSettings: non-shell text files (correct). Kernel config: non-shell text files (correct). |
| git-hygiene | Force-push to master hard-blocked (correct). Public-content audit scans HEAD (not working tree) — correct. Stale-master check — correct. hooksPath not set on this worktree (C2). |
