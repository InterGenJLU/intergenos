# Live Session & Installer Architecture — FINALIZED
**Date:** April 10, 2026 | **For:** InterGenOS 1.0-dev
**Status:** APPROVED — All decisions made. Ready for formal implementation plan.

---

## Document History

| Date | Document | Purpose |
|------|----------|---------|
| Apr 9 | `live_session_and_installer_architecture_2026-04-09.md` | Original research (8 open questions) |
| Apr 10 | `live_session_architecture_review_2026-04-10.md` | Architecture review (all 8 answered, 2 pushbacks) |
| Apr 10 | **This document** | Finalized architecture with all decisions incorporated |

---

## Table of Contents
1. [What We're Building](#1-what-were-building)
2. [Approved Decisions](#2-approved-decisions)
3. [Architecture Overview](#3-architecture-overview)
4. [Live Session Boot Chain](#4-live-session-boot-chain)
5. [Custom Initramfs — The InterGenOS Way](#5-custom-initramfs)
6. [Squashfs Image — Source and Integrity](#6-squashfs-image)
7. [The Forge GUI Installer](#7-forge-gui-installer)
8. [Installation Flow — unsquashfs + Configure](#8-installation-flow)
9. [The "Try or Install" Experience](#9-try-or-install)
10. [ISO Assembly Pipeline](#10-iso-assembly-pipeline)
11. [Implementation Order](#11-implementation-order)
12. [What Already Exists](#12-what-already-exists)
13. [What Needs Building](#13-what-needs-building)
14. [Sources](#14-sources)

---

## 1. What We're Building <a name="1-what-were-building"></a>

A user downloads an InterGenOS ISO, writes it to USB, boots it, and sees a GRUB menu:

```
┌──────────────────────────────────────┐
│         InterGenOS 1.0                │
│                                      │
│  > Try InterGenOS                    │
│    Install InterGenOS                │
│    ─────────────────                 │
│    Boot from first hard disk         │
│                                      │
│  Use arrows to select, Enter to boot │
└──────────────────────────────────────┘
```

- **"Try"** boots a full GNOME desktop from RAM. The user can explore, test hardware, browse the web. An "Install InterGenOS" launcher sits on the desktop.
- **"Install"** boots the same live desktop but auto-launches the Forge GUI installer.

Both paths use the same live image — one squashfs, one initramfs, one kernel.

### End-to-End Requirements
1. A compressed root filesystem (squashfs) containing the full InterGenOS desktop
2. A custom initramfs that finds, verifies, mounts, and overlays that squashfs
3. A GRUB bootloader configured for ISO/USB with try/install options
4. An overlayfs (tmpfs upper) so the live session is writable
5. A GTK4/libadwaita GUI installer (Forge) that runs from the live session
6. An ISO assembly pipeline (xorriso) producing a hybrid BIOS+UEFI ISO
7. USB writing instructions (dd, Ventoy, Etcher)

---

## 2. Approved Decisions <a name="2-approved-decisions"></a>

All questions from the original research have been answered. These are **final decisions**, not recommendations.

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Package group selection during install? | **Full desktop image only (Phase 1)** | Ship one complete desktop. Package removal is a Phase 2 feature. |
| 2 | Multiple squashfs images or one? | **One squashfs** | Simpler initramfs, simpler installer, one thing to test. |
| 3 | Persistent live sessions? | **Not Phase 1** | Real complexity for a nice-to-have. Revisit after first release. |
| 4 | Live session auto-login user? | **`liveuser` with auto-login, no password** | Standard approach. Deleted during installation. |
| 5 | Plymouth for live boot? | **No — GRUB theme straight to GDM** | We have the DRM boot animation for installed systems. Keep live boot simple. |
| 6 | Network install option? | **Not Phase 1** | Adds complexity for a niche use case. Full ISO is sufficient. |
| 7 | Installer framework or custom? | **Custom (Forge)** | We have the backend. unsquashfs path is a small addition. No framework assumptions. |
| 8 | Build order? | **GUI installer first, then ISO wrapping** | Test on a running system pointed at a target disk. Once it works, the ISO is just packaging. |

### Additional Decisions from Review

| Item | Decision | Rationale |
|------|----------|-----------|
| Initramfs approach | **Custom /init script** | LFS way. ~100 lines of shell + busybox. No casper, no dracut, no mkinitcpio. Prime Directive: transparent, auditable. |
| Install method | **Hybrid: unsquashfs + pkm registration** | unsquashfs for speed (~2x faster than rsync), pkm registration for package tracking. |
| Squashfs source | **Clean deployed image, NOT raw build chroot** | Build chroot contains sources, artifacts, infrastructure. Squash from create-image.sh output or add dedicated `phase_iso` to orchestrator. |
| Squashfs integrity | **SHA256 verification in initramfs before mount** | Security: tampered USB must be caught before user sees a desktop. SHA256 for Phase 1, GPG-signed or dm-verity for later. |

---

## 3. Architecture Overview <a name="3-architecture-overview"></a>

### The Complete Stack
```
┌─────────────────────────────────────────────────────────────────┐
│                        ISO/USB Media                             │
│                                                                  │
│  /boot/vmlinuz              InterGenOS kernel (6.18.x)          │
│  /boot/initramfs-live.img   Custom initramfs (busybox + init)   │
│  /boot/intel-ucode.img      Intel microcode (early-load)        │
│  /boot/grub/grub.cfg        GRUB config (try/install entries)   │
│  /boot/grub/themes/         InterGenOS GRUB theme               │
│  /EFI/BOOT/grubx64.efi     GRUB EFI binary                     │
│  /LiveOS/filesystem.squashfs  Complete InterGenOS desktop        │
│  /LiveOS/filesystem.sha256   SHA256 checksum for verification   │
│  /IGOS_LIVE                  Media identification marker        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Filesystem Layers (at runtime)               │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  overlayfs merge → /  (what the user sees)                │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  upper: tmpfs in RAM (all writes — lost on reboot)        │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  lower: squashfs (read-only — the real OS, ~2-4GB)        │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  media: ISO/USB device (read-only — accessible at         │  │
│  │         /run/initramfs/live for installer access)         │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### RAM Requirements
- Squashfs is NOT decompressed into RAM — accessed via random-access decompression
- Only modified files consume RAM (in the tmpfs upper layer)
- GNOME desktop: ~800MB running
- tmpfs overlay: ~200-500MB (depends on user activity)
- **Minimum: 4GB RAM** for comfortable live session with GNOME

---

## 4. Live Session Boot Chain <a name="4-live-session-boot-chain"></a>

```
BIOS/UEFI
  → GRUB (from ISO/USB)
    → loads kernel + initramfs + microcode
      → initramfs /init runs:
        1. Mount /proc, /sys, /dev
        2. Load kernel modules (squashfs, overlay, loop, storage drivers)
        3. Search block devices for /LiveOS/filesystem.squashfs
        4. Verify SHA256 checksum (security check)
        5. Mount squashfs read-only
        6. Create tmpfs for writes
        7. Mount overlayfs (lower=squashfs, upper=tmpfs)
        8. Move media mount into new root at /run/initramfs/live
        9. exec switch_root → /sbin/init (systemd)
          → systemd boots normally
            → GDM starts
              → Auto-login as liveuser
                → GNOME desktop with "Install InterGenOS" launcher
```

---

## 5. Custom Initramfs — The InterGenOS Way <a name="5-custom-initramfs"></a>

No casper. No dracut. No mkinitcpio. A custom `/init` script — the LFS way.

### Why Custom
- Complete control, no external dependencies or framework assumptions
- Tiny: busybox (static) + ~100-line shell script
- Transparent: auditable in 5 minutes
- Prime Directive: no hidden magic

### Initramfs Contents
```
initramfs-live.img (cpio.gz)
├── /init                          # Custom init script
├── /bin/busybox                   # Static busybox
├── /bin/sh → busybox              # Symlinks for all needed applets
├── /lib/modules/<version>/
│   ├── squashfs.ko                # Squashfs filesystem
│   ├── overlay.ko                 # Overlayfs
│   ├── loop.ko                    # Loop device
│   ├── iso9660.ko                 # CD/DVD boot
│   ├── vfat.ko                    # USB FAT partitions
│   ├── usb-storage.ko             # USB mass storage
│   ├── sd_mod.ko                  # SCSI disk
│   ├── sr_mod.ko                  # SCSI CD-ROM
│   ├── ehci-hcd.ko               # USB 2.0
│   ├── xhci-hcd.ko               # USB 3.x
│   └── nvme.ko                    # NVMe (for NVMe USB enclosures)
├── /dev/                          # Mount point (devtmpfs)
├── /proc/                         # Mount point
├── /sys/                          # Mount point
├── /mnt/{media,squashfs,tmpfs,root}  # Working mount points
└── /etc/                          # Minimal config
```

### Init Script (with integrity check)
```bash
#!/bin/sh
# InterGenOS Live Boot Init
# Prime Directive: transparent, auditable, no hidden magic

# Essential filesystems
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

# Load required modules
for mod in overlay squashfs loop iso9660 vfat usb_storage sd_mod sr_mod \
           ehci_hcd xhci_hcd nvme; do
    modprobe "$mod" 2>/dev/null
done

# Allow USB devices to settle
sleep 2

# Rescue shell on failure
rescue() {
    echo ""
    echo "InterGenOS live boot failed: $1"
    echo "Dropping to rescue shell. Type 'reboot' to restart."
    exec /bin/sh
}

# Find live media — search all block devices for our marker
LIVE_FOUND=0
for dev in /dev/sd[a-z]* /dev/sr[0-9]* /dev/nvme[0-9]*p[0-9]*; do
    [ -b "$dev" ] || continue
    mount -o ro "$dev" /mnt/media 2>/dev/null || continue
    if [ -f /mnt/media/LiveOS/filesystem.squashfs ]; then
        LIVE_FOUND=1
        break
    fi
    umount /mnt/media
done
[ "$LIVE_FOUND" = "1" ] || rescue "Could not find InterGenOS live media"

# Security: Verify squashfs integrity before mounting
if [ -f /mnt/media/LiveOS/filesystem.sha256 ]; then
    echo "Verifying installation media integrity..."
    EXPECTED=$(cat /mnt/media/LiveOS/filesystem.sha256)
    ACTUAL=$(sha256sum /mnt/media/LiveOS/filesystem.squashfs | cut -d' ' -f1)
    if [ "$EXPECTED" != "$ACTUAL" ]; then
        echo ""
        echo "============================================"
        echo "  SECURITY: Integrity check FAILED"
        echo "============================================"
        echo "  Expected: $EXPECTED"
        echo "  Actual:   $ACTUAL"
        echo ""
        echo "  The installation media may be corrupted"
        echo "  or tampered with. DO NOT PROCEED."
        echo "============================================"
        rescue "Squashfs integrity verification failed"
    fi
    echo "Integrity verified."
fi

# Mount squashfs read-only
mount -t squashfs -o ro /mnt/media/LiveOS/filesystem.squashfs /mnt/squashfs \
    || rescue "Failed to mount squashfs"

# Create tmpfs for overlay writes (75% of RAM)
mount -t tmpfs -o size=75% tmpfs /mnt/tmpfs
mkdir -p /mnt/tmpfs/upper /mnt/tmpfs/work

# Create overlayfs union
mount -t overlay overlay \
    -o lowerdir=/mnt/squashfs,upperdir=/mnt/tmpfs/upper,workdir=/mnt/tmpfs/work \
    /mnt/root \
    || rescue "Failed to mount overlayfs"

# Make live media accessible to the installer
mkdir -p /mnt/root/run/initramfs/live
mount --move /mnt/media /mnt/root/run/initramfs/live

# Hand off to systemd
umount /proc /sys /dev
exec switch_root /mnt/root /sbin/init
```

---

## 6. Squashfs Image — Source and Integrity <a name="6-squashfs-image"></a>

### Source: Clean Deployed Image (NOT Raw Chroot)

**Critical decision from review:** The squashfs MUST be built from a clean, deployed system image — NOT from the raw build chroot at `/mnt/igos`.

The build chroot contains:
- `/mnt/intergenos/` — the entire build infrastructure
- `/sources/` — hundreds of source tarballs (~6GB)
- Build work directories, logs, staging artifacts (potentially 50GB+)

**Approach:** Either:
1. Mount the qcow2 image produced by `create-image.sh` and squash from that, OR
2. Add a `phase_iso` to the build orchestrator that performs a dedicated clean-and-squash step after `phase_image`

The pipeline must **enforce** cleanliness, not rely on manual cleanup.

### What the Squashfs Must Contain
- Full InterGenOS desktop (core + base + desktop tiers)
- Clean configuration (no user-specific state from the build)
- The Forge installer application
- Pre-populated pkm database (all installed packages registered)
- Post-install hook scripts (for running in chroot on the target)
- GRUB binaries (for installing bootloader to target disk)
- squashfs-tools (for `unsquashfs` during installation)
- Welcome greeter and boot animation (for first boot on target)
- `liveuser` account with GDM auto-login configured
- Desktop launcher for installer (`/usr/share/applications/forge-installer.desktop`)

### What the Squashfs Must NOT Contain
- Source tarballs
- Build scripts and infrastructure (`igos-build/`, `scripts/`)
- Package templates (`packages/`)
- Build logs and work directories
- Development tools not needed at runtime
- The build chroot's `/boot` (kernel/initramfs are separate on the ISO)

### Integrity Verification (Security)

At ISO build time:
```bash
sha256sum filesystem.squashfs | awk '{print $1}' > filesystem.sha256
```

At boot time (in the init script): the SHA256 is verified BEFORE the squashfs is mounted. A mismatch halts boot with a clear security warning and drops to rescue shell. The user never sees a desktop from tampered media.

**Phase 2 enhancements:**
- GPG-signed checksum file, verified with public key embedded in initramfs
- dm-verity on the squashfs (kernel-level block integrity, Android/ChromeOS approach)

### Compression
```bash
mksquashfs <source> filesystem.squashfs \
    -comp xz -Xbcj x86 -b 1M \
    -e boot \
    -e var/cache/pkm \
    -e tmp \
    -e var/tmp
```
- **XZ compression:** ~23% better ratio than gzip
- **`-Xbcj x86`:** Bytecode filter for better compression of x86 binaries
- **1MB block size:** Good balance of compression ratio vs random access speed
- **Expected size:** ~2-4GB compressed for full GNOME desktop

---

## 7. The Forge GUI Installer <a name="7-forge-gui-installer"></a>

### Technology: GTK4 + libadwaita (Python)
Matches the welcome greeter, matches the desktop, matches our visual language. No new dependencies.

### Screen Flow
```
Forge Installer — GTK4/libadwaita

  Screen 1: Welcome
  ├── InterGenOS branding
  ├── Language selection
  └── Keyboard layout

  Screen 2: Disk Selection
  ├── Available disks (excludes live media device)
  ├── EFI/BIOS auto-detected and displayed
  ├── "Use entire disk" (Phase 1 — only option)
  └── Advanced partitioning (Phase 2 — LVM, LUKS, manual)

  Screen 3: User Setup
  ├── Full name, username
  ├── Password + confirmation (strength indicator)
  ├── Hostname
  ├── Timezone (auto-detect from IP or manual)
  └── Root password (or "same as user" option)

  Screen 4: Confirmation
  ├── Summary of all selections
  ├── "THIS WILL ERASE ALL DATA ON <disk>" warning
  └── Requires explicit confirmation to proceed

  Screen 5: Installation Progress
  ├── Partitioning and formatting
  ├── Extracting system (unsquashfs with progress bar)
  ├── Configuring system (fstab, hostname, locale, etc.)
  ├── Creating user accounts
  ├── Installing bootloader
  ├── Running post-install hooks
  └── Each step with status indicator

  Screen 6: Complete
  ├── "Installation complete!"
  ├── "Remove the USB drive and restart your computer"
  └── Reboot button
```

### Backend Architecture
```
installer/
├── __main__.py          # Entry point (EXISTING — extend for GUI)
├── __init__.py          # Module definition (EXISTING)
├── frontend/
│   ├── tui.py           # ncurses TUI (EXISTING — keep for server/SSH installs)
│   └── gui.py           # NEW: GTK4/libadwaita GUI
├── backend/
│   ├── disks.py         # EXISTING: partition, mount, format, EFI/BIOS detect
│   ├── extract.py       # NEW: unsquashfs with progress parsing
│   ├── config.py        # EXISTING: fstab, hostname, locale, timezone, os-release
│   ├── users.py         # EXISTING: user creation, passwords, groups
│   ├── bootloader.py    # EXISTING: GRUB install (EFI + BIOS)
│   ├── hooks.py         # EXISTING: chroot, virtual FS, post-install hooks
│   └── packages.py      # EXISTING: pkm integration (extend for post-unsquashfs registration)
└── data/
    ├── forge-installer.desktop  # NEW: desktop launcher
    └── forge-installer.css      # NEW: GTK4 CSS (InterGenOS visual language)
```

### Key Design Constraints
- Runs on the live overlay (tmpfs) — no persistent storage available
- Must exclude the live media device from disk selection
- Must handle both EFI and BIOS boot modes
- Permission escalation via pkexec (or launched as root)
- Must parse `unsquashfs` output for progress reporting
- Must not crash under RAM pressure (live session + GNOME + installer)

---

## 8. Installation Flow — unsquashfs + Configure <a name="8-installation-flow"></a>

### The Hybrid Method (APPROVED)
**unsquashfs** for speed (entire OS extracted in one operation), then **pkm registration** for package tracking, then **chroot configuration** for the target system.

### Step-by-Step

```
1. PARTITION & FORMAT
   └── partition_disk() creates GPT table
   └── EFI: 512MB FAT32 ESP + remaining ext4 root
   └── BIOS: 1MB BIOS boot partition + remaining ext4 root

2. MOUNT TARGET
   └── Mount root at /mnt/target
   └── Mount ESP at /mnt/target/boot/efi (if EFI)

3. EXTRACT SYSTEM (unsquashfs)
   └── unsquashfs -f -d /mnt/target /run/initramfs/live/LiveOS/filesystem.squashfs
   └── Parse unsquashfs stdout for progress (file count / total)
   └── ~150 seconds for a full desktop image (vs ~284s with rsync)
   └── Peak memory: ~540MB (adjustable with -data-queue and -fragment-queue)

4. CLEAN LIVE SESSION ARTIFACTS
   └── Remove /mnt/target/etc/gdm/custom.conf auto-login for liveuser
   └── Delete liveuser account from /etc/passwd, /etc/shadow, /etc/group
   └── Remove /mnt/target/home/liveuser/
   └── Remove installer .desktop from autostart (if present)
   └── Remove /mnt/target/usr/share/applications/forge-installer.desktop

5. MOUNT VIRTUAL FILESYSTEMS
   └── bind mount /dev, mount /proc, /sys, /run into /mnt/target

6. CHROOT CONFIGURATION
   └── Generate /etc/fstab with target disk UUIDs
   └── Set hostname in /etc/hostname and /etc/hosts
   └── Set timezone (ln -sf /usr/share/zoneinfo/...)
   └── Set locale in /etc/locale.conf
   └── Create user account (useradd, groups: wheel, audio, video, cdrom, input)
   └── Set user and root passwords (chpasswd via stdin)
   └── Install GRUB to target disk (grub-install + grub-mkconfig)
   └── Run post-install hooks:
       - glib-compile-schemas
       - gtk-update-icon-cache
       - update-mime-database
       - fc-cache
       - ldconfig
       - systemd-tmpfiles --create
   └── Enable systemd services (GDM, NetworkManager, etc.)
   └── Set first-boot flag (/var/lib/intergen/.firstboot-pending)
       → triggers welcome greeter + boot animation on first real boot

7. PKM DATABASE UPDATE
   └── pkm database came with the squashfs — already correct
   └── If any live-session-only packages were removed in step 4, update db

8. UNMOUNT
   └── Unmount virtual filesystems (reverse order)
   └── Unmount /mnt/target/boot/efi
   └── Unmount /mnt/target

9. COMPLETE
   └── Show success screen: "Remove USB drive and restart"
```

### Why NOT pkm install (for Phase 1)
The current Forge TUI installs packages one by one via pkm archives. This works but is:
- Slower (hundreds of individual archive extractions)
- Requires all .igos.tar.gz archives on the media
- More complex progress tracking

The unsquashfs approach is faster, simpler, and how every major distro does it. The pkm archive approach remains available for future use cases (server installs, recovery, network installs in Phase 2+).

---

## 9. The "Try or Install" Experience <a name="9-try-or-install"></a>

### GRUB Configuration for ISO
```bash
set default=0
set timeout=30

# Load InterGenOS GRUB theme
set theme=/boot/grub/themes/intergenos/theme.txt

# Find the live media
search --set=root --file /IGOS_LIVE

menuentry "Try InterGenOS" {
    linux /boot/vmlinuz boot=live quiet splash
    initrd /boot/intel-ucode.img /boot/initramfs-live.img
}

menuentry "Install InterGenOS" {
    linux /boot/vmlinuz boot=live quiet splash igos.installer=auto
    initrd /boot/intel-ucode.img /boot/initramfs-live.img
}

menuentry "Boot from first hard disk" {
    chainloader +1
}
```

### How It Works
Both "Try" and "Install" boot the **same** live environment. The only difference:
- **"Try"** → boots to GNOME desktop. User clicks "Install InterGenOS" launcher when ready.
- **"Install"** → adds `igos.installer=auto` to kernel cmdline. A systemd unit detects this and auto-launches the Forge installer after GDM login.

### Auto-Launch Mechanism
```ini
# /etc/systemd/system/forge-installer-autolaunch.service
[Unit]
Description=Auto-launch Forge Installer
After=graphical-session.target
ConditionKernelCommandLine=igos.installer=auto

[Service]
Type=oneshot
ExecStart=/usr/bin/forge-installer
User=liveuser
Environment=DISPLAY=:0

[Install]
WantedBy=graphical-session.target
```

Or simpler — an XDG autostart .desktop that checks `/proc/cmdline` for `igos.installer=auto`.

### Desktop Launcher
```ini
[Desktop Entry]
Name=Install InterGenOS
Comment=Install InterGenOS to your hard drive
Exec=pkexec /usr/bin/forge-installer
Icon=intergenos-installer
Terminal=false
Type=Application
Categories=System;
```

### Live Session User
- Username: `liveuser`
- Password: none (auto-login)
- GDM configured for auto-login in the squashfs
- Deleted during installation (step 4 of install flow)

---

## 10. ISO Assembly Pipeline <a name="10-iso-assembly-pipeline"></a>

This extends the existing build pipeline. Runs AFTER `create-image.sh` produces a clean system image.

### Pipeline Overview
```
create-image.sh output (clean qcow2)
  → Mount qcow2 image
    → mksquashfs → filesystem.squashfs
    → sha256sum → filesystem.sha256
    → Extract vmlinuz + generate initramfs-live.img
    → Build GRUB EFI + BIOS images
    → Assemble ISO directory structure
    → xorriso → InterGenOS-1.0-dev-x86_64.iso
```

### Step 1: Mount the clean system image
```bash
# Mount the qcow2 from create-image.sh as the squashfs source
qemu-nbd --connect=/dev/nbd0 /path/to/intergenos.qcow2
mount /dev/nbd0p3 /mnt/clean-image  # root partition
```

### Step 2: Add live session components
```bash
# Add liveuser with auto-login
# Add installer .desktop launcher
# Add installer autolaunch service
# These should be part of the image build, not manual additions
```

### Step 3: Create squashfs
```bash
mksquashfs /mnt/clean-image filesystem.squashfs \
    -comp xz -Xbcj x86 -b 1M \
    -e boot \
    -e var/cache/pkm \
    -e tmp \
    -e var/tmp
```

### Step 4: Generate integrity checksum
```bash
sha256sum filesystem.squashfs | awk '{print $1}' > filesystem.sha256
```

### Step 5: Build live initramfs
```bash
mkdir -p initramfs/{bin,dev,etc,lib/modules/<ver>,mnt/{media,squashfs,tmpfs,root},proc,sys}
cp busybox-static initramfs/bin/busybox
# Create applet symlinks
# Copy required kernel modules (squashfs, overlay, loop, iso9660, vfat, usb-storage, etc.)
# Copy init script
cd initramfs && find . | cpio -o -H newc | gzip > ../initramfs-live.img
```

### Step 6: Build GRUB images
```bash
# EFI
grub-mkstandalone --format=x86_64-efi \
    --output=EFI/BOOT/grubx64.efi \
    --modules="part_gpt part_msdos fat iso9660 linux normal search search_fs_file" \
    "boot/grub/grub.cfg=grub-embed.cfg"

# EFI partition image
dd if=/dev/zero of=efiboot.img bs=1M count=16
mkfs.vfat efiboot.img
mmd -i efiboot.img EFI EFI/BOOT
mcopy -i efiboot.img EFI/BOOT/grubx64.efi ::EFI/BOOT/

# BIOS
grub-mkstandalone --format=i386-pc \
    --output=bios.img \
    --install-modules="linux normal iso9660 biosdisk memdisk search tar ls" \
    --modules="linux normal iso9660 biosdisk search" \
    "boot/grub/grub.cfg=grub-embed.cfg"
```

### Step 7: Assemble ISO directory
```
iso-root/
├── boot/
│   ├── vmlinuz
│   ├── initramfs-live.img
│   ├── intel-ucode.img
│   └── grub/
│       ├── grub.cfg
│       ├── bios.img
│       └── themes/intergenos/
├── EFI/
│   └── BOOT/
│       └── grubx64.efi
├── LiveOS/
│   ├── filesystem.squashfs
│   └── filesystem.sha256
└── IGOS_LIVE
```

### Step 8: Generate hybrid ISO
```bash
xorriso -as mkisofs \
    -iso-level 3 \
    -full-iso9660-filenames \
    -volid "IGOS_LIVE" \
    -r -J -joliet-long \
    --grub2-boot-info \
    --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
    -eltorito-boot boot/grub/bios.img \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    --eltorito-catalog boot/grub/boot.cat \
    -eltorito-alt-boot \
    -e --interval:appended_partition_2:all:: \
    -no-emul-boot \
    -append_partition 2 0xEF efiboot.img \
    -appended_part_as_gpt \
    --protective-msdos-label \
    -o InterGenOS-1.0-dev-x86_64.iso \
    iso-root/
```

This produces a **hybrid ISO** bootable from:
- CD/DVD (BIOS El Torito)
- USB drive (BIOS MBR)
- USB/CD (UEFI via ESP)

---

## 11. Implementation Order <a name="11-implementation-order"></a>

**APPROVED ORDER — build GUI first, wrap in ISO second.**

### Phase A: GUI Installer (Critical Path)
1. Build `installer/frontend/gui.py` — GTK4/libadwaita, 6 screens
2. Build `installer/backend/extract.py` — unsquashfs with progress callbacks
3. Extend `installer/backend/packages.py` — post-unsquashfs pkm registration
4. Add live-session cleanup logic (remove liveuser, installer launcher, auto-login)
5. Test on running InterGenOS (laptop or VM) pointed at a target disk
6. Iterate on UX with owner feedback

### Phase B: ISO Infrastructure
7. Compile busybox (static) — for initramfs
8. Write the init script (with integrity check)
9. Build the initramfs assembly script
10. Write the ISO assembly script (create-iso.sh or phase_iso in orchestrator)
11. Create the GRUB theme for ISO boot menu
12. Create liveuser setup and GDM auto-login config
13. Create installer .desktop launcher and autolaunch service

### Phase C: Integration Testing
14. Build a test ISO from the current system image
15. Boot in VM (QEMU, both BIOS and UEFI)
16. Test "Try" path — live desktop, click installer, install to disk
17. Test "Install" path — auto-launch, install to disk
18. Boot the installed system — verify first-boot animation + welcome greeter
19. Test on real hardware (USB boot on HP laptop or another machine)

### Phase D: Polish
20. UX iteration based on real-world testing
21. Error handling edge cases (no disk found, disk too small, RAM too low)
22. Accessibility audit (keyboard nav, screen reader, high contrast)
23. Final ISO size optimization

---

## 12. What Already Exists <a name="12-what-already-exists"></a>

| Component | Status | Location | Lines |
|-----------|--------|----------|-------|
| Forge TUI installer backend | Built | `installer/backend/` | 833 |
| Forge TUI frontend | Built | `installer/frontend/tui.py` | 417 |
| Forge entry point | Built | `installer/__main__.py` | 50 |
| Disk management (partition, mount, EFI detect) | Built | `installer/backend/disks.py` | 218 |
| Config generation (fstab, hostname, locale, etc.) | Built | `installer/backend/config.py` | 186 |
| User account creation | Built | `installer/backend/users.py` | 80 |
| Bootloader installation (GRUB EFI + BIOS) | Built | `installer/backend/bootloader.py` | 58 |
| Post-install hooks (chroot, virtual FS) | Built | `installer/backend/hooks.py` | 140 |
| Package groups | Built | `installer/backend/packages.py` | 151 |
| Image creation (qcow2/raw from chroot) | Built | `scripts/create-image.sh` | 598 |
| pkm package manager | Built | `pkm/` | ~2000+ |
| Kernel (squashfs, overlayfs, initrd support) | Configured | `config/kernel/` | — |
| Welcome greeter (GTK4/libadwaita) | Built | `assets/intergen-welcome/` | 991 |
| Boot animation (DRM/KMS) | Built | `assets/intergen-firstboot-drm/` | ~800 |
| GNOME Shell theme | Built | `assets/intergen-shell-theme/` | 938 |
| DeepSeek GUI proposal (reference) | Designed | `docs/research/GUI_Installer_Proposal_Deepseek/` | 1,652 |

---

## 13. What Needs Building <a name="13-what-needs-building"></a>

| Component | Effort | Phase | Notes |
|-----------|--------|-------|-------|
| **GUI installer frontend** | Large | A | GTK4/libadwaita, 6 screens, InterGenOS visual language |
| **extract.py backend module** | Small | A | unsquashfs wrapper with progress parsing |
| **packages.py extension** | Small | A | Post-unsquashfs pkm registration |
| **Live session cleanup logic** | Small | A | Remove liveuser, installer launcher, auto-login |
| **Busybox (static)** | Small | B | For the initramfs |
| **Custom init script** | Small | B | ~100 lines with integrity check |
| **Initramfs build script** | Small | B | Assembles cpio.gz from components |
| **ISO assembly script** | Medium | B | xorriso + directory layout + GRUB images |
| **GRUB theme for ISO** | Small | B | Branded boot menu |
| **liveuser setup** | Small | B | Account, GDM auto-login, sudoers |
| **Installer .desktop launcher** | Trivial | B | 5-line file |
| **Installer autolaunch service** | Trivial | B | systemd unit checking kernel cmdline |
| **Integration testing** | Medium | C | VM + real hardware, both BIOS and UEFI |

---

## 14. Sources <a name="14-sources"></a>

### Live Session Architecture
- [SquashFS - Wikipedia](https://en.wikipedia.org/wiki/SquashFS)
- [Fedora LiveOS Image Wiki](https://fedoraproject.org/wiki/LiveOS_image)
- [Understanding SquashFS - Baeldung](https://www.baeldung.com/linux/squashfs-filesystem-mount)
- [OverlayFS and SquashFS in Embedded Linux](https://medium.com/@akashsainisaini37/how-overlayfs-and-squashfs-power-embedded-linux-storage-75273028ef20)
- [Adventures in Live Booting - Major Hayden](https://major.io/p/adventures-in-live-booting-linux-distributions/)

### Building Live ISOs
- [Custom Debian Live Environment with GRUB](https://www.willhaley.com/blog/custom-debian-live-environment-grub-only/)
- [Building Custom LiveISO from Scratch](https://medium.com/pranav-kulshrestha/building-your-own-customized-liveiso-from-scratch-e32b82522bf7)
- [Ubuntu Live Boot Filesystem Creation](https://github.com/nordeim/Ubuntu_Live_Boot_Filesystem_Creation)
- [Building a Custom Linux Live CD](https://linuxvox.com/blog/building-a-custom-linux-live-cd/)
- [Customizing a Live ISO Image](https://ccoff.github.io/customizing-live-iso-image)

### Initramfs
- [Custom Initramfs - Gentoo Wiki](https://wiki.gentoo.org/wiki/Custom_Initramfs)
- [initramfs-overlay (GitHub)](https://github.com/jumperfly/initramfs-overlay)
- [Casper Manpage](https://manpages.ubuntu.com/manpages/jammy/man7/casper.7.html)
- [Dracut dmsquash-live-root.sh](https://github.com/dracutdevs/dracut/blob/master/modules.d/90dmsquash-live/dmsquash-live-root.sh)

### Installer Architecture
- [ArchISO Architecture - DeepWiki](https://deepwiki.com/archlinux/archiso)
- [distinst - Pop!_OS Installer Backend](https://github.com/pop-os/distinst)
- [Penguins Eggs](https://github.com/pieroproietti/penguins-eggs)
- [Anaconda unsquashfs PR](https://github.com/rhinstaller/anaconda/pull/2292)

### InterGenOS Internal
- [Installer Design Plan (2026-04-05)](installer_design_plan_2026-04-05.md)
- [Framework Survey (2026-03-31)](installer_frameworks_2026-03-31.md)
- [Installer UX Research (2026-03-31)](installer_ux_and_design_2026-03-31.md)
- [Custom Installer Examples (2026-03-31)](custom_installer_examples_2026-03-31.md)
- [Architecture Review (2026-04-10)](live_session_architecture_review_2026-04-10.md)
- [DeepSeek GUI Proposal](../../docs/research/GUI_Installer_Proposal_Deepseek/)
