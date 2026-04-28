# InterGenOS — Approved Application List

**Date:** 2026-04-08
**Status:** Approved by owner
**Based on:** Flathub 2025 data, Snap Store data, Arch Community Survey 2024, DistroWatch top 10, default app analysis across 6 major distributions, owner's daily-use list

---

## Guiding Decisions

1. **No Snaps or Flatpaks.** InterGenOS builds from source. We don't have the version mismatch problem they solve. They add bloat, duplicate libraries, and hide how the system works — violates Prime Directive.

2. **Proprietary apps via install helpers.** `pkm install-helper <name>` downloads vendor binaries. No proprietary code in our tree.

3. **Ship everything a user needs on day one.** Mint/Pop!_OS model, not Arch model. The Prime Directive says the system should work without hunting for basic tools.

4. **AI tier is the differentiator.** No other distro ships local AI. This is why someone chooses InterGenOS over Mint.

---

## Must-Have Applications (build from source)

Data: default in 4+ distros OR #1 in category across all sources

| # | Application | Category | Evidence |
|---|-------------|----------|----------|
| 1 | LibreOffice | Office suite | Default in 5/6 distros |
| 2 | VLC | Media player | Default in 5/6 distros, top Flathub + Snap |
| 3 | Thunderbird | Email client | Default in 4/6 distros |
| 4 | GIMP | Image editor | Default in MX, every recommendation list |
| 5 | Inkscape | Vector graphics | Default in MX, needed for our own branding |

## Strong Applications (build from source)

Data: 3-4 independent sources agree, or fills obvious gap

| # | Application | Category | Evidence |
|---|-------------|----------|----------|
| 6 | Transmission | Torrent client | Default in Mint, lightweight |
| 7 | Kdenlive | Video editor | Most recommended FOSS video editor |
| 8 | OBS Studio | Screen recording | Top Flathub |
| 9 | Audacity | Audio editor | Universal recommendation |
| 10 | Rhythmbox | Music player | Default in 3 distros |

## Install Helpers (proprietary — scripts only, no code in tree)

| # | Application | Category | Evidence |
|---|-------------|----------|----------|
| 11 | Discord | Communication | #3 Flathub (2.1M downloads) |
| 12 | Spotify | Music streaming | #1 snap on ALL distros surveyed |
| 13 | Steam | Gaming | 5.33% Linux market share, record high |
| 14 | VS Code | Code editor | Already planned |
| 15 | Claude Code | AI assistant | Already planned |
| 16 | Chrome | Browser | Already planned |
| 17 | Edge | Browser | Owner uses it |
| 18 | Brave | Browser | Owner uses it, default in Zorin |

## System Tools (build from source)

| # | Application | Category | Evidence |
|---|-------------|----------|----------|
| 19 | Timeshift | System snapshots | Default in Mint, Prime Directive: recover from mistakes |
| 20 | GParted | Partition editor | Universal utility |
| 21 | htop | System monitor (CLI) | Universal utility |
| 22 | Remmina | Remote desktop | Default in Ubuntu |
| 23 | Handbrake | Media converter | Most recommended converter |

## AI Tier (our differentiator — build from source)

| # | Application | Category | Purpose |
|---|-------------|----------|---------|
| 24 | llama.cpp | LLM inference | Local AI chat |
| 25 | whisper.cpp | Speech-to-text | Voice input |
| 26 | espeak-ng | Text-to-speech | Voice output (fallback) |
| 27 | piper-tts | Text-to-speech | Voice output (natural) |
| 28 | intergen app | AI assistant | InterGen assistant + Sentinel module |

## GNOME Core Apps (build from source — non-negotiable)

These ARE the GNOME desktop experience. Without them the app drawer is empty.

| # | Application | Package | What it does |
|---|-------------|---------|-------------|
| 29 | Calculator | gnome-calculator | Calculator |
| 30 | Text Editor | gnome-text-editor | Simple text editor |
| 31 | Document Viewer | evince | PDF/document viewer |
| 32 | Image Viewer | loupe | View images |
| 33 | Archive Manager | file-roller | Zip/tar/etc |
| 34 | System Monitor | gnome-system-monitor | Task manager |
| 35 | Disk Utility | gnome-disk-utility | Disk/partition manager |
| 36 | Disk Usage | baobab | Visual disk usage |
| 37 | Videos | totem | Video player |
| 38 | Passwords & Keys | seahorse | GPG/SSH key manager |
| 39 | Screenshot | gnome-screenshot | Screen capture |
| 40 | Calendar | gnome-calendar | Calendar (shows in clock dropdown) |
| 41 | Clocks | gnome-clocks | World clocks, timers, alarms |
| 42 | Weather | gnome-weather | Weather (shows in clock dropdown) |
| 43 | Contacts | gnome-contacts | Address book |
| 44 | Font Viewer | gnome-font-viewer | Preview fonts |
| 45 | Characters | gnome-characters | Unicode/emoji picker |
| 46 | Logs | gnome-logs | System log viewer |
| 47 | Music | gnome-music | Music player |
| 48 | Connections | gnome-connections | Remote desktop viewer |
| 49 | Snapshot | gnome-snapshot | Camera/webcam |
| 50 | Maps | gnome-maps | Maps |

## Phase 3 / Later (approved for future, not initial release)

| # | Application | Category | Notes |
|---|-------------|----------|-------|
| 51 | Blender | 3D modeling | High complexity |
| 52 | FreeCAD | CAD | 194% Flathub growth |
| 53 | Krita | Digital art | KDE ecosystem |
| 54 | Docker/Podman | Containerization | Owner uses it |
| 55 | GnuCash | Finance | Most recommended FOSS accounting |
| 56 | Lutris/Bottles | Gaming compat | Run Windows games |

---

## Summary

| Category | Count | Method |
|----------|-------|--------|
| Must-have apps | 5 | Build from source |
| Strong apps | 5 | Build from source |
| Install helpers | 8 | Download scripts |
| System tools | 5 | Build from source |
| AI tier | 5 | Build from source |
| GNOME core apps | 22 | Build from source |
| Phase 3 / later | 6 | Future |
| **Total** | **56** | |

42 packages built from source + 8 install helper scripts + 6 deferred = 56 total

---

## Database Enhancement Needed

The BLFS package database needs updates to track these decisions:

1. **igos_status.inclusion** — 'ship', 'helper', 'later', 'rejected'
2. **igos_status.category** — 'gnome-app', 'must-have', 'system-tool', 'ai', etc.
3. **New table: igos_packages** — for non-BLFS packages (AI tier, proprietary helpers) that have no BLFS entry

---

## Decided NOT to include

- **gnome-software** — we have pkm, not Flatpak/Snap
- **gnome-photos** — deprecated in favor of Loupe
- **gnome-tour** — we have our own boot animation
- **Snap/Flatpak runtime** — we build from source, don't need containerized app delivery
