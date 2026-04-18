# InterGenOS — AI-Integrated Linux Distribution

**For:** AI Assistants (Claude, etc.) | **Created:** March 30, 2026 | **Updated:** April 18, 2026

## THE HOLY GRAIL — Security-Only Alignment

**READ FIRST.** Anthropic's Claude Mythos Preview finds and exploits vulnerabilities at superhuman scale — 181 working exploits where Opus 4.6 managed 2. Found thousands of high-severity vulns in every major OS and browser. These capabilities WILL proliferate. We build assuming adversaries have superhuman vulnerability discovery. Security is not first — it is ONLY. No trade-offs for convenience. Secure Boot mandatory. Every package decision is a security decision. See full document: `/home/christopher/.claude/projects/-mnt-intergenos/memory/holy_grail_security_alignment.md`

## What Is This Project?

**InterGenOS** — A Linux distribution built entirely from source with switchable desktop environments and a tiered local AI assistant. Every component chosen deliberately.

**Owner:** InterGenJLU | **License:** GPL-3.0-or-later
**Original:** ~2015 (build_001 through build_003 on GitHub under github.com/InterGenOS)
**Revival:** March 2026 | **Development Drive:** `/mnt/intergenos`

## The PRIME DIRECTIVE

*InterGenOS exists to put the user in control of their own machine. Every design decision, every default, every included component must serve this purpose: giving people a system they understand, can modify, and can trust. Any complexity that doesn't serve the user — or that hides how the system works — is not welcome, regardless of how conventional it may be.*

**The PRIME DIRECTIVE and the HOLY GRAIL are complementary: a machine the user cannot trust is a machine they do not control.**

**Apply the PRIME DIRECTIVE to every decision.** It hasn't steered us wrong yet.

## Guiding Principles

1. **PRIME DIRECTIVE front and center** — run every proposal against it
2. **Best practices everywhere** — proper version control, documented decisions, reproducible builds
3. **Fun first** — no deadlines, no grind. If it stops being fun, stop and reassess
4. **Research before building** — check the LFS/BLFS book, check build_003, don't cargo-cult
5. **Stop, examine, identify, correct, proceed** — don't knee-jerk fix problems

## Build Architecture

**All packages are built in a chroot on the Ubuntu build VM. Never on a live target.**

The build pipeline:
```
Build VM (Ubuntu 24.04) → chroot at /mnt/igos → build everything → bootable disk image → target VM
```

**Master orchestrator:** `scripts/build-intergenos.sh`
- Single entry point for the entire build
- Phases: validate → setup → toolchain → chroot-prep → chroot-tools → core → config → core-extra → base → desktop → extra → image
- Controls: `--user`, `--start-at`, `--stop-after`, `.build-stop` file, Ctrl+C

**Self-contained chroot** (build_003 approach): sources and scripts are copied onto the target filesystem. No bind mounts for content. The chroot is transparent and inspectable.

## Package Tiers

| Tier | Purpose |
|------|---------|
| toolchain | Cross-compilation (LFS Ch. 5-7) |
| core | Full system (LFS Ch. 8 + TLS chain, PAM, glib2, curl/wget/git, cmake) |
| base | End-user CLI tools (htop, rsync, strace, screen, etc.) |
| desktop | GNOME on Wayland (X11 libs, GTK, Mesa, GStreamer, GNOME Shell) |
| extra | User-facing applications (Code-OSS, Node.js, etc.) |

The `extra` tier follows the Arch Linux convention — optional packages that extend the
system beyond the base desktop. Proprietary software (VS Code, Claude Code) is handled
via download helpers, not bundled — consistent with how Debian (non-free), Void (nonfree),
and Arch (AUR) separate free from proprietary.

## Repository Structure

```
/mnt/intergenos/
├── igos-build/          # Python build system (parser, graph, builder, styles)
├── packages/            # 458 package templates (YAML + build.sh)
│   ├── toolchain/       # LFS Ch. 5-7
│   ├── core/            # LFS Ch. 8 + extras
│   ├── base/            # End-user CLI tools
│   ├── desktop/         # GNOME desktop stack
│   └── extra/           # User-facing applications
├── scripts/             # Build orchestrator, chroot scripts, tools
│   ├── build-intergenos.sh      # Master orchestrator
│   ├── generate-templates.py    # Batch template generator
│   ├── host-check.py            # Build host validation
│   └── chroot-*.sh              # Chroot management
├── pkm/                 # pkm package manager (install, remove, query, verify)
├── installer/           # Forge installer (TUI + backend, uses pkm engine)
├── vm/                  # Cloud-init configs for automated VM setup
├── build/               # Sources, patches, logs (not committed)
├── docs/                # Vision document
└── config/              # Kernel configs, gsettings overrides
```

## Build VM

- **Name:** `igos-build` | **OS:** Ubuntu 24.04.2
- **Specs:** 16 vCPU, 32GB RAM, 300GB disk
- **virtiofs:** `/mnt/intergenos` shared from host
- **Snapshot:** `fresh-ubuntu` (clean restore point)
- **SSH:** `ssh christopher@<vm-ip>`

## Related Projects

- **JARVIS** (`/home/christopher/jarvis`) — AI assistant to be adapted for InterGenOS
- **Original InterGenOS** (`github.com/InterGenOS/build_003`) — 2015-2016 LFS builds (study for approach, not code)

## Critical Rules

- **NO rushing.** This is a passion project, not a product deadline.
- **Research first.** Check LFS book, BLFS book, and build_003 before implementing.
- **PROPOSE → WAIT → PERMISSION → CHANGE.** Every time. No exceptions.
- **Automate everything.** No manual GUI clicks, no fat-finger opportunities.
- **Document decisions.** When we choose something, we write down WHY.
- **No hardcoded values.** Derive counts dynamically, parameterize usernames.
- **Fun is mandatory.** If it's not fun, we're doing it wrong.
