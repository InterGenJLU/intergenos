# Live Session & Installer Architecture Research
**Date:** April 9, 2026 | **For:** InterGenOS 1.0-dev

---

## Table of Contents
1. [The Big Picture — What We're Building](#1-the-big-picture)
2. [How Live Sessions Work — The Full Stack](#2-how-live-sessions-work)
3. [How Other Distros Do It](#3-how-other-distros-do-it)
4. [The Three Initramfs Approaches](#4-the-three-initramfs-approaches)
5. [Installation Methods from a Live Session](#5-installation-methods)
6. [The "Try or Install" Boot Flow](#6-try-or-install-flow)
7. [How This Constrains Our Installer](#7-installer-constraints)
8. [What InterGenOS Already Has](#8-what-we-have)
9. [Recommended Architecture for InterGenOS](#9-recommended-architecture)
10. [Build Pipeline — ISO Creation](#10-build-pipeline)
11. [Open Questions for Owner](#11-open-questions)
12. [Sources](#12-sources)

---

## 1. The Big Picture — What We're Building <a name="1-the-big-picture"></a>

The goal: a user downloads an InterGenOS ISO, writes it to USB, boots it, and sees either:
- **Option A:** A GRUB menu with "Try InterGenOS" and "Install InterGenOS"
- **Option B:** Boot directly into a live desktop with a desktop shortcut to launch the installer

This is the Ubuntu/Pop!_OS/elementary/Fedora model. The user gets to experience the OS before committing to installation.

### What This Requires (end to end)
1. **A compressed root filesystem** (squashfs) containing the full InterGenOS desktop
2. **An initramfs** that knows how to find, mount, and overlay that squashfs
3. **A bootloader** (GRUB) configured for ISO/USB media with try/install options
4. **An overlay filesystem** (overlayfs on tmpfs) so the live session is writable
5. **A GUI installer** that can run FROM the live session and install TO a target disk
6. **An ISO assembly pipeline** (xorriso) that packages everything into hybrid ISO (BIOS+UEFI)
7. **A USB writing tool** or instructions (dd, Ventoy, Etcher, etc.)

---

## 2. How Live Sessions Work — The Full Stack <a name="2-how-live-sessions-work"></a>

### The Boot Chain
```
BIOS/UEFI → GRUB (from ISO/USB) → kernel + initramfs
    → initramfs /init finds squashfs on media
    → mounts squashfs read-only
    → creates tmpfs for writes
    → overlayfs merges squashfs (lower) + tmpfs (upper)
    → switch_root to overlay mount
    → systemd takes over → GDM → GNOME desktop
    → user sees desktop with "Install InterGenOS" launcher
```

### The Filesystem Layers
```
┌─────────────────────────────────────┐
│         User sees: / (root)          │  ← overlayfs merge
├─────────────────────────────────────┤
│  Upper (tmpfs in RAM)               │  ← all writes go here
│  - new files, modified configs      │  ← lost on reboot
├─────────────────────────────────────┤
│  Lower (squashfs, read-only)        │  ← the real OS
│  - /usr, /etc, /bin, everything     │  ← compressed ~2-4GB
├─────────────────────────────────────┤
│  ISO/USB media (read-only)          │  ← physical device
│  - /LiveOS/filesystem.squashfs      │
│  - /boot/vmlinuz, initramfs         │
│  - /EFI/BOOT/grubx64.efi           │
└─────────────────────────────────────┘
```

### Copy-on-Write (CoW)
When the live session modifies a file (e.g., user changes a setting):
1. The file is read from the squashfs (lower) layer
2. A copy is created in the tmpfs (upper) layer
3. Future reads see the upper copy
4. Original squashfs is never modified
5. All changes are lost on reboot (unless persistent storage is configured)

### RAM Requirements
The squashfs itself is NOT decompressed into RAM — it's accessed via random-access decompression. Only modified files consume RAM (in the tmpfs upper layer). A typical GNOME live session needs:
- ~800MB for the desktop environment running
- ~200-500MB for the tmpfs overlay (depends on user activity)
- **Minimum viable: 4GB RAM** for comfortable live session with GNOME

---

## 3. How Other Distros Do It <a name="3-how-other-distros-do-it"></a>

### Ubuntu (Casper + Subiquity)
- **Live boot:** Casper package (initramfs hook) finds `/casper/filesystem.squashfs`
- **Overlay:** AUFS historically, now overlayfs
- **Installer:** Subiquity (Flutter UI + curtin backend)
- **Install method:** Extracts squashfs via `unsquashfs` to target
- **Boot menu:** GRUB with "Try Ubuntu" / "Install Ubuntu" entries
- **Casper is Ubuntu/Debian-specific** — NOT usable for InterGenOS

### Fedora (Dracut dmsquash-live + Anaconda)
- **Live boot:** Dracut's `dmsquash-live` module finds `/LiveOS/squashfs.img`
- **Nesting:** squashfs contains an ext4 `rootfs.img` (extra layer)
- **Overlay:** Device-mapper snapshots (problematic — fills up and corrupts)
- **Installer:** Anaconda (Python + React WebUI)
- **Install method:** rsync from mounted live filesystem, or unsquashfs
- **Dracut is RPM-ecosystem-oriented** — heavy, many assumptions

### Pop!_OS / elementary (distinst)
- **Live boot:** Casper (Ubuntu-derived)
- **Installer:** distinst (Rust backend) + Vala/GTK frontend
- **Install method:** `unsquashfs /cdrom/casper/filesystem.squashfs` to target
- **Post-install:** chroot into target, run configure.sh, install bootloader
- **Key insight:** Backend makes NO assumptions about OS/packaging/toolkit

### Arch Linux (archiso + mkinitcpio)
- **Live boot:** Custom `mkinitcpio-archiso` hooks
- **Squashfs:** `mksquashfs` with xz compression, `-Xbcj x86` filter
- **Overlay:** overlayfs with tmpfs upper
- **Installer:** archinstall (Python TUI) — launched manually from shell
- **No GUI installer on ISO** — CLI only
- **archiso approach is clean but Arch-specific** (mkinitcpio hooks)

### OpenSUSE
- **Overlay:** overlayfs (best approach — graceful degradation when full)
- **Unlike Fedora's device-mapper snapshots**, files can be deleted to recover space

### Key Takeaway
Every distro uses a **distro-specific initramfs mechanism** (casper, dracut, mkinitcpio-archiso). Since InterGenOS is built from scratch (LFS), we need **our own initramfs init script**. This is actually simpler than it sounds — the core is ~50-100 lines of shell.

---

## 4. The Three Initramfs Approaches <a name="4-the-three-initramfs-approaches"></a>

### Option A: Custom init script (RECOMMENDED)
Write our own `/init` script for the live initramfs. This is what LFS-derived distros should do.

**Pros:**
- Complete control, no external dependencies
- Tiny — just busybox + a shell script
- No framework assumptions (no casper, no dracut, no mkinitcpio)
- Aligns with PRIME DIRECTIVE: transparent, understandable, no hidden magic
- ~50-100 lines of shell for the core logic

**Cons:**
- Must handle edge cases ourselves (device detection, module loading)
- No community-maintained updates

**Core init script structure:**
```bash
#!/bin/sh
# Mount essential filesystems
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

# Load required modules
modprobe overlay
modprobe squashfs
modprobe loop
modprobe iso9660    # for CD/DVD boot
modprobe vfat       # for USB boot
modprobe usb_storage
modprobe sd_mod

# Find the live media (search all block devices for /LiveOS/ marker)
for dev in /dev/sd* /dev/sr* /dev/nvme*; do
    [ -b "$dev" ] || continue
    mount -o ro "$dev" /mnt/media 2>/dev/null || continue
    if [ -f /mnt/media/LiveOS/filesystem.squashfs ]; then
        LIVE_MEDIA="$dev"
        break
    fi
    umount /mnt/media
done

# Mount squashfs read-only
mount -t squashfs -o ro,loop /mnt/media/LiveOS/filesystem.squashfs /mnt/squashfs

# Create tmpfs for writes
mount -t tmpfs -o size=75% tmpfs /mnt/tmpfs
mkdir -p /mnt/tmpfs/upper /mnt/tmpfs/work

# Create overlayfs
mount -t overlay overlay \
    -o lowerdir=/mnt/squashfs,upperdir=/mnt/tmpfs/upper,workdir=/mnt/tmpfs/work \
    /mnt/root

# Move mounts into new root
mkdir -p /mnt/root/run/initramfs/live
mount --move /mnt/media /mnt/root/run/initramfs/live

# Clean up and switch
umount /proc /sys /dev
exec switch_root /mnt/root /sbin/init
```

### Option B: Dracut with dmsquash-live module
Use dracut (we'd need to build it) with its live boot module.

**Pros:** Well-tested, handles many edge cases
**Cons:** Heavy dependency, RPM-ecosystem assumptions, device-mapper snapshots have known corruption issues, overkill for our needs

### Option C: mkinitcpio with custom hooks
Write mkinitcpio-style hooks (Arch approach).

**Pros:** Clean hook system
**Cons:** Requires mkinitcpio (Arch-specific tool), another dependency

### Verdict
**Option A (custom init) is the right choice.** It's the LFS way. It's transparent. We control it completely. The script is small enough to audit in 5 minutes. Busybox provides everything we need (mount, modprobe, switch_root, sh).

---

## 5. Installation Methods from a Live Session <a name="5-installation-methods"></a>

When the user clicks "Install InterGenOS" from the live desktop, the installer has several ways to get the OS onto the target disk:

### Method 1: unsquashfs (RECOMMENDED)
Extract the squashfs image directly to the target partition.

```bash
unsquashfs -f -d /mnt/target /run/initramfs/live/LiveOS/filesystem.squashfs
```

**Pros:**
- ~2x faster than rsync (284s vs 150s in Anaconda benchmarks)
- Reads squashfs sequentially (optimal I/O pattern)
- Aware of filesystem structure
- Handles extended attributes, permissions, symlinks
- Memory: ~540MB peak (adjustable)

**Cons:**
- No exclude mechanism (must delete unwanted files after)
- Requires squashfs-tools on the live system

### Method 2: rsync from mounted live filesystem
Copy from the running live overlay to the target.

```bash
rsync -aAX --exclude={"/dev/*","/proc/*","/sys/*","/tmp/*","/run/*","/mnt/*"} / /mnt/target/
```

**Pros:**
- Can exclude paths during copy
- Handles interruption gracefully (can resume)

**Cons:**
- 2-3x slower than unsquashfs
- CPU-bound (socket overhead between rsync processes)
- Copies overlay modifications (may include unwanted live session state)

### Method 3: pkm package installation (our current Forge approach)
Install packages individually from archives to the target.

```bash
pkm install --root /mnt/target core base desktop-gnome
```

**Pros:**
- Clean install, no live session artifacts
- Exact package tracking from day one
- pkm database populated correctly

**Cons:**
- Slowest method (extracting hundreds of individual archives)
- Requires all .igos.tar.gz archives on the media
- More complex progress tracking

### Method 4: Hybrid (RECOMMENDED for InterGenOS)
Use `unsquashfs` for the bulk copy, then run pkm to register packages in its database.

```bash
# 1. Partition and mount target
# 2. unsquashfs the base image
unsquashfs -f -d /mnt/target /run/initramfs/live/LiveOS/filesystem.squashfs
# 3. chroot and configure (hostname, users, timezone, locale, etc.)
# 4. Register packages in pkm database
# 5. Install bootloader (GRUB)
# 6. Generate fstab with target UUIDs
```

This gives us speed (unsquashfs) AND package tracking (pkm awareness). The squashfs IS the installed system — no conversion needed.

### Critical Constraint This Reveals About the Installer
**The GUI installer does NOT need to install packages one by one.** It extracts the squashfs (which already contains the full desktop) and then customizes the result. This is FUNDAMENTALLY different from our current Forge TUI approach (which installs via pkm archives).

This means:
- The installer backend needs an `unsquashfs` path, not just a `pkm install` path
- The squashfs on the ISO IS the install source — it must be a clean, complete system
- Post-extraction configuration (fstab, hostname, users, bootloader, pkm registration) happens in chroot
- Package group selection at install time becomes "what to REMOVE" not "what to install" (or we ship multiple squashfs images per tier)

---

## 6. The "Try or Install" Boot Flow <a name="6-try-or-install-flow"></a>

### GRUB Menu on the ISO
```
┌──────────────────────────────────────┐
│         InterGenOS 1.0                │
│                                      │
│  ► Try InterGenOS                    │
│    Install InterGenOS                │
│    ─────────────────                 │
│    Boot from first hard disk         │
│    Advanced options                  │
│                                      │
│  Use ↑↓ to select, Enter to boot    │
└──────────────────────────────────────┘
```

### grub.cfg for the ISO
```bash
set default=0
set timeout=30

# Find the ISO/USB media
search --set=root --file /LiveOS/filesystem.squashfs

menuentry "Try InterGenOS" {
    linux /boot/vmlinuz boot=live quiet splash
    initrd /boot/initramfs-live.img
}

menuentry "Install InterGenOS" {
    linux /boot/vmlinuz boot=live quiet splash igos.installer=auto
    initrd /boot/initramfs-live.img
}

menuentry "Boot from first hard disk" {
    chainloader +1
}
```

### How "Try" vs "Install" Works
Both options boot the SAME live environment. The difference:
- **"Try"** boots to the normal GNOME desktop. User can click "Install InterGenOS" launcher on the desktop whenever ready.
- **"Install"** passes a kernel parameter (`igos.installer=auto`) that triggers the installer to launch automatically after desktop loads (via systemd unit or autostart .desktop file).

This is exactly how Ubuntu, Pop!_OS, and elementary do it. Same live image, different entry points.

### Desktop Launcher (.desktop file)
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

---

## 7. How This Constrains Our Installer <a name="7-installer-constraints"></a>

### Constraint 1: The installer must run from a live session
The installer runs ON the live system (in RAM, on the overlay). It cannot assume:
- Persistent storage (everything is tmpfs)
- That the target disk is mounted (it must partition and mount it)
- That network is available (may be offline install)
- That the running system IS the system being installed (it's not — it's a live copy)

### Constraint 2: The squashfs IS the install source
The installer's primary job is to extract the squashfs to the target disk, NOT to install packages one by one. This is a major departure from our current Forge TUI design.

**Current Forge approach:** pkm install → extracts .igos.tar.gz archives individually
**Live session approach:** unsquashfs → extracts entire OS in one shot

Both approaches can coexist. The installer should support:
- **Live ISO mode:** unsquashfs from media
- **Network/archive mode:** pkm install from archives (the current approach, useful for server installs or recovery)

### Constraint 3: Post-install configuration happens in chroot
After extraction, the installer must chroot into the target and:
1. Generate `/etc/fstab` with the target's UUIDs
2. Set hostname, timezone, locale
3. Create user accounts
4. Install GRUB to the target disk's ESP/MBR
5. Generate GRUB config
6. Register packages in pkm database (so pkm knows what's installed)
7. Run post-install hooks (glib-compile-schemas, gtk-update-icon-cache, etc.)
8. Enable systemd services (NetworkManager, GDM, etc.)
9. Set first-boot flag (so welcome greeter + boot animation trigger)

**This is mostly what our Forge backend already does** — the chroot configuration code in `installer/backend/` covers most of this.

### Constraint 4: The installer must handle disk operations carefully
- Must NOT touch the USB/ISO media it's running from
- Must exclude the live media device from the disk selection list
- Must handle both EFI and BIOS boot modes
- Should detect existing OS installations and warn

### Constraint 5: The GUI must work on the live overlay
- GTK4/libadwaita available (it's in the squashfs)
- Must handle permission escalation (pkexec or run as root)
- Should be able to show progress of the unsquashfs extraction
- Must not crash if RAM is tight (live session + installer + GNOME = heavy)

### Constraint 6: The squashfs must be a clean, complete system
The squashfs on the ISO must contain:
- The full InterGenOS desktop (all tiers: core + base + desktop)
- Clean configuration (no user-specific state)
- The installer application itself
- pkm package database (pre-populated with installed packages)
- All post-install hook scripts
- GRUB binaries (for installing to the target)
- squashfs-tools (for unsquashfs)
- The welcome greeter and boot animation (for first boot on target)

### Constraint 7: pkm database must survive the transition
When we unsquashfs the image, the pkm database comes along. But it reflects the LIVE system's package state. After extraction:
- pkm database should already be correct (same packages)
- If the user deselected packages during install, we remove them post-extraction
- pkm must be aware that it's now on a real disk, not a squashfs

---

## 8. What InterGenOS Already Has <a name="8-what-we-have"></a>

### Ready to Use
| Component | Status | Notes |
|-----------|--------|-------|
| Forge TUI installer backend | ✅ Built | disks.py, config.py, users.py, bootloader.py, hooks.py |
| Kernel with squashfs/overlayfs/initrd support | ✅ Configured | All as modules (=m), INITRD=y |
| squashfs compression algorithms | ✅ All enabled | zlib, lz4, lzo, xz, zstd |
| GRUB (EFI + BIOS) | ✅ Built | Already installing GRUB in create-image.sh |
| create-image.sh | ✅ Working | Creates bootable images from chroot — similar pipeline |
| pkm package manager | ✅ Built | install, remove, query, verify, repo layer |
| DeepSeek GUI installer proposal | ✅ Designed | GTK4 Python, 4 screens, uses same backend |
| Welcome greeter | ✅ Built | First-boot experience ready |
| Boot animation | ✅ Built | DRM/KMS, first-boot-only |
| GNOME Shell theme | ✅ Built | Will be in the squashfs |
| Installer design spec | ✅ Documented | 3-phase roadmap |

### Needs Building
| Component | Effort | Notes |
|-----------|--------|-------|
| Custom live initramfs (/init script) | Small | ~100 lines of shell + busybox |
| ISO assembly script | Medium | xorriso command + directory layout |
| GUI installer frontend (GTK4) | Large | 4-6 screens, GTK4/libadwaita |
| `unsquashfs` install path in backend | Small | Add to existing backend alongside pkm path |
| GRUB theme for ISO boot menu | Small | We need this anyway |
| Desktop .desktop launcher for installer | Trivial | 5-line file |
| Live session auto-detect (kernel param) | Small | systemd unit checks cmdline |
| Busybox (statically compiled) | Small | For the initramfs |

---

## 9. Recommended Architecture for InterGenOS <a name="9-recommended-architecture"></a>

### The Stack
```
ISO/USB Media
├── /boot/
│   ├── vmlinuz                    # InterGenOS kernel
│   ├── initramfs-live.img         # Custom initramfs (busybox + init)
│   └── intel-ucode.img            # Intel microcode (prepended)
├── /EFI/
│   └── BOOT/
│       ├── grubx64.efi            # GRUB EFI binary
│       └── grub.cfg               # Embedded GRUB config
├── /LiveOS/
│   └── filesystem.squashfs        # Complete InterGenOS desktop
├── /boot/grub/
│   ├── grub.cfg                   # BIOS GRUB config
│   └── themes/intergenos/         # GRUB theme
└── IGOS_LIVE                      # Media identification file
```

### The Initramfs
```
initramfs-live.img (cpio.gz)
├── /init                          # Our custom init script
├── /bin/busybox                   # Static busybox (shell, mount, modprobe, etc.)
├── /bin/sh → busybox              # Symlinks
├── /lib/modules/<version>/
│   ├── squashfs.ko                # Squashfs module
│   ├── overlay.ko                 # Overlayfs module
│   ├── loop.ko                    # Loop device
│   ├── iso9660.ko                 # CD/DVD support
│   ├── vfat.ko                    # USB FAT support
│   ├── usb-storage.ko             # USB mass storage
│   ├── sd_mod.ko                  # SCSI disk
│   ├── sr_mod.ko                  # SCSI CD-ROM
│   ├── ehci-hcd.ko, xhci-hcd.ko  # USB host controllers
│   └── nvme.ko                    # NVMe support
├── /dev/                          # Minimal device nodes
├── /proc/                         # Mount point
├── /sys/                          # Mount point
├── /mnt/
│   ├── media/                     # Live media mount
│   ├── squashfs/                  # Squashfs mount
│   ├── tmpfs/                     # Tmpfs for overlay upper
│   └── root/                      # Final overlay mount (switch_root target)
└── /etc/                          # Minimal config
```

### The Installer (Forge GUI)
```
Forge Installer — GTK4/libadwaita
├── Screen 1: Welcome
│   └── Language, keyboard layout
├── Screen 2: Disk Selection
│   └── "Use entire disk" (Phase 1) / Advanced partitioning (Phase 2)
│   └── EFI/BIOS auto-detect
│   └── Excludes live media device
├── Screen 3: User Setup
│   └── Hostname, timezone, username/password, root password
├── Screen 4: Package Groups (optional — Phase 2)
│   └── What to include/exclude from the base image
├── Screen 5: Confirmation
│   └── Summary, "THIS WILL ERASE THE DISK" warning
├── Screen 6: Installation Progress
│   └── unsquashfs extraction with progress bar
│   └── Post-install configuration steps
│   └── Bootloader installation
├── Screen 7: Complete
│   └── "Remove USB and reboot"
│
└── Backend (existing Forge code, extended)
    ├── disks.py      — partition, mount, format (EXISTING)
    ├── extract.py    — NEW: unsquashfs with progress callbacks
    ├── config.py     — fstab, hostname, locale, etc. (EXISTING)
    ├── users.py      — user creation (EXISTING)
    ├── bootloader.py — GRUB install (EXISTING)
    ├── hooks.py      — post-install hooks (EXISTING)
    └── packages.py   — pkm registration (EXISTING, extend)
```

### Install Flow (what happens when user clicks "Install")
```
1. User selects disk → partition_disk() creates GPT + ESP + root
2. Mount target at /mnt/target
3. unsquashfs /run/initramfs/live/LiveOS/filesystem.squashfs → /mnt/target
   └── Progress: unsquashfs reports file count, we parse and display
4. Mount virtual filesystems (dev, proc, sys) in target
5. chroot into target:
   a. Generate /etc/fstab (target disk UUIDs)
   b. Set hostname, timezone, locale
   c. Create user accounts
   d. Set passwords
   e. Install GRUB to target disk
   f. Generate GRUB config for target
   g. Run glib-compile-schemas, gtk-update-icon-cache, etc.
   h. Enable systemd services
   i. Set first-boot flag (for welcome greeter + boot animation)
   j. Update pkm database if packages were removed
6. Unmount everything
7. Show "Installation complete — remove USB and reboot"
```

---

## 10. Build Pipeline — ISO Creation <a name="10-build-pipeline"></a>

This extends our existing build pipeline. After the chroot at /mnt/igos is complete:

### Step 1: Prepare the squashfs source
```bash
# The chroot IS the squashfs source — clean it first
# Remove build artifacts, logs, source tarballs
# Ensure pkm database is populated
# Ensure installer app is installed
# Ensure desktop launcher for installer exists
# Set default user to "liveuser" with auto-login (for live session)
```

### Step 2: Create the squashfs
```bash
mksquashfs /mnt/igos /tmp/iso-build/LiveOS/filesystem.squashfs \
    -comp xz -Xbcj x86 -b 1M \
    -e boot \
    -e var/cache/pkm \
    -e tmp \
    -e var/tmp
```
- XZ compression: best ratio (~23% better than gzip)
- `-Xbcj x86`: x86 bytecode filter for better compression of binaries
- 1MB block size: good balance of compression ratio vs random access speed
- Exclude /boot (kernel/initramfs are separate on the ISO)
- Expected size: ~2-4GB compressed for full GNOME desktop

### Step 3: Build the live initramfs
```bash
# Create initramfs directory
mkdir -p initramfs/{bin,dev,etc,lib/modules,mnt/{media,squashfs,tmpfs,root},proc,sys}

# Copy statically-linked busybox
cp /path/to/busybox-static initramfs/bin/busybox
# Create symlinks
for cmd in sh mount umount modprobe switch_root mkdir; do
    ln -s busybox initramfs/bin/$cmd
done

# Copy required kernel modules
cp /lib/modules/$(uname -r)/kernel/fs/squashfs/squashfs.ko initramfs/lib/modules/
cp /lib/modules/$(uname -r)/kernel/fs/overlayfs/overlay.ko initramfs/lib/modules/
# ... (loop, iso9660, vfat, usb-storage, sd_mod, sr_mod, nvme, xhci-hcd, ehci-hcd)

# Copy init script
cp init initramfs/init
chmod +x initramfs/init

# Create cpio archive
cd initramfs
find . | cpio -o -H newc | gzip > ../initramfs-live.img
```

### Step 4: Prepare GRUB for the ISO
```bash
# Create EFI image
grub-mkstandalone --format=x86_64-efi \
    --output=efiboot/EFI/BOOT/grubx64.efi \
    --modules="part_gpt part_msdos fat iso9660 linux normal search search_fs_file" \
    "boot/grub/grub.cfg=grub-embed.cfg"

# Create FAT EFI partition image
dd if=/dev/zero of=efiboot.img bs=1M count=16
mkfs.vfat efiboot.img
mmd -i efiboot.img EFI EFI/BOOT
mcopy -i efiboot.img efiboot/EFI/BOOT/grubx64.efi ::EFI/BOOT/

# Create BIOS GRUB image
grub-mkstandalone --format=i386-pc \
    --output=bios.img \
    --install-modules="linux normal iso9660 biosdisk memdisk search tar ls" \
    --modules="linux normal iso9660 biosdisk search" \
    "boot/grub/grub.cfg=grub-embed.cfg"
```

### Step 5: Assemble the ISO directory
```bash
iso-root/
├── boot/
│   ├── vmlinuz
│   ├── initramfs-live.img
│   ├── intel-ucode.img
│   └── grub/
│       ├── grub.cfg
│       └── themes/intergenos/
├── EFI/
│   └── BOOT/
│       └── grubx64.efi
├── LiveOS/
│   └── filesystem.squashfs
└── IGOS_LIVE
```

### Step 6: Generate the hybrid ISO
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

This creates a **hybrid ISO** that boots from:
- CD/DVD (BIOS El Torito)
- USB (BIOS MBR)
- USB/CD (UEFI via ESP)

---

## 11. Open Questions for Owner <a name="11-open-questions"></a>

### Q1: Package group selection during install?
**Option A:** Install the full desktop image (what's in the squashfs). Simple. Fast.
**Option B:** Allow removing tiers during install (e.g., "don't install desktop, server only"). More complex — requires removing packages after unsquashfs or shipping multiple squashfs images.

Recommendation: **Option A for Phase 1.** Ship one squashfs with the full desktop. Phase 2 can add multiple profiles (minimal, server, desktop, full).

### Q2: Multiple squashfs images or one?
**One image (recommended):** Simpler, one squashfs has everything. ~3-4GB ISO.
**Multiple images:** Separate core.squashfs + desktop.squashfs, layer them. More complex initramfs and installer, but smaller minimal installs.

### Q3: Persistent live sessions?
Should the live USB support saving changes between reboots (like Ubuntu's persistence)?
- Requires a separate partition or file on the USB for the overlay
- Adds complexity to the initramfs
- Nice-to-have, not essential for first release

### Q4: Live session auto-login user
The live session needs a user to auto-login to GNOME. Standard approach: create a `liveuser` account with no password, auto-login via GDM config, delete this user during installation.

### Q5: Plymouth boot splash for live session?
Currently we skip Plymouth (GRUB → DRM animation → GDM). For the live ISO boot, do we want:
- GRUB theme → straight to GDM (simplest)
- GRUB theme → Plymouth → GDM (prettier, but another component)

### Q6: Network install option?
Should the ISO support installing from a network repo (download packages during install) in addition to the squashfs extraction? This would enable a smaller "netinstall" ISO.

### Q7: Installer framework vs custom?
Our research evaluated Calamares, distinst, etc. The current plan is custom (Forge). The live session research reinforces this — our `unsquashfs` + chroot approach is simpler than any framework's assumptions, and we already have most of the backend code.

### Q8: When to build the GUI installer?
The GUI installer is the critical path. The live session infrastructure (initramfs, squashfs, ISO assembly) is mechanical — well-documented, straightforward. The GUI is where the user experience lives. Should we:
- Build the GUI first, test it on a running system, THEN wrap it in a live ISO?
- Build the live ISO infrastructure first, then add the GUI?

Recommendation: **Build the GUI first.** Test it by running it on the laptop or in a VM, pointed at a target disk. Once it works, wrapping it in a live ISO is just packaging.

---

## 12. Sources <a name="12-sources"></a>

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
- [Installer Design Plan (2026-04-05)](../installer/installer_design_plan_2026-04-05.md)
- [Framework Survey (2026-03-31)](../installer/installer_frameworks_2026-03-31.md)
- [Installer UX Research (2026-03-31)](../installer/installer_ux_and_design_2026-03-31.md)
- [Custom Installer Examples (2026-03-31)](../installer/custom_installer_examples_2026-03-31.md)
- [DeepSeek GUI Proposal](../../docs/research/GUI_Installer_Proposal_Deepseek/)
