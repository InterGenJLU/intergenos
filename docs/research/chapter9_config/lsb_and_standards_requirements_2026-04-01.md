# LSB & Standards Requirements for Chapter 9 Config Files

**Date:** April 1, 2026
**Purpose:** Determine what LSB 5.0, FHS 3.0, and other standards require for system configuration files before making Chapter 9 decisions.

---

## Summary Table

| File | LSB 5.0 | FHS 3.0 | Other Standard | Recommendation |
|------|---------|---------|----------------|----------------|
| `/etc/hostname` | Silent | Optional | RFC 1123 | Follow RFC 1123 naming |
| `/etc/hosts` | Silent | Optional | Convention | FQDN first, standard format |
| `/etc/resolv.conf` | Silent | Optional | glibc resolver | Let systemd-resolved manage or write manually |
| `/etc/adjtime` | Silent | Optional (at `/var/lib/hwclock/`) | hwclock(8) | Use UTC |
| `/etc/os-release` | Silent | Not mentioned | freedesktop.org | Set NAME, ID, VERSION_ID, PRETTY_NAME minimum |
| `/etc/lsb-release` | Implicit (backing for `lsb_release`) | Not mentioned | LSB 5.0 | Provide for compatibility |
| `lsb_release` cmd | REQUIRED for conformance | N/A | LSB 5.0 | Provide a compatibility script |
| `/etc/locale.conf` | Silent (C/POSIX required) | N/A | systemd | Set `LANG=en_US.UTF-8` |
| `/etc/vconsole.conf` | Silent | Not mentioned | systemd | Set KEYMAP and optionally FONT |
| Boot screen clearing | Silent | N/A | systemd | UX decision |

---

## Detailed Findings

### 1. `/etc/hostname`

**LSB 5.0:** Silent. No requirements.
**FHS 3.0:** Listed as optional under `/etc`.
**RFC 952 / RFC 1123:** Labels may contain only ASCII a-z (case-insensitive), 0-9, and hyphen. Must not end with hyphen. Max 63 chars per label, 255 total FQDN. No underscores.

### 2. `/etc/hosts`

**LSB 5.0:** Silent.
**FHS 3.0:** Optional.
**Convention:** FQDN should come before short hostname on the same line. 127.0.1.1 is conventional loopback for FQDN on DHCP systems.

### 3. `/etc/resolv.conf`

**LSB 5.0:** Silent.
**FHS 3.0:** Optional.
**systemd:** systemd-resolved manages this automatically, creating a symlink to `/run/systemd/resolve/stub-resolv.conf` on boot.

### 4. `/etc/adjtime` / Hardware Clock

**LSB 5.0:** Silent.
**FHS 3.0:** Optional, at `/var/lib/hwclock/adjtime`.
**systemd:** If `/etc/adjtime` doesn't exist, assumes UTC.
**Best practice:** UTC strongly recommended for Linux-only systems. LOCAL only needed for dual-boot with Windows.

### 5. `/etc/os-release`

**LSB 5.0:** Silent (predates widespread adoption).
**freedesktop.org specification (governing standard):**

Format: Shell-compatible KEY=VALUE, UTF-8, one per line. Values with spaces must be quoted.

Fields (none strictly required — all have defaults):
- `NAME` — defaults to "Linux"
- `ID` — defaults to "linux", must be lowercase `[a-z0-9._-]`
- `PRETTY_NAME` — defaults to "Linux"
- `VERSION` — optional (may be unset for rolling releases)
- `VERSION_ID` — optional
- `VERSION_CODENAME` — optional
- `ID_LIKE` — optional (space-separated list of related distro IDs)
- `HOME_URL`, `BUG_REPORT_URL`, `SUPPORT_URL` — optional
- `BUILD_ID` — optional
- `VARIANT` / `VARIANT_ID` — optional
- `ANSI_COLOR` — optional ESC code
- `DEFAULT_HOSTNAME` — optional
- `LOGO` — optional (freedesktop icon name)

File locations: `/etc/os-release` (primary), `/usr/lib/os-release` (vendor fallback).

### 6. `/etc/lsb-release` and `lsb_release` Command

**LSB 5.0:** The `lsb_release` command is REQUIRED for conformance. The spec defines the command, not a specific backing file.

`lsb_release` flags: `-v` (version), `-i` (id), `-d` (description), `-r` (release), `-c` (codename), `-a` (all), `-s` (short).

**Industry status:** LSB 5.0 last updated 2015, effectively abandoned. Major distros (RHEL 9, Fedora, Arch) have dropped `lsb_release`. Industry moved to `/etc/os-release`. However, some older scripts and BLFS packages still call `lsb_release`.

**Recommendation:** Provide BOTH `/etc/os-release` (primary) and `/etc/lsb-release` (compatibility). Provide an `lsb_release` script that reads from os-release for backward compatibility. This connects to the vendor string build issue flagged in project memory.

### 7. Boot Screen Clearing

**LSB 5.0:** Silent.
**systemd default:** Clears screen before login prompt via agetty. Controlled by `TTYVTDisallocate=` in getty service.
**What disabling the clear does:** It prevents systemd from wiping the boot messages off the screen before showing the login prompt. Boot messages (systemd unit starts, kernel output) remain visible above the login prompt. You can still scroll up to see them. `journalctl -b` always works regardless.

### 8. `/etc/vconsole.conf`

**LSB 5.0:** Silent.
**FHS 3.0:** Not mentioned.
**systemd:** Recognized keys: KEYMAP, KEYMAP_TOGGLE, FONT, FONT_MAP, FONT_UNIMAP.

### 9. Locale Settings

**LSB 5.0:** REQUIRED to support POSIX and C locales. Beyond that, locale-related C library functions must work correctly.
**systemd:** Reads `/etc/locale.conf` for system-wide locale.

---

## build_003 Original Branding (from GitHub InterGenOS/build_003)

### `/etc/os-release` (from `finalize_system.sh`, lines 948-953):
```
NAME="InterGen OS"
VERSION=".003SD"
ID=igos
PRETTY_NAME="InterGen OS"
```

### `/etc/igos-release` (from `finalize_system.sh`, line 955):
```
.003SD
```

### `/etc/lsb-release` (from `finalize_system.sh`, lines 960-965):
```
DISTRIB_ID="InterGen OS"
DISTRIB_RELEASE=".003SD"
DISTRIB_CODENAME="InterGen"
DISTRIB_DESCRIPTION="InterGen OS"
```

### Other branding:
- **Hostname:** `InterGenOS`
- **GRUB_DISTRIBUTOR:** `"InterGenOS"`
- **Kernel naming:** `vmlinuz-3.19-intergen-003-systemd`
- **Version scheme:** `.003SD` (SD = systemd)
- **ID string:** `igos`
- **DISTRIB_DESCRIPTION example:** `InterGenOS 1.0`
- **URLs:** `http://intergenstudios.com/intergen_os/` and `https://intergenstudios.com`
- **GitHub org:** `https://github.com/InterGenOS`

---

## Sources

- LSB 5.0 Core Specification (refspecs.linuxfoundation.org)
- FHS 3.0 (refspecs.linuxfoundation.org)
- freedesktop.org os-release(5) specification
- RFC 1123 (hostname requirements)
- LFS 13.0 systemd book Chapter 9 (local copy)
- GitHub InterGenOS/build_003 repository (finalize_system.sh)
