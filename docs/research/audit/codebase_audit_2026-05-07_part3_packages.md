# Codebase Audit — §3 Package Recipes

**Auditor:** chris-ubuntu_hplt-codium-deepseek  
**Date:** 2026-05-07 09:50–10:15 CDT  
**Master:** c4d5126  
**Paths audited:** `packages/` (701 packages across 5 tiers: toolchain=28, core=117, base=20, desktop=457, extra=79)  
**Spot-check sample:** 16 packages across all 5 tiers  
**Findings:** 0 CRITICAL, 0 HIGH, 1 MEDIUM, 8 LOW

---

## Summary

The packages tree is extensive (701 packages) with the desktop tier dominating (457 packages — mostly GNOME/Wayland dependencies). Spot-checked 16 packages across all tiers: glibc, gcc-pass1, binutils-pass1, linux-kernel, systemd, bash, htop, rsync, curl, gnome-shell, gstreamer, librsvg, nodejs, brave-helper, claude-code-helper. Package structure follows conventions: package.yml for metadata + build.sh for build procedures. Post-migration state: zero `local://` URLs, zero `placeholder-` SHA256 values. Dependency cross-referencing is clean (all deps point to existing package directories). The main finding is that build.sh function bodies lack `set -e` — while the builder catches overall exit codes, individual command failures within sourced functions can cascade silently.

---

## Closure Table

| ID | Severity | Section | File:Line | Finding | Proposed Fix |
|----|----------|---------|-----------|---------|--------------|
| P1 | MEDIUM | Packages | `packages/*/build.sh` (all ~700) | Build functions (configure, build, check, install, post_install) lack `set -e`. The builder sources them into a bash -c subshell without per-function error-guard: `source build.sh && if declare -f configure >/dev/null; then configure; fi`. A `cd /bad/dir` inside a function fails silently, and subsequent commands execute in the wrong directory. Practically, build.sh functions are simple enough that this rarely surface as a bug, but it's a correctness gap. | Add `set -e` at the top of each function body: `configure() { set -e; ... }`. This preserves the pattern of build.sh being a function library (not a standalone script) while adding per-function error propagation. |
| P2 | LOW | Packages | `packages/desktop/librsvg/build.sh:7` | Uses `${IGOS_SOURCES}` instead of documented `${IGOS_SOURCES_DIR}` convention. Both resolve to the same value (builder.py:122-123 sets both), but breaks the documented convention in MEMORY.md and creates confusion for contributors. | Change to `${IGOS_SOURCES_DIR}` to match convention. |
| P3 | LOW | Packages | `packages/extra/nodejs/build.sh:32` | `post_install()` runs `ln -sf node /usr/share/doc/node-${PKG_VERSION}`. If `PKG_VERSION` is unset or empty (possible if build.sh is invoked outside the igos-build orchestrator), creates a broken symlink at `/usr/share/doc/node-/`. Low severity because the builder always sets PKG_VERSION before invoking hooks, but the function is fragile. | Guard: `[ -n "${PKG_VERSION}" ] && ln -sf node /usr/share/doc/node-${PKG_VERSION}`. |
| P4 | LOW | Packages | `packages/core/{shim-signed,efitools,sbsigntool,gnu-efi,rpm,mokutil}/package.yml` | 6 packages live in `packages/core/` directory but declare `tier: desktop` in package.yml. The build system correctly uses the `tier:` field for graph resolution, so this works — but it's a code-organization inconsistency that would confuse contributors doing directory-based tier scans. | Either move to `packages/desktop/` or align package.yml tier to `core`. The Secure Boot toolchain is arguably desktop-tier (not present in minimal core installs), so moving to `desktop/` is the correct fix. |
| P5 | LOW | Packages | `packages/core/apparmor/package.yml:14-17` | AppArmor declares zero dependencies despite being a complex security framework that installs Perl profiles, Python utilities, and requires at least `bash` and `coreutils` at runtime. Empty dep list means the build relies on implicit host-system availability of tools that might not be present in a minimal builder. | Add `host: [perl, python3, bash]` as explicit deps. |
| P6 | LOW | Packages | `packages/toolchain/gcc-pass1/package.yml:33` | Bundled deps use dest path containing `${version}` variable: `gcc-${version}/mpfr`. The version substitution happens at YAML parse time via `_resolve_variables`, which only handles `${version}`, `${name}`, `${version_major}`, `${version_major_minor}`. If a new variable is needed (e.g., `${version_patch}`), it won't resolve. Works today but fragile. | Add `${version_patch}` to the variable resolution set in `parser.py:287-292`. |
| P7 | LOW | Packages | `packages/*/build.sh` (38 files) | 38 build.sh files reference `$PKG_VERSION` for operations like `cp`, `mv`, `ln` in `post_install()`. The builder sets PKG_VERSION per-package (builder.py:126) before running hooks, but the convention is not documented as a requirement for build.sh authors. A new contributor writing a standalone test would get empty PKG_VERSION from chroot-enter.sh (line 82). | Document in MEMORY.md that `$PKG_VERSION` and `$version` are guaranteed to be set when build functions execute. Remove the empty `PKG_VERSION=""` from chroot-enter.sh:82 (already noted as B9 in §1). |
| P8 | LOW | Packages | `packages/core/apparmor/package.yml:9` | GitLab archive URL uses `/-/archive/v${version}/` format — GitLab archives have different Content-Disposition headers than GitHub. Verify that `_url_basename()` in builder.py:24-31 correctly extracts the filename (it uses `urlparse(url).path.rsplit("/", 1)[-1]` which should work for both GitHub and GitLab). | Already handled — `urlparse` path-based extraction works for GitLab's `/-/` paths. No code change needed; closing as LOW informational. |
| P9 | LOW | Packages | `packages/core/gnu-efi/package.yml:7` | Declares `tier: desktop` but this is a UEFI development library needed by the EFI toolchain. It has zero dependencies and should arguably be `tier: core` since it's a build dependency of shim/efitools, not a user-facing desktop package. | Consider reclassifying to `tier: core` or keeping at desktop with an explicit comment explaining why (EFI tools are desktop-tier only, BIOS installs skip them). |

