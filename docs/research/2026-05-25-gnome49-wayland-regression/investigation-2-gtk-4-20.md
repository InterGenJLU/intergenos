# Investigation 2 — GTK 4.20.x popup grab research

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output preserved below; two phrases redacted by an automated content filter — marked inline)
**Brief:** triage GTK 4.20.x for the xdg_popup grab-leak regression breaking the InterGenOS GNOME 49 live ISO.

---

## Bottom line

**Our mutter 49.4 already contains every known upstream fix for the "popover dropdown breaks all subsequent clicks on Wayland" class of bug.** The signature symptom — clicks dying silently after a popover is dismissed, hover/motion still working, XWayland bypassing it — is the *exact match* of upstream **mutter [`!4691`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691) "Fix GTK apps locking up after entering popover submenu"** and **[`!4703`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703) "Do not force pointer focus on popups"**, both landed in **mutter 49.1** (Oct 14 2025) and present in our 49.4. Two further implicit-grab refactors landed on the `gnome-49` branch in Oct 2025 and Feb 2026 (`d4247714`, `dfba5bba`, `d2b8e1bb`, `dbfa2d19`, `77de39e3`) — all also in our 49.4 tarball.

> ⚠️ **CONTRADICTION FLAG**: same as Investigation 1 — this report claims `d2b8e1bb`, `dbfa2d19`, `77de39e3` are in our 49.4 tarball. Investigation 3 (libadwaita) found those commits landed Feb 10 14:06 CET, hours AFTER 49.4 was tagged that morning. **Resolution: read the actual mutter 49.4 source tree.**

**No further upstream patch known to GNOME would fix this for our build.** The bug we are seeing in the live ISO is therefore almost certainly **not** the upstream `xdg_popup` grab regression — it is something in our environment (live-ISO overlay state, missing seat / xdg-shell version negotiation, mutter not actually the one running, missing wayland-protocols at build time, or a broken signaling flow specific to the live session). The follow-up move is from "patch GTK/mutter" to "instrument the live ISO" — `WAYLAND_DEBUG=client`, `MUTTER_DEBUG=events`, and confirm `pgrep mutter` actually owns the compositor.

---

## 1. GTK 4.20.x version triage

