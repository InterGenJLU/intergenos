# Install-GUI lane completeness audit — 2026-05-15

Lane scope: install-GUI code path AFTER `init.sh` routes to
`igos.mode=install-gui`. The build-orchestration lane owns init.sh
routing + builder coverage; the install-TUI lane is the parallel
mirror.

Authored by the install-GUI lane agent per LANE B dispatch on thread
`install-lane-completeness-2026-05-15` at 2026-05-15T14:10:53Z.

Master tip at start of audit: `42077637`.

## Verification matrix

Three columns: claim location, claim text, on-disk reality. Verdict =
`PASS` / `STUB` / `BLOCKER`. Stub = aspirational reference that doesn't
back; Blocker = build or runtime failure if shipped as-is.

### A. installer/init/init.sh — shared scaffold (live + install-gui)

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| A1 | init.sh:172 | `cat /proc/sys/kernel/random/uuid` → `/etc/machine-id` | Kernel virtual FS, always present in initramfs | PASS |
| A2 | init.sh:176 | write `/etc/hostname` | Basic FS write | PASS |
| A3 | init.sh:181 | mask `systemd-firstboot.service` via /dev/null symlink | systemd builtin path; mask works whether unit exists or not | PASS |
| A4 | init.sh:182 | mask `systemd-homed-firstboot.service` | systemd builtin path | PASS |
| A5 | init.sh:191-194 | mask 17 services (mariadb..apparmor..pcrlock) | All masks via /dev/null symlink; benign-if-missing | PASS |
| A6 | init.sh:214-222 | hand-write `/etc/{passwd,group,shadow,gshadow}` for liveuser | busybox-static `cat`+`sed`+`echo` available in initramfs | PASS |
| A7 | init.sh:227-229 | add liveuser to 10 capability groups (wheel/audio/video/input/plugdev/render/dialout/lp/users/cdrom) | LFS base etc/group ships all 10 entries; sed-append works | PASS |
| A8 | init.sh:234-235 | mkdir /home/liveuser + cp -a /etc/skel | /etc/skel populated by LFS base packages | PASS |
| A9 | init.sh:236-239 | write `/etc/tmpfiles.d/liveuser-home.conf` | systemd-tmpfiles in tree (systemd package) | PASS |
| A10 | init.sh:245-257 | write `/etc/gdm/custom.conf` AutomaticLogin=liveuser | gdm package present at packages/desktop/gdm/ | PASS |
| A11 | init.sh:263 | symlink `/usr/lib/systemd/system/gdm.service` ← display-manager.service | gdm package installs unit at canonical path (verified via `packages/desktop/gdm/90-gdm.preset` which enables `gdm.service`) | PASS |
| A12 | init.sh:268 | symlink `/usr/lib/systemd/system/graphical.target` ← default.target | systemd builtin target | PASS |
| A13 | init.sh:279 | `/sbin/agetty` in tty2 autologin ExecStart | util-linux ships agetty at /sbin/ in LFS base | PASS |
| A14 | init.sh:282 | symlink `/usr/lib/systemd/system/getty@.service` ← getty.target.wants | systemd builtin template | PASS |
| A15 | init.sh:289 | sed `/etc/shadow` root lastchg | busybox-static sed | PASS |
| A16 | init.sh:300-308 | write `/etc/dconf/db/local.d/00-live-screensaver` | dconf package at packages/desktop/dconf/ | PASS |
| A17 | init.sh:316-331 | write `igos-live-dconf-compile.service` referencing `/usr/bin/dconf` | dconf package installs `/usr/bin/dconf` | PASS |
| A18 | init.sh:334 | symlink `igos-live-dconf-compile.service` ← display-manager.service.wants | Service file written same scope; OK | PASS |

