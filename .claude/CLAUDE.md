# InterGenOS — AI-Integrated Linux Distribution

**For:** AI Assistants (Claude, etc.) | **Created:** March 30, 2026

## What Is This Project?

**InterGenOS** — A Linux From Scratch distribution with switchable desktop environments and a tiered local AI assistant. Built from source, every component chosen deliberately.

**Owner:** Christopher | **Original:** ~2015 (build_001 through build_003 on GitHub under github.com/InterGenOS)
**Revival:** March 2026 | **Development Drive:** `/mnt/intergenos`

## The PRIME DIRECTIVE

*InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust. Any complexity that doesn't serve the user — or that hides how the system works — is not welcome, regardless of how conventional it may be.*

## Guiding Principles

1. **Best practices everywhere** — proper version control, documented decisions, reproducible builds, tested at every stage
2. **Fun first** — no deadlines, no grind. If it stops being fun, stop and reassess
3. **Research before building** — data-driven decisions, read documentation, don't cargo-cult
4. **PRIME DIRECTIVE front and center** at all times

## Key Features (Vision)

- **Linux From Scratch** — built entirely from source, total control
- **Switchable Desktop Environments** — pick your DE at login, isolated configs, first-class multi-DE support
- **Tiered Local AI Assistant** — hardware-detected, fully local, no cloud dependency
  - Tier 1 (4GB RAM): Qwen3-0.6B + Vosk STT + Piper TTS (text + basic voice)
  - Tier 2 (8GB RAM): Qwen3-1.7B + Whisper tiny + Piper TTS (full voice)
  - Tier 3 (16GB+ RAM, GPU): Full JARVIS-class stack
- **The AI helps you learn Linux** — not hide it from you

## Repository Structure

```
/mnt/intergenos/
├── .claude/           # Claude Code project config
├── docs/              # Vision, architecture, decisions
├── research/          # Research findings (not committed to public)
├── build/             # Build scripts and toolchain
├── scripts/           # Utility scripts
└── config/            # Kernel configs, system configs
```

## Related Projects

- **JARVIS** (`/home/christopher/jarvis`) — the AI assistant that will be adapted for InterGenOS
- **VOQR** (`/home/christopher/voqr`) — VS Code voice extension (separate product)
- **Original InterGenOS** (`github.com/InterGenOS/build_003`) — the 2015-2016 LFS builds

## Critical Rules

- **NO rushing.** This is a passion project, not a product deadline.
- **Research first.** Every major decision gets a research phase.
- **Document decisions.** When we choose something, we write down WHY.
- **Test everything.** The build system must be reproducible.
- **Fun is mandatory.** If it's not fun, we're doing it wrong.
