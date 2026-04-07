# Forge — InterGenOS System Installer — Design Plan

## Context

InterGenOS can build itself from source (toolchain through desktop+extra), create a bootable disk image, and deploy to a VM. What's missing is the ability to install InterGenOS onto arbitrary hardware from installation media — the installer.

Our package manager (`pkm`) already handles archive extraction, file deployment, dependency tracking, and manifest generation. The installer is a guided wrapper around `pkm install --root /mnt/target`.

Our existing research (March 31, 2026) evaluated Calamares, distinst, Anaconda, and custom approaches. The conclusion was to prototype both Calamares and a custom installer, then commit to one.

## Approach: Custom Python Installer with pkm Backend

Given that we already have:
- `pkm` with SQLite database and archive-based installs
- A complete build pipeline producing `.igos.tar.gz` archives
- 38 packages with post_install hooks that need orchestration
- Hardware detection needs for AI tier inference (unique to InterGenOS)

A custom installer gives us full control over the InterGenOS-specific experience (AI tier detection, Prime Directive transparency) while reusing `pkm` as the engine.

Calamares remains a future option if we need a polished GUI quickly, but the backend must be ours regardless — Calamares doesn't know about `pkm`, `.igos.tar.gz`, or AI tiers.

## Architecture

```
┌──────────────────────────────────────────┐
│         Installer Frontend               │
│  Phase 1: Python + ncurses (TUI)         │
│  Phase 2: Tauri or GTK4 (GUI)           │
│                                          │
│  Screens: Welcome → Disk → Config →      │
│           Packages → Install → Done      │
└──────────────────┬───────────────────────┘
                   │
┌──────────────────▼───────────────────────┐
│         Installer Backend                │
│  Python — orchestrates the full install  │
│                                          │
│  1. Partition + format target disk       │
│  2. Mount filesystems                    │
│  3. pkm install --root /target <groups>  │
│  4. Generate config (fstab, hostname...) │
│  5. Run post_install hooks via chroot    │
│  6. Install bootloader                   │
│  7. Create user accounts                 │
│  8. Unmount + reboot                     │
└──────────────────┬───────────────────────┘
                   │
┌──────────────────▼───────────────────────┐
│  pkm (Package Manager)                   │
│  Archive extraction + file deployment    │
│  Dependency resolution + tracking        │
└──────────────────────────────────────────┘
```

## Installer Flow

### Screen 1: Welcome
- Language selection (locale list from glibc)
- Keyboard layout selection
- Display hardware profile summary (CPU, RAM, GPU, storage)
- Explain what InterGenOS is (Prime Directive)

### Screen 2: Disk Selection
- Detect disks via `lsblk` / `/sys/block/`
- Simple mode: "Use entire disk" → auto-partition
- Advanced mode: manual partitioning (via libparted)
- Partitioning scheme:
  - EFI: ESP (512MB FAT32) + root (ext4)
  - BIOS: bios_grub (1MB) + root (ext4)
  - Optional: swap partition
- Future: LVM, LUKS encryption

### Screen 3: System Configuration
- Hostname (default: "intergenos")
- Timezone (via tzselect logic)
- Root password
- First user account (username, password, groups: wheel, audio, video)
- Network: DHCP (default) or static

### Screen 4: Package Groups
- Core (always installed): toolchain artifacts not included, just core+base runtime
- Desktop: GNOME (default), future: KDE, XFCE
- Extra: Node.js, helpers
- Show package count and estimated disk usage per group

### Screen 5: Installation Progress
- Extract archives to target with progress bar
- Show current package name and count (e.g., "Installing bash [42/350]...")
- Run post_install hooks
- Generate system config files
- Install bootloader
- Estimated time remaining

### Screen 6: Complete
- Summary of what was installed
- Instructions for first boot
- "Remove installation media and reboot"

## Package Groups

```python
GROUPS = {
    "core": {
        "description": "Essential system (kernel, shell, coreutils, systemd)",
        "tiers": ["core"],
        "required": True,
    },
    "base": {
        "description": "CLI tools (htop, rsync, strace, screen)",
        "tiers": ["base"],
        "required": False,
        "default": True,
    },
    "desktop-gnome": {
        "description": "GNOME desktop environment on Wayland",
        "tiers": ["desktop"],
        "required": False,
        "default": True,
    },
    "extra": {
        "description": "Node.js, Chrome/VS Code/Claude Code helpers",
        "tiers": ["extra"],
        "required": False,
        "default": False,
    },
}
```

## pkm --root Integration

The key addition to `pkm` for installer support:

