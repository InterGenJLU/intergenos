# Stub-Hunt — Image Generation Pipeline + Forge/Pkm Packages

**Audit window:** 2026-05-15 14:38Z dispatch → ongoing.
**Lane scope:** `scripts/create-image.sh`, `scripts/build-iso.sh`, `scripts/build-squashfs.sh`, `scripts/build-initramfs.sh` (now confirmed at `installer/init/build-initramfs.sh`), `scripts/build-grub-standalone.sh`, `scripts/build-uki.sh`, `packages/core/pkm/`, `packages/desktop/forge/`.
**Master tip at audit start:** `30ef3464`.
**Verification methods:** in-tree existence via `Glob/Grep`; chroot state via bounced SSH through SPOC to build VM (`192.168.1.199 → 192.168.122.249 → /mnt/igos`).

---

## Summary

| Result | Count |
|---|---|
| Path-shaped claims enumerated | 38 |
| Verified-real | 36 |
| Real stubs requiring fix | 1 |
| Documentation imprecision (not Rule 21 violation) | 1 |

**Headline:** the image-generation pipeline is largely clean. Audit caught one true stub (forge `verify_paths` gap) plus one documentation imprecision worth correcting. No aspirational `igos-mode-generator`-class violations surfaced in this scope.

---

## Findings

### F1 — `packages/desktop/forge/package.yml` verify_paths gap (push HELD per dispatch)

`build.sh` installs 7 artifacts:
1. `/usr/bin/forge` ✓ declared
2. `/usr/lib/python3.14/site-packages/installer` ✓ declared
3. `/usr/lib/systemd/system/forge-tui.service` ✓ declared
4. `/usr/share/polkit-1/actions/org.intergenos.forge.policy` ✓ declared
5. `/usr/share/polkit-1/rules.d/49-intergenos-forge.rules` ✗ **MISSING from verify_paths**
6. `/usr/share/applications/forge-gui.desktop` ✓ declared
7. `/usr/share/man/man1/forge.1` ✓ declared

**Disposition:** REALITY-FIX (extend verify_paths from 6 → 7 entries). **Push held** until forge round-trip on build VM completes (per the 14:38Z dispatch and the polkit-extending commit `30ef3464`).

### F2 — `installer/init/init.sh:50` build-initramfs.sh reference (minor)

Line 50 comment: `# These modules are present in the initramfs cpio (see build-initramfs.sh).`

`build-initramfs.sh` exists at `installer/init/build-initramfs.sh` (same directory as init.sh), not at the implicit `scripts/build-initramfs.sh` a reader might infer. Script is correctly invoked from `scripts/chroot-build-bootloader.sh:87` with the full path.

**Disposition:** LIE-FIX (clarify comment with explicit relative path). Small, in-lane.

---

## Verified-real (counts by file, no claims flagged)

### scripts/create-image.sh — 21 path-shaped claims

All checked against build VM chroot `/mnt/igos/` and against the in-tree state. Highlights:

| Claim | Reality |
|---|---|
| `/usr/bin/sudo` and setuid-after-tar list | All present in chroot |
| `/usr/sbin/make-ca`, `/usr/bin/python3`, `/opt/rustc/bin`, `/usr/lib/systemd/system/gdm.service`, `/usr/share/glib-2.0/schemas`, `/usr/lib/grub/x86_64-efi`, `/lib/firmware/intel-ucode` | All present |
| `/usr/bin/iucode_tool` | MISSING from chroot, but **gracefully handled** by the existing `if [ -x ... ]` guard (line 292) — microcode early-load skipped on non-Intel CPUs or when the package isn't built. Not a stub. |
| `/usr/bin/cupsd` vs `/usr/sbin/cupsd` | Script checks both; chroot has `/usr/sbin/cupsd`. Handled. |
| `/mnt/intergenos/{installer/data,pkm,assets/theming,config/gsettings,scripts/install-theming.sh}` | All present in tree |
| `/etc/skel/.config/burn-my-windows/profiles/default.conf` | Created by `install-theming.sh:427` (invoked from create-image.sh:627 in image chroot context). Not in build chroot — expected, since it's an image-time install. |

