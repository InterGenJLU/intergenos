# Essential Linux Desktop Applications — Research Report

**Date:** 2026-04-06
**Purpose:** Data-driven analysis for InterGenOS extra tier application selection

---

## 1. Flathub Download Data (2025)

Source: Flathub 2025 Year in Review — 433.5 million total downloads, 3,243 apps

### Top Applications by Downloads
| Rank | App | Downloads (2025) |
|------|-----|-----------------|
| 1 | Firefox | 2.7M |
| 2 | Google Chrome | 2.4M |
| 3 | Discord | 2.1M |

### Top Games
| Rank | App | Downloads (2025) |
|------|-----|-----------------|
| 1 | Sober (Roblox launcher) | 1.3M |
| 2 | Minecraft | 260K |
| 3 | Space Cadet Pinball | 123.3K |

### Top Emulators
| Rank | App | Downloads (2025) |
|------|-----|-----------------|
| 1 | RetroArch | 1.0M |
| 2 | Dolphin Emulator | 977.8K |
| 3 | PPSSPP | 910.9K |

### Developer Tools
- Visual Studio Code: category leader
- Zed: 137% year-over-year growth

### Graphics
- FreeCAD: 194% growth ("most improved" app)

### Previously Reported Top Apps (from Flathub blog, 1M+ active users era)
Firefox, Google Chrome, Discord, VLC, Spotify, Telegram, Microsoft Edge, Steam, OBS Studio, Zoom, Thunderbird

---

## 2. Snap Store — Most Popular Snaps by Distribution

Source: Snapcraft blog — Popular snaps per distro

| Distribution | Top 5 Snaps |
|-------------|-------------|
| Arch Linux | Spotify, VS Code, Skype, Discord, Helm |
| CentOS | WeKan, LXD, Microk8s, Spotify, Postman |
| Debian | Spotify, LXD, Firefox, Nextcloud, PyCharm Community |
| Fedora | Spotify, VLC, VS Code, Spotify, Slack |
| Manjaro | Spotify, VS Code, Slack, Skype, Canonical-Livepatch |
| Ubuntu | VLC, Spotify, Skype, Discord, Chromium |

**Key finding:** Spotify appears in top 5 on ALL distributions surveyed.

---

## 3. AUR Most Voted Packages (Arch Linux)

Source: AUR website, community forums

Most popular AUR package: **Hyprland** (tiling Wayland compositor)

Frequently mentioned high-vote AUR packages:
- google-chrome, spotify, visual-studio-code-bin
- timeshift, ventoy-bin, ttf-ms-fonts
- brave-bin, tor-browser, freetube-bin
- yt-dlp, joplin-desktop, pamac-aur

---

## 4. Arch Linux Community Survey 2024 (3,923 responses)

### Web Browser Preferences
| Browser | Share |
|---------|-------|
| Firefox | 58% |
| Firefox-based (other) | 17% |
| Brave | 9% |
| Google Chrome | 5% |

### Desktop Environment
| DE | Share |
|----|-------|
| KDE Plasma | 36% |
| (Others not specified in available data) | — |

### Display Protocol
- Wayland: ~80%
- X11: ~20%

### Top Activities
1. Web browsing
2. Music playback
3. Personal productivity
4. Software development
5. Gaming
6. Work tasks

---

## 5. DistroWatch Top 10 (Page Hits Per Day, Past 12 Months)

| Rank | Distribution | HPD |
|------|-------------|-----|
| 1 | CachyOS | 4,117 |
| 2 | Linux Mint | 2,532 |
| 3 | MX Linux | 1,968 |
| 4 | Debian | 1,570 |
| 5 | Pop!_OS | 1,389 |
| 6 | EndeavourOS | 1,346 |
| 7 | Zorin | 1,152 |
| 8 | Manjaro | 1,126 |
| 9 | Fedora | 1,083 |
| 10 | Ubuntu | 1,050 |

---

## 6. Default Applications Across Top Distributions

### Ubuntu 24.04 (GNOME)
Firefox, LibreOffice (Writer/Calc/Impress/Draw/Math), Thunderbird, Rhythmbox, Shotwell, Videos (Totem), Files (Nautilus), Text Editor, Document Viewer (Evince), Archive Manager, Calculator, Calendar, Camera, Characters, Disks, Disk Usage Analyzer, Document Scanner, Image Viewer (Eye of GNOME), Remmina, Terminal, System Monitor, Fonts, Backups (Deja Dup), Startup Disk Creator

### Linux Mint (Cinnamon)
Firefox, LibreOffice, Thunderbird, VLC, Transmission, Timeshift, Celluloid, Hypnotix, Rhythmbox, Pix, Drawing, Warpinator, Calculator, Calendar, Notes