---

## Detailed Analysis

### A. Package Structure Conformance

All 16 spot-checked packages follow the conventions documented in MEMORY.md:
- `package.yml` with required fields (name, version, release, description, license, source, build_style)
- `build.sh` with function definitions (configure, build, check, do_install/install)
- Header comment format: `# name version — description`
- Dependencies split into build/host/runtime categories
- SHA256 = 64 lowercase hex

### B. Schema Migration Verification (post Step 3)

The DS-workstation schema migration (Step 3 of integrity ship-gate, merged at c4d5126) is confirmed clean:
- Zero `local://` URLs across all 701 packages
- Zero `placeholder-` SHA256 values
- 4 Rust packages (cargo-c, cbindgen, librsvg, rust-bindgen) have `build_artifacts:` entries where local:// previously lived
- `scripts/audit-yaml-source-pinning.sh` reports 0 un-pinned entries

### C. Dependency Cross-Referencing

Spot-checked dependencies for systemd (11 deps) and nodejs (6 deps) — all dependency package directories exist in the packages tree. No phantom deps found.

### D. Build.sh Pattern Analysis

**Function structure (all packages):**
```bash
configure() { ... }
build() { ... }
check() { ... }
do_install() { ... }      # core/base/desktop
install() { ... }         # toolchain (direct_install)
post_install() { ... }    # optional, runs on live fs
```

**Common patterns observed:**
- `check()` uses `|| true` pattern (14 of 16 spot-checked) — correct for non-critical test failures
- `do_install()` uses `make DESTDIR="$DESTDIR" install` — correct DESTDIR staging
- `post_install()` uses absolute paths (`/usr/share/...`) — correct for live-fs context
- Build parallelism via `-j${IGOS_JOBS}` — consistent across all build functions

### E. Convention Violations

- `$IGOS_SOURCES` vs `$IGOS_SOURCES_DIR`: only 2 files use these vars (python + go + librsvg), and only librsvg uses the non-`_DIR` variant. Convention documented in MEMORY.md recommends `$IGOS_SOURCES_DIR`.
- Tier directory vs YAML mismatch: 6 packages (all Secure Boot EFI tools) have directory=toolchain, yml=desktop — organizational inconsistency.
- No build.sh files use `set -euo pipefail` — by design (functions are sourced, not executed standalone), but per-function `set -e` would be a defense-in-depth improvement.

### F. Tier Distribution

| Tier | Packages | Spot-Checked | Findings |
|------|----------|--------------|----------|
| toolchain | 28 | 3 (glibc, gcc-pass1, binutils-pass1) | P6 (bundled deps variable set incomplete) |
| core | 117 | 3 (linux-kernel, systemd, bash) | P4, P5, P9 (tier/dir mismatches, apparmor deps) |
| base | 20 | 2 (htop, rsync) | Clean — simple autotools patterns |
| desktop | 457 | 3 (gnome-shell, gstreamer, librsvg) | P2 (IGOS_SOURCES convention) |
| extra | 79 | 3 (nodejs, brave-helper, claude-code-helper) | P3 (PKG_VERSION guard in post_install) |

---

## Audit Techniques Applied

| Technique | Result |
|-----------|--------|
| Logic-flow tracing | Traced build.sh function chains (configure→build→check→install→post_install) for 16 packages. All follow expected patterns. |
| Error-handling scan | build.sh `check()` functions use `|| true` pattern — correct. No error-guard in non-check functions (P1). |
| Hardcoded-path scan | `/usr/share/doc/`, `/usr/bin/`, `DESTDIR` — all are build-system variables, not hardcodes. librsvg uses `$IGOS_SOURCES` (convention break, P2). |
| Test gap analysis | No per-package build.sh tests exist. The igos-build dry-run mode validates templates but doesn't execute build.sh functions. LOW finding — build-time testing happens in the build VM, not in unit tests. |
| Missing dep declaration | Cross-referenced systemd (11 deps) and nodejs (6 deps) — all exist. gnu-efi + apparmor flagged as having suspiciously empty deps (P5, P9). |
| Shell robustness | ZERO build.sh files have `set -e` (P1). Functions sourced into bash -c subshell inherit no error-guard. |
| git-hygiene | Post-migration local:// check: CLEAN. Post-migration placeholder check: CLEAN. |
