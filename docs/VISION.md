# InterGenOS Vision Document

**Created:** March 30, 2026
**Author:** InterGenJLU
**Status:** Draft — living document
**Last Updated:** April 7, 2026

---

## The Dream

A Linux distribution that puts you in control. Not a reskin. Not a derivative. Built from source, understood completely, designed for people who want to know how their machine works — and for people who wish they could.

## Core Pillars

### 1. Built From Source (LFS 13.0)

Every package compiled from source with deliberate choices. No inherited bloat, no mystery dependencies. The build system is the product — reproducible, documented, tested.

**Base:** Linux From Scratch 13.0-systemd (released March 5, 2026 — first systemd-only LFS release)
**Target triple:** `x86_64-igos-linux-gnu` (carrying forward from the original builds)

**Core toolchain (LFS 13.0):**
- Linux kernel 6.18.10
- GCC 15.2.0
- glibc 2.43
- Binutils 2.46.0
- systemd 259.1
- Python 3.14.3
- 75+ packages, ~603 MB total source download

**Why LFS:** Evaluated Gentoo stage1, Buildroot, Yocto, Alpine, Void, CRUX, T2 SDE, and NixOS. LFS remains the only foundation that provides 100% control and understanding with zero inherited baggage — critical since we're building our own build system and package manager. (Full research: `research/build_systems/lfs_alternatives_2026-03-31.md`)

### 2. Custom Build System (igos-build)

**Architecture:** Python orchestrator + YAML package templates + Bash build functions

- **YAML** for declarative metadata (readable, machine-parseable, reorderable)
- **Bash** for imperative build steps (configure/make/install is shell work)
- **Python** for orchestration (dependency graphs, topological sort, build ordering, validation, caching)
- **Build styles** inspired by Void Linux's xbps-src (autotools, cmake, meson, make, custom)
- **Build profiles** for flag remixing (minimal, standard, full) — simpler than Gentoo USE flags, still powerful
- **Binary cache** — build once, install from cache. Never rebuild GCC unnecessarily.

**Why not pure Bash:** The original build_003 proved bash works for building packages, but it's the wrong tool for orchestration, dependency resolution, and metadata management at scale. The Gentoo model (Python orchestrator + bash templates) is proven. (Full research: `research/build_systems/survey_2026-03-31.md`)

**Template example:**
```yaml
# packages/core/bash/package.yml
name: bash
version: "5.3"
release: 1
description: "The GNU Bourne Again Shell"
license: GPL-3.0-or-later
source:
  - url: https://ftp.gnu.org/gnu/bash/bash-${version}.tar.gz
    sha256: ...
dependencies:
  build: [bison]
  host: [readline, ncurses]
  runtime: [readline, ncurses]
build_style: autotools
configure_flags:
  - --without-bash-malloc
  - --with-curses
  - --enable-readline
```

### 3. Custom Package Manager (`pkm`)

**Philosophy:** Honest about trade-offs. Transparency over automation.