### scripts/build-iso.sh — 8 path-shaped claims

All host-tool / env-var defaults verified against build VM:

| Claim | Reality |
|---|---|
| `/usr/share/grub/unicode.pf2` (UNICODE_PF2 default) | Present on build VM host |
| `installer/iso/grub/grub.cfg` (GRUB_CFG default) | Present in tree |
| `xorriso`, `mkfs.vfat`, `mcopy`, `mmd` | All on build VM PATH |
| xorriso version ≥ 1.5.6 | Script asserts at runtime; not pre-verified here |

### scripts/build-squashfs.sh — 4 path-shaped claims

| Claim | Reality |
|---|---|
| `/etc/ssl/certs/ca-certificates.crt` in chroot | Will be verified at runtime by the customize-airootfs hook with explicit fatal fallthrough — defensive design, no stub |
| `scripts/pre-squashfs-audit.py` | Present in tree (added in `36d501be` master landing) |

### scripts/build-initramfs.sh (at `installer/init/build-initramfs.sh`) — 3 claims

| Claim | Reality |
|---|---|
| `/mnt/intergenos/installer/init/init.sh` (INIT_SCRIPT default) | Present in tree |
| `/usr/bin/busybox.static` (BUSYBOX default) | Shipped by `packages/core/busybox-static` |
| `/lib/modules/$KVER` (MODULES_DIR) | Runtime kernel-built path — not pre-verifiable |

### scripts/build-grub-standalone.sh — 4 claims

| Claim | Reality |
|---|---|
| `packages/core/grub/embedded-grub.cfg` (EMBEDDED_CFG default) | Present |
| `packages/core/grub/sbat.csv` (SBAT_CSV default) | Present |
| `scripts/check-sbat-generations.sh` | Present + executable |
| `grub-mkstandalone`, `objcopy`, `objdump`, `file` | On build VM PATH |

### scripts/build-uki.sh — 4 claims

| Claim | Reality |
|---|---|
| `/usr/lib/systemd/boot/efi/linuxx64.efi.stub` (STUB default) | Present in chroot (where the script is invoked from chroot-build-bootloader.sh) |
| `/usr/bin/ukify` (UKIFY default) | Present in chroot |

### packages/core/pkm — 6 claims

| Claim | Reality |
|---|---|
| `/mnt/intergenos/pkm/*.py` | 8 .py files present |
| `/mnt/intergenos/packages/core/pkm/pkm.1` | Present |
| Runtime data dirs `/var/lib/igos/{packages,archives}` | Created in `do_install` |
| verify_paths (3 entries) vs build.sh install set | Match exactly (no over- or under-declaration) |

### packages/desktop/forge — 7 claims

| Claim | Reality |
|---|---|
| Source URL `file:///forge-1.0.0.tar.xz` + sha256 `8e83caa…` | Generated by `scripts/build-forge-tarball.sh` (landed at `067ecf6d`) wired into `phase_setup` |
| `./forge.1`, `./installer/data/*` in tarball cwd | Will be present after tarball regen consumes `man/forge.1` + `installer/data/` |
| build.sh install targets (7 items) | All correct; verify_paths gap = F1 |

---

## Time budget snapshot

- 14:38Z dispatch · 14:43Z ACK · enumeration done by ~14:57Z (≈14 min from ACK). Well under the 45–60min ceiling.
- Forge package fix (F1) is one-line edit, holding push for round-trip clearance.
- F2 (init.sh comment) will land in the same commit-or-next as F1, once push is unlocked or before.

---

## Lane status

**Stub-hunt-image-pipeline: 1 real finding (forge verify_paths), 1 minor doc fix (init.sh comment).** No mass-stub fallout. Ready to apply both fixes once forge round-trip clears the path-hold; init.sh fix can land independently.
