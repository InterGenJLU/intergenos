# InterGenOS

**A Linux distribution built entirely from source with a custom package manager, system installer, and a tiered local AI assistant.**

InterGenOS puts the user in control of their own machine. Every package is compiled from source with deliberate choices. Every design decision serves one purpose: giving people a system they understand, can modify, and can trust.

## The Prime Directive

*InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust. Any complexity that doesn't serve the user — or that hides how the system works — is not welcome, regardless of how conventional it may be.*

## Features

- **Built from source** — Based on LFS 13.0 / BLFS 13.0, every component chosen deliberately
- **Custom package manager** (`pkm`) — Natural-language CLI with SQLite + text manifest hybrid storage
- **System installer** (`forge`) — TUI-based installer powered by pkm, from partition to bootable desktop
- **Custom build system** (`igos-build`) — Python orchestrator with YAML templates, dependency resolution, and full build logging
- **BLFS package database** — 926 packages with 4,679 dependencies queryable via SQL
- **GNOME desktop** — Wayland-native with dark theme and InterGenOS branding
- **Extra tier** — Node.js, Google Chrome, VS Code, and Claude Code via download helpers
- **Tiered local AI assistant** — Hardware-detected, fully offline (planned)

## Tools

| Tool | Purpose |
|------|---------|
| `pkm` | Package manager — install, remove, search, verify, depends |
| `forge` | System installer — partition, deploy archives, configure, boot |
| `igos-build` | Build system — source to archives with dependency resolution |
| `blfs-query` | BLFS database query tool — deps, gaps, chain-cost, versions |

## Package Tiers

| Tier | Purpose |
|------|---------|
| toolchain | Cross-compilation (LFS Ch. 5-7) |
| core | Full system: kernel, shell, coreutils, systemd, GCC, SSH |
| base | CLI tools: htop, rsync, strace, screen |
| desktop | GNOME on Wayland: GTK, Mesa, GStreamer, GNOME Shell |
| extra | User applications: Node.js, Chrome/VS Code/Claude Code helpers |

## Build System

Single command builds the entire system:

```bash
sudo bash scripts/build-intergenos.sh --user christopher --checkpoint
```

Phases: `validate → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → kernel → desktop → extra → image`

Resume from any phase with `--start-at`, stop with `--stop-after`. Checkpoints saved after toolchain, core, kernel, and desktop.

## Quick Start

```bash
# Build the OS (on Ubuntu 24.04 build VM)
sudo bash scripts/build-intergenos.sh --user christopher --checkpoint

# Query the BLFS package database
python3 scripts/blfs-query.py info samba
python3 scripts/blfs-query.py deps mesa --recursive
python3 scripts/blfs-query.py chain-cost openldap

# Package management (on a running InterGenOS system)
pkm list installed
pkm search audio
pkm info openssh
pkm provides /usr/bin/bash
pkm verify --all

# Install applications (on a running InterGenOS system)
sudo igos-install-chrome        # Google Chrome
sudo igos-install-vscode        # Visual Studio Code
igos-install-claude-code        # Claude Code CLI + extension
```

## Project Structure

```
intergenos/
├── igos-build/          # Build system (Python — parser, graph, builder)
├── pkm/                 # Package manager (Python — install, remove, query, verify)
├── installer/           # Forge installer (Python — TUI + backend)
├── packages/            # Package templates (YAML + build.sh)
│   ├── toolchain/       # LFS Ch. 5-7
│   ├── core/            # LFS Ch. 8 + TLS/PAM/SSH
│   ├── base/            # End-user CLI tools
│   ├── desktop/         # GNOME desktop stack
│   └── extra/           # User-facing applications
├── scripts/             # Build orchestrator, chroot scripts, BLFS tools
├── config/              # Kernel config, systemd units, gsettings overrides
├── build/               # Sources, patches, logs, archives (not committed)
└── docs/                # LFS/BLFS reference books (not committed)
```

## Status

Active development. Originally built in 2015-2016 (build_001 through build_003). Revived March 2026.

**Current:** Core system proven (134 packages, zero failures, boots and runs). Desktop build in progress. Package manager and installer written. Extra tier established.

## History

- **2015:** build_001 — First LFS attempt
- **2016:** build_002, build_003 — 83 packages, fully automated
- **2016-2025:** Project shelved
- **2026:** Revival — new build system, package manager (pkm), installer (forge), BLFS database, GNOME desktop, AI integration planned

## Acknowledgments

InterGenOS is built on the foundation of [Linux From Scratch](https://www.linuxfromscratch.org/) (LFS 13.0) and [Beyond Linux From Scratch](https://www.linuxfromscratch.org/blfs/) (BLFS 13.0). See [CREDITS](CREDITS) for full attribution.

## License

InterGenOS build system, tools, and templates: [GNU General Public License v3.0 or later](LICENSE).

Individual packages retain their respective upstream licenses as declared in each `package.yml`.

## Author

InterGenJLU — [InterGen Studios](https://intergenstudios.com)