- **Package format:** `.igos.tar.gz` (Zstandard-compressed tarball + metadata.json)
- **Dependency tracking:** Explicit, declared in templates, topological sort for resolution
- **Binary cache:** Build once, install from cache
- **Atomic operations:** No partial installs (learned from Void's XBPS)
- **Core operations:** install, remove, query, list, verify

**What it does NOT promise:** NP-complete dependency auto-resolution. The build system handles build ordering; the package manager handles installed-system state.

**Research basis:** Surveyed LFS's seven documented approaches, studied successes (CRUX pkgutils, Slackware pkgtools, Alpine apk, Void XBPS, Nix) and failures (RPM→YUM→DNF trajectory, Corel Linux). (Full research: `research/package_management/pm_history_and_approaches_2026-03-31.md`)

### 4. Switchable Desktop Environments

Pick your experience at login. Not a dropdown afterthought — a first-class feature with isolated per-DE configurations. No `~/.config` contamination between environments.

**Target DEs (initial):**
- GNOME (Wayland)
- KDE Plasma (Wayland)
- COSMIC (System76's Rust-based DE — Wayland-native)

**Architecture approach:**
- Per-DE config directories (not shared `~/.config`)
- systemd-homed `preferredSessionLauncher` integration
- Polished greeter (greetd + custom frontend) — not a buried dropdown
- `xdg-session-management-v1` protocol for session restore

### 5. Tiered Local AI Assistant

Hardware-detected, fully local, no cloud. The AI helps you learn Linux — it doesn't hide it from you.

| Tier | Hardware | STT | LLM | TTS | Capability |
|------|----------|-----|-----|-----|------------|
| 1 | 4GB RAM, any CPU | Vosk (50MB) | Qwen3-0.6B Q4 (0.9GB) | Piper | Text + basic voice commands |
| 2 | 8GB RAM, 4+ cores | Whisper tiny (273MB) | Qwen3-1.7B Q4 (1.6GB) | Piper | Full voice assistant |
| 3 | 16GB+ RAM, discrete GPU | Whisper fine-tuned | Full LLM (7B-35B) | Kokoro | Full JARVIS-class experience |

**Key design decisions:**
- Installer detects hardware tier automatically
- AI is an optional service — power users can disable it entirely
- Piper TTS for Tier 1-2 (MOS 4.3, runs on RPi), Kokoro for Tier 3
- CPU inference via llama.cpp is the baseline (66% of machines lack discrete GPUs)
- NPUs are NOT used for LLM inference (CPU outperforms NPU on current hardware)

### 6. The AI Teaches, Not Hides

When the AI runs a command, it explains what it's doing. When it configures something, it shows you the config file and what changed. The goal is a user who understands their system better after every interaction — not one who depends on the AI more.

### 7. Custom Installer

A polished, modern installation experience that reflects InterGenOS's philosophy of transparency and user control.

- **Hardware detection:** Automatic AI tier detection, GPU identification, RAM/CPU profiling
- **DE selection:** First-class desktop environment choice during install
- **Transparency:** Every installation step is explained — what's happening and why
- **Partitioning:** Guided and manual options
- **Post-install:** System validation, first-boot configuration, AI assistant introduction

*(Installer architecture and technology stack to be determined through research.)*

---

## What Makes InterGenOS Different

| Feature | InterGenOS | Ubuntu/Mint | Arch | NixOS |
|---------|-----------|-------------|------|-------|
| Built from source | Yes | No | No | Partially |
| Custom build system | Yes (Python+YAML+Bash) | No | No | Nix language |
| AI assistant | Local, tiered | None | None | None |
| Multi-DE (first-class) | Yes | No | DIY | Declarative but complex |
| Config isolation per DE | Yes | No | No | Via Home Manager |
| Runs on 4GB RAM | Yes (Tier 1) | Barely | Yes | Yes |
| User learns the system | By design | Incidental | By necessity | By necessity |
| Custom installer | Yes | Ubiquity | archinstall | nixos-install |

---

## Technical Architecture

### Build System (igos-build)
- Python orchestrator reads YAML package templates
- Dependency graph → topological sort → build order
- Build styles handle common patterns (autotools, cmake, meson)
- Custom build.sh for complex packages (GCC, glibc)
- Binary cache in .igos.tar.gz format
- Two-phase toolchain validation (carried forward from build_003)
- Full logging with timestamps
- Fatal sanity checks halt build on failure

### Package Management (`pkm`)
- `.igos.tar.gz` package format with Slackware-style text manifests + SQLite database
- `pkm install-helper` for proprietary software (Chrome, VS Code, Claude Code, Steam, Discord, Spotify) — transparent download via vendor helpers, tracked in pkm database
- Dependency tracking and reverse-dependency awareness
- File ownership queries (`pkm provides /usr/bin/bash`)
- Package integrity verification (`pkm verify --all`)
- Operation history logging

### Virtualization / Development Environment
- **KVM/QEMU with libvirt** for build and test VMs
- Near-native performance (0-5% overhead vs VirtualBox's higher overhead)
- Kernel-native — no DKMS compatibility issues
- Fully scriptable via virt-install + virsh
- virtiofs for host-guest file sharing
- qcow2 disk format for snapshots at build milestones
- **(Full research: `research/virtualization/kvm_decision_2026-03-31.md`)**

**Recommended VM configuration:**
- 16 vCPUs, 32GB RAM (on Ryzen 9 5900X / 64GB host)
- 200GB qcow2 disk with virtio-scsi + IOThreads
- virtiofs for source tarball sharing
- Ubuntu 24.04 as build host inside VM

### Init System
- systemd (required for homed/logind DE integration; LFS 13.0 is systemd-only)

### Display Server
- Wayland only (GNOME 49, KDE 6.8, and COSMIC are all Wayland-only in 2026)

### Boot
- systemd-boot or GRUB 2
- Custom Plymouth splash theme

---

## Development Environment

- **Build host:** Ubuntu 24.04 (AMD Desktop — Ryzen 9 5900X, 64GB RAM)
- **Virtualization:** KVM/QEMU with libvirt (replaced VirtualBox — kernel-native, better performance)
- **Build/test:** KVM VMs with qcow2 snapshots
- **Development drive:** `/mnt/intergenos` (466GB ext4)
- **Future:** Dedicated NVMe over USB (1TB, ordered)
- **Version control:** Git (github.com/InterGenOS)

---

## Research Completed (March 2026)

1. **AI-integrated Linux distros** — nobody has shipped a fully local AI assistant in a distro
2. **Minimum viable LLMs** — Qwen3-0.6B through Phi-4-mini, with hard benchmark numbers
3. **Switchable DE architecture** — systemd-homed has the spec, nobody wired it up
4. **Base distro options** — LFS chosen deliberately over derivatives (re-evaluated March 31, 2026 — decision stands)
5. **Distro building toolchains** — Calamares, live-build, Penguins Eggs, GitHub Actions
6. **Consumer hardware 2018-2026** — 8GB RAM is the floor, 16GB growing fast, NPUs irrelevant for LLMs
7. **Build systems beyond Bash** — Surveyed 9 systems (ALFS, Buildroot, Yocto, Gentoo, Void, Nix, CRUX, Arch, Alpine). Chose Python+YAML+Bash hybrid.
8. **Package template formats** — Real-world analysis of template formats across 6 distros. Designed InterGenOS template format.
9. **Package management history** — Studied 7 LFS approaches, 6 successful minimal PMs, multiple failure cases. Designed honest, minimal PM.
10. **Virtualization** — KVM vs VirtualBox vs VMware. KVM selected for performance, kernel compatibility, and scriptability.
11. **LFS version pinning** — Evaluated LFS 12.3 through 13.0. Pinned to 13.0 (March 2026 release).

Full research archives in `/mnt/intergenos/research/`

---

## Milestones (No Deadlines)

1. ☑ Development environment (KVM setup, build host validation)
2. ☑ Build system core (igos-build — parser, graph, builder, tracker, styles)
3. ☑ First package templates (LFS 13.0 toolchain — 28 packages)
4. ☑ Toolchain build (cross-compilation toolchain in KVM chroot)
5. ☑ Core system build (full LFS 13.0 Chapter 8 — 106 packages)
6. ☑ Package manager implementation (`pkm` — install, remove, query, verify, depends, install-helper)
7. ☑ Single DE working (GNOME 49.4 on Wayland — first boot April 7, 2026)
8. ☑ BLFS package database + meson feature database
9. ☑ 5-distro kernel convergence (Ubuntu, Fedora, Arch, Debian, openSUSE)
10. ☑ 4-LLM code review + 77-issue audit remediation
11. ☐ Bare metal boot (HP Laptop 14-dq1xxx — kernel config ready, USB image pending)
12. ☐ Custom installer — GUI (GTK4, DeepSeek proposal reviewed)
13. ☐ First-boot animation (ECG heartbeat pulse + text sequence, SDL → DRM/KMS)
14. ☐ FLUX.2-generated branding (logo, GRUB, Plymouth, GDM, wallpaper)
15. ☐ VPS package mirror (pre-built archives for pkm download)
16. ☐ Application roadmap Phase 1 (install helpers: Discord, Spotify, Steam, Edge, Brave)
17. ☐ Application roadmap Phase 2 (LibreOffice, VLC, Thunderbird, GIMP, Inkscape, Firefox)
18. ☐ Multi-DE with config isolation (KDE Plasma, COSMIC)
19. ☐ AI Tier 1 integration (text-based assistant)
20. ☐ AI Tier 2 integration (voice — adapted from JARVIS)
21. ☐ First public release + community announcement

---

## Development Pipeline (Planned)

### Persistent Build Environment
Snapshot the build VM after a successful build (`virsh snapshot-create-as igos-build build-ready`). Any time a new package needs to be built, start the VM — the full toolchain and all libraries are ready. No 5-hour rebuild for a single package.

### Package Generator
A tool that generates package templates from a name + version: detects build system (autotools/meson/cmake), queries BLFS database for dependencies, downloads source, generates `package.yml` + `build.sh`. Turns "add a new package" from a 20-minute research task into a 30-second command.

### Automated Build Testing
After a successful build, run a scripted test suite inside the booted VM:
- Verify all systemd services start without errors
- Verify GNOME session launches and renders
- Verify gvfs backends load (`/usr/lib/gvfs/`)
- Verify Vulkan (`vulkaninfo`)
- Verify GSettings schemas compiled
- Verify icon/font/pixbuf caches populated
- Verify pkm can query and install packages

### VPS Package Mirror
Upload `.igos.tar.gz` archives to the VPS at `origin.intergenstudios.com`. `pkm update` fetches a package index; `pkm install <name>` downloads and installs pre-built binaries. No compilation on the user's machine.

### Universal Kernel Config
Kernel configuration derived from convergence analysis of 5 major distributions. The baseline covers 3,434 options where 4+ distros agree. InterGenOS-specific overrides add desktop optimizations (PREEMPT, HZ=1000), IoT support, and hardware-specific drivers (HP laptop SOF audio, RTW88 WiFi, LPSS I2C).

---

## History

- **2015:** InterGenOS build_001 — first LFS attempt (Linux 3.18.2)
- **2016:** build_002, build_003 — refined, 83 packages, fully automated build on multiple machines
- **2016-2025:** Life happened. Project shelved.
- **2026:** Revival. Leveraging a decade of growth and AI development experience to bring Linux closer to everyday users — proving that a from-source distribution can be both deeply educational and genuinely accessible.
