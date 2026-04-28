# Plan: Build System Security Remediation

## Context

A full systems audit was performed on April 8, 2026 (12,370 lines across 5 parts) and reviewed by ChatGPT. The review identified 4 critical, 4 high-priority, and 3 medium-priority findings. After careful evaluation against the actual codebase, some findings are fully valid, some partially valid, and some don't apply to our architecture. This plan addresses everything that's genuinely actionable.

The driving motivation: InterGenOS currently operates on **implicit correctness** — builds succeed because we happen to run things in the right order, not because the system enforces it. __PROJECT_Sentinel__ integration demands **enforced correctness**.

---

## Phase 1: SHA256 Verification in Bash Scripts (CRITICAL)

**Problem:** The Python builder (`builder.py:218-236`) verifies SHA256 before extraction. The bash scripts (`toolchain-build.sh:74`, `chroot-build-ch8.sh:88`) do NOT. A tampered tarball in the toolchain phase compromises the entire OS.

**Fix:** Add SHA256 verification to `pkg-functions.sh` as a shared function, then call it from `toolchain-build.sh` and `chroot-build-ch8.sh` before every `tar -xf`.

### Files to modify:
- `/mnt/intergenos/scripts/pkg-functions.sh` — add `verify_checksum()` function
- `/mnt/intergenos/scripts/toolchain-build.sh:74` — call verify before tar
- `/mnt/intergenos/scripts/chroot-build-ch8.sh:88` — call verify before tar
- `/mnt/intergenos/scripts/chroot-build-core-extra.sh` — same pattern

### Implementation:
```bash
# In pkg-functions.sh — new function
verify_checksum() {
    local file="$1"
    local expected="$2"
    
    if [ -z "$expected" ]; then
        log "WARNING: No checksum provided for $(basename $file)"
        return 0
    fi
    
    local actual
    actual=$(sha256sum "$file" | cut -d' ' -f1)
    
    if [ "$actual" != "$expected" ]; then
        log "ERROR: Checksum mismatch for $(basename $file)"
        log "  expected: $expected"
        log "  actual:   $actual"
        return 1
    fi
    return 0
}
```

In toolchain-build.sh and chroot-build-ch8.sh, each package has a known tarball. We need a checksum lookup. Two approaches:
- **Option A:** Hardcode checksums in the bash scripts (fragile, duplicates package.yml)
- **Option B:** Read SHA256 from the package's `package.yml` before extraction

**Recommendation:** Option B — read from package.yml using a simple grep. The package templates are the single source of truth.

### Also add tar safety flags:
Currently bash uses: `tar -xf "$tarball" -C "$workdir" --strip-components=1`
Python uses: `tar -xf "$tarball" -C "$dir" --strip-components=1 --no-same-owner --no-same-permissions`

Add `--no-same-owner --no-same-permissions` to all bash tar commands.

---

## Phase 2: Desktop Tier Dependency Audit (CRITICAL)

**Problem:** Many desktop packages have empty dependency declarations (`build: [], host: [], runtime: []`). The build order happens to be correct because the graph sorts alphabetically within tiers, but this is accidental.

**Fix:** Audit all desktop packages with empty deps and fill in the real dependencies.

### Approach:
1. Generate list of all desktop packages with zero declared dependencies
2. For each, check what libraries it links against (use the BLFS database)
3. Add the missing dependency declarations
4. Verify the graph still resolves with no cycles

### Files to modify:
- Multiple `packages/desktop/*/package.yml` files
- No code changes — data only

### Scope: This is a research + data entry task, not a code change. Should be done methodically, not rushed. Can use `blfs-query.py deps` to cross-reference.

---

## Phase 3: Cross-Tier Dependency Resolution (HIGH)

**Problem:** The Python graph builder loads all 541 packages but only BUILDS packages in the requested tier. Cross-tier deps (desktop→core) resolve in the graph because all packages are loaded. However, package names must match exactly (e.g., `perl-core` not `perl`). We hit this with parallel→perl.

**Fix:** No code change needed — the architecture is already correct (`__main__.py:101` uses `all_packages`). The fix is documentation + a naming convention.

### Rule to document:
- Dependencies must use the exact `name:` from the target package's `package.yml`
- A validation step should warn when a dep resolves to a package in a different tier (informational, not blocking)

