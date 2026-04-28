# InterGenOS Full Build Remediation Plan — All Tiers

## Context

A full audit of 163 built desktop packages against BLFS 13.0 revealed systemic issues that undermine confidence in the entire build:

- **131 packages with dead code** — build.sh files never sourced due to build_style mismatch (exists across ALL tiers)
- **~19 desktop packages with lib64 install path issue** — meson defaulting to lib64 instead of lib
- **11 critical build-breaking problems** in desktop templates
- **22 packages missing required/recommended dependencies**
- **Duplicate build infrastructure** — hand-crafted bash script for Chapter 8 duplicates what the Python builder does

Two rogue sessions introduced unauthorized changes and template errors. Combined with the systemic dead code and lib64 issues, the entire build from toolchain forward needs to be audited, corrected, and rebuilt from scratch.

**The Prime Directive demands it:** *"InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust."*

**Key architectural decision:** The temporary Python built in LFS Chapter 7 (toolchain phase) is available inside the chroot before Chapter 8 begins. By pre-installing PyYAML, the Python builder can orchestrate the ENTIRE Chapter 8 build — eliminating the duplicate bash builder and unifying the pipeline under one orchestrator from Chapter 8 through desktop.

**Scope:** 497 packages across 4 tiers. Full audit against LFS 13.0 and BLFS 13.0. Builder refactoring. Unified build pipeline. Clean rebuild from scratch.

---

## Phase 1: Builder Refactoring

**Goal:** Three changes to the builder that fix systemic issues permanently.

### 1.1 Make build.sh always authoritative

**File:** `/mnt/intergenos/igos-build/builder.py` (~line 588)

When build.sh exists, always use CustomStyle regardless of build_style field:
```python
build_sh = pkg.template_path.parent / "build.sh" if pkg.template_path else None
if build_sh and build_sh.exists():
    style = get_style("custom")
else:
    style = get_style(pkg.build_style)
```

build_style becomes a label for humans and generate-templates.py, not a builder instruction.

### 1.2 Add --libdir=/usr/lib to MesonStyle fallback

**File:** `/mnt/intergenos/igos-build/styles/meson.py`

For the rare case of meson packages without build.sh, add `--libdir=/usr/lib` to the generated meson setup command. Belt-and-suspenders with the template fix.

### 1.3 Update generate-templates.py meson template

**File:** `/mnt/intergenos/scripts/generate-templates.py`

Add `--libdir=/usr/lib` to the `MESON_BUILD_SH` template so all future generated meson build.sh files include it.

### 1.4 Validate with dry-run

Parse all 497 packages to verify no breakage.

---

## Phase 2: Unify Build Pipeline — Python Builder for Chapter 8+

**Goal:** Eliminate the duplicate bash builder. One orchestrator from Chapter 8 through desktop.

### 2.1 Create chroot bootstrap for PyYAML

**New file or update to:** `/mnt/intergenos/scripts/chroot-setup.sh` or new `chroot-prep-builder.sh`

Before Chapter 8 starts, install PyYAML into the temporary Python:
```bash
# Extract PyYAML tarball and install into temporary Python
tar -xf /sources/PyYAML-*.tar.gz -C /tmp
cd /tmp/PyYAML-*
python3 setup.py install
cd / && rm -rf /tmp/PyYAML-*
```

Copy igos-build into the chroot:
```bash
rsync -av /mnt/intergenos/igos-build/ /mnt/igos/mnt/intergenos/igos-build/
```

### 2.2 Update build-intergenos.sh orchestrator

**File:** `/mnt/intergenos/scripts/build-intergenos.sh`

Change the core phase from calling `chroot-build-ch8.sh` to calling the Python builder:
```bash
# BEFORE (bash duplicate builder):
chroot $IGOS /bin/bash /mnt/intergenos/scripts/chroot-build-ch8.sh

# AFTER (unified Python builder):
chroot $IGOS /bin/bash -c "cd /mnt/intergenos && python3 -m igos-build \
    --tier core --tracked --skip-built"
```

Same pattern already used for desktop. Now core, base, and desktop all use the same builder.

### 2.3 Retire chroot-build-ch8.sh

Move to `scripts/archive/` or delete. The Python builder replaces it entirely. Keep for reference if desired.

### 2.4 Verify core packages parse correctly

Run `python3 -m igos-build --tier core --dry-run` inside the chroot to verify all 106 core packages parse and resolve dependencies correctly.