```python
# pkm install --root /mnt/target core base desktop-gnome
#
# For each package archive:
#   1. Extract to /mnt/target (not /)
#   2. Register in /mnt/target/var/lib/igos/pkm.db
#   3. Write manifest to /mnt/target/var/lib/igos/packages/
#
# After all packages:
#   4. chroot /mnt/target and run post_install hooks
#   5. Generate config files in /mnt/target/etc/
```

## Post-Install Hook Orchestration

38 packages have `post_install()` functions. These must run inside a chroot of the target:

```bash
# Mount virtual filesystems for chroot
mount --bind /dev  /mnt/target/dev
mount -t proc proc /mnt/target/proc
mount -t sysfs sys /mnt/target/sys

# Run hooks
for pkg in $POST_INSTALL_PACKAGES; do
    chroot /mnt/target /bin/bash -c "
        source /mnt/intergenos/packages/$tier/$pkg/build.sh
        PKG_VERSION=$version post_install
    "
done

# Unmount
umount /mnt/target/{sys,proc,dev}
```

Hook categories and handling:
- **Service enables** (systemctl enable) — run in chroot, systemd preset handles it
- **User/group creation** (useradd, groupadd) — run in chroot
- **Schema compilation** (glib-compile-schemas) — run in chroot after all packages extracted
- **Cache updates** (gdk-pixbuf, icon cache) — run in chroot
- **Locale generation** (localedef) — run in chroot

## Config Generation

The installer generates these files on the target (replacing the hardcoded chroot-config-ch9.sh):

| File | Source |
|------|--------|
| `/etc/fstab` | Generated from partition UUIDs (blkid) |
| `/etc/hostname` | User input |
| `/etc/hosts` | Generated from hostname |
| `/etc/locale.conf` | User selection |
| `/etc/vconsole.conf` | User keyboard layout |
| `/etc/localtime` | Symlink to user timezone |
| `/etc/systemd/network/10-dhcp.network` | Default DHCP or user config |
| `/etc/resolv.conf` | Symlink to systemd-resolved |
| `/etc/os-release` | Template with version |
| `/etc/default/grub` | Generated for target hardware |
| `/etc/issue` | InterGenOS branding |
| `/etc/motd` | InterGenOS branding |

## Bootloader Installation

```python
def install_bootloader(target, disk, efi=False):
    if efi:
        # Mount ESP
        os.makedirs(f"{target}/boot/efi", exist_ok=True)
        mount(esp_partition, f"{target}/boot/efi")
        # Install GRUB for EFI
        chroot(target, "grub-install --target=x86_64-efi "
                       "--efi-directory=/boot/efi --bootloader-id=InterGenOS")
    else:
        # Install GRUB for BIOS
        chroot(target, f"grub-install --target=i386-pc {disk}")

    # Generate config
    chroot(target, "grub-mkconfig -o /boot/grub/grub.cfg")
```

## File Layout

```
installer/
├── __init__.py
├── __main__.py          # Entry point
├── backend/
│   ├── __init__.py
│   ├── disks.py         # Disk detection + partitioning (libparted)
│   ├── packages.py      # pkm integration for --root installs
│   ├── config.py        # System config generation
│   ├── bootloader.py    # GRUB/EFI installation
│   ├── hooks.py         # Post-install hook orchestration
│   └── users.py         # User account creation
├── frontend/
│   ├── __init__.py
│   └── tui.py           # ncurses text UI (Phase 1)
└── data/
    ├── groups.yaml      # Package group definitions
    └── locales.txt      # Available locales
```

## Implementation Phases

### Phase 1: Archive-based Installer (implement soon)
- TUI (ncurses) — works over SSH, serial console, bare TTY
- Partitioning: "use entire disk" only (auto GPT + BIOS/EFI)
- Single DE: GNOME
- `pkm install --root` as the engine
- Post-install hooks via chroot
- Config generation from user input
- GRUB installation (BIOS + EFI)
- Target: boot from USB/ISO, install to disk

### Phase 2: Enhanced Installer
- GUI frontend (GTK4 or Tauri)
- Advanced partitioning (resize, dual-boot, LVM)
- Multiple DE support
- Hardware detection + AI tier inference
- Encryption (LUKS2)
- Network installation (download packages during install)

### Phase 3: Installation Media
- Live ISO generation (squashfs + GRUB)
- USB creation tool
- PXE/network boot support
- Recovery mode

## Verification

1. **Partition test:** Create VM disk, run partitioner, verify GPT layout with `fdisk -l`
2. **Install test:** `pkm install --root /mnt/test core` → verify files deployed correctly
3. **Hook test:** Run post_install hooks in chroot → verify services enabled, users created
4. **Config test:** Generate fstab from blkid → verify UUIDs match
5. **Boot test:** Install GRUB → boot the VM → verify systemd starts
6. **End-to-end:** Full installer flow in a VM → bootable GNOME desktop
