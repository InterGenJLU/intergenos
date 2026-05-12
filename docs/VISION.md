# InterGenOS Vision Document

**Created:** March 30, 2026
**Author:** InterGenJLU
**Status:** Draft — living document
**Last Updated:** 2026-05-12

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

**Why LFS:** Evaluated Gentoo stage1, Buildroot, Yocto, Alpine, Void, CRUX, T2 SDE, and NixOS. LFS remains the only foundation that provides 100% control and understanding with zero inherited baggage — critical since we're building our own build system and package manager. (Full research: [LFS Alternatives Assessment](research/build_systems/lfs_alternatives_2026-03-31.md))

### 2. Custom Build System (igos-build)

**Architecture:** Python orchestrator + YAML package templates + Bash build functions

- **YAML** for declarative metadata (readable, machine-parseable, reorderable)
- **Bash** for imperative build steps (configure/make/install is shell work)
- **Python** for orchestration (dependency graphs, topological sort, build ordering, validation, caching)
- **Build styles** inspired by Void Linux's xbps-src (autotools, cmake, meson, make, custom)
- **Build profiles** for flag remixing (minimal, standard, full) — simpler than Gentoo USE flags, still powerful
- **Binary cache** — build once, install from cache. Never rebuild GCC unnecessarily.

**Why not pure Bash:** The original build_003 proved bash works for building packages, but it's the wrong tool for orchestration, dependency resolution, and metadata management at scale. The Gentoo model (Python orchestrator + bash templates) is proven. (Full research: [Build System Survey](research/build_systems/survey_2026-03-31.md))

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

**Research basis:** Surveyed LFS's seven documented approaches, studied successes (CRUX pkgutils, Slackware pkgtools, Alpine apk, Void XBPS, Nix) and failures (RPM→YUM→DNF trajectory, Corel Linux). (Full research: [Package Management History](research/package_management/pm_history_and_approaches_2026-03-31.md))

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

Hardware-detected, fully local, no cloud. Text-only by design — the AI helps you learn Linux, it doesn't hide it from you.

| Tier | Hardware | LLM | Capability |
|------|----------|-----|------------|
| 1 | 4GB RAM, any CPU | Qwen3-0.6B Q4 (397MB) | Lightweight assistant — answers, simple commands |
| 2 | 8-15GB RAM, 4+ cores | Qwen3-1.7B Q4 (1.3GB) on CPU; Qwen3-9B Q4 with discrete GPU | Multi-turn reasoning, file analysis, package management |
| 3 | 16GB+ RAM, discrete GPU | Qwen3-9B+ Q4 (4.9GB+) | Full local-AI capabilities — long-context reasoning, complex orchestration |

**Key design decisions:**
- Installer detects hardware tier automatically
- AI is an optional service — power users can disable it entirely
- CPU inference via llama.cpp is the baseline (66% of machines lack discrete GPUs)
- NPUs are NOT used for LLM inference (CPU outperforms NPU on current hardware)
- Text interaction only — voice was evaluated and dropped to keep the assistant simple and predictable

**AI packages (new "ai" tier):**
- llama.cpp — built from source
- InterGen application — D-Bus service with CLI, GNOME Shell extension, hardware detection
- Model management — automatic tier detection, download from Hugging Face or source mirror

### 5b. InterGen Sentinel — Pluggable Security Scanning

InterGen Sentinel is the security-scanning layer for the local assistant: a vendor-neutral, pluggable provider chain that classifies installed packages, configurations, and runtime activity for known and emerging risks.

**Default chain (always available, fully local):**
- `Local-Rules` — rule-based classifier (CVE feeds, known-bad-pattern matchers, supply-chain checks)
- `Local-Qwen` — local-LLM-backed safety review using the same tier model already loaded for the AI assistant

**Optional cloud providers (opt-in, off by default):** Glasswing-Anthropic, Gemini-Google, CoPilot-Microsoft, ChatGPT-OpenAI, Grok-xAI, DeepSeek. Each provider is a discrete plug-in; users select zero or more, and credentials are stored in GNOME Keyring (libsecret) — never plaintext.

**Commands:**
- `intergen scan` — scan installed packages for known vulnerabilities (default chain)
- `intergen harden` — AI-guided system hardening recommendations (default chain plus any opt-in providers)
- `intergen audit` — full security audit of the running system

**Design rationale:** The assistant doesn't just find vulnerabilities — it explains what they are, why they matter, and how to fix them. Users understand their security posture, not just their system configuration. The vendor-neutral provider chain means the project doesn't bind itself to any single security ecosystem; users keep control of where their data flows.

**Availability:** The Sentinel module is always installed. The default Local-Rules + Local-Qwen chain works fully offline. Cloud providers activate only when explicitly opted in and credentials are configured.

### 6. The AI Teaches, Not Hides

When the AI runs a command, it explains what it's doing. When it configures something, it shows you the config file and what changed. The goal is a user who understands their system better after every interaction — not one who depends on the AI more.

### 7. Custom Installer (Forge)

A polished, modern installation experience that reflects InterGenOS's philosophy of transparency and user control.

- **Hardware detection:** Automatic AI tier detection, GPU identification, RAM/CPU profiling
- **DE selection:** First-class desktop environment choice during install
- **Transparency:** Every installation step is explained — what's happening and why
- **Partitioning:** Guided and manual options
- **Post-install:** System validation, first-boot configuration, AI assistant introduction
- **Secure Boot enrollment:** MOK key generation per-machine; signed-chain verification; user is the trust anchor

