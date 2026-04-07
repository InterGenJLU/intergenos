# Installer UX, Design, and First-Boot Experience

**Date:** March 31, 2026
**Context:** Researching best practices for InterGenOS installer user experience

---

## Best-in-Class Installer UX (2025-2026)

### Top Performers

- **elementary OS** — Gold standard for visual polish and UX coherence. macOS-like principles.
- **Pop!_OS** — Gaming/developer-focused, excels at dual-boot simplicity.
- **Ubuntu (Subiquity/Flutter)** — Modern, responsive UI. Dark/light theme. Improved accessibility.
- **Fedora (Anaconda WebUI)** — New linear step-by-step flow. PatternFly + Cockpit.
- **Zorin OS** — Windows migration focus, professional appearance.

### Common User Complaints

1. Partitioning complexity intimidates beginners
2. Hardware detection gaps (fingerprint, printers, Wi-Fi)
3. Technical jargon — confusing for newcomers
4. Accessibility failures — most installers don't support screen readers
5. Too many options presented at once
6. Unclear progress indication

### Cross-Platform Patterns to Adopt

- Linear, guided flows (not hub-and-spoke)
- Sensible defaults with override options ("Next, Next, Finish" for 80%)
- Rich context tooltips inline
- Dark/light theme detection
- Clear progress indication with time estimates
- Rollback/recovery options to reduce fear

---

## Modern UI Technologies

### Recommended

- **Tauri (Rust + Web):** Under 10MB, 0.5s startup, 30-40MB RAM. 35% YoY adoption growth.
- **Flutter:** Ubuntu's choice. Modern, responsive, touch-aware. Multi-platform.
- **Qt6/QML:** Calamares's choice. Mature, GPU-accelerated, comprehensive.

### Not Recommended

- **Electron:** Bloated (100+ MB, 200-300MB RAM). Overkill for installer.
- **GTK4 alone:** Limited for complex installer UIs. Fine for simple frontends.

### TUI Installers

- Appropriate for: remote/headless, SSH, serial consoles, accessibility
- Libraries: ncurses, newt/libnewt, urwid (Python)
- Can complement graphical installer as alternative mode

---

## Innovative Concepts

### Live OS as Installer (Fedora model)

- Users test the OS before committing
- Familiar desktop during install
- No surprises post-install

### Hardware Detection → AI Tier

Suggested installer flow:
1. Auto-detect CPU, GPU, RAM, storage
2. Display hardware profile summary
3. Recommend AI tier with explanation
4. Allow override to higher/lower tier
5. Show performance estimates per tier

### First-Boot Experience

Best practices (KDE KISS Wizard, GNOME InitialSetup, systemd-firstboot):
- Theme selection (dark/light)
- Network configuration
- Privacy settings
- User account creation
- AI assistant introduction and onboarding
- DE customization

### AI Assistant Introduction

Recommended: During first-boot setup wizard
- Clear explanation of capabilities
- Show hardware tier and what it enables
- Opt-in by default
- "Based on your hardware, here's what InterGen can do"
- Quick demo/test of AI capabilities

---

## Multi-DE Installer Design

### Approaches

1. **Multiple ISOs** — one per DE (Linux Mint model). Simpler but requires pre-choice.
2. **DE selection in installer** — one ISO, choose during install (OpenSUSE model). Larger ISO.
3. **Base install + post-install selection** — minimal first, add DEs later.

### Recommended for InterGenOS

- Single ISO with DE selection during install
- Show screenshots, key features, and resource requirements per DE
- "Recommended for you" based on hardware
- Default to single DE install (avoid config conflicts)
- Make adding DEs post-install trivial

---

## Accessibility Requirements

- WCAG 2.1 AA compliance during install
- Test with Orca and Odilia screen readers
- Keyboard-only navigation
- High contrast mode available during install
- Alt text for all graphical elements

---

## ISO / Live Environment Creation

### Tools

- **mkarchiso** — Arch-based, profile-driven
- **live-build** — Debian/Ubuntu, highly configurable
- **Penguins Eggs** — Universal (Debian, Arch, Fedora, Alpine), overlayfs-based, zstd compression
- **Squashfs + Overlayfs** — Standard for live media

### Flow

Build rootfs → compress to squashfs → overlay config → create initramfs → add kernel + bootloader → mkisofs → test

---

## Sources

- Fedora Anaconda WebUI: https://fedoramagazine.org/anaconda-installer-redesign/
- Ubuntu Flutter installer: https://ubuntu.com/blog/how-we-designed-the-new-ubuntu-desktop-installer
- KDE KISS Wizard: https://www.webpronews.com/kde-plasma-6-5-unveils-kiss-wizard-for-easy-linux-onboarding/
- Calamares: https://calamares.io/
- elementary installer: https://blog.elementary.io/meet-the-upcoming-installer/
- Penguins Eggs: https://github.com/pieroproietti/penguins-eggs
