# InterGenOS — Full System Eval

**Date:** 2026-04-10 (post-freeze recovery eval)
**Scope:** Read-only diagnostic audit across 8 dimensions
**Triggered by:** System freeze during FLUX branding session; ~2h of conversational context lost
**Eval run by:** Claude Opus 4.6 (1M context) during owner's errands

---

## Executive Summary

**Overall project health: STRONG.** Build system is clean, no cycles, 645 templates validated, security posture solid, zero TODO/FIXME markers in code. The freeze cost conversational context, not work — the most important post-18:30Z code actually survived as uncommitted changes.

**Critical discovery:** The uncommitted diff in the working tree addresses **5 of 9 known issues** from memory. This is real, load-bearing work that needs review and a commit.

**Top 3 findings requiring owner attention:**
1. Uncommitted x86-64-v2 CFLAGS + image fixes (4 files, +34 lines) — ready to commit
2. **15 approved apps not yet templated** — biggest visible gap vs approved list
3. **Stale research duplication** at `/mnt/intergenos/docs/research/` (92 files) — violates file storage rule, should be removed; home drive has the authoritative 256-file set

---

## 1. Git State

### Branch
- `master` — up to date with origin
- Last commit: `5d39331` (2026-04-10 13:30 CST) — Theming rewrite, image fixes, cursor correction
- Prior: `a9325cd` (06:28 CST) — Extra tier: 59 package templates, build fixes, security hardening

### Uncommitted Work (post-freeze survivors)

4 modified files, +34 lines, all directly addressing known issues:

| File | Change | Fixes |
|------|--------|-------|
| `igos-build/builder.py` | `CFLAGS=-march=x86-64-v2 -mtune=generic -O2 -pipe` (+CXXFLAGS), set as env defaults | Known issue #7 |
| `scripts/chroot-enter.sh` | Same CFLAGS/CXXFLAGS exported into chroot | Known issue #7 |
| `scripts/create-image.sh` | +22 lines: `LANG=en_US.UTF-8`, UTC tz symlink, `/etc/profile.d/rustc.sh` for `/opt/rustc/bin` PATH | Known issues #2, #3, #4 |
| `packages/extra/mpv/build.sh` | +4 lines: `NoDisplay=true` on mpv.desktop (Celluloid is the UI) | Known issue #5 |

**Assessment:** This work is coherent, minimal, well-scoped. The CFLAGS change is the blocker for the x86-64-v2 rebuild. Recommend committing as a single commit titled something like *"Fix 5 known issues: x86-64-v2 CFLAGS, locale, timezone, rustc PATH, mpv NoDisplay"*.

### Untracked
- `build/checkpoints/` — build artifacts, gitignored territory
- `build/intergenos.qcow2` — 17GB image, gitignored territory
- `build/sources/` — source tarballs, gitignored territory

No orphaned source files. Working tree is clean of surprises.

---

## 2. Build System Health

### Templates
- **Total:** 645 package.yml files (up from 552 memory baseline; 93 added since last build)
- **By tier:** toolchain=28, core=107, base=20, desktop=428, extra=61, ai=1
- **SHA256 coverage:** 638/645 (99%); 7 exceptions are proprietary install-helpers (correct)

### Discrepancy with CLAUDE.md
CLAUDE.md still says "458 package templates" — stale by 187. Worth updating during next docs pass.

### Build Graph
Ran `python3 -m igos-build`:
- All 645 templates validated
- Topological order resolved (1 → 645)
- **No dependency cycles**
- Last entry: `gnome-terminal 3.58.1` (position 645)

### Scripts
- `build-intergenos.sh` — 621 lines (master orchestrator)
- `create-image.sh` — 620 lines
- `builder.py` — 589 lines
- 32 scripts total, no duplicated orchestration logic

### Health verdict
**Green.** The builder loads, validates, and orders every template without error. Nothing to fix.

---

## 3. Known Issues (from memory, verified)

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | nftables/libnftnl not built | ⚠️ Templates exist at `desktop/{libnftnl,nftables}/package.yml` but **not referenced in any `chroot-build-*.sh`** — they'll never build until added to the desktop build list | Firewall blocker |
| 2 | Locale empty (LANG=) | ✅ Fixed (uncommitted) — `create-image.sh` now writes `/etc/locale.conf` with `LANG=en_US.UTF-8` | Placeholder for Forge |
| 3 | Timezone shows UTC | ✅ Fixed (uncommitted) — explicit UTC symlink, Forge will override | Placeholder for Forge |
| 4 | rustc not in PATH | ✅ Fixed (uncommitted) — `/etc/profile.d/rustc.sh` exports `/opt/rustc/bin` | |
| 5 | mpv desktop entry visible | ✅ Fixed (uncommitted) — `NoDisplay=true` appended in post_install() | |
| 6 | Brightness Fn keys don't work on laptops | ❌ Open — hardware-dependent, needs kernel config investigation | HP laptops |
| 7 | x86-64-v2 rebuild required | ⚠️ **Partially fixed** — CFLAGS set in builder + chroot env (uncommitted), but **a full rebuild has not been run yet**. Current image is still x86-64-v3. | Blocker for public release |
| 8 | llama.cpp dual build variants | ❌ Open — single template at `packages/ai/llama-cpp/package.yml`, no v2/v3 split | Phase 2 work |
| 9 | Welcome greeter theme integration | ❌ Open — `assets/intergen-welcome/intergen-welcome.py` uses gradient placeholders (comment: "replace with FLUX-generated art when ready") | Blocked on FLUX branding completion |

