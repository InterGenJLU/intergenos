# Installer Framework Survey

**Date:** March 31, 2026
**Context:** Researching installer options for InterGenOS

---

## Frameworks Evaluated

### Calamares (Top Community Choice)

- **Language:** C++17 core, Qt5/6 UI, Python modules via pybind11
- **Version:** 3.4.2 (March 2026), migrated to Codeberg mid-2025
- **License:** LGPL (permissive)
- **Used by:** 30+ distros (Manjaro, Garuda, KDE Neon, EndeavourOS, CachyOS, Lubuntu, etc.)
- **Module system:** Job modules (C++/Python, background tasks) + View modules (UI pages)
- **Configuration:** YAML-based, branding system for distro customization without core changes
- **QML support** for advanced UI customization, QSS/CSS for styling
- **Recent features (2025-2026):** Bootloader selection (GRUB, systemd-boot, Limine), NVMe/MMC recognition, 4K partition alignment, Plymouth improvements
- **Strengths:** Fastest time-to-market, excellent module system, proven at scale, permissive license
- **Weaknesses:** Qt dependency, may fight against highly custom workflows
- **Repo:** https://codeberg.org/Calamares/calamares

### Anaconda (Fedora/Red Hat)

- **Language:** Python, new WebUI in React + Cockpit
- **License:** GPL
- **Used by:** Fedora, RHEL, CentOS, Rocky, AlmaLinux, Qubes OS
- **Architecture:** DBus-based modules, add-on API
- **WebUI:** Default as of Fedora 43 (Oct 2025) — linear flow, PatternFly design
- **Strengths:** Modern web UI, comprehensive hardware detection, enterprise-proven
- **Weaknesses:** Heavily tied to RPM ecosystem, high complexity to adapt for non-RPM distros
- **Repo:** https://github.com/rhinstaller/anaconda

### Subiquity + Curtin (Ubuntu)

- **Language:** Python (server), Flutter (desktop UI)
- **License:** LGPL
- **Curtin:** Low-level installation engine — handles partitioning, filesystem creation, target population
- **Architecture:** Frontend (Subiquity) + Backend (Curtin), YAML autoinstall format
- **Strengths:** Modern Flutter UI, clean architecture separation, curthooks for post-install customization
- **Weaknesses:** More moving parts, somewhat opinionated about architecture
- **Repos:** https://github.com/canonical/subiquity, https://curtin.readthedocs.io/

### distinst (Pop!_OS / System76)

- **Language:** Rust (backend), Vala/GTK (frontend)
- **License:** GPL-3.0
- **Architecture:** Backend library (Rust) + Frontend (Vala/GTK), fully separated
- **Why custom:** System76 wanted to escape Ubiquity's complexity
- **Key insight:** Backend makes NO assumptions about OS, DE, packaging, or UI toolkit
- **Features:** C, Rust, and Vala API bindings, EFI/BIOS bootloader selection, squashfs extraction + chroot config
- **Reused by:** elementary OS, Vanilla OS (forked)
- **Strengths:** Clean architecture, toolkit-agnostic backend, CSS theming for branding
- **Repo:** https://github.com/pop-os/distinst (backend), https://github.com/pop-os/installer (frontend)

### Albius (Vanilla OS)

- **Language:** Python
- **Architecture:** Recipe-based (YAML), CLI + GUI, SquashFS + OCI installation support
- **Why custom:** Needed immutable/atomic system support (ABRoot), OCI container installation path
- **Strengths:** Distro-agnostic, headless-capable, modern immutable OS support
- **Repo:** https://github.com/Vanilla-OS/Albius

### COSMIC Installer (System76, in development)

- **Language:** Rust + Iced (custom GUI toolkit)
- **Status:** In development alongside COSMIC DE (released Dec 2025)
- **Relevance:** InterGenOS plans COSMIC support — directly relevant
- **Strengths:** Full Rust stack, modern, aligns with COSMIC ecosystem
- **Repo:** https://github.com/pop-os/cosmic-epoch

### archinstall

- **Language:** Pure Python
- **License:** GPL
- **Architecture:** Library + CLI tool, JSON configuration
- **Strengths:** Doubles as Python library, fully scriptable, good inspiration
- **Weaknesses:** Assumes pacman, requires major modification for other PMs
- **Repo:** https://github.com/archlinux/archinstall

### Debian Installer (d-i)

- **Language:** Shell scripts + C
- **Architecture:** Modular udeb (micro-Debian package) system, Preseed configuration
- **Strengths:** Extremely mature, well-tested, highly modular
- **Weaknesses:** Steep learning curve, dated UI, requires Debian packaging knowledge

### EndeavourOS (Calamares fork)

- **Approach:** Calamares + custom modules + pre/post-install scripts
- **Key insight:** Calamares customization IS viable — they didn't need to fork the core
- **Effort:** Moderate — mostly configuration + small custom modules
- **Repo:** https://github.com/endeavouros-team/EndeavourOS-ISO

---

## InterGenOS-Specific Requirements

No existing installer handles all of these:

1. AI tier detection — hardware profiling → automatic tier recommendation
2. DE selection as first-class feature — previews, resource estimates, isolated configs
3. Educational transparency — every step explains what and why (PRIME DIRECTIVE)
4. rEFInd + GRUB + Plymouth — full boot chain branding
5. Custom package manager integration (igos-pkg, not pacman/apt/dnf)
6. Future: aarch64 support (Pi and ARM, x86-64 first)

---

## Recommended Approach

**Phased — don't commit yet:**

Phase 1 (Now): No installer. Build system is the installer. Focus on getting a bootable system.

Phase 2 (After bootable system): Research spike — try Calamares with custom modules AND prototype a minimal custom installer. See which fits the project's identity.

Phase 3 (Before first public release): Commit based on Phase 2 findings. Either heavily customized Calamares, or custom installer with distinst-style backend/frontend split.

---

## Key Lessons from the Research

- **Backend/frontend separation is essential** (proven by Pop!_OS/elementary)
- **Don't write partition logic from scratch** — use libparted
- **YAML configuration beats code changes** for most customization (Calamares proves this)
- **Linear flow beats hub-and-spoke** (Fedora learned this, redesigned Anaconda)
- **CSS/QML theming enables branding without forking** (elementary, Calamares)
- **Monolithic installers fail** (Ubiquity's death proves this)