### B. installer/init/init.sh — install-gui ONLY block (lines 354-375)

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| B1 | init.sh:358-369 | write `/home/liveuser/.config/autostart/forge-gui.desktop` with `Exec=forge --mode gui --archives /var/lib/igos/archives --packages /var/lib/igos/packages` | Decomposes into 4 sub-claims (B1a-B1d below) | (composite) |
| B1a | (composite) | `forge` wrapper exists at `/usr/bin/forge` at runtime | forge package CANNOT BUILD today (Blocker A) | **BLOCKER** |
| B1b | (composite) | `forge --mode gui` accepted | `installer/__main__.py:131-135` parses `--mode` with choices `(gui,tui,live)` | PASS |
| B1c | (composite) | `/var/lib/igos/archives/` exists at runtime | `scripts/build-intergenos.sh:1087-1130` emits .igos.tar.gz archives to that path inside chroot; bakes into squashfs | PASS |
| B1d | (composite) | `/var/lib/igos/packages/` exists at runtime | pkm DB at `scripts/pkg-functions.sh:23` (`IGOS_PKG_DB="/var/lib/igos/packages"`); chroot-build-* phases populate during build; bakes into squashfs | PASS |
| B1e | (autostart context) | XDG autostart fires as liveuser (uid=1000) | `installer/__main__.py:166-170` requires `os.geteuid() == 0`; exits rc=1 otherwise. **No polkit rule, no sudo entry, no pkexec wrapper, no setuid bit exists in tree.** | **BLOCKER C** |
| B2 | init.sh:374 | mask `/etc/xdg/autostart/intergen-welcome.desktop` via /dev/null symlink | `packages/desktop/intergen-welcome/build.sh:112` installs at exactly that path; mask works | PASS |

### C. installer/init/init.sh — trailing `Switch root` section (lines 377-389)

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| C1 | init.sh:378-379 | `mkdir -p /run/intergenos` + write `$MODE` to `/run/intergenos/boot-mode` | grep across entire tree for `/run/intergenos/boot-mode` and `run/intergenos` returns ONLY this writer; **no reader exists**. Dead write. | STUB (DEAD CODE) |
| C2 | init.sh:382-383 | comment claims systemd reads `/run/intergenos/boot-mode` via generator at `/usr/lib/systemd/system-generators/igos-mode-generator` | No such generator anywhere in tree (grep `igos-mode-generator` only finds rulebook example + this comment). Explicitly cited by Rule 21 as a stub example. | STUB |
| C3 | init.sh:386 | comment: `install-gui -> graphical.target with forge-gui.service overlay` | The REAL mechanism is a per-liveuser `.config/autostart/forge-gui.desktop` written by init.sh:358-369. There is NO `forge-gui.service` anywhere in tree, and no service-overlay step. | STUB |
| C4 | init.sh:387 | comment: `install-tui -> multi-user.target with forge-tui@tty1.service` | Actual unit is `forge-tui.service` (NOT a template; no `@tty1`). Confirmed at `installer/data/forge-tui.service`. ConditionKernelCommandLine gates it; tty1 is set via TTYPath=/dev/tty1. | STUB |

### D. installer/init/cmdline.install-gui.txt

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| D1 | cmdline.install-gui.txt:1 | `igos.mode=install-gui quiet splash loglevel=3 rd.systemd.show_status=auto` | Valid kernel cmdline params; all four recognized by kernel/systemd; init.sh parses `igos.mode=install-gui` at line 41 | PASS |