---

## Phase 3: Audit Toolchain (28 packages — LFS Ch. 5-7)

**Reference:** `docs/lfs-13.0/LFS-BOOK-13.0-SYSD.html` (Chapters 5-7)

**Note:** Toolchain runs OUTSIDE the chroot on the host, before Python exists. These packages use bash scripts and the existing toolchain build mechanism. They are NOT affected by the builder refactoring.

### 3.1 Audit all 28 toolchain packages

Launch parallel agents (2 batches of 14) to verify against LFS 13.0 Ch. 5-7:
- Version match with LFS 13.0
- Build instructions match exactly — configure flags, patches, seds
- Cross-compilation flags correct (--host, --build, --target)
- For 12 custom packages with build.sh: verify content matches LFS
- For 16 autotools packages without build.sh: verify autotools style produces correct output

### 3.2 Fix all issues found

### 3.3 Document findings

Output: `docs/research/build_system/toolchain_audit_2026-04-04.md`

---

## Phase 4: Audit Core (106 packages — LFS Ch. 8 + extras)

**Reference:** LFS 13.0 Ch. 8 + BLFS for extras (make-ca, curl, wget, git, cmake, etc.)

**Critical:** After Phase 1, the 51 packages with `build_style: autotools` + dead build.sh will become live code. Their build.sh content MUST be verified before rebuilding.

### 4.1 Audit all 106 core packages

Launch parallel agents (5-6 batches of ~18-20) to verify:
- Version match with LFS/BLFS 13.0
- Build instructions match exactly — seds, patches, configure flags, make targets
- All required/recommended deps listed
- build.sh content correct (will become live after Phase 1 builder change)
- Post-install hooks present where needed
- systemd (only meson package): verify --libdir=/usr/lib in build.sh

### 4.2 Fix all issues found

This tier is the foundation — get it right.

### 4.3 Document findings

Output: `docs/research/build_system/core_audit_2026-04-04.md`

---

## Phase 5: Audit Base (20 packages — BLFS end-user tools)

**Reference:** BLFS 13.0

### 5.1 Audit all 20 base packages

Single agent — small tier. Verify against BLFS 13.0:
- Version match
- Build instructions match
- Dependencies correct
- build.sh accurate (7 dead build.sh files become live after Phase 1)

### 5.2 Fix all issues found

### 5.3 Document findings

Output: `docs/research/build_system/base_audit_2026-04-04.md`

---

## Phase 6: Fix Built Desktop Packages (163 packages)

Audit already completed: `docs/research/build_system/desktop_audit_full_2026-04-04.md`

### 6.1 Fix 11 critical build-breaking issues

| Package | Fix |
|---------|-----|
| icu | Fix source URL to match version 78.2, update sha256 |
| vala | Change `make` to `make bootstrap` in build.sh |
| speex | Add speexdsp as second source, build both per BLFS |
| x265 | Add `-D GIT_ARCHETYPE=1` to cmake flags |
| shared-mime-info | Add `-Dupdate-mimedb=true` to build.sh |
| trove-classifiers | Add required sed for version string |
| npth | Remove wrong NSPR seds, replace with correct build |
| newt | Add BLFS sed for static lib to build.sh |
| slang | Use `make -j1 RPATH=` per BLFS |
| lame | Add BLFS sed for hardcoded lib path |
| lynx | Fix `--enable-locale-strstrcase` → `--enable-locale-charset` |

### 6.2 Add --libdir=/usr/lib to all meson build.sh files (~34 packages)

Script across all affected packages. Remove any standalone --libdir from package.yml configure_flags.

### 6.3 Fix missing dependencies (22 packages)

### 6.4 Fix missing/wrong configure flags (19 packages + 17 Xorg $XORG_CONFIG)

### 6.5 Clean up dead configure_flags in package.yml

Remove configure_flags from package.yml where build.sh is authoritative (prevents future confusion).

### 6.6 Generate build.sh for 9 packages missing them

bash-completion, help2man, libass, libatasmart, libbytesize, libfyaml, libnvme, localsearch, xcursorgen

### 6.7 Update stale version comments (~52 packages)

---

## Phase 7: Audit Unbuilt Desktop Packages (~180 packages)

**The largest audit phase.**

### 7.1 Audit all ~180 unbuilt desktop packages