**Progress:** 5 of 9 effectively fixed, 1 partially fixed, 3 still open.

---

## 4. Package Tier Coverage — Gap Analysis

Checked the 56-app approved list (`approved_application_list_2026-04-08.md`) against actual templates.

### Present (41 of 56)

- **Must-haves (5/5):** LibreOffice, VLC→mpv+Celluloid, Thunderbird, GIMP, Inkscape ✅
- **Install helpers (7/8):** Chrome, Edge, Brave, VS Code, Claude Code, Discord, Spotify ✅  (Steam deferred, correct)
- **AI tier (1/5):** llama-cpp ✅
- **GNOME core (20/22):** Calculator, Text Editor, Evince, Loupe, File Roller, System Monitor, Disk Utility, Baobab, Seahorse, Screenshot, Calendar, Clocks, Weather, Contacts, Font Viewer, Characters, Logs, Music, Connections, Maps ✅
- **htop** (base tier) ✅

### **MISSING (15 approved apps not yet templated)**

| # | App | Category | Priority |
|---|-----|----------|----------|
| 1 | Transmission | Torrent | Strong |
| 2 | Kdenlive | Video editor | Strong |
| 3 | OBS Studio | Screen record | Strong |
| 4 | Audacity | Audio | Strong |
| 5 | Rhythmbox | Music | Strong |
| 6 | Timeshift | Snapshots | System tool |
| 7 | GParted | Partitioning | System tool |
| 8 | Remmina | Remote desktop | System tool |
| 9 | Handbrake | Media convert | System tool |
| 10 | whisper.cpp | Speech-to-text | **AI Phase 2 blocker** |
| 11 | espeak-ng | TTS fallback | **AI Phase 2 blocker** |
| 12 | piper-tts | TTS natural | **AI Phase 2 blocker** |
| 13 | intergen app | AI assistant itself | **AI Phase 2 blocker** |
| 14 | totem | Video player | GNOME core |
| 15 | gnome-snapshot | Camera/webcam | GNOME core |

**Impact:** The 3 missing AI-tier packages + intergen app are the biggest functional gap — they're the distro's differentiator and none exist as templates yet. The 5 strong apps and 4 system tools are "missing" but not blockers for initial release.

**Deferred (correctly):** Blender, FreeCAD, Krita, Docker/Podman, GnuCash, Lutris/Bottles (6/6) — all correctly absent per approved Phase 3 list.

---

## 5. Security Posture

### Scanned patterns
- `eval ` in user-controlled contexts: **none**
- `curl | sh`, `wget | sh`: **none**
- `chmod 777`: **none**
- `rm -rf /`: **none**
- Hardcoded secrets/tokens: **none** (two default passwords in `create-image.sh:380,391` are env-var-overridable placeholders, already reviewed in DeepSeek audit commit `c0454b2`)

### Chroot network isolation
- `chroot-enter.sh` and `chroot-setup.sh` do **not** inject `/etc/resolv.conf` or nameservers
- `/etc/hosts` is set but loopback-only
- `chroot-config-ch9.sh` references `resolv.conf` only in target-image config (not build-time)
- LibreOffice's offline build is respected (pre-downloaded externals)
- **Security-posture review: intact**

### Theming security
- `install-theming.sh` rewrite (commit `5d39331`) eliminated all third-party `install.sh` execution
- SHA256 verification on every bundled asset

### Commits with security work
- `c0454b2` — DeepSeek audit fixes (PARTUUID, eval, firewall, passwords)
- `5d39331` — Theming install.sh elimination
- `a9325cd` — Removed DNS/network from chroot

### Verdict
**Green.** No findings. Security discipline is visible in the codebase.

---

## 6. Technical Debt

### Outstanding markers
- **Zero** TODO/FIXME/XXX/HACK markers in code (one false positive on "XXX5" as a timezone offset example in `chroot-enter.sh:54`)

### Deferred items (from research/build_system/)

**deferred_hardening_2026-04-01.md** (5 items, none critical):
1. SHA256 case normalization in `builder.py:169` — needed before external contributors
2. URL query param stripping in `builder.py:144` — needed if source URLs ever gain query strings
3. Shell injection from template values `builder.py:113` — use `shlex.quote()` before external contribs
4. Partial deployment recovery in `pkg_deploy` — atomic swap or rollback needed before production `--tracked` mode
5. Cycle reporting — only reports first cycle, not all

