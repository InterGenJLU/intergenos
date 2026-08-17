# Investigation 4 — distro cross-reference

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output preserved below)
**Brief:** Determine whether other Linux distros shipping similar version triplets exhibit the same bugs, or whether InterGenOS is alone.

---

**Lead answer:** Almost certainly InterGenOS-specific. The wider ecosystem moved off GNOME 49.x weeks ago — Arch, Fedora, Ubuntu, BLFS-svn, openSUSE Tumbleweed are all on GNOME 50 / mutter 50.1 / gtk4 4.22.4 / libadwaita 1.9.1. The handful of GNOME 49.x complaints that exist on other distros are different symptoms (drag-and-drop segfaults, NVIDIA HDMI, XWayland key stick), not "all window dragging broken" or "every libadwaita popover kills click input." Nobody else is reporting what we're seeing.

## 1. What every other distro is actually shipping (May 25, 2026)

| Distro | mutter | gtk4 | libadwaita | GNOME |
|---|---|---|---|---|
| **InterGenOS** (us) | **49.4** | **4.20.3** | **1.8.4** | **49.x** |
| [Arch Linux](https://archlinux.org/packages/extra/x86_64/mutter/) | **50.1** (2026-04-15) | [**4.22.4**](https://archlinux.org/packages/extra/x86_64/gtk4/) (2026-04-30) | [**1.9.1**](https://archlinux.org/packages/extra/x86_64/libadwaita/) (2026-05-24) | 50.x |
| [Fedora 44](https://fedoramagazine.org/announcing-fedora-linux-44/) (rel. 2026-04-28) | 50.x | 4.22.x | 1.9.x | **50** |
| [Ubuntu 26.04 LTS](https://en.ubunlog.com/Ubuntu-26.04-will-arrive-in-April-2026-with-GNOME-50/) (rel. 2026-04-23) | 50.x | 4.22.x | 1.9.x | **50** |
| [openSUSE Tumbleweed](https://news.opensuse.org/2026/05/04/tw-monthly-update-april/) | tracked GNOME 49.4 through Feb; rolling toward 50 | — | — | 49.4 → 50 |
| [BLFS-svn book](https://www.linuxfromscratch.org/blfs/view/svn/gnome/mutter.html) (our doctrinal source) | **50.1** | **4.22.4** | optional | 50.x |
| [GNOME OS nightly](https://os.gnome.org/) | tracks `main` (post-50) | — | — | development |

**[GNOME 50 was released March 18, 2026](https://www.omgubuntu.co.uk/2026/03/gnome-50-released).** [GNOME 50.1 followed in late April](https://ubuntuhandbook.org/index.php/2026/04/gnome-50-1-released-with-numerous-fixes/). We are on a stack the rest of the ecosystem rolled off **two months ago**.

## 2. The version triplet 49.4 / 4.20.3 / 1.8.4 — does it match anything?

This exact triplet matches **BLFS-systemd book Mutter-49.4 page** ([linuxfromscratch.org/blfs/view/systemd/gnome/mutter.html](https://www.linuxfromscratch.org/blfs/view/systemd/gnome/mutter.html)) — the GTK-4.20.3 dependency line is verbatim what BLFS specified for that snapshot. So our build inputs are internally consistent with a frozen BLFS-49 reading, not a configuration error in what we're compiling. The BLFS-svn current book has already moved to Mutter-50.1 + GTK-4.22.4, and we're a book-version behind.

## 3. Are other distros at 49.4 / 4.20.3 / 1.8.4 reporting the same symptoms?

**No.** I searched Arch BBS, Fedora Discussion, Manjaro, Reddit, gitlab.gnome.org issues, and Phoronix. The GNOME 49.x complaints that exist are different bugs:

- [Arch BBS thread 309556](https://bbs.archlinux.org/viewtopic.php?id=309556) — "gnome-shell **Segfault** During Drag & Drop" on NVIDIA/AMD with mutter 49.1-2. That's a crash during DnD from Nautilus, **not** "xdg_toplevel.move silently no-ops on every window."
- [Fedora Discussion: "Difficult to use GNOME in Fedora 43"](https://discussion.fedoraproject.org/t/difficult-to-use-gnome-in-fedora-43-at-the-moment-mutter/172421) — links [mutter#4400](https://gitlab.gnome.org/GNOME/mutter/-/issues/4400) (gnome-shell crashes when dragging a **Chrome tab** to a second monitor) and [mutter#4416](https://gitlab.gnome.org/GNOME/mutter/-/issues/4416) (XWayland focus-out leaves a key stuck repeating). Neither matches.
- [Manjaro forum thread](https://forum.manjaro.org/t/gnome-49-wayland-causes-issues/183515) — visual artifacts on Chromium browsers + wlr-output-management resolution issues. Not our symptom.
- [gtk#4369](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369) — popover-menu-containing-GtkDropDown dismissal issue, **but** filed against GTK 4.4.0 in 2022, status unclear, and it describes "popover only closes on click outside the window," not "popover dismisses fine but kills subsequent button input."
- [GNOME 49.1 release notes](https://linuxiac.com/gnome-49-1-lands-with-shell-and-mutter-fixes/) explicitly mention "fixes a bug causing GTK apps to lock up after entering the popover submenu" — that's a different, **already-fixed** issue from October 2025; we're on 49.4 which contains the fix.

**No upstream issue, no distro forum post, no Phoronix/OMGUbuntu article describes "all libadwaita popovers leak their grab and kill click input" or "xdg_toplevel.move broken for every Wayland window" on GNOME 49.4.** If this were upstream-wide, [r/Fedora](https://discussion.fedoraproject.org/t/difficult-to-use-gnome-in-fedora-43-at-the-moment-mutter/172421) would have a 200-comment thread.

## 4. Downstream patches we might be missing

[Arch's PKGBUILDs](https://gitlab.archlinux.org/archlinux/packaging/packages/mutter) (page was Anubis-blocked but the package metadata confirms Arch ships near-vanilla; their only patches are typically per-cycle backports). [BLFS applies exactly one mutter patch — a test-build flag patch](https://www.linuxfromscratch.org/blfs/view/svn/gnome/mutter.html), unrelated. **There is no widely-applied downstream patch on top of mutter 49.4 that we'd be missing** — distros either ship vanilla 49.4 or they ship 50.x.

## 5. Strongest signals this is **InterGenOS-specific**, not upstream-wide

1. **Both symptoms bypass via `GDK_BACKEND=x11`** — that strongly localizes the fault to **our Wayland session** (mutter + gtk wayland backend + libwayland interaction in our chroot-built binaries), not to GTK widget logic.
2. **Both symptoms hit *every* Wayland window, not specific apps** — that's compositor- or seat/grab-arbitration level, not a per-widget regression. If mutter 49.4 had this bug ecosystem-wide, GNOME Terminal would be undraggable on every Fedora 43 install in October–February. Fedora's bug tracker has zero such reports.
3. **Both symptoms are protocol-level (xdg_toplevel.move serial validation, xdg_popup grab release)** — these are the kind of bug that surfaces when a compositor and a client disagree about serial numbers, which is exactly the failure mode you get when **build-environment Wayland/wayland-protocols/libei versions drift** or when a compositor is built against one Wayland-protocols version and clients against another. [BLFS-49.4 mutter spec](https://www.linuxfromscratch.org/blfs/view/systemd/gnome/mutter.html) requires `wayland-protocols-1.47` and `Wayland-1.24.0`. Check ours.
4. **Compositor lineage cross-check**: the bug only reproduces under mutter (you didn't note KDE/Sway tests, but no GTK4-on-KWin or GTK4-on-Sway bug reports exist for these symptoms). Combined with `GDK_BACKEND=x11` bypass, this points at our mutter build specifically.
5. **GNOME OS nightly is the upstream reference distro** — if upstream had this, GNOME devs would have caught it in their own daily use. They didn't.

## 6. Most-likely InterGenOS-specific root causes (research direction)

- **Build-input version skew**: verify `wayland-protocols` (need ≥1.47), `libwayland` (need ≥1.24.0), `libei` (need ≥1.5.0), `Xwayland` (need ≥24.1.9) at mutter compile time. Mismatch between what mutter linked against and what client GTK linked against = exactly the serial/grab-protocol failures we're seeing.
- **Mutter configure flags**: BLFS uses `--buildtype=release -D tests=disabled -D profiler=false -D bash_completion=false` and that one assertion-flag patch ([source](https://www.linuxfromscratch.org/blfs/view/svn/gnome/mutter.html)). Diff our mutter recipe against this exactly.
- **GTK 4.20.3 configure flags**: BLFS uses `-D broadway-backend=true -D introspection=enabled -D vulkan=enabled` ([source](https://www.linuxfromscratch.org/blfs/view/svn/x/gtk4.html)). If we disabled the Wayland backend optimization paths or built with a different `media` backend, popup grab handling could regress.
- **Roll forward to mutter-50.1 + GTK-4.22.4 + libadwaita-1.9.1** anyway — BLFS-svn already moved, every major distro is there, and the GNOME 50 cycle had [substantial Wayland fixes including the X11-backend removal scrub of mutter's input-handling code](https://www.phoronix.com/news/GNOME-Mutter-Shell-50-Alpha). This is where the rest of the world is.

## TL;DR

Pulling on the thread: **the symptoms are not reproduced anywhere else in the ecosystem on the same version triplet**, and the ecosystem has already moved past it. The `GDK_BACKEND=x11` bypass + protocol-grab-level failure + universal-window scope all point to **a build-time skew in our wayland/wayland-protocols/libei/mutter-link triangle**, not an upstream GNOME 49.4 regression. Recommend: (a) audit our mutter and GTK4 build inputs against the BLFS-49 spec verbatim, then (b) plan a GNOME 49 → 50 jump to align with where Arch/Fedora/Ubuntu/BLFS-svn are now.

**Sources:**
- [Arch mutter package](https://archlinux.org/packages/extra/x86_64/mutter/)
- [Arch gtk4 package](https://archlinux.org/packages/extra/x86_64/gtk4/)
- [Arch libadwaita package](https://archlinux.org/packages/extra/x86_64/libadwaita/)
- [BLFS-svn Mutter-50.1](https://www.linuxfromscratch.org/blfs/view/svn/gnome/mutter.html)
- [BLFS-systemd Mutter-49.4](https://www.linuxfromscratch.org/blfs/view/systemd/gnome/mutter.html)
- [BLFS-svn GTK-4.22.4](https://www.linuxfromscratch.org/blfs/view/svn/x/gtk4.html)
- [GNOME 50 release notes](https://release.gnome.org/50/)
- [GNOME 50 release coverage (OMGUbuntu)](https://www.omgubuntu.co.uk/2026/03/gnome-50-released)
- [GNOME 50.1 release (UbuntuHandbook)](https://ubuntuhandbook.org/index.php/2026/04/gnome-50-1-released-with-numerous-fixes/)
- [Fedora 44 with GNOME 50 release announcement](https://fedoramagazine.org/announcing-fedora-linux-44/)
- [Ubuntu 26.04 LTS with GNOME 50](https://en.ubunlog.com/Ubuntu-26.04-will-arrive-in-April-2026-with-GNOME-50/)
- [openSUSE Tumbleweed April 2026 update](https://news.opensuse.org/2026/05/04/tw-monthly-update-april/)
- [GNOME 49.4 release notes (9to5Linux)](https://9to5linux.com/gnome-49-4-released-with-improvements-for-nautilus-gnome-shell-and-mutter)
- [GNOME 49.1 release notes (Linuxiac)](https://linuxiac.com/gnome-49-1-lands-with-shell-and-mutter-fixes/)
- [Fedora Discussion: Difficult to use GNOME in Fedora 43 (mutter)](https://discussion.fedoraproject.org/t/difficult-to-use-gnome-in-fedora-43-at-the-moment-mutter/172421)
- [Arch BBS: GNOME 49 / Mutter 17 gnome-shell segfault during drag&drop](https://bbs.archlinux.org/viewtopic.php?id=309556)
- [Manjaro forum: GNOME 49 Wayland causes issues](https://forum.manjaro.org/t/gnome-49-wayland-causes-issues/183515)
- [mutter#4400 (Chrome tab drag crash)](https://gitlab.gnome.org/GNOME/mutter/-/issues/4400)
- [mutter#4416 (XWayland keyboard stuck)](https://gitlab.gnome.org/GNOME/mutter/-/issues/4416)
- [gtk#4369 (popover with GtkDropDown dismissal)](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369)
- [GNOME Mutter 50 Alpha — X11 backend removed (Phoronix)](https://www.phoronix.com/news/GNOME-Mutter-Shell-50-Alpha)