Launch 7-8 parallel agents (batches of ~22-25) against BLFS 13.0. For each package:
1. Version matches BLFS 13.0
2. All required + recommended deps listed
3. Build instructions match — patches, seds, configure flags
4. build.sh correct for the build system
5. Meson packages include `--libdir=/usr/lib`
6. Source URLs correct and downloadable
7. SHA256 checksums present
8. Post-install hooks present where BLFS requires them

### 7.2 Apply all fixes from audit

New templates, build.sh corrections, dep additions, flag corrections, source URL fixes.

### 7.3 Document findings

Output: `docs/research/build_system/desktop_audit_unbuilt_2026-04-04.md`

---

## Phase 8: Final Validation

### 8.1 Dry-run parse all 497 packages

Verify every template parses cleanly.

### 8.2 Dependency graph validation

Run the builder's graph resolver across all tiers — no missing deps, no cycles.

### 8.3 Commit and push

Logical series of commits with all fixes.

---

## Phase 9: Restore VM and Full Rebuild

### 9.1 Restore build VM to fresh-ubuntu snapshot

Clean slate.

### 9.2 Full build from Chapter 5

```
toolchain (bash) → chroot-prep → PyYAML bootstrap → core (Python builder) → config → core-extra (Python builder) → base (Python builder) → desktop (Python builder) → image
```

### 9.3 Monitor and fix

With thorough auditing, failures should be minimal.

---

## Unified Build Pipeline (After This Plan)

```
┌─────────────────────────────────────────────────────┐
│  TOOLCHAIN (Ch. 5-7) — Bash scripts on host         │
│  28 packages, cross-compiled, includes python-tmp    │
└────────────────────────┬────────────────────────────┘
                         │ enter chroot
                         ▼
┌─────────────────────────────────────────────────────┐
│  BOOTSTRAP — Install PyYAML into temporary Python    │
│  Copy igos-build into chroot                         │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  CORE (Ch. 8) — Python builder                       │
│  106 packages, dependency-resolved, tracked          │
├─────────────────────────────────────────────────────┤
│  CONFIG (Ch. 9) — Shell scripts for system config    │
├─────────────────────────────────────────────────────┤
│  CORE-EXTRA — Python builder                         │
│  Additional core packages (TLS chain, PAM, etc.)     │
├─────────────────────────────────────────────────────┤
│  BASE — Python builder                               │
│  20 end-user tools                                   │
├─────────────────────────────────────────────────────┤
│  DESKTOP — Python builder                            │
│  343 packages, GNOME on Wayland                      │
└─────────────────────────────────────────────────────┘
```

**One builder. One set of templates. build.sh is always authoritative.**

---

## Key Files

### Builder (Phase 1)
- `/mnt/intergenos/igos-build/builder.py` — style selection logic
- `/mnt/intergenos/igos-build/styles/meson.py` — lib64 fallback
- `/mnt/intergenos/scripts/generate-templates.py` — meson template update

### Pipeline (Phase 2)
- `/mnt/intergenos/scripts/build-intergenos.sh` — master orchestrator
- `/mnt/intergenos/scripts/chroot-build-ch8.sh` — to be retired
- `/mnt/intergenos/scripts/chroot-build-desktop.sh` — reference for PyYAML bootstrap pattern

### Templates (Phases 3-7)
- `/mnt/intergenos/packages/toolchain/` — 28 packages
- `/mnt/intergenos/packages/core/` — 106 packages
- `/mnt/intergenos/packages/base/` — 20 packages
- `/mnt/intergenos/packages/desktop/` — 343 packages

### Reference Docs
- `docs/lfs-13.0/LFS-BOOK-13.0-SYSD.html`
- `docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html`

### Research Output
- `docs/research/build_system/desktop_audit_full_2026-04-04.md` — done
- `docs/research/build_system/toolchain_audit_2026-04-04.md` — Phase 3
- `docs/research/build_system/core_audit_2026-04-04.md` — Phase 4
- `docs/research/build_system/base_audit_2026-04-04.md` — Phase 5
- `docs/research/build_system/desktop_audit_unbuilt_2026-04-04.md` — Phase 7

## Verification

After full rebuild (Phase 9):
- All packages build without manual intervention through unified Python builder
- No libraries in `/usr/lib64/` — all in `/usr/lib/`
- Every build.sh is live code — no dead functions
- All caches populated (GIO modules, glib schemas, MIME database, gdk-pixbuf loaders)
- Manifest tracking clean and accurate for every package
- System boots and GNOME desktop loads
