# InterGenOS Application Roadmap — Extra Tier

**Date:** April 6, 2026
**Based on:** Flathub 2025 download stats, Snap Store data, Arch Community Survey, DistroWatch rankings, default app analysis across 6 major distributions, and owner's personal daily-use list.

---

## Guiding Principle

The Prime Directive applies to application selection: if a user expects it to be there, it should be there. A system the user can trust doesn't make them hunt for basic tools.

---

## What We Already Have

| Package | Tier | Type |
|---------|------|------|
| Firefox | desktop | Built from source (GNOME default) |
| Google Chrome | extra | Install helper |
| VS Code | extra | Install helper |
| Claude Code | extra | Install helper |
| Node.js | extra | Built from source |
| GNOME Terminal | desktop | Built from source |
| Nautilus (Files) | desktop | Built from source |
| Evince (Document Viewer) | desktop | Built from source |
| Eye of GNOME (Image Viewer) | desktop | Built from source |
| GNOME Calculator | desktop | Built from source |
| GNOME System Monitor | desktop | Built from source |
| GNOME Text Editor | desktop | Built from source |
| GNOME Disk Utility | desktop | Built from source |

---

## Phase 1 — Install Helpers (Proprietary)

Download helpers follow the Chrome/VS Code pattern: `pkm install-helper <name>` downloads from the vendor and installs. No proprietary code in our tree.

| Application | Justification | Priority |
|-------------|--------------|----------|
| **Discord** | #3 Flathub (2.1M downloads), top snap on 2 distros | High |
| **Spotify** | #1 snap on ALL distros surveyed — no other app has this unanimity | High |
| **Steam** | 5.33% Linux market share, owner uses it, dominant gaming platform | High |
| **Microsoft Edge** | Owner uses it, same .deb pattern as Chrome | Medium |
| **Brave** | Owner uses it, default in Zorin, 9% Arch survey share | Medium |

---

## Phase 2 — Core Applications (Build from Source)

These are FOSS applications that 5/6 major distros either ship by default or that every recommendation list includes. Building from source is consistent with our model.

### Must-Have (ships in 4+ distros by default)

| Application | Justification | Complexity |
|-------------|--------------|------------|
| **LibreOffice** | Default in 5/6 distros. The single most conspicuous omission. | High (large, many deps) |
| **VLC** | Universal media player, default in Mint/MX, top Flathub | Medium |
| **Thunderbird** | Dominant Linux email client, default in 3/6 distros | Medium-High |
| **GIMP** | Image editor, default in MX, universally recommended | Medium |
| **Inkscape** | Vector graphics, default in MX, owner uses it, needed for our own branding | Medium |

### Strong Additions

| Application | Justification | Complexity |
|-------------|--------------|------------|
| **Audacity** | Audio editor, owner uses it, universal recommendation | Low-Medium |
| **Transmission** | BitTorrent client, default in Mint, lightweight | Low |
| **Timeshift** | System snapshots/backup, default in Mint, AUR favorite. Prime Directive: users should be able to recover from their own mistakes. | Low-Medium |
| **Remmina** | Remote desktop client, default in Ubuntu, owner uses it | Medium |
| **Docker** | Containerization, owner uses it. Consider Podman (daemonless, rootless) as alternative. | Medium |
| **OBS Studio** | Screen recording/streaming, top Flathub, on owner's machine | Medium |

---

## Phase 3 — Extended Applications

Lower priority but valuable. Add as the extra tier matures.

| Application | Justification | Complexity |
|-------------|--------------|------------|
| **Kdenlive** | Most recommended FOSS video editor | Medium |
| **Blender** | Industry standard 3D modeling, FOSS | High |
| **Krita** | Digital art, default in MX, KDE ecosystem | Medium |
| **Handbrake** | Media converter, frequently recommended | Low-Medium |
| **GParted** | Partition editor, universal utility | Low |
| **Bitwarden** | Password manager, open source | Low (helper) |
| **Lutris** | Gaming compatibility layer, run Windows games | Medium |
| **Bottles** | Wine frontend, default in Zorin | Medium |
| **Joplin** | Note taking, AUR popular, cross-platform | Low (helper) |
| **FreeCAD** | CAD, 194% Flathub growth | High |

---

## Phase 4 — Desktop Integration (GNOME Ecosystem)

These are small GNOME apps that round out the desktop experience. Many are already dependencies of gnome-shell or gnome-control-center.

| Application | Status |
|-------------|--------|
| GNOME Calendar | Check if already in desktop tier |
| GNOME Clocks | Check if already in desktop tier |
| GNOME Weather | Not critical, add if requested |
| GNOME Maps | Not critical, add if requested |
| GNOME Contacts | Not critical, add if requested |
| File Roller (Archive Manager) | Should be in desktop tier |
| Cheese (Camera) | Nice to have |
| Baobab (Disk Usage) | Useful utility |
| Seahorse (Passwords/Keys) | Security tool |
| GNOME Screenshot | Wayland: built into shell |

---

## Implementation Order

1. **Install helpers first** — Discord, Spotify, Steam, Edge, Brave. These are fast (shell scripts, same pattern as Chrome/VS Code). Can be done in a single session.

2. **VLC + Transmission + Audacity** — smaller FOSS apps, lower complexity, quick wins.

3. **GIMP + Inkscape** — medium complexity, we need Inkscape for our own branding work.

4. **Timeshift + Remmina + Docker/Podman** — system tools that add real value.

5. **Thunderbird** — medium-high complexity, Mozilla build system.

6. **LibreOffice** — the big one. Large build, many dependencies. But it's what users expect. Save for when the build infrastructure is proven.

7. **Phase 3 apps** — as time and interest allow.

---

## Data Sources

- Flathub 2025 Year in Review (433.5M downloads)
- Snap Store popular snaps per distribution
- Arch Linux Community Survey 2024 (3,923 responses)
- DistroWatch rankings (top 10 distributions)
- Default app analysis: Ubuntu, Mint, Fedora, MX Linux, Pop!_OS, Zorin OS
- Owner's personal daily-use application list
- Full research report: `docs/research/essential_desktop_apps_research_2026-04-06.md`