### Files to modify:
- `/mnt/intergenos/igos-build/graph.py` — add informational warning for cross-tier deps during resolve()
- `CLAUDE.md` or build docs — document the naming requirement

---

## Phase 4: Patch Checksum Verification (HIGH)

**Problem:** Patches are applied without integrity verification. While patches are git-tracked (tampering requires repo compromise), defense in depth says verify anyway.

**Fix:** Add optional `sha256` field to patch entries in `package.yml`. When present, verify before applying.

### Files to modify:
- `/mnt/intergenos/igos-build/parser.py` — add sha256 to patch parsing
- `/mnt/intergenos/igos-build/builder.py` — verify patch checksum before `patch -Np1`
- Package templates with patches — add sha256 values (non-breaking: field is optional)

---

## Phase 5: Reproducible Build Foundations (MEDIUM)

**Problem:** Builds are not reproducible — timestamps, environment drift, non-deterministic tool behavior.

**Fix:** Add `SOURCE_DATE_EPOCH` to the build environment. This is the standard mechanism for reproducible builds (adopted by Debian, Arch, NixOS).

### Files to modify:
- `/mnt/intergenos/igos-build/builder.py` — set `SOURCE_DATE_EPOCH` in env
- `/mnt/intergenos/scripts/toolchain-build.sh` — export `SOURCE_DATE_EPOCH`
- `/mnt/intergenos/scripts/chroot-build-ch8.sh` — export `SOURCE_DATE_EPOCH`

### Value: Use the timestamp of the most recent git commit:
```bash
export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
```

---

## Phase 6: Structured Logging (MEDIUM)

**Problem:** Logs are human-readable but not machine-parseable. Can't automate failure analysis.

**Fix:** Add optional JSON log output alongside the existing human-readable logs.

### Files to modify:
- `/mnt/intergenos/igos-build/log.py` — add JSON output mode
- No changes to existing log format (additive only)

---

## What We Are NOT Doing (and why)

### Unifying bash + Python build systems
The bash pipeline follows LFS 13.0 exactly. Rewriting it into the Python DAG would:
- Break the 1:1 correspondence with the LFS book
- Add risk to the most critical phase (toolchain + core)
- Gain minimal benefit (the bash order IS correct per LFS)
Instead: add checksum verification to bash (Phase 1) to close the integrity gap.

### Per-package build isolation
The chroot IS the sandbox. Inside the chroot, all packages share the same filesystem — this is by design (LFS model). Per-package isolation (like Nix/Guix) would require a fundamentally different architecture.
Instead: rely on DESTDIR staging (already implemented) and skip_tracking for pass packages.

### Hermetic build environment
The toolchain PATH ordering follows LFS exactly. The `$IGOS/tools/bin` is prepended and `set +h` ensures it's searched first. Host tools are fallbacks only for commands not yet built. After entering the chroot, the host is completely isolated.
Instead: document this design decision and verify it's working correctly.

### Kernel config reduction
The "permissive" kernel config is intentional — 5-distro convergence ensures broad hardware support. Reducing it would break bare metal compatibility. This is a feature, not a bug.

---

## Implementation Order

| Priority | Phase | Effort | Impact |
|----------|-------|--------|--------|
| 1 | SHA256 verification in bash | 2-3 hours | Closes supply chain gap |
| 2 | Desktop dependency audit | 4-6 hours | Prevents silent build breakage |
| 3 | Cross-tier dep documentation | 30 min | Prevents future naming bugs |
| 4 | Patch checksum verification | 1-2 hours | Defense in depth |
| 5 | SOURCE_DATE_EPOCH | 30 min | Reproducibility foundation |
| 6 | Structured logging | 2-3 hours | Enables automated analysis |

---

## Verification

After each phase:
1. Run a full build and verify no regressions
2. Test that checksum verification catches a deliberately tampered tarball
3. Verify the dependency graph resolves cleanly (541 packages, 0 missing, 0 cycles)
4. Confirm cross-tier deps resolve correctly
5. Check SOURCE_DATE_EPOCH is set in build environment

---

## Relationship to Sentinel

Phases 1-3 are prerequisites for submitting InterGenOS to the Claude for Open Source program for Sentinel security scanning. The audit document (384KB, 12,370 lines) provides the baseline. These remediation steps move us from "implicit correctness" to "enforced correctness" — the minimum bar for a __Sentinel_CLASS__ analysis to produce meaningful results rather than flagging known gaps.
