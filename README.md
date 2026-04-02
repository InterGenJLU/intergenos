# InterGenOS

**A Linux distribution built entirely from source with switchable desktop environments, a custom build system, and a tiered local AI assistant.**

InterGenOS puts the user in control of their own machine. Every package is compiled from source with deliberate choices. Every design decision serves one purpose: giving people a system they understand, can modify, and can trust.

The built-in AI assistant — **InterGen** — helps you learn Linux, not hide it from you.

## Features (In Development)

- **Built from source** — Based on LFS 13.0, every component chosen deliberately
- **Custom build system** (`igos-build`) — Python orchestrator with YAML package templates and bash build functions. Dependency-aware, reproducible, with full build logging
- **Master build orchestrator** — Single command builds the entire system from fresh VM to bootable disk image, with phase control and resume capability
- **Template generator** — Batch-generate package templates from YAML definitions
- **Switchable desktop environments** — Pick your DE at login with fully isolated per-DE configurations
- **Wayland-native** — GNOME (first), then KDE Plasma and COSMIC
- **Tiered local AI assistant** — Hardware-detected, fully offline, no cloud dependency
- **Custom package manager** — Honest, minimal, with binary caching
- **Custom installer** — Polished installation experience with hardware profiling and AI tier detection

## AI Tiers

| Tier | Hardware | STT | LLM | TTS | Capability |
|------|----------|-----|-----|-----|------------|
| 1 | 4GB RAM, any CPU | Vosk | Qwen3-0.6B Q4 | Piper | Text + basic voice |
| 2 | 8GB RAM, 4+ cores | Whisper tiny | Qwen3-1.7B Q4 | Piper | Full voice assistant |
| 3 | 16GB+ RAM, GPU | Whisper fine-tuned | Full LLM (7B-35B) | Kokoro | Full experience |

## Build System

InterGenOS uses a multi-layer build system:

**Master orchestrator** — drives the entire build:
```bash
sudo bash scripts/build-intergenos.sh --user christopher                    # Full build
sudo bash scripts/build-intergenos.sh --user christopher --start-at core    # Resume at phase
sudo bash scripts/build-intergenos.sh --user christopher --stop-after base  # Stop after phase
```

**Package builder** (`igos-build`) — dependency resolution and build execution:
```
python -m igos-build                          # Parse templates, show build order
python -m igos-build --dry-run                # Preview build commands
python -m igos-build --build --tracked        # Build with package tracking
python -m igos-build --build --skip-built     # Skip already-built packages
```

**Template generator** — batch-create package templates:
```
python scripts/generate-templates.py tier-definition.yml                # Generate templates
python scripts/generate-templates.py tier-definition.yml --download-checksums  # With SHA256
```

Packages are defined as YAML templates:

```yaml
name: bash
version: "5.3"
release: 1
description: "The GNU Bourne Again Shell"
license: GPL-3.0-or-later
source:
  - url: https://ftp.gnu.org/gnu/bash/bash-${version}.tar.gz
    sha256: 0d5cd86965f869a26cf64f4b71be7b96...
dependencies:
  build: [bison-core]
  host: [readline, ncurses]
  runtime: [readline, ncurses]
build_style: autotools
configure_flags:
  - --without-bash-malloc
  - --with-curses
  - --enable-readline
```

## Package Count

| Tier | Packages | Purpose |
|------|----------|---------|
| Toolchain | 28 | Cross-compilation toolchain (LFS Ch. 5-7) |
| Core | 98 | Full system (LFS Ch. 8 + TLS chain, curl/wget/git, PAM, glib2, cmake) |
| Base | 20 | End-user tools and services (htop, rsync, strace, etc.) |
| Desktop | 312 | GNOME on Wayland (X11 libs, GTK, GStreamer, Mesa, GNOME Shell, etc.) |
| **Total** | **458** | |

## Project Structure

```
intergenos/
├── igos-build/          # Build system (Python)
│   ├── parser.py        # YAML template parser with validation
│   ├── graph.py         # Dependency graph + topological sort
│   ├── builder.py       # Build executor with DESTDIR staging and tracking
│   ├── log.py           # Full build logging (never truncated)
│   └── styles/          # Build styles (autotools, cmake, meson, make, custom)
├── packages/            # Package templates (458 packages)
│   ├── toolchain/       # Cross-compilation toolchain (LFS Ch. 5-7)
│   ├── core/            # Core system packages (LFS Ch. 8 + extras)
│   ├── base/            # End-user tools and services
│   └── desktop/         # GNOME desktop environment stack
├── scripts/             # Build scripts
│   ├── build-intergenos.sh      # Master orchestrator (single entry point)
│   ├── generate-templates.py    # Batch template generator
│   ├── toolchain-build.sh       # Cross-toolchain (Ch. 5)
│   ├── temp-tools-build.sh      # Temporary tools (Ch. 6)
│   ├── chroot-*.sh              # Chroot setup/enter/build/teardown
│   ├── create-image.sh          # Package chroot into bootable disk image
│   └── host-check.py            # Build host validation
├── vm/                  # VM configuration (cloud-init for automated setup)
├── build/               # Build output (not committed)
│   ├── sources/         # Cached source tarballs
│   ├── patches/         # LFS patches
│   └── logs/            # Build logs
├── docs/                # Vision and architecture documents
└── config/              # Kernel configs, build profiles
```

## Build Architecture

InterGenOS is built in a chroot on an Ubuntu build VM. The target system is never used as a build host — the chroot is self-contained with all sources and scripts copied directly onto the target filesystem (no bind mounts).

Build phases: `validate → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → base → image`

The final phase packages the chroot into a bootable qcow2 disk image.

## Status

Active development. Originally built in 2015-2016 (build_001 through build_003 — 83 packages, fully automated). Revived March 2026 with modern tooling, a custom build system, and a decade more experience.

**Current milestone:** Core system building. 458 package templates ready. GNOME desktop dependency chain fully mapped. Next: complete core build, then desktop tier.

## History

- **2015:** build_001 — First LFS attempt (Linux 3.18.2)
- **2016:** build_002, build_003 — Refined, 83 packages, fully automated builds
- **2016-2025:** Life happened. Project shelved.
- **2026:** Revival. New build system, AI integration, three desktop environments, and the conviction that a from-source distribution can be both deeply educational and genuinely accessible.

## Acknowledgments

InterGenOS is built on the foundation of [Linux From Scratch](https://www.linuxfromscratch.org/) (LFS 13.0) and [Beyond Linux From Scratch](https://www.linuxfromscratch.org/blfs/) (BLFS 13.0). The LFS project and its contributors have made from-source Linux building accessible and educational for over two decades. This project would not exist without their work.

All included packages carry their own licenses as tracked in their package templates.

## License

InterGenOS build system, scripts, and templates are licensed under the [GNU General Public License v3.0 or later](LICENSE).

Individual packages included in the distribution retain their respective upstream licenses (GPL, LGPL, MIT, BSD, etc.) as declared in each package's `package.yml` template.

## Author

InterGenJLU — [InterGen Studios](https://intergenstudios.com)
