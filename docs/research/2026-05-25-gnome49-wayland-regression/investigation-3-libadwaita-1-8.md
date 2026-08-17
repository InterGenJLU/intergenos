# Investigation 3 — libadwaita 1.8.x ComboRow research

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output preserved below)
**Brief:** triage libadwaita 1.8.x for an Adw.ComboRow / Adw.Toast input-grab regression.

---

## BOTTOM LINE

**The bug is NOT in libadwaita 1.8.x — it's in mutter 49.4.** Specifically: a popup-grab teardown race ([commit `d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025), [MR `!4886`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886)) that frees the `MetaWaylandPopupGrab` struct while still iterating its popup-surface list, leaving the per-seat pointer-button grab pointing at freed memory. After teardown, motion events continue (cursor still tracked), but button events route to the dead grab. XWayland bypasses it because XWayland popups use X11 grab semantics, not `xdg_popup`.

> ⚠️ **CONTRADICTION RESOLUTION (vs Investigations 1 + 2)**: This agent found commit metadata showing `d2b8e1bb` landed on `gnome-49` at **2026-02-10T14:06:39 CET** — Florian Müllner cut the 49.4 tag the same morning, **hours earlier**. Therefore the popup-grab teardown fixes ARE NOT in our 49.4 build — they first ship in **mutter 49.5** (15 Mar 2026). Investigations 1 and 2 were wrong to claim 49.4 contains them.

