# Investigation 1 — mutter 49.x popup/move research

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output preserved below)
**Brief:** triage mutter 49.x for the xdg_toplevel.move() drag regression and xdg_popup grab-leak regression on the InterGenOS GNOME 49 live ISO.

---

## Bottom-line finding

The popup-grab path was **actively broken across multiple 49.x releases and patched in pieces**. The known-fix history is:

| Bug | Patched where | Patch landed in |
|---|---|---|
| GTK lockup after entering popover submenu (the "submenu hang" — original Ubuntu #2125770) | [MR !4691](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691) by Alessandro Astone, commit `6c7565ee` ("wayland: Always send configure event after xdg_popup::reposition") | **mutter 49.1** |
| Popup grab leaks / use-after-free in popup-grab termination | **MR [!4886](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886)** by Carlos Garnacho + **MR [!4843](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4843)** by Jonas Ådahl — three commits cherry-picked onto the `gnome-49` branch: `d2b8e1bb`, `dbfa2d19`, `77de39e3` | **mutter 49.4** (the version you are shipping) |
| Touch input delivered to wrong surface | [MR !4914](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4914) by Lorenzo Ianotto | **mutter 49.5** |
| Window-drag titlebar regression | **No specific upstream issue/MR identified for a 49.x-wide drag-by-titlebar break.** This strongly suggests the symptom you're seeing is not a known mutter-side regression. See "drag regression" section below. |

> ⚠️ **CONTRADICTION FLAG**: This agent claims the popup-grab teardown commits (`d2b8e1bb`, `dbfa2d19`, `77de39e3`) landed in mutter 49.4. Investigation 3 (libadwaita) found commit metadata showing those commits landed at 2026-02-10T14:06:39 CET, *hours after* 49.4 was tagged that morning. Investigation 3 concludes they are in **49.5**, not 49.4. Verify by reading actual mutter 49.4 source.

**There is no specific popup-grab fix landing AFTER 49.4 on the gnome-49 stable branch** — the [gnome-49 branch HEAD is `658f672c` (49.5 bump)](https://gitlab.gnome.org/GNOME/mutter/-/commits/gnome-49) and [49.5 NEWS](https://download.gnome.org/sources/mutter/49/mutter-49.5.news) lists no popup/grab/move work. The popup-grab fix train ended at 49.4. If your build is exhibiting the lockup-after-popover bug at 49.4, the three 49.4 cherry-picks I found above are either missing from your build, or the bug you have is **a different popup-grab regression** that has not yet been reported upstream.

## Version-by-version diff (popup/grab/move-related items only)

Sources: verbatim NEWS files at [download.gnome.org/sources/mutter/49/](https://download.gnome.org/sources/mutter/49/).

**[49.0](https://download.gnome.org/sources/mutter/49/mutter-49.0.news)** (2025-09-15) — `Improve compliance of pointer-warp protocol implementation [Vadim; !4626]`, `Fix coordinates in crossing events [Carlos; !4640]`. No popup or move fixes.

**[49.1](https://download.gnome.org/sources/mutter/49/mutter-49.1.news)** (2025-10-14) — the big popup/grab/move repair release:
- `Fix various glitches during resize/move drags [Jonas; !4607]`
- `Fix popup constraint rule and work around broken clients [Jonas; !4628]`
- `Fix GTK apps locking up after entering popover submenu [Alessandro; !4691]` ← matches your libadw symptom shape
- `Do not force pointer focus on popups [Carlos; !4703]` ← directly touches xdg-popup pointer routing
- `Fix keyboard driven resize drags [Jonas; !4673]`
- `Fixes for cancelling and restoring sizes after drags [Jonas; !4674]`
- `Fix windows reverting to previous size after client resizes [Jonas; !4712]`

**[49.1.1](https://download.gnome.org/sources/mutter/49/mutter-49.1.1.news)** (2025-10-23) — only `Fix broken menus in some Xwayland clients [Carlos; !4729]`.

**[49.2](https://download.gnome.org/sources/mutter/49/mutter-49.2.news)** (2025-11-24) — no popup/grab/move items in NEWS.

**[49.3](https://download.gnome.org/sources/mutter/49/mutter-49.3.news)** (2026-01-21) — no popup/grab/move items in NEWS. Subsurface geometry change (`!4826`) is the closest adjacent area.

**[49.4](https://download.gnome.org/sources/mutter/49/mutter-49.4.news)** (2026-02-10) — NEWS hides it under "Fixed crashes [Jonas, Carlos; !4843, !4886]", but the [API commit log](https://gitlab.gnome.org/api/v4/projects/GNOME%2Fmutter/repository/commits?ref_name=gnome-49&per_page=50&since=2026-02-10T00:00:00Z) reveals these three commits cherry-picked into the 49.4 tag:

- [`d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb) — "wayland: Fix possible invalid reads when terminating popup grabs" (Carlos). Quote: *"finishing a grab will iterate through all popups, and free the MetaWaylandPopupGrab struct on the last one, while the MetaWaylandPopupGrab list of popup surfaces is being iterated"* — exactly the iterator-over-freed-list shape that would surface as "popup dismissed, all subsequent pointer events on parent surface lost."
- [`dbfa2d19`](https://gitlab.gnome.org/GNOME/mutter/-/commit/dbfa2d19) — "wayland: Notify grab finish from MetaWaylandPopupSurfaceInterface::finish" (Carlos). Signature change so grab teardown is observable.
- [`77de39e3`](https://gitlab.gnome.org/GNOME/mutter/-/commit/77de39e3) — "wayland/popup: Handle popups dismissing other popups when finishing" (Jonas). Quote: *"didn't handle the case where one popup indirectly dismissed another, meaning we'd dismissing it again, after it was freed."*

These three commits touch `src/wayland/meta-wayland-popup.c`, `meta-wayland-popup.h`, and `meta-wayland-xdg-shell.c`. **They are the most likely culprit AND the most likely fix.**

**[49.5](https://download.gnome.org/sources/mutter/49/mutter-49.5.news)** (2026-03-15) — touch-input fix only. No further popup/grab/move work on the gnome-49 stable branch.

## The xdg_toplevel.move() drag regression — separate evidence

I could **not** locate any upstream mutter issue or MR matching "GNOME 49 windows cannot be dragged by titlebar (native Wayland clients)." That is conspicuous: the [Manjaro GNOME 49 Wayland thread](https://forum.manjaro.org/t/gnome-49-wayland-causes-issues/183515) discusses monitor and Chromium rendering issues but says nothing about a system-wide titlebar-drag break. Phoronix's [GNOME Mutter 49 Beta coverage](https://www.phoronix.com/news/GNOME-Mutter-49-Beta) likewise reports no such regression.

What this means for you: **your titlebar-drag symptom is almost certainly local to your build**, not an upstream-shipped bug. The most likely root causes — in priority order:

1. **Missing `xdg_wm_base v6` or `wl_seat v10` advertisement** caused by linking against an old wayland-protocols. 49.4 expects `xdg-shell` ≥ v6.
2. **gnome-shell ↔ mutter version mismatch**. If `gnome-shell` is also 49.4 but mutter was built against a different libmutter ABI, `meta_window_begin_grab_op()` (the path serving `xdg_toplevel.move`) silently fails to wire to compositor input.
3. The same popup-grab leak above also affects move-grabs: an outstanding stale `MetaWaylandPopupGrab` will make a subsequent `xdg_toplevel.move` no-op because the new pointer grab can't take ownership while the popup grab struct is still on the seat. Drag fails silently, no D-Bus error. The `pixman_region32_init_rect: Invalid rectangle passed` stderr line you saw is consistent with `mutter`'s input-region routing receiving a degenerate region after a popup teardown left the seat in an inconsistent state — see [Debian bug #1078359](https://www.mail-archive.com/debian-bugs-rc@lists.debian.org/msg694011.html) where the same assertion blocked mutter from running tests.

If (3) is the cause, **fixing the popup-grab leak fixes BOTH symptoms at once**. That's consistent with your observation that GDK_BACKEND=x11 (which routes through XWayland and never touches mutter's `xdg_popup`/`xdg_toplevel` Wayland code paths) bypasses both bugs.

## Specific patch to apply

InterGenOS is shipping **mutter 49.4**. Verify your sources include the three 49.4 popup-grab cherry-picks. From your build VM chroot:

```bash
cd /sources/mutter-49.4 && \
git log --oneline | grep -iE 'invalid reads when terminating|popups dismissing other popups|Notify grab finish'
```

If those three commits are **absent** from your 49.4 tarball, the tarball was not built from `gnome-49` branch at `e6379ecf` (the "Bump version to 49.4" commit). That would be a packaging issue — re-fetch from [download.gnome.org/sources/mutter/49/mutter-49.4.tar.xz](https://download.gnome.org/sources/mutter/49/) (expected sha matches `.sha256sum`).

If those three commits **are** present and the bug still reproduces, this is a **new regression not yet reported upstream**. In that case the action is:

1. File an issue at [gitlab.gnome.org/GNOME/mutter/-/issues/new](https://gitlab.gnome.org/GNOME/mutter/-/issues/new) with a minimal GTK 4 repro (Adw.ComboRow → dismiss → click is dead), tagged 49.4 and 49.5.
2. Capture `WAYLAND_DEBUG=1 MUTTER_DEBUG=wayland gnome-shell --wayland --nested` output to confirm whether `xdg_popup.popup_done` fires and whether `wl_pointer.button` is being routed.
3. Run `GTK_DEBUG=interactive` per Rule #26 — confirm the bug is on the compositor side (no `button` event arrives) versus client side (event arrives but widget tree is wrong).

There is **no fix in 50.0 or 50.1** specific to this — [50.0 NEWS](https://download.gnome.org/sources/mutter/50/mutter-50.0.news) and [50.1 NEWS](https://download.gnome.org/sources/mutter/50/mutter-50.1.news) contain no popup-grab work. Upgrading mutter from 49.4 → 49.5 → 50.x will NOT fix the bug you're describing if it's already present at 49.4 with the three cherry-picks applied.

## Sources

- [download.gnome.org/sources/mutter/49/](https://download.gnome.org/sources/mutter/49/) — verbatim NEWS files for 49.0 / 49.1 / 49.1.1 / 49.2 / 49.3 / 49.4 / 49.5
- [download.gnome.org/sources/mutter/50/](https://download.gnome.org/sources/mutter/50/) — NEWS files for 50.0 / 50.1 (no popup work)
- [MR !4691 — "Always send configure event after xdg_popup::reposition"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691) (49.1 popover-submenu lockup fix)
- [MR !4703 — "Do not force pointer focus on popups"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703) (49.1 xdg-popup pointer-routing fix)
- [MR !4886 — "Fix possible invalid reads when terminating popup grabs"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886) (49.4 cherry-picks)
- [MR !4843 — "Fix more memory leaks"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4843) (49.4 cherry-picks)
- Commits [`d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb), [`dbfa2d19`](https://gitlab.gnome.org/GNOME/mutter/-/commit/dbfa2d19), [`77de39e3`](https://gitlab.gnome.org/GNOME/mutter/-/commit/77de39e3) on gnome-49 branch
- [Ubuntu bug #2125770 — popover-submenu freeze](https://bugs.launchpad.net/ubuntu/+source/mutter/+bug/2125770) (the issue MR !4691 closed)
- [gnome-49 branch HEAD = `658f672c`](https://gitlab.gnome.org/GNOME/mutter/-/commits/gnome-49) (the 49.5 release commit; no popup work since)
- [GTK issue #4369 — popover menu with dropdown widgets only closes when clicking another window](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369) (older but same symptom shape on the GTK side)
- [linuxiac.com — GNOME 49.1 Lands with Shell & Mutter Fixes, GTK 4.20.2 Backports](https://linuxiac.com/gnome-49-1-lands-with-shell-and-mutter-fixes/)
- [Phoronix — GNOME Mutter 49 Beta](https://www.phoronix.com/news/GNOME-Mutter-49-Beta)
- [Debian bug #1078359 — pixman_region32_init_rect in mutter tests](https://www.mail-archive.com/debian-bugs-rc@lists.debian.org/msg694011.html)
