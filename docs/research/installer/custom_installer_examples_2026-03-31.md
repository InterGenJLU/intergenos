# Custom-Built Linux Installer Examples

**Date:** March 31, 2026
**Context:** Studying real-world custom installers to inform InterGenOS decisions

---

## Pop!_OS Installer (distinst)

**Why custom:** Escape Ubiquity's complexity. Give UI teams clean separation from disk logic.

**Stack:** Rust backend (distinst) + Vala/GTK frontend
**License:** GPL-3.0
**Key insight:** Backend makes NO assumptions about OS, DE, packaging, or toolkit. C, Rust, Vala bindings.
**Architecture:** Squashfs extraction + chroot configuration. EFI/BIOS bootloader selection.
**Reused by:** elementary OS, Vanilla OS (forked)

**Lesson:** Backend/frontend separation is the right architecture. CSS theming enables branding without forking.

## elementary OS Installer

**Stack:** Vala + GTK (shares distinst backend with Pop!_OS)
**Design:** Dual-pane layout — left pane for OEM branding, right pane for installer steps
**Modes:** Demo, Auto (erase & install), Custom (advanced partitioning)
**Innovation:** OEM integration via oem.conf, full CSS theming

## Vanilla OS Installer (Albius)

**Stack:** Python backend, reduced GNOME session
**Innovation:** Recipe-based (YAML), SquashFS + OCI installation support, LUKS2 encryption
**Why custom:** Immutable/atomic system support (ABRoot), OCI container installation path
**Lesson:** Custom backends excel for distros with non-standard installation models.

## COSMIC Installer (System76, in development)

**Stack:** Rust + Iced (custom GUI toolkit)
**Status:** In development alongside COSMIC DE (released Dec 2025)
**Relevance:** InterGenOS plans COSMIC support — directly relevant
**Note:** Full Rust stack. Iced gaining traction. Monitor progress.

## EndeavourOS (Calamares customization)

**Approach:** Stock Calamares + custom modules + pre/post-install bash scripts
**Key insight:** Calamares customization IS viable for 80% of use cases
**Effort:** Moderate — mostly YAML config + small custom modules
**Online installer mode:** Installs fresh from network, not just from live ISO

---

## Partitioning Tools & Libraries

- **libparted (C):** Battle-tested. Used by GParted, Calamares, many installers. Use this.
- **blivet (Python):** Anaconda's backend. More complex, enterprise-focused.
- **distinst (Rust):** Custom abstraction. Experimental ECS architecture. Fork-worthy if going Rust.

**Recommendation:** Don't write partition logic from scratch. Use libparted.

---

## Development Effort Estimates

| Approach | Effort | Code | Maintainability |
|----------|--------|------|-----------------|
| Calamares + custom modules | 2-3 months | ~500-1000 lines config | High (upstream updates) |
| Custom backend + frontend (distinst-style) | 6-12 months | 10k-30k lines | Medium (your responsibility) |
| Full Rust stack (COSMIC-style) | 12+ months | 20k-50k+ lines | Medium (ecosystem still maturing) |

---

## What Failed vs What Worked

### Worked
- Backend/frontend separation (Pop!_OS, elementary)
- Using libparted (universal)
- YAML configuration (Calamares)
- CSS theming for branding (elementary)
- Reusing work (Vanilla OS forked distinst, EndeavourOS customized Calamares)

### Failed
- Monolithic installers (Ubiquity) — unmaintainable
- Writing partition logic from scratch — waste of time
- Over-engineering early — start simple
- Tight coupling to one UI toolkit — limits future options

---

## Repos

- Pop!_OS installer: https://github.com/pop-os/installer
- distinst: https://github.com/pop-os/distinst
- elementary installer: https://github.com/elementary/installer
- Vanilla OS Albius: https://github.com/Vanilla-OS/Albius
- Calamares: https://github.com/calamares/calamares
- EndeavourOS ISO: https://github.com/endeavouros-team/EndeavourOS-ISO
- COSMIC: https://github.com/pop-os/cosmic-epoch
- archinstall: https://github.com/archlinux/archinstall