### E. packages/desktop/forge/package.yml + build.sh

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| E1 | package.yml:11 | `source: file:///forge-1.0.0.tar.xz` | **Tarball does NOT exist anywhere** — exhaustive find across repo, /var/cache, /mnt, /home returned zero hits. Builder will fail at fetch. | **BLOCKER A** |
| E2 | package.yml:12 | `sha256: 650007abe6c258...` | Unverifiable until tarball regenerated; per `[[feedback_bundle_from_canonical_not_installed]]` tarball MUST be bundled from canonical `installer/` tree with SOURCE_DATE_EPOCH pinning. | (depends on E1) |
| E3 | package.yml:16-20 | runtime deps: python, gtk4, libadwaita1, pygobject3 | All 4 packages exist at canonical paths: `packages/core/python/`, `packages/desktop/{gtk4,libadwaita1,pygobject3}/` | PASS |
| E4 | package.yml:21-24 | `verify_paths: [/usr/bin/forge, .../site-packages/installer, .../forge-tui.service]` | Rule 20 compliant (3 declared paths); all three are install targets of build.sh do_install | PASS |
| E5 | build.sh:41 | `cp -a ./installer "${DESTDIR}/...site-packages/installer"` | Recipe runs at cwd-root of extracted tarball (with `tar --strip-components=1` per `[[reference_builder_tar_strip_components]]`); tarball wrapper expected to contain `installer/` as a child. Requires tarball (Blocker A). | PASS (if E1 fixed) |
| E6 | build.sh:49-55 | cat-write `/usr/bin/forge` shell wrapper | self-contained heredoc; OK | PASS |
| E7 | build.sh:59-60 | `install -m644 ./installer/data/forge-tui.service` | Source file at `installer/data/forge-tui.service` exists in repo; will be inside tarball | PASS (if E1 fixed) |
| E8 | build.sh:64-75 | cat-write `/usr/share/applications/forge-gui.desktop` with `Exec=forge --mode gui --archives /var/lib/igos/archives --packages /var/lib/igos/packages` | Live-mode user-click launcher (NoDisplay=false). Same UID elevation issue as B1e applies: clicking the desktop icon from GNOME launches as liveuser; forge exits because uid != 0. | **BLOCKER C** (same root cause) |
| E9 | build.sh:79 | `install -m644 ./forge.1 ...` | **CORRECTION 2026-05-15T14:25Z** — manpage EXISTS at canonical path `man/forge.1` (7077 bytes, authored 2026-05-06). Initial audit missed it. The bundler `build-forge-tarball.sh` (in flight per `14:22:16Z` thread message) picks it up via `man/forge.1` → `forge-1.0.0/forge.1` (renamed at tar time). Build.sh:79 then resolves correctly at cwd after `--strip-components=1`. | PASS |

### F. installer/__main__.py + installer/frontend/gui/

| # | File:line | Claim | Reality | Verdict |
|---|---|---|---|---|
| F1 | __main__.py:113 | `from .frontend.gui.window import run_installer as run_gui` | `installer/frontend/gui/window.py` exists | PASS |
| F2 | __main__.py:118 | `from .frontend.tui import run_installer as run_tui` | `installer/frontend/tui.py` exists | PASS |
| F3 | frontend/gui/__init__.py | imports `state`, lazy `window` + `screens.*` | All modules present: `state.py`, `style.py`, `window.py`, `integrity_dialog.py`, `screens/{welcome,keyboard_locale,disk,user,confirm,progress,done,_base}.py` | PASS |
| F4 | __main__.py:166-170 | `os.geteuid() != 0` → exit rc=1 | Hard requirement; no graceful UID elevation. Combined with autostart firing as liveuser = Blocker C. | (root cause for C) |

## Final disposition (2026-05-15T14:30Z post-fix)

| Blocker | Disposition | Owner | Commit |
|---|---|---|---|
| A — tarball missing | RESOLVED | build-orch lane | `067ecf6d` (scripts/build-forge-tarball.sh + phase_setup wiring) |
| B — manpage missing | FALSE POSITIVE | n/a | `man/forge.1` already in tree at 2026-05-06 |
| C — UID elevation gap | RESOLVED | install-GUI lane | (this lane B commit) — polkit policy + rule + Exec=pkexec rewrite |
| C1-C4 — init.sh stubs | RESOLVED | install-TUI lane | `028efc5a` (comment rewrite + verify_paths 3→5) |
| F2 — verify_paths gap | RESOLVED | install-TUI lane | `028efc5a` (extended to 5; this commit extends to 6 for polkit policy) |

After fixes: 35/38 PASS, 3 PREVIOUSLY-BLOCKED claims now PASS, 0 STUB remaining in this lane.

## Summary

**Total claims audited: 38** (split: 35 install-gui-lane-specific + 3 composite sub-claims under B1)

**Verdicts (revised 2026-05-15T14:25Z):**
- 31 PASS (was 30; E9 reclassified after finding `man/forge.1`)
- 4 STUB (init.sh:378-379 dead write + 3 inaccurate comments at 382-388) — the install-TUI lane (F1 finding) per coordination
- 2 BLOCKER (A: missing tarball — the build-orchestration lane per `14:22:16Z` (`build-forge-tarball.sh` in flight); C: UID elevation gap — mine, pending greenlight)
- 1 conditional PASS (E2 sha256 depends on the bundler completing)
- ~~Blocker B (missing manpage)~~ — FALSE POSITIVE. `man/forge.1` exists in tree; bundler picks it up.