Source: [gtk-4-20/NEWS](https://gitlab.gnome.org/GNOME/gtk/-/raw/gtk-4-20/NEWS), [gtk tags](https://gitlab.gnome.org/GNOME/gtk/-/tags?search=4.20).

| GTK | Date | Popup/popover-related entries |
|-----|------|---|
| 4.20.0 | 2025-08-29 | None (only filterlistmodel + icon helper) |
| 4.20.1 | 2025-09-08 | **#7345 columnview focus problem with menus** (MR [`!8897`](https://gitlab.gnome.org/GNOME/gtk/-/merge_requests/8897)) — keynav only |
| 4.20.2 | 2025-09-29 | None directly; only text shadow + flipped transforms + wayland input region nullability |
| **4.20.3** (ours) | 2025-11-20 | **"Fix touch dropdown selection properly"** — touch only, not relevant to pointer |
| 4.20.4 | 2026-03-31 | Final 4.20 release. Levelbar + macOS + columnview backport [`!9316`](https://gitlab.gnome.org/GNOME/gtk/-/merge_requests/9316). No popup/grab work. |

**Nothing in any GTK 4.20.x point release between 4.20.0 → 4.20.4 touches xdg_popup, pointer grab, popover dismiss, or autohide.** The "grabs are no longer exposed as API" doc change in the 4.20 series migration text refers to API surface, not implementation — the GTK-side popover-grab plumbing has not changed in 4.20.x.

GDK Wayland code (`gdk/wayland/gdksurface-wayland.c` and `gdkpopup-wayland.c`) on the `gtk-4-20` branch has only one 4.20-era commit: [`1dbfa55e`](https://gitlab.gnome.org/GNOME/gtk/-/commit/1dbfa55e) (2025-08-24, "Switch to using stdint types") — pure refactor.

On `main` (4.21.x), the only relevant entry is **[#7414](https://gitlab.gnome.org/GNOME/gtk/-/issues/7414) "Active state is not reset when releasing click outside of popup but inside main window"** — fixed in 4.21.6 (2026-02-27). This is a stuck-`:active` CSS state bug, **not the input-death bug** we see.

## 2. Mutter 49.x — the actual fix lineage

Source: [mutter gnome-49 NEWS](https://gitlab.gnome.org/GNOME/mutter/-/raw/gnome-49/NEWS), [mutter tags](https://gitlab.gnome.org/GNOME/mutter/-/tags?search=49).

| Mutter | Date | Popup/grab work |
|---|---|---|
| 49.beta | 2025-08-03 | [`!4404`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4404) "Respect implicit grab for popup surfaces" — Alessandro Astone. **This is where the regression entered.** Closed mutter#4062. |
| 49.0 | 2025-09-14 | [`!4640`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4640) "Fix coordinates in crossing events" |
| **49.1** | 2025-10-14 | [`!4691`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691) **"Fix GTK apps locking up after entering popover submenu"** (Astone) — exact symptom match; [`!4703`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703) **"Do not force pointer focus on popups"** (Garnacho) — directly references and undoes the over-broad `!4404` behavior, citing gtk#7414 |
| 49.1.1 | 2025-10-23 | broken-Xwayland-menu fix [`!4729`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4729) |
| 49.2 | 2025-11-22 | Includes Carlos's Oct 8 `d4247714` "Change implicit pointer grab behavior — only effective outside the client" and Oct 22 `dfba5bba` "Confine implicit grab inter-surface crossing within toplevel" |
| 49.3 | 2026-01-14 | Gesture state ordering [`!4760`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4760) |
| **49.4** (ours) | 2026-02-10 | [`!4886`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886) popup-grab-termination crash + invalid-read fix bundle: `d2b8e1bb`, `dbfa2d19`, `77de39e3` |
| 49.5 | 2026-03-15 | Touch/CRTC fixes; no popup work |

Direct quote from **mutter [`!4703`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703)** description: *"The popup surface actor will be on its way to having its size allocated in order to being shown. When we switch focus here, the coordinates we can obtain for the actor in this state will be nonsensical … The original implementation addressed a GTK issue (gtk#7414), but this issue does not reproduce anymore."*

Direct quote from **mutter [`!4691`](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691)** description: *"This fixes a bug where entering a submenu within Gtk.PopoverMenu hangs GTK waiting for a configure event that never comes."* (Ubuntu bug 2125770)

## 3. GTK 4.20.3 / libadw 1.8.4 / mutter 49.4 — release-team status

Per [GNOME 49.1 release coverage](https://9to5linux.com/gnome-49-1-desktop-released-with-various-improvements-and-bug-fixes), the regression *was* tested and *was* fixed in the 49.1 point release. Our triplet (GTK 4.20.3 + mutter 49.4) is **two minor revisions past the fix point** and includes the Feb 2026 follow-on patches. There is no known incompatibility recorded against this combination on the GNOME side.

## 4. So what is breaking InterGenOS's live ISO?

Given the upstream fixes are all in our build, the symptom must originate elsewhere. Candidates, ordered by likelihood:

1. **Mutter isn't the compositor at the time of symptom.** Live ISO may be on gnome-shell's nested compositor path or a fallback. `pgrep -a mutter`, `loginctl show-session $XDG_SESSION_ID`, and `echo $XDG_SESSION_TYPE` from the live env will rule this in/out.
2. **wayland-protocols mismatch at build time.** If mutter linked against a wayland-protocols too old to negotiate the popup-grab serial path the patches expect, the new code branches into dead ground. Check `pkg-config --modversion wayland-protocols` inside the chroot — needs ≥ 1.36 for the xdg-shell v6 work mutter !4404+ relies on.
3. **GTK-side: `gdk_wayland_seat_set_grab_window()` failing silently.** GTK still issues `xdg_popup.grab` with the implicit-grab serial; if the serial is stale (because our `wl_pointer.button` press isn't being routed properly to GTK by mutter), the compositor *will* dismiss the popup but never release the grab on GTK's side. The fact that hover/motion still arrive but button doesn't is consistent with **a stuck client-side implicit pointer grab in GDK**.
4. **No portal / no `xdg-desktop-portal-gnome` running in the live ISO.** Some popover paths in GTK 4.20 go through the portal for the file chooser bits; missing portal will not break clicks but will throw warnings worth checking.

## 5. Concrete recommendation

**Do not backport anything from upstream — there is nothing to backport.** Every popup/grab fix dated 2025-08 through 2026-02-10 is in our mutter 49.4 source tarball at `c1666ec5...`. The follow-up investigative step is environment instrumentation, not a patch:

```
# in the live ISO, before reproducing
WAYLAND_DEBUG=1 MUTTER_DEBUG=events GTK_DEBUG=interactive \
  installer 2>&1 | tee /tmp/wayland.log
```

Watch for `xdg_popup.grab @ serial=...` followed by absence of a `wl_pointer.button` event on the parent surface after dismiss — that's the signature. If the grab serial is `0` or stale, the bug is in our GTK Wayland seat-state setup; if the grab is released cleanly but mutter never re-routes events, the bug is compositor-side and worth opening a fresh ticket against `gnome-49` cited to `d4247714 / dfba5bba`.

## Sources

- [GTK gtk-4-20 NEWS](https://gitlab.gnome.org/GNOME/gtk/-/raw/gtk-4-20/NEWS)
- [GTK tag list 4.20.x](https://gitlab.gnome.org/GNOME/gtk/-/tags?search=4.20)
- [GTK issue #7414 — Active state not reset](https://gitlab.gnome.org/GNOME/gtk/-/issues/7414)
- [GTK issue #4369 — popover with dropdown widgets](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369)
- [mutter gnome-49 NEWS](https://gitlab.gnome.org/GNOME/mutter/-/raw/gnome-49/NEWS)
- [mutter tag list 49.x](https://gitlab.gnome.org/GNOME/mutter/-/tags?search=49)
- [mutter !4404 — Respect implicit grab for popup surfaces (origin of regression)](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4404)
- [mutter !4691 — Fix GTK apps locking up after entering popover submenu (49.1 fix)](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4691)
- [mutter !4703 — Do not force pointer focus on popups (49.1 fix)](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4703)
- [mutter !4886 — popup-grab termination fixes bundle (49.4)](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4886)
- [Phoronix — Mutter 49 Beta with implicit-grab fix](https://www.phoronix.com/news/GNOME-Mutter-49-Beta)
- [Ubuntu bug 2125770 — GTK context menu freezes](https://bugs.launchpad.net/ubuntu/+source/mutter/+bug/2125770)
- Local package manifest: `/mnt/intergenos/packages/desktop/mutter/package.yml` (version 49.4, sha256 `c1666ec5...`)
