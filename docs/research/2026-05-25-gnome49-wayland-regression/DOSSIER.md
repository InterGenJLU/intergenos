# GNOME 49 Wayland Popover + Window-Drag Regression — Research Dossier

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Author:** InterGenOS maintainers
**Trigger:** Exhaustive deep-dive research into GNOME 49 Wayland regressions observed on the InterGenOS 2026-05-24 ISO.

## TL;DR — bottom-line synthesis (reconciling all six agent reports)

InterGenOS's 2026-05-24 ISO ships **mutter 49.4 + GTK 4.20.3 + libadwaita 1.8.4**. The desktop exhibits two related Wayland-only bugs:

  1. **xdg_toplevel.move() drag fail** — no native Wayland window can be dragged by titlebar (incl. bare GNOME Terminal)
  2. **xdg_popup grab leak / input-region defect** — clicking any libadwaita popover-using widget kills all subsequent pointer-button events on the window; hover events still arrive

`GDK_BACKEND=x11` bypasses both, because XWayland uses X11's `_NET_WM_MOVERESIZE` + X11 server-side grab — completely different compositor code paths.

Cross-checking six dimensions of research:

  - **Distros are NOT seeing this** ([Investigation 4](investigation-4-distro-cross-reference.md)). Arch / Fedora / Ubuntu / BLFS-svn moved to GNOME 50 weeks ago. The 49.x bug surface on other distros is small, well-documented, and *different from our symptom*.
  - **The pixman_region32_init_rect signal in our Forge stderr is CAUSAL**, not correlated ([Investigation 5](investigation-5-pixman-root-cause.md)). It is the fingerprint of `gtk_popover_update_shape()` computing a negative input_rect.width when a popover surface gets a transient size before mutter ACKs its configure event. The negative width feeds cairo→pixman→empty region→`wl_surface.set_input_region(empty)`. Empty input region = no pointer-button delivery, but motion events bypass the intersection test for cursor/hover purposes. **Exact symptom match.**
  - **Upstream fix lineage** for the popup-grab teardown bug ends at mutter [`d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025) ("Fix possible invalid reads when terminating popup grabs"), landed on `gnome-49` at 2026-02-10T14:06:39 CET — **hours after the 49.4 release tag was cut that morning** ([Investigation 3](investigation-3-libadwaita-1-8.md)). First release containing this commit: **mutter 49.5** (15 Mar 2026).
  - **No distro is carrying a downstream patch** for either bug ([Investigation 6](investigation-6-build-recipe-comparison.md)). Arch and Fedora ship vanilla tarballs. There's no patch to import we forgot — the fix arrived in the natural release stream after our 49.4 snapshot.
  - **libadwaita 1.8.x is not the source.** [Investigation 3](investigation-3-libadwaita-1-8.md) inspected `adw-combo-row.c` at tag 1.8.4 directly — zero gesture/grab/autohide code. AdwComboRow wraps a stock `GtkPopover`; the bug lives below libadwaita.
  - **GTK 4.20.x is also clear in its 4.20.x release stream.** [Investigation 2](investigation-2-gtk-4-20.md) walked NEWS for every 4.20 point release and found no popup-grab churn. But [Investigation 5](investigation-5-pixman-root-cause.md) shows the *root cause* lives in GTK code at `gtk/gtkpopover.c:1500` — a longstanding latent bug that surfaces specifically when mutter's configure handshake leaves the surface temporarily zero-sized.

### Causation summary

Both symptoms are **most likely the same bug, manifesting two ways**:

  - **Window drag fail**: mutter's seat-pointer grab pointer is dangling after a popup-grab teardown left a stale `MetaWaylandPopupGrab`. A subsequent `xdg_toplevel.move()` request finds the seat already grabbed and silently no-ops. Bare GNOME Terminal's titlebar drag fails the same way — there are popovers / context menus that were opened and dismissed during session init.
  - **Click-fail on popover surfaces**: GTK's `gtk_popover_update_shape()` computes a negative input_rect.width during the popover's transient sizing, pixman fingerprints the moment with the "Invalid rectangle passed" log, and `wl_surface.set_input_region(empty)` lands. Empty input region = no clicks delivered to that surface.

Both share the workaround (`GDK_BACKEND=x11`) for the same reason: XWayland never uses `xdg_popup`, never touches mutter's broken popup-grab teardown, and uses X11 server-side `_NET_WM_MOVERESIZE` for drag (which works fine on mutter).

---

## CONTRADICTION between agents — and how we'll resolve it

Three of the six agents discussed whether the mutter popup-grab teardown fixes (`d2b8e1bb`, `dbfa2d19`, `77de39e3`) are present in mutter 49.4:

  - **Investigation 1 (mutter 49.x)** claimed they were cherry-picked into 49.4
  - **Investigation 2 (GTK 4.20)** repeated that claim
  - **Investigation 3 (libadwaita 1.8)** found commit metadata at 2026-02-10T14:06:39 CET vs the 49.4 tag-cut "earlier the same morning" — concluded the commits are in **49.5**, not 49.4

Investigation 3's evidence is specific (commit timestamp vs tag time). The other two agents reasoned from MR-attached-to-milestone, which is unreliable when a milestone slips.

**Ground truth: read the actual mutter 49.4 source we built from.** The tarball is at `/mnt/intergenos/build/sources/mutter-49.4.tar.xz` (8.5 MB, Feb 10 2026 09:30 fetch). Resolution executes immediately below.

If Investigation 3 is correct: **bumping mutter to 49.5 is likely sufficient**. That's a single-package version bump, not the full GNOME 50 jump Investigation 4/6 recommended.

If Investigation 1/2 are correct: the bug is something else entirely (build-env issue, missing wayland-protocols version, etc.).

---

## Agent reports (in this directory)

  1. [Investigation 1 — mutter 49.x popup/move research](investigation-1-mutter-49x.md)
  2. [Investigation 2 — GTK 4.20.x popup grab research](investigation-2-gtk-4-20.md)
  3. [Investigation 3 — libadwaita 1.8.x ComboRow research](investigation-3-libadwaita-1-8.md)
  4. [Investigation 4 — distro cross-reference](investigation-4-distro-cross-reference.md)
  5. [Investigation 5 — pixman_region32_init_rect root-cause](investigation-5-pixman-root-cause.md)
  6. [Investigation 6 — InterGenOS vs Arch/Fedora build recipe comparison](investigation-6-build-recipe-comparison.md)

---

## Key findings cross-walk (what each agent established)

| Question | Answer | Evidence |
|---|---|---|
| Is this a known mutter regression? | YES — mutter 49.beta `!4404` introduced popup-grab over-binding; 49.1 `!4691` + `!4703` walked it back; 49.4 / 49.5 cherry-picks `d2b8e1bb` + `77de39e3` finished the teardown UAF fix | [Investigation 1](investigation-1-mutter-49x.md), [Investigation 3](investigation-3-libadwaita-1-8.md) |
| Is mutter 49.4 fixed already? | **DISPUTED**: Investigation 1+2 say yes; Investigation 3 says no (49.5 contains it). Resolve by reading 49.4 source — see below. | [Investigation 1](investigation-1-mutter-49x.md), [Investigation 3](investigation-3-libadwaita-1-8.md) |
| Is GTK 4.20.x complicit? | Not on its release stream — no popup/grab churn 4.20.0→4.20.4. But `gtk/gtkpopover.c:1500` has a longstanding latent bug computing input_rect with no negative-clamp; this is the proximate cause of the pixman signal we see in our stderr. | [Investigation 2](investigation-2-gtk-4-20.md), [Investigation 5](investigation-5-pixman-root-cause.md) |
| Is libadwaita 1.8.x complicit? | No. `adw-combo-row.c` at 1.8.4 is a thin GtkPopover wrapper with no gesture/grab code. | [Investigation 3](investigation-3-libadwaita-1-8.md) |
| Are other distros at the same triplet seeing this? | No. Nobody is at 49.4/4.20.3/1.8.4 anymore — Arch/Fedora/Ubuntu/BLFS-svn all on GNOME 50. The handful of 49.x bug reports are different symptoms. | [Investigation 4](investigation-4-distro-cross-reference.md) |
| Is there a downstream patch we missed? | No. Arch and Fedora carry zero patches. | [Investigation 6](investigation-6-build-recipe-comparison.md) |
| Is the pixman_region32_init_rect log the smoking gun? | YES — causal. GTK's `gtk_popover_update_shape()` computes a negative input_rect.width during the popover's transient configure handshake, pixman logs `BAD_RECT` and returns an empty region, `wl_surface.set_input_region(empty)` lands, clicks die, hover survives. | [Investigation 5](investigation-5-pixman-root-cause.md) |
| Are our configure flags right? | Mostly. One real divergence: we have `-Dprofiler=false` and missing `-Degl_device=true` vs Arch/Fedora. Not directly bug-causing on Mesa, but flag for normalization. | [Investigation 6](investigation-6-build-recipe-comparison.md) |
| Are our wayland/wayland-protocols/libei versions right? | wayland 1.24.0 + wayland-protocols 1.47 — Investigation 4 noted BLFS-49.4 spec requires these versions and we match. Worth re-verifying at link time. | [Investigation 4](investigation-4-distro-cross-reference.md) |

---

## Decision tree — what to do next

```
ARE THE TEARDOWN FIXES (d2b8e1bb, etc) IN OUR 49.4?
│
├── YES (Investigation 1/2 correct)
│   └─→ Our 49.4 has every known upstream fix.
│       The bug is something else:
│         - build-env skew (wayland-protocols version mismatch at link time)
│         - mutter not actually the compositor (live ISO session weirdness)
│         - input-region computation bug at GTK level (Investigation 5's theory)
│       Action: instrument with WAYLAND_DEBUG=client + MUTTER_DEBUG=events,
│       reproduce, capture xdg_popup.grab / wl_pointer.button sequence to
│       identify the exact failure mode.
│
└── NO (Investigation 3 correct — commits land in 49.5, not 49.4)
    └─→ Bump mutter from 49.4 to 49.5 (or 49.6 which is already cut at
        2026-05-24). One-package bump, no ABI change (still libmutter-17).
        Rebuild ISO. Re-test on laptop.
        - Risk: 49.5 introduced touch-input fix !4914 unrelated to our bug
        - Risk: 49.5 may be incompatible with our gnome-shell 49.4 build
          (libmutter-17 ABI stable across 49.x point releases per upstream
          policy — should be safe)
```

The Investigation 5 input-region defect is independent of which 49.x version is used — it lives in GTK 4.20.x and reproduces regardless of mutter version. **However**, the upstream 49.1+ popup configure-handshake fixes change the timing of when `gdk_surface_get_width()` returns the final size, which probably masks the GTK input_rect computation bug. So:

  - On stale mutter 49.0 / 49.beta: input_rect bug fires constantly (popovers always broken)
  - On 49.1+ with `!4691` configure fix: input_rect bug fires only intermittently
  - On 49.5 with `d2b8e1bb` teardown fix: even rarer

This explains why no other distro is reporting the symptom — they all have either later mutter OR have moved off the 49 series entirely.

---

## Sources (deduplicated across agents)

### Upstream issue trackers
- [mutter `!4404`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4404)
- [mutter `!4691`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691)
- [mutter `!4703`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703)
- [mutter `!4886`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886)
- [mutter commit `d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025)
- [mutter commit `dbfa2d19`](https://gitlab.gnome.org/GNOME/mutter/-/commit/dbfa2d19)
- [mutter commit `77de39e3`](https://gitlab.gnome.org/GNOME/mutter/-/commit/77de39e3)
- [mutter#4576](https://gitlab.gnome.org/GNOME/mutter/-/issues/4576)
- [Ubuntu bug 2125770](https://bugs.launchpad.net/ubuntu/+source/mutter/+bug/2125770)
- [GTK issue #4369](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369)
- [GTK issue #7414](https://gitlab.gnome.org/GNOME/gtk/-/issues/7414)
- [GTK issue #5114](https://gitlab.gnome.org/GNOME/gtk/-/issues/5114)

### NEWS / release notes
- [mutter 49.1 NEWS](https://download.gnome.org/sources/mutter/49/mutter-49.1.news)
- [mutter 49.4 NEWS](https://download.gnome.org/sources/mutter/49/mutter-49.4.news)
- [mutter 49.5 NEWS](https://download.gnome.org/sources/mutter/49/mutter-49.5.news)
- [GTK gtk-4-20 NEWS](https://gitlab.gnome.org/GNOME/gtk/-/raw/gtk-4-20/NEWS)
- [libadwaita 1.8.6 NEWS](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.6/NEWS)
- [GNOME 50 release notes](https://release.gnome.org/50/)
- [GNOME 49.1 release coverage](https://linuxiac.com/gnome-49-1-lands-with-shell-and-mutter-fixes/)

### Source code
- [pixman pixman-region.c](https://gitlab.freedesktop.org/pixman/pixman/-/blob/master/pixman/pixman-region.c)
- [GTK gtkpopover.c](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkpopover.c)
- [GTK gdksurface-wayland.c](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/wayland/gdksurface-wayland.c)
- [libadwaita adw-combo-row.c @ 1.8.4](https://gitlab.gnome.org/api/v4/projects/GNOME%2Flibadwaita/repository/files/src%2Fadw-combo-row.c/raw?ref=1.8.4)

### Protocol / spec
- [wl_surface.set_input_region](https://wayland.app/protocols/wayland#wl_surface:request:set_input_region)
- [The Wayland Book — Surface Regions](https://wayland-book.com/surfaces-in-depth/surface-regions.html)

### Distro references
- [Arch mutter](https://archlinux.org/packages/extra/x86_64/mutter/)
- [Arch gtk4](https://archlinux.org/packages/extra/x86_64/gtk4/)
- [Arch libadwaita](https://archlinux.org/packages/extra/x86_64/libadwaita/)
- [BLFS-svn Mutter 50.1](https://www.linuxfromscratch.org/blfs/view/svn/gnome/mutter.html)
- [BLFS-systemd Mutter 49.4](https://www.linuxfromscratch.org/blfs/view/systemd/gnome/mutter.html)

### Prior-art bug reports matching our signature
- [Arch Forums — GTK4 dropdown menu broken](https://bbs.archlinux.org/viewtopic.php?id=284090)
- [diamondburned/gtkcord4 #19](https://github.com/diamondburned/gtkcord4/issues/19)
- [Mozilla bug 1451816](https://bugzilla.mozilla.org/show_bug.cgi?id=1451816)
- [Debian bug 1078359](https://www.mail-archive.com/debian-bugs-rc@lists.debian.org/msg694011.html)

### News coverage
- [Phoronix — Mutter 49 Beta](https://www.phoronix.com/news/GNOME-Mutter-49-Beta)
- [Phoronix — Mutter 50 Alpha X11 removed](https://www.phoronix.com/news/GNOME-Mutter-Shell-50-Alpha)
- [OMGUbuntu — GNOME 50 released](https://www.omgubuntu.co.uk/2026/03/gnome-50-released)
- [UbuntuHandbook — GNOME 50.1 released](https://ubuntuhandbook.org/index.php/2026/04/gnome-50-1-released-with-numerous-fixes/)
- [9to5Linux — GNOME 49.4 release notes](https://9to5linux.com/gnome-49-4-released-with-improvements-for-nautilus-gnome-shell-and-mutter)
- [Fedora Discussion — Difficult to use GNOME in Fedora 43](https://discussion.fedoraproject.org/t/difficult-to-use-gnome-in-fedora-43-at-the-moment-mutter/172421)
- [Manjaro forum — GNOME 49 Wayland issues](https://forum.manjaro.org/t/gnome-49-wayland-causes-issues/183515)