**Implementation status:** Backend modules complete (disks / packages / config / users / hooks / mok / bootloader / extract / live-cleanup — ~2,000 lines, 186 unit tests). Forge TUI frontend complete (656 lines). Forge GUI (GTK4 + libadwaita) and the live-ISO infrastructure (custom initramfs, squashfs builder, 3-entry GRUB menu — Try / Install GUI / Install TUI-Builder) are in design — see milestones below.

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
- **(Full research: [KVM Decision](research/virtualization/kvm_decision_2026-03-31.md))**

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
- **Version control:** Git (github.com/InterGenJLU)

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

Full research archives in [docs/research/](research/INDEX.md) (180+ documents across 24 topical subdirectories — including post-mortems on the signing ceremony, pkm migration design lessons, code audit deliverables, and shim-review preparation)

---

## Milestones (No Deadlines)

### Foundation

1. ☑ Development environment (KVM setup, build host validation)
2. ☑ Build system core (igos-build — parser, graph, builder, tracker, styles)
3. ☑ First package templates (LFS 13.0 toolchain — 28 packages)
4. ☑ Toolchain build (cross-compilation toolchain in KVM chroot)
5. ☑ Core system build (full LFS 13.0 Chapter 8 — 115 packages)
6. ☑ Package manager implementation (`pkm` — install, remove, query, verify, depends, install-helper)
7. ☑ Single DE working (GNOME 49.4 on Wayland — first boot April 7, 2026)
8. ☑ BLFS package database + meson feature database
9. ☑ 5-distro kernel convergence (Ubuntu, Fedora, Arch, Debian, openSUSE)
10. ☑ 4-LLM code review + 77-issue audit remediation

### Build system maturity (April–May 2026)

11. ☑ Forge installer backend (~2,000 lines across 9 modules — disks/packages/config/users/hooks/mok/bootloader/extract/live-cleanup)
12. ☑ Forge installer TUI frontend (656 lines)
13. ☑ Test harness (186 unit + integration tests covering installer backend, MOK validation, Class 1 signing-chain verification, supersedes parser/db/verifier)
14. ☑ AppArmor LSM (apparmor 3.0.8 + starter profiles, default-on for v1.0)
15. ☑ Supersedes RFC v1 (parser supersedes field, pkm DB columns, atomic supersede transaction, content-hash verifier, Phase 7 unit tests)
16. ☑ Theming canonical (InterGenOS GTK + Shell themes, Cybernetic-Blue icon theme, Bibata-Modern-Classic cursor, dark scheme)
17. ☑ First themed qcow2 image build (May 3, 2026 — kernel 6.18.10, GNOME 49 on Wayland, 478 packages, full theming applied)

### Signing chain + secure boot (May 2026)

18. ☑ Signing-key ceremony (Tails 7.7 air-gap; RSA-4096 master + 4 signing subkeys + encryption sub; LUKS-encrypted master backup; base16 paperkey × 2; revocation cert)
19. ☑ Hardware token chain (Nitrokey 3 NFC × 4; signing subs keytocard'd to all 4; UIF touch-policy enabled; 2-year expiry 2028-05-04)
20. ☑ EFI vendor cert (Nitrokey #1 PIV slot 9c; 2-year cert; rotated AES-256 management key)
21. ☑ Master pubkey published (`keys.openpgp.org` email-verified + `keyserver.ubuntu.com` SKS index; `docs/signing-key.asc` committed)
22. ☑ Forge Secure Boot toolchain (gnu-efi + rpm + shim-signed + efitools + mokutil + sbsigntool all built from source)
23. ☐ Shim-review submission (rhboot/shim-review PR — target ~2026-05-15; replaces piggyback path)
24. ☐ Microsoft 2011 CA migration (own MS-signed shim before 2026-06-27 cutoff)

### v1.0 application + experience layer

25. ☑ Bare metal first-light boot — HP laptop running active InterGenOS install. Pending: HP dw0037wm second-target deploy + end-to-end ISO-installer-path validation.
26. ☐ Live ISO infrastructure (custom initramfs + squashfs builder + 3-entry GRUB menu: Try / Install GUI / Install TUI-Builder)
27. ☐ Forge GUI frontend (GTK4 + libadwaita; 4–6 screen flow; Welcome → Disk → User → Install → Done)
28. ☑ First-boot animation — Phase 2 SDL backend complete (~800 lines at `assets/intergen-firstboot/`). Phase 3 (DRM/KMS direct framebuffer) is post-v1.0 polish.
29. ☑ FLUX-generated branding (theming canonical assets — logo, icon theme, cursor; GRUB / Plymouth / GDM polish pending)
30. ☑ Application roadmap Phase 1 — 7 install-helpers shipping (Brave, Chrome, Claude Code, Discord, Edge, Spotify, VS Code). Steam deferred to Phase 3 (x86 binary compatibility).
31. ☑ Application roadmap Phase 2 partial — Firefox 140.9.0esr, Audacity 3.7.7, Transmission 4.1.1, Rhythmbox 3.4.9 + LV2 plugin host stack landed; LibreOffice / VLC / Thunderbird / GIMP / Inkscape pending
32. ☐ Multi-DE with config isolation (KDE Plasma, COSMIC)
33. ☐ AI Tier 1 integration (intergen-console + intergen-daemon; opt-in LLM endpoint)
34. ☐ InterGen Sentinel security scanning (Local-Rules + Local-Qwen default; opt-in cloud providers)
35. ☐ Public binary mirror + ISO download infrastructure — `repo.intergenos.org` infrastructure (DNS, TLS, SSH access, docroot) provisioned 2026-05-11; first-publish via `pkm sync` against the live mirror pending Build #9 completion + `InterGenOS.db` signed index generation + first end-to-end sync test

### Release

36. ☐ First public v1.0 release + community announcement

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