**Fix: upgrade mutter to 49.5 (15 Mar 2026, [tag `658f672c`](https://gitlab.gnome.org/GNOME/mutter/-/tags?search=49)) or cherry-pick three commits onto 49.4.** Libadwaita 1.8.4 is fine — leave it alone.

## Timeline / why 49.4 specifically

| Component | Our version | Date tagged | Fix status |
|---|---|---|---|
| libadwaita | **1.8.4** | 1 Feb 2026 | No relevant change since 1.8.2 |
| GTK | **4.20.3** | 20 Nov 2025 | No popover-grab churn in 4.20.x |
| **mutter** | **49.4** | **10 Feb 2026** (e6379ecf, AM) | **Missed popup-grab fix by hours** |
| mutter (fix) | 49.5 | 15 Mar 2026 | Contains the fix |

Carlos Garnacho landed the two popup-grab teardown commits on `gnome-49` at **2026-02-10T14:06:39 CET**, mere hours after Florian Müllner cut the 49.4 tag the same morning. Source: [GitLab commit metadata for `d2b8e1bb`](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025).

## Libadwaita 1.8.x triage (full)

From [NEWS at tag 1.8.6](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.6/NEWS) and the [tags page](https://gitlab.gnome.org/GNOME/libadwaita/-/tags?search=1.8):

| Version | Date | Popup-relevant entries |
|---|---|---|
| 1.8.0 | 12 Sep 2025 | (no popup/grab churn) |
| 1.8.1 | 9 Oct 2025 | AdwComboRow: **"Allow selecting items via touchscreen"** ([commit `18ea3605`](https://gitlab.gnome.org/GNOME/libadwaita/-/commit/18ea3605d3af74ebde86d5d3d68fd180ddd9900e)) |
| 1.8.2 | 21 Nov 2025 | AdwComboRow: **"Revert touchscreen fix from 1.8.1, since it's been fixed in GTK"** ([commit `c397550b`](https://gitlab.gnome.org/GNOME/libadwaita/-/-/commit/c397550b)) |
| 1.8.3 | 3 Jan 2026 | AdwAlert/MessageDialog: padding fix only |
| **1.8.4** | **1 Feb 2026** | AdwDialog: initial-focus fix in bottom-sheet mode only |
| 1.8.5 / 1.8.5.1 | Feb–Mar 2026 | Translation-only |
| 1.8.6 | 24 May 2026 | AdwAlertDialog: chain-up crash fix; AdwEntryRow: edit-icon click fix |

**Inspection of [`adw-combo-row.c` at tag 1.8.4](https://gitlab.gnome.org/api/v4/projects/GNOME%2Flibadwaita/repository/files/src%2Fadw-combo-row.c/raw?ref=1.8.4) confirms zero `GtkGestureClick` / `GtkEventController` / explicit `gtk_grab_*` / `autohide` code.** AdwComboRow just wraps a `GtkPopover` with `gtk_popover_popup()` / `gtk_popover_popdown()`. The 1.8.1→1.8.2 churn was a one-line model-selection fix that does not touch the grab. There is no libadwaita 1.8.x regression that matches our symptom.

**AdwAlertDialog** inherits from [`AdwDialog`](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.4/src/adw-dialog.c). When it has no host it calls `gtk_window_new()` (a real `xdg_toplevel`); when hosted in `AdwApplicationWindow` it reparents in-process. Neither path uses an `xdg_popup` — so the "MessageDialog dismiss kills input" arm of our symptom is the *host window's* implicit grab leaking, not a dialog popup. Same root cause as ComboRow's `xdg_popup` grab leak.

**AdwToastWidget** is a plain `GtkBox` ([toast widget source](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.4/src/adw-toast-widget.c)) — no popup. The Toast trigger in our symptom is likely the *focus / pointer movement* that happens when `AdwToastOverlay` raises the toast, which lands on a freed grab struct from a prior popover interaction.

## The mutter-side smoking gun

[Mutter NEWS at tag 49.5](https://gitlab.gnome.org/GNOME/mutter/-/raw/49.5/NEWS) shows the matching arc started in 49.1:

> 49.1 (14 Oct 2025): **"Fix GTK apps locking up after entering popover submenu"** [Alessandro Astone; [`!4691`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691)] — *"This fixes a bug where entering a submenu within Gtk.PopoverMenu hangs GTK waiting for a configure event that never comes."*
>
> 49.1: **"Do not force pointer focus on popups"** [Carlos Garnacho; [`!4703`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703)] — removed a workaround for [gtk#7414](https://gitlab.gnome.org/GNOME/gtk/-/issues/7414).

`!4703` introduced **two** regressions tracked upstream:
- [mutter#4576](https://gitlab.gnome.org/GNOME/mutter/-/issues/4576) "49.1 regression: tooltip popups conflict with autofilling cells in LibreOffice Calc" — bisected to `d4247714` (from `!4703`). **Still open.**
- The popup-grab teardown invalid-read fixed by [`!4886`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886) — landed on `gnome-49` Feb 10 at 14:06 CET, hours after 49.4 was tagged.

The teardown bug matches our symptom exactly:

> "There is a bit of an egg-and-chicken problem between a popup grab, and the surfaces it applies to. Finishing a grab will iterate through all popups, and free the `MetaWaylandPopupGrab` struct on the last one, while the `MetaWaylandPopupGrab` list of popup surfaces is being iterated."
> — [commit message](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025)

After the use-after-free the seat-pointer's button-grab pointer is dangling, but the *cursor-position machinery* lives on `MetaWaylandSeat`, not the grab — which is why **hover/motion survives and only button events die.** XWayland uses a separate X11 grab path and is unaffected, matching the `GDK_BACKEND=x11` workaround.

## Recommended action for InterGenOS

**Bump mutter to 49.5 (or wait for 49.6 which is already cut at [`6ec04384`](https://gitlab.gnome.org/GNOME/mutter/-/tags?search=49) on 24 May 2026).**

If we want to stay on 49.4 for the live ISO, cherry-pick onto our 49.4 source tree:

1. `b6a5aff28e47c624670881a67fdd2668942d55d9` — "wayland: Notify grab finish from MetaWaylandPopupSurfaceInterface" (refactor; required parent).
2. `d5981ff119402b420c61a8302aa3f0e87805284c` (main) / `d2b8e1bb922113bf29d11a2f002a5ece16196025` (gnome-49) — "wayland: Fix possible invalid reads when terminating popup grabs."
3. `77de39e3f706dba6c03408f54b2a3ce02ddc2253` — "wayland/popup: Handle popups dismissing other popups when finishing" (Jonas Ådahl; sibling fix on the same surface-iteration teardown path).

All three live in `src/wayland/` and `src/wayland/meta-wayland-popup.c` specifically — small, surgical, and already validated by being on `gnome-49` for 49.5.

Leave libadwaita at 1.8.4 (or bump to 1.8.6 for the unrelated AdwAlertDialog chain-up crash fix — not required for this symptom).

## Sources

- [libadwaita NEWS @ 1.8.6](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.6/NEWS)
- [libadwaita 1.8.x tags](https://gitlab.gnome.org/GNOME/libadwaita/-/tags?search=1.8)
- [libadwaita 1.8.4 adw-combo-row.c](https://gitlab.gnome.org/api/v4/projects/GNOME%2Flibadwaita/repository/files/src%2Fadw-combo-row.c/raw?ref=1.8.4)
- [libadwaita 1.8.4 adw-dialog.c](https://gitlab.gnome.org/GNOME/libadwaita/-/raw/1.8.4/src/adw-dialog.c)
- [mutter NEWS @ 49.5](https://gitlab.gnome.org/GNOME/mutter/-/raw/49.5/NEWS)
- [mutter 49.x tags](https://gitlab.gnome.org/GNOME/mutter/-/tags?search=49)
- [mutter commit `d2b8e1bb` (gnome-49)](https://gitlab.gnome.org/GNOME/mutter/-/commit/d2b8e1bb922113bf29d11a2f002a5ece16196025)
- [mutter MR `!4886`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886)
- [mutter MR `!4703` "Do not force pointer focus on popups"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703)
- [mutter MR `!4691` "Fix GTK apps locking up after entering popover submenu"](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691)
- [mutter#4576 (49.1 regression bisected to !4703)](https://gitlab.gnome.org/GNOME/mutter/-/issues/4576)
- [GTK issue #7414 (original GTK bug !4703 was working around)](https://gitlab.gnome.org/GNOME/gtk/-/issues/7414)
- [GTK NEWS](https://gitlab.gnome.org/GNOME/gtk/-/raw/main/NEWS)
- [Gtk.Popover docs](https://docs.gtk.org/gtk4/class.Popover.html)
- [Gtk.DropDown docs](https://docs.gtk.org/gtk4/class.DropDown.html)
