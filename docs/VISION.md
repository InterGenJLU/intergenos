# InterGenOS Vision Document

**Created:** March 30, 2026
**Author:** Christopher Cork
**Status:** Draft — living document

---

## The Dream

A Linux distribution that puts you in control. Not a reskin. Not a derivative. Built from source, understood completely, designed for people who want to know how their machine works — and for people who wish they could.

## Core Pillars

### 1. Built From Source (LFS)

Every package compiled from source with deliberate choices. No inherited bloat, no mystery dependencies. The build system is the product — reproducible, documented, tested.

**Base:** Linux From Scratch 12.3 (current stable)
**Target triple:** `x86_64-igos-linux-gnu` (carrying forward from the original builds)

### 2. Switchable Desktop Environments

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

### 3. Tiered Local AI Assistant

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

### 4. The AI Teaches, Not Hides

When the AI runs a command, it explains what it's doing. When it configures something, it shows you the config file and what changed. The goal is a user who understands their system better after every interaction — not one who depends on the AI more.

---

## What Makes InterGenOS Different

| Feature | InterGenOS | Ubuntu/Mint | Arch | NixOS |
|---------|-----------|-------------|------|-------|
| Built from source | Yes | No | No | Partially |
| AI assistant | Local, tiered | None | None | None |
| Multi-DE (first-class) | Yes | No | DIY | Declarative but complex |
| Config isolation per DE | Yes | No | No | Via Home Manager |
| Runs on 4GB RAM | Yes (Tier 1) | Barely | Yes | Yes |
| User learns the system | By design | Incidental | By necessity | By necessity |

---

## Technical Architecture (Planned)

### Build System
- Phased build scripts (carrying forward from build_003 architecture)
- Toolchain validation tests between phases
- Comprehensive logging
- Reproducible builds (same source → same result)

### Package Management (Future)
- Custom package manager (the original vision included "Linpack")
- Source-based with binary cache option
- Dependency resolution

### Init System
- systemd (proven, required for homed/logind DE integration)

### Display Server
- Wayland only (GNOME 49, KDE 6.8, and COSMIC are all Wayland-only in 2026)

### Installer
- Calamares (proven, brandable, used by Manjaro/Garuda/KDE Neon)
- Custom branding + AI tier detection during install

### Boot
- systemd-boot or GRUB 2
- Custom Plymouth splash theme

---

## Development Environment

- **Build host:** Ubuntu 24.04 (Christopher's desktop — Ryzen 9 5900X, 64GB RAM)
- **Build/test:** VirtualBox VMs
- **Development drive:** `/mnt/intergenos` (466GB ext4)
- **Future:** Dedicated NVMe over USB (1TB, ordered)
- **Version control:** Git (github.com/InterGenOS)

---

## Research Completed (March 2026)

1. **AI-integrated Linux distros** — nobody has shipped a fully local AI assistant in a distro
2. **Minimum viable LLMs** — Qwen3-0.6B through Phi-4-mini, with hard benchmark numbers
3. **Switchable DE architecture** — systemd-homed has the spec, nobody wired it up
4. **Base distro options** — LFS chosen deliberately over derivatives
5. **Distro building toolchains** — Calamares, live-build, Penguins Eggs, GitHub Actions
6. **Consumer hardware 2018-2026** — 8GB RAM is the floor, 16GB growing fast, NPUs irrelevant for LLMs

Full research archives in `/mnt/intergenos/research/` (to be populated)

---

## Milestones (No Deadlines)

1. ☐ Development environment (VirtualBox, toolchain validation)
2. ☐ LFS 12.3 base build (toolchain → core system → bootable)
3. ☐ Single DE working (GNOME or KDE on Wayland)
4. ☐ Multi-DE with config isolation
5. ☐ AI Tier 1 integration (text-based assistant)
6. ☐ AI Tier 2 integration (voice)
7. ☐ Installer (Calamares branded)
8. ☐ First bootable ISO

---

## History

- **2015:** InterGenOS build_001 — first LFS attempt (Linux 3.18.2)
- **2016:** build_002, build_003 — refined, 60+ packages, automated build
- **2016-2025:** Life happened. Project shelved.
- **2026:** Revival. Christopher now has AI development experience (JARVIS, VOQR), Python/C toolchain expertise, and Claude as a development partner. The dream continues.