### Fedora Workstation 41 (GNOME)
Firefox, LibreOffice, Rhythmbox, Videos (Totem), GNOME Photos, Document Scanner, GNOME Boxes, Ptyxis (terminal), Files, Text Editor, Calculator, Calendar, Characters, Clocks, Connections, Contacts, Disks, Document Viewer, Font Viewer, Image Viewer, Logs, Maps, System Monitor, Weather

### MX Linux 25 (Xfce)
Firefox, Thunderbird, LibreOffice, GIMP, VLC, Inkscape, Krita, Git, Vim

### Pop!_OS 24.04 (COSMIC)
Firefox, COSMIC Files, COSMIC Text Editor, COSMIC Terminal, COSMIC Store, COSMIC Media Player, COSMIC Settings
(Notably minimal — no office suite, no email client)

### Zorin OS 18
Brave, LibreOffice, Evolution, Wine 9.0, Bottles, Logseq, Calendar, Camera, Files

---

## 7. Cross-Distribution Default App Matrix

Apps that ship by default in 4+ of the 6 distributions surveyed:

| Application | Ubuntu | Mint | Fedora | MX | Pop | Zorin | Count |
|------------|--------|------|--------|----|----|-------|-------|
| Firefox/Browser | Y | Y | Y | Y | Y | Y(Brave) | 6/6 |
| File Manager | Y | Y | Y | Y | Y | Y | 6/6 |
| Terminal | Y | Y | Y | Y | Y | Y | 6/6 |
| Text Editor | Y | Y | Y | Y | Y | — | 5/6 |
| Calculator | Y | Y | Y | — | — | — | 3/6 |
| Calendar | Y | Y | Y | — | — | Y | 4/6 |
| LibreOffice | Y | Y | Y | Y | — | Y | 5/6 |
| Document Viewer | Y | — | Y | — | — | — | 2/6 |
| Image Viewer | Y | Y | Y | — | — | — | 3/6 |
| System Monitor | Y | — | Y | — | — | — | 2/6 |
| Media Player | Y | Y(VLC) | Y | Y(VLC) | Y | — | 5/6 |
| Email Client | Y(Tbird) | Y(Tbird) | — | Y(Tbird) | — | Y(Evo) | 4/6 |
| Music Player | Y(Rhythmbox) | Y(Rhythmbox) | Y(Rhythmbox) | — | — | — | 3/6 |
| Disk Utility | Y | — | Y | — | — | — | 2/6 |
| Archive Manager | Y | — | — | — | — | — | 1/6 |
| Screenshot Tool | Y | Y | Y | — | — | — | 3/6 |
| Backup Tool | Y | Y(Timeshift) | — | — | — | — | 2/6 |

---

## 8. Most Recommended/Essential Apps (Aggregated from Multiple Sources)

Based on It's FOSS, GeeksMint, Neowin, GeeksforGeeks, and community forums.

### Tier 1 — Universally Recommended (appears in 5+ sources)
| Category | Application | Notes |
|----------|------------|-------|
| Web Browser | Firefox | #1 Flathub, 58% Arch survey, default in 5/6 distros |
| Office Suite | LibreOffice | Default in 5/6 distros, universal recommendation |
| Media Player | VLC | Default in Mint/MX, top snap, top Flathub |
| Image Editor | GIMP | Default in MX, recommended everywhere |
| Email Client | Thunderbird | Default in 3/6 distros, top Flathub |
| Code Editor | VS Code | Top Flathub dev tool, top snap on 3 distros |
| Communication | Discord | #3 Flathub downloads, top snap on 2 distros |
| Music Streaming | Spotify | #1 snap on ALL distros surveyed |
| Video Editor | Kdenlive | Most recommended FOSS video editor |
| Screen Recording | OBS Studio | Top Flathub, universal recommendation |

### Tier 2 — Frequently Recommended (appears in 3-4 sources)
| Category | Application | Notes |
|----------|------------|-------|
| Vector Graphics | Inkscape | Default in MX, widely recommended |
| Digital Art | Krita | Default in MX, KDE ecosystem |
| Torrent Client | Transmission | Default in Mint, lightweight |
| Screenshot Tool | Flameshot/Ksnip | Community favorites |
| System Backup | Timeshift | Default in Mint, AUR favorite |
| Virtualization | VirtualBox/Boxes | Recommended for virtualization |
| Password Manager | Bitwarden | Open-source, recommended |
| Audio Editor | Audacity | Universal audio editor |
| Remote Desktop | Remmina | Default in Ubuntu |
| Video Converter | Handbrake | Most recommended converter |
| File Transfer | LocalSend | Rising star, It's FOSS 2024 pick |
| Note Taking | Joplin | AUR popular, cross-platform |
| Gaming | Steam | Dominant gaming platform |
| Gaming Compat | Lutris/Bottles | Run Windows games |
| Terminal | (varies by DE) | Every distro includes one |