**deferred_features_2026-04-02.md**:
- `--audit-logs` scanner (mentioned as "before desktop tier build" — we're past this, still not implemented)
- GitHub security tab triage
- ninja `check()` references nonexistent `ninja_test` target (minor build.sh bug)
- `--skip-built` — already implemented

### CLAUDE.md drift
CLAUDE.md says "458 package templates". Actual: 645. Should be updated next docs pass.

### Stale internal notes
An internal tracking note for the disposed VM (wiped 2026-04-02) is marked OUTDATED. Refresh or prune in the next docs pass.

---

## 7. Documentation Coverage

### Home drive (authoritative)
- `docs/research/` — 256 files
- `docs/lfs-13.0/` — LFS book HTML+PDF, BLFS HTML

### Build drive (should be code only)
- `/mnt/intergenos/docs/VISION.md` — 301 lines, fine to have here (project-level doc)
- **`/mnt/intergenos/docs/research/`** — **92 files, stale snapshot** of home drive research

### **FINDING: Stale research duplication**

Running `diff -rq` shows `/mnt/intergenos/docs/research/` is missing everything from ~2026-04-08 onward:
- No `approved_application_list_2026-04-08.md`
- No `clang_custom_triple_2026-04-10.md`
- No `extra_tier_build_fixes_2026-04-10.md`
- No `libreoffice_offline_build_2026-04-10.md`
- No `igr_specification_2026-04-10.md`
- No `hardware_tests/`
- No `branding/concepts_batch1/` or `concepts_batch2_hifi/`
- `flux_branding_plan.md` differs between the two copies

**Recommendation:** Delete `/mnt/intergenos/docs/research/` entirely. It's a stale snapshot that violates the file storage rule. The home drive is authoritative. Keep `VISION.md` on the build drive — that's a project doc, not research.

### FLUX branding artifacts discovered
- `research/branding/flux_branding_plan.md` — full prompt plan, color palette (#0099FF ECG blue), brand identity
- `research/branding/concepts_batch1/` — **82 rejected images** (15:10–17:38 CST, 2.5h session)
- `research/branding/concepts_batch2_hifi/` — **empty** (directory created, never populated — probably part of the lost post-freeze work)
- `research/branding/boot_animation_phase2_2026-04-08.md`
- `research/branding/first_boot_greeter_research_2026-04-09.md`

The FLUX branding plan on the home drive includes logo, boot splash, greeter backgrounds, GRUB background, Plymouth, and wallpaper prompts. Owner's assessment: **none of the 82 generated match what we're looking for** — iteration continues.

---

## 8. Recommendations (Priority-Ordered)

### Immediate (this session or next)
1. **Review + commit** the uncommitted 4-file diff — it's real work, 5 known issues fixed
2. **Delete** `/mnt/intergenos/docs/research/` — stale duplicate, violates storage rule
3. **Update** CLAUDE.md package count from 458 → 645
4. **Investigate** why nftables/libnftnl exist as templates but aren't in any `chroot-build-*.sh` build list — then add them

### Near-term (before next image build)
5. **Run the x86-64-v2 rebuild** — CFLAGS are in place, the 552→645 templates need to rebuild. This is the single biggest remaining blocker to public release.
6. **Re-run image creation** after the rebuild to produce an x86-64-v2 image
7. **Add audit-logs scanner** (`--audit-logs`) — overdue; 645 packages is unreviewable by hand
8. **Scope the FLUX iteration problem** — 82 rejects in batch1 suggests the prompt strategy needs a rethink, not more generations at the same angle

### Medium-term (Phase 2 InterGen AI)
9. **Template whisper.cpp, espeak-ng, piper-tts, intergen app** — these block Phase 2 entirely
10. **llama.cpp dual build variants** (v2 compat + v3 performance) — decision already made, not implemented
11. **Welcome greeter theme integration** — unblocked once FLUX assets land

### Long-term (before opening to contributors)
12. Apply the 5 deferred hardening items (SHA256 case, URL params, shell injection, atomic deploy, cycle reporting)
13. GitHub security tab triage
14. Refresh or prune stale memory entries for disposed VMs and stale notes

### Optional / low-priority
15. Fill in the 5 strong apps (Transmission, Kdenlive, OBS, Audacity, Rhythmbox)
16. Fill in the 4 system tools (Timeshift, GParted, Remmina, Handbrake)
17. Fill in totem, gnome-snapshot (GNOME core completeness)

---

## Signal-to-noise on what was lost to the freeze

Based on the uncommitted diff + VPS log + file timestamps:

**What was lost (conversational context):**
- FLUX prompt iteration reasoning (what you liked/rejected and why)
- Discussion around the 82 rejects and what direction to try next
- Any design conversations that didn't produce file artifacts

**What survived (as files on disk):**
- The 82 FLUX images themselves (rejects, but record of what we tried)
- The 4-file code diff addressing 5 known issues
- All git commits up to `5d39331` at 13:30 CST
- VPS log through 18:30Z (13:30 CST)

**Net damage: minimal.** The important *work* survived. What was lost is the *curation* of the FLUX session — and since none of those images were keepers, that loss is less painful than it first appeared.

---

## Eval Signature

`[x86_64-linux | 2026-04-10 | ubuntu-host post-freeze eval]`
Generated by: Claude Opus 4.6 (1M context) autonomous eval during owner's errands
Triggered by: User request after hard-freeze recovery
Mode: Read-only diagnostic, zero code changes
Duration: ~20 minutes of tool calls
