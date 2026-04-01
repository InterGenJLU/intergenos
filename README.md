# InterGenOS

**A Linux distribution built entirely from source with switchable desktop environments, a custom build system, and a tiered local AI assistant.**

InterGenOS puts the user in control of their own machine. Every package is compiled from source with deliberate choices. Every design decision serves one purpose: giving people a system they understand, can modify, and can trust.

The built-in AI assistant — **InterGen** — helps you learn Linux, not hide it from you.

## Features (In Development)

- **Built from source** — Based on LFS 13.0, every component chosen deliberately
- **Custom build system** (`igos-build`) — Python orchestrator with YAML package templates and bash build functions. Dependency-aware, reproducible, with full build logging
- **Switchable desktop environments** — Pick your DE at login with fully isolated per-DE configurations. No `~/.config` contamination between environments
- **Tiered local AI assistant** — Hardware-detected, fully offline, no cloud dependency
- **Wayland-native** — GNOME, KDE Plasma, COSMIC
- **Custom package manager** — Honest, minimal, with binary caching
- **Custom installer** — Polished installation experience with hardware profiling and AI tier detection

## AI Tiers

| Tier | Hardware | STT | LLM | TTS | Capability |
|------|----------|-----|-----|-----|------------|
| 1 | 4GB RAM, any CPU | Vosk | Qwen3-0.6B Q4 | Piper | Text + basic voice |
| 2 | 8GB RAM, 4+ cores | Whisper tiny | Qwen3-1.7B Q4 | Piper | Full voice assistant |
| 3 | 16GB+ RAM, GPU | Whisper fine-tuned | Full LLM (7B-35B) | Kokoro | Full experience |

## Build System

InterGenOS uses `igos-build`, a custom build system designed for this project:

```
python -m igos-build                  # Parse templates, show build order
python -m igos-build --dry-run        # Preview build commands
python -m igos-build --build          # Execute the build
python -m igos-build --only bash      # Build a single package
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
  build: [bison]
  host: [readline, ncurses]
  runtime: [readline, ncurses]
build_style: autotools
configure_flags:
  - --without-bash-malloc
  - --with-curses
  - --enable-readline
```

Currently tracking **106 packages** across the full LFS 13.0 system (toolchain + core).

## Project Structure

```
intergenos/
├── igos-build/          # Build system (Python)
│   ├── parser.py        # YAML template parser
│   ├── graph.py         # Dependency graph + topological sort
│   ├── builder.py       # Build executor with logging
│   └── styles/          # Build styles (autotools, cmake, meson, make, custom)
├── packages/            # Package templates
│   ├── toolchain/       # Cross-compilation toolchain (LFS Ch. 5-7)
│   └── core/            # Core system packages (LFS Ch. 8)
├── build/               # Build output (not committed)
│   ├── sources/         # Cached source tarballs
│   ├── patches/         # LFS patches
│   └── logs/            # Per-package build logs
├── docs/                # Vision and architecture documents
├── config/              # Kernel configs, build profiles
└── scripts/             # Utility scripts
```

## Status

Active development. Originally built in 2015-2016 (build_001 through build_003 — 83 packages, fully automated). Revived March 2026 with modern tooling, a custom build system, and a decade more experience.

**Current milestone:** Build system complete, 106 package templates validated, KVM build environment operational. Next: first toolchain build.

## History

- **2015:** build_001 — First LFS attempt (Linux 3.18.2)
- **2016:** build_002, build_003 — Refined, 83 packages, fully automated builds
- **2016-2025:** Life happened. Project shelved.
- **2026:** Revival. New build system, AI integration, three desktop environments, and the conviction that a from-source distribution can be both deeply educational and genuinely accessible.

## Acknowledgments

InterGenOS is built on the foundation of [Linux From Scratch](https://www.linuxfromscratch.org/) (LFS 13.0). The LFS project and its contributors have made from-source Linux building accessible and educational for over two decades. This project would not exist without their work.

All packages include their respective licenses as tracked in their package templates.

## License

TBD

## Author

InterGenJLU — [InterGen Studios](https://intergenstudios.com)
