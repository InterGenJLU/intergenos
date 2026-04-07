# InterGenOS

**A Linux distribution built entirely from source with a custom package manager, system installer, and a tiered local AI assistant.**

InterGenOS puts the user in control of their own machine. Every package is compiled from source with deliberate choices. Every design decision serves one purpose: giving people a system they understand, can modify, and can trust.

![InterGenOS First Boot — GNOME 49 on Wayland](images/FirstBoot_InterGenOS_Revival.png)

## The Prime Directive

*InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust. Any complexity that doesn't serve the user — or that hides how the system works — is not welcome, regardless of how conventional it may be.*

## Features

- **Built from source** — Based on LFS 13.0 / BLFS 13.0, every component chosen deliberately
- **Custom package manager** (`pkm`) — Natural-language CLI with SQLite + text manifest hybrid storage
- **System installer** (`forge`) — TUI-based installer powered by pkm, from partition to bootable desktop
- **Custom build system** (`igos-build`) — Python orchestrator with YAML templates, dependency resolution, and full build logging
- **BLFS package database** — 926 packages with 4,679 dependencies queryable via SQL, plus meson feature database (2,558 options across 133 packages)
- **5-distro kernel convergence** — kernel config derived from Ubuntu, Fedora, Arch, Debian, and openSUSE consensus (3,434 universal options)
- **GNOME desktop** — Wayland-native with dark theme and InterGenOS branding
- **Extra tier** — Node.js, Google Chrome, VS Code, and Claude Code (proprietary packages fetched transparently via pkm)
- **Tiered local AI assistant** — Hardware-detected, fully offline (planned)

## Tools

| Tool | Purpose |
|------|---------|
| `pkm` | Package manager — install, remove, search, verify, depends |
| `forge` | System installer — partition, deploy archives, configure, boot |
| `igos-build` | Build system — source to archives with dependency resolution |
| `blfs-query` | BLFS database query tool — deps, gaps, chain-cost, versions, meson-flags, meson-audit |
| `populate-meson-db` | Meson feature database populator — parses options from source tarballs |

## Package Tiers

| Tier | Purpose |
|------|---------|
| toolchain | Cross-compilation (LFS Ch. 5-7) |
| core | Full system: kernel, shell, coreutils, systemd, GCC, SSH |
| base | CLI tools: htop, rsync, strace, screen |
| desktop | GNOME on Wayland: GTK, Mesa, GStreamer, GNOME Shell |
| extra | User applications: Node.js, Google Chrome, VS Code, Claude Code |

## Build System

Single command builds the entire system:

```bash
sudo bash scripts/build-intergenos.sh --user <username> --checkpoint
```

Phases: `validate → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → kernel → desktop → extra → image`

Resume from any phase with `--start-at`, stop with `--stop-after`. Checkpoints saved after toolchain, core, kernel, and desktop phases.

## Quick Start

```bash
# Build the OS (on Ubuntu 24.04 build VM)
sudo bash scripts/build-intergenos.sh --user <username> --checkpoint

# Query the BLFS package database
python3 scripts/blfs-query.py info samba
python3 scripts/blfs-query.py deps mesa --recursive
python3 scripts/blfs-query.py chain-cost openldap

# Meson feature database — what flags does a package need?
python3 scripts/blfs-query.py meson-flags gtk4
python3 scripts/blfs-query.py meson-audit --tier desktop
python3 scripts/blfs-query.py meson-impact shaderc

# Package management (on a running InterGenOS system)
pkm install alsa-utils
pkm install-helper chrome       # Fetches from Google, installs via pkm
pkm install-helper vscode       # Fetches from Microsoft, installs via pkm
pkm install-helper claude-code  # Fetches from Anthropic, installs via pkm
pkm remove htop
pkm list installed
pkm search audio
pkm info openssh
pkm provides /usr/bin/bash
pkm verify --all
```

## Project Structure

```
intergenos/
├── igos-build/          # Build system (Python — parser, graph, builder, tracker)
├── pkm/                 # Package manager (Python — install, remove, query, verify)
├── installer/           # Forge installer (Python — TUI + backend)
├── packages/            # 530+ package templates (YAML + build.sh)
│   ├── toolchain/       # LFS Ch. 5-7
│   ├── core/            # LFS Ch. 8 + TLS/PAM/SSH
│   ├── base/            # End-user CLI tools
│   ├── desktop/         # GNOME desktop stack (~368 packages)
│   └── extra/           # User-facing applications
├── scripts/             # Build orchestrator, chroot scripts, BLFS tools
├── data/                # Curated metadata (meson option-to-dep mappings)
├── config/              # Kernel config, systemd units, gsettings overrides
├── build/               # Sources, patches, logs, archives (not committed)
└── docs/                # LFS/BLFS reference books (not committed)
```

## Status

Active development. Originally built in 2015-2016 (build_001 through build_003). Revived March 2026.

**Current:** 530+ packages across 5 tiers. First successful GNOME desktop boot achieved April 7, 2026 — GNOME 49.4 on Wayland, kernel 6.18.10, 478 packages built from source. Kernel config derived from 5-distro convergence analysis. Codebase reviewed by 4 external LLMs with all 77 audit findings remediated. Targeting HP laptop bare metal test via bootable USB.

## History

- **2015:** build_001 — First LFS attempt
- **2016:** build_002, build_003 — 83 packages, fully automated
- **2016-2025:** Life happened. Project shelved.
- **2026:** Revival. New build system, package manager (pkm), installer (forge), BLFS database, GNOME desktop, and the conviction that a from-source distribution can be both deeply educational and genuinely accessible.

## Acknowledgments

InterGenOS is built on the foundation of [Linux From Scratch](https://www.linuxfromscratch.org/) (LFS 13.0) and [Beyond Linux From Scratch](https://www.linuxfromscratch.org/blfs/) (BLFS 13.0). The LFS project and its contributors have made from-source Linux building accessible and educational for over two decades. This project would not exist without their work.

All included packages carry their own licenses as tracked in their package templates. See [CREDITS](CREDITS) for full attribution.

## License

InterGenOS build system, tools, and templates: [GNU General Public License v3.0 or later](LICENSE).

Individual packages retain their respective upstream licenses as declared in each `package.yml`.

## Author

InterGenJLU — [InterGen Studios](https://intergenstudios.com)