### Tier 3 — Niche but Popular (appears in 1-2 sources)
| Category | Application | Notes |
|----------|------------|-------|
| 3D Modeling | Blender | Industry standard FOSS |
| CAD | FreeCAD | 194% Flathub growth |
| Finance | GnuCash | Most recommended FOSS accounting |
| PDF Editor | LibreOffice Draw | Dual-purpose |
| Music Player | Rhythmbox | Default in 3 distros |
| Disk Management | GParted | Universal partition tool |
| System Info | htop/btop | CLI system monitor |
| VPN | ProtonVPN | Privacy-focused |
| Font Management | Font Manager | Recommended utility |
| Hex Editor | GHex | Niche but valued |

---

## 9. Steam on Linux — Gaming Data (March 2026)

- Linux at 5.33% Steam marketshare (record high)
- SteamOS: 27.18% of Linux Steam users (declining share as desktop Linux grows)
- Top Linux distros on Steam: SteamOS, Bazzite, Ubuntu, Mint, CachyOS, Debian
- Growth driven by Proton compatibility layer and Steam Deck

---

## 10. Key Findings for InterGenOS Extra Tier

### Universal (every distro should include):
1. **Web browser** — Firefox (58% Arch preference, #1 Flathub, default everywhere)
2. **Office suite** — LibreOffice (default in 5/6 distros)
3. **Media player** — VLC (universal, cross-platform)
4. **Email client** — Thunderbird (dominant Linux email client)
5. **File manager** — (part of desktop tier, not extra)
6. **Terminal emulator** — (part of desktop tier, not extra)
7. **Text editor** — (part of desktop tier, not extra)

### Strong candidates for extra tier:
8. **GIMP** — Image editor, default in MX
9. **Inkscape** — Vector graphics, default in MX
10. **Transmission** — BitTorrent, lightweight, default in Mint
11. **Timeshift** — System snapshots, default in Mint, AUR popular
12. **GParted** — Disk partitioning (likely in base/core already)
13. **Flameshot or GNOME Screenshot** — Screen capture
14. **Document Viewer (Evince)** — PDF viewing (GNOME ecosystem)
15. **Archive Manager (File Roller)** — (GNOME ecosystem)
16. **Calculator** — (GNOME ecosystem)
17. **System Monitor** — (GNOME ecosystem)

### Download helpers (proprietary, not bundled):
- VS Code / Code-OSS (already planned)
- Discord
- Spotify
- Steam
- Chrome/Chromium

---

## Sources

- [Flathub 2025 Year in Review](https://flathub.org/en/year-in-review/2025)
- [Flathub Sees Over 435 Million Downloads in 2025 — Linuxiac](https://linuxiac.com/flathub-sees-over-435-million-downloads-in-2025/)
- [XDA: Flathub breaks record for 2025](https://www.xda-developers.com/flathub-breaks-its-previous-record-of-yearly-app-downloads-for-2025/)
- [Flathub Statistics](https://flathub.org/en/statistics)
- [Flathub: Over One Million Active Users](https://docs.flathub.org/blog/over-one-million-active-users-and-growing)
- [Popular Snaps Per Distro — Snapcraft](https://snapcraft.io/blog/popular-snaps-per-distro)
- [AUR Packages](https://aur.archlinux.org/packages)
- [Arch Linux Community Survey Results — Linuxiac](https://linuxiac.com/arch-linux-community-survey-results/)
- [DistroWatch Popularity Rankings](https://distrowatch.com/dwres.php?resource=popularity)
- [Ubuntu 24.04 Default Apps Guide — UbuntuBuzz](https://www.ubuntubuzz.com/2024/06/a-complete-guide-to-ubuntu-2404-default-apps-and-their-purposes.html)
- [Linux Mint — Wikipedia](https://en.wikipedia.org/wiki/Linux_Mint)
- [Fedora Workstation Default App Guidelines](https://fedoraproject.org/wiki/Workstation/Default_App_Guidelines)
- [DistroWatch: MX Linux](https://distrowatch.com/table.php?distribution=MX)
- [Pop!_OS Default Apps — System76 Support](https://support.system76.com/articles/default-apps/)
- [Zorin OS — Wikipedia](https://en.wikipedia.org/wiki/Zorin_OS)
- [It's FOSS: Essential Linux Applications](https://itsfoss.com/essential-linux-applications/)
- [GeeksMint: 96 Must-Have Linux Applications](https://www.geeksmint.com/most-used-linux-applications/)
- [GeeksforGeeks: 10 Best Linux Apps 2025](https://www.geeksforgeeks.org/linux-unix/10-best-linux-apps-and-software-in-2023/)
- [Debian Popularity Contest](https://popcon.debian.org/main/by_inst)
- [Stack Overflow Developer Survey 2025](https://survey.stackoverflow.co/2025)
- [CachyOS Topped DistroWatch Rankings — Linuxiac](https://linuxiac.com/cachyos-topped-distrowatch-rankings/)
- [DistroWatch Ranking](https://distrowatch.com/dwres.php?resource=ranking)
- [Flathub Chart — GitHub](https://github.com/paulcarroty/flathub-chart)
