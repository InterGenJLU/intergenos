# Research Resolution — primary-source verification complete

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Status:** ✅ **FIX VERIFIED LIVE** — patched libgtk-4.so deployed to live test overlay, log-out/in to load it, Forge KeyboardLocale ComboRow popovers now respond to clicks. Diagnosis confirmed in production.

## Verification 1 — popup-grab teardown UAF fix IS in our 49.4

Extracted `/mnt/intergenos/build/sources/mutter-49.4.tar.xz` → `meta-wayland-popup.c`.

**Git blob hash of our file: `288b577bce820a55d1210cd9ba41257828dbe82a`**

The d2b8e1bb upstream patch header reads:
```
diff --git a/src/wayland/meta-wayland-popup.c b/src/wayland/meta-wayland-popup.c
index 801289e00b0..288b577bce8 100644
```

**Our blob hash matches the patch's POST-image (`288b577bce8`).** The fix is in our build.

Confirmed by content inspection — line 224 of our 49.4 popup.c:
```c
if (meta_wayland_popup_surface_finish (popup_surface))
  break;
```
And line 297:
```c
if (!meta_wayland_popup_surface_finish (popup_surface))
  meta_wayland_popup_grab_repick_keyboard_focus (popup_grab);
```

Both are the post-patch shape from d2b8e1bb. **The primary-source check confirms the fix is present; the earlier timestamp-based reasoning was wrong** — likely confused author-date with commit-date on the gnome-49 branch.

49.4 and 49.5 popup.c files are **byte-identical** (verified `diff -u` empty). The 49.5 NEWS doesn't itemize !4886 because the fix was already in 49.4 — 49.5's NEWS focuses on touch/CRTC fixes only.

## Verification 2 — the GTK input-region defect IS our bug

The claim — that `gtk/gtkpopover.c:1500` computes input_rect.width by direct subtraction without a negative guard — confirmed verbatim against our deployed GTK 4.20.3:

```c
// /tmp/gtk-4.20.3/gtk/gtkpopover.c:1502-1507
input_rect.width =
  gdk_surface_get_width (priv->surface) -
  (shadow_width.left + shadow_width.right);
input_rect.height =
  gdk_surface_get_height (priv->surface) -
  (shadow_width.top + shadow_width.bottom);
```

Also confirmed in upstream GTK `main` (fetched via WebFetch): **identical code, no clamp, no guard.** This is a longstanding latent upstream defect that has never been fixed.

## Why our build is uniquely hit

The bug fires when `gdk_surface_get_width()` returns a value smaller than `shadow_width.left + shadow_width.right` during the popover's transient configure handshake on Wayland. Reasons this is rarer on other distros:

  - **Modern GNOME themes** with smaller shadow extents reduce the negative-width window (Adwaita-dark on GNOME 50 reportedly trimmed defaults vs 49)
  - **Mutter 49.1+ configure-handshake speed improvements** (`!4691`) narrow the timing window where surface size is transiently zero
  - **Specific session timing on the live-ISO autologin** likely makes our hit-rate near-100%

That last point is the key — no other distro appears to be reporting this. Our live ISO's specific init sequence (intergenos autologin via GDM, no prior user state, autostart-fired Forge) is probably hitting a configure-handshake race window that's brief in a normal interactive session but persistent in our flow.

## The fix

**Patch our GTK 4.20.3 build** with the fix at `gtk/gtkpopover.c:1502-1507`:

```c
input_rect.width  = MAX (0, gdk_surface_get_width (priv->surface)
                            - (shadow_width.left + shadow_width.right));
input_rect.height = MAX (0, gdk_surface_get_height (priv->surface)
                            - (shadow_width.top + shadow_width.bottom));
```

This eliminates the pixman "Invalid rectangle passed" log. But it still produces an EMPTY input region when surface width < shadow extents — which still kills clicks. So pair the clamp with a guard against emitting `set_input_region` when the region would be empty.

**Two-stage patch** (write as `gtk4-fix-popover-input-region.patch` in `packages/desktop/gtk4/patches/`):

  1. `gtk/gtkpopover.c` — clamp inputs to MAX(0, ...), AND skip `gdk_surface_set_input_region` entirely when width<=0 or height<=0 (leaving region as NULL which is "infinite" per Wayland spec — safe default until a valid size lands).
  2. `gdk/wayland/gdksurface-wayland.c:649-671` — refuse to emit `wl_surface_set_input_region` when `cairo_region_is_empty(impl->input_region)` and surface dimensions are degenerate. Defense-in-depth.

The window-drag bug may share the root cause (if a popover surface's empty input region absorbs subsequent button events including drag-start) OR may be a separate mutter-side issue. Test the GTK patch first; if drag also recovers, single bug. If drag remains broken, investigate further.

## Upstream contribution

This defect is in upstream GTK main as of 2026-05-25. The right thing to do:

  1. Land the fix in our packages/desktop/gtk4 build with a patch file
  2. File a GTK issue at gitlab.gnome.org/GNOME/gtk/-/issues/new referencing our findings + the minimal Adw.ComboRow repro
  3. Submit a merge request to upstream GTK once the fix is verified working in our build

Filing the upstream issue reduces the attack surface for the wider ecosystem, and a popover that silently kills input means the user cannot fully control their own GNOME apps — fixing it upstream is the right outcome.

## Next concrete steps (in order)

  1. **Write the patch** as `packages/desktop/gtk4/patches/gtk4-fix-popover-input-region.patch`. Include both the `MAX(0, ...)` clamp AND the skip-on-empty guard.
  2. **Update `packages/desktop/gtk4/build.sh`** to apply the patch in `configure()`.
  3. **Update `packages/desktop/gtk4/package.yml`** to bump release number (from 1 to 2) so pkm rebuilds.
  4. **Rebuild gtk4 in the build VM chroot**, verify the patched library lands.
  5. **Deploy the patched libgtk-4.so to the live test overlay** (overlay-ephemeral; copy from chroot's `/usr/lib/`).
  6. **Operator validates**: launch Forge fresh, navigate to KeyboardLocale, click a ComboRow. If popover opens + clicks work + subsequent input on the window is fine, fix confirmed.
  7. **If drag is still broken**: separate investigation. Likely candidates: missing `wl_seat` v10 / `xdg_wm_base` v6, gnome-shell ABI, or independent mutter issue.
  8. **File upstream issue** at gitlab.gnome.org/GNOME/gtk/-/issues with our findings + the bare repro from `/tmp/adw_combo_repro.py`.

## What the dossier closes out

The exhaustive research is done. We have:
  - Primary-source confirmation that mutter 49.4 is correctly patched
  - Primary-source confirmation that GTK 4.20.3 has the input-region defect
  - Confirmation that upstream GTK main is also unfixed
  - A specific patch location with line numbers (`gtk/gtkpopover.c:1502-1507`)
  - A specific patch design (clamp + skip-on-empty)
  - A test procedure that returns a definite signal (pixman log gone + popovers click cleanly)
  - A path to upstream contribution

This is no longer trailblazing. It's surgery on a known latent defect with a specific reproduction, a specific code location, and a specific fix.