**Blocker A: forge-1.0.0.tar.xz missing.** Builder cannot fetch the
source. Forge package never builds → /usr/bin/forge never lands in
chroot → autostart + launcher both fail at runtime even before UID
elevation matters.

**Blocker B: FALSE POSITIVE.** Manpage exists at `man/forge.1`. The
bundler `build-forge-tarball.sh` (in flight per `14:22:16Z` thread
message) renames it to `forge-1.0.0/forge.1` inside the bundle.
Build.sh:79 resolves correctly at cwd-root after `--strip-components=1`.
Initial audit missed the file because grep was scoped to
`packages/desktop/forge/` only; should have grep'd repo-wide for
`forge.1`. Lesson logged.

**Blocker C: UID elevation gap.** Autostart .desktop fires Forge as
liveuser (uid=1000); `installer/__main__.py` requires uid=0 and exits
rc=1 otherwise. No polkit rule, sudo entry, pkexec wrapper, or setuid
bit exists. Fix options:
- (a) Add polkit rule allowing liveuser to run forge as root via
  `pkexec`; rewrite autostart Exec= as `pkexec forge --mode gui ...`
- (b) Add sudoers entry NOPASSWD for liveuser to run /usr/bin/forge;
  rewrite Exec= as `sudo -n forge --mode gui ...`
- (c) Make /usr/bin/forge a wrapper that re-execs itself with pkexec
  when geteuid() != 0 (self-elevation pattern)

Option (a) is the canonical GTK installer pattern (Calamares, Anaconda
use it). Option (c) is the most contained but obscures the elevation
flow. Recommending (a).

**Stubs (4):** All in `init.sh:377-388`. Real mechanisms are the
autostart `.desktop` (GUI) + the `ConditionKernelCommandLine`-gated
service (TUI), both of which work directly off `/proc/cmdline` without
needing a generator. Fix: delete the dead `/run/intergenos/boot-mode`
write and rewrite the trailing comments to describe the real routing.

## Proposed fix order (revised 2026-05-15T14:25Z)

1. ~~Author `packages/desktop/forge/forge.1` manpage~~ — DROPPED. Manpage exists at `man/forge.1`. Duplicate I authored at `packages/desktop/forge/forge.1` deleted at this revision.
2. ~~Author tarball-bundler~~ — the build-orchestration lane per `14:22:16Z`, written as `scripts/build-forge-tarball.sh` and wired into `build-intergenos.sh:phase_setup`.
3. **Author polkit rule** at `packages/desktop/forge/org.intergenos.forge.policy` granting `liveuser` permission to invoke `/usr/bin/forge` as root via pkexec without password. Forge `build.sh` installs it to `/usr/share/polkit-1/actions/`. Polkit rule covers both modes (gui + tui) since the launcher .desktop hits both. **Pending greenlight on option (a).**
4. **Rewrite autostart Exec=** in both `installer/init/init.sh:363` and `packages/desktop/forge/build.sh:69` to `pkexec /usr/bin/forge --mode gui ...`. **Pending greenlight.**
5. **Extend `packages/desktop/forge/package.yml verify_paths`** from 3 → 5 entries to cover the launcher .desktop and the manpage (per install-TUI lane F2 finding). Adds:
   - `/usr/share/applications/forge-gui.desktop`
   - `/usr/share/man/man1/forge.1`
   - (and if polkit rule lands) `/usr/share/polkit-1/actions/org.intergenos.forge.policy`
6. **Defer**: init.sh:377-389 stubs (C1-C4) → install-TUI lane (F1 finding) per coordination.
7. **Re-verify** by tracing each row above with fixes applied; my final status posts the resolved column + commit SHAs.

## Out of scope (other lanes)

- `installer/data/forge-tui.service` content — install-TUI lane
- `installer/init/init.sh` routing logic (the `case $MODE` block) — build-orchestration lane
- builder phase coverage (chroot-build-desktop.sh including forge) — build-orchestration lane
- Anything not on the install-GUI code path
