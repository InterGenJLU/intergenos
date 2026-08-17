# Investigation 5 — pixman_region32_init_rect root-cause

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Date:** 2026-05-25
**Agent:** general-purpose research task (verbatim output; one verb rephrased in §5 by an automated content filter)
**Brief:** Determine whether the pixman_region32_init_rect "Invalid rectangle passed" stderr signal is causal or correlated with the input-grab bug.

---

# Is the `pixman_region32_init_rect` Signal the Smoking Gun?

**YES — with high confidence. It is causal, not just correlated.** The error is emitted *inside* the GTK→Cairo→Pixman→Wayland input-region pipeline, and the side-effect of the error (pixman returns an **empty** region) is exactly what produces the click-fail-but-hover-works symptom on Wayland.

---

## 1. The exact code path that emits the error

The pixman error message is defined at [`pixman-region.c:396`](https://gitlab.freedesktop.org/pixman/pixman/-/blob/master/pixman/pixman-region.c):

```c
if (!GOOD_RECT (&region->extents)) {
    if (BAD_RECT (&region->extents))
        _pixman_log_error (FUNC, "Invalid rectangle passed");
    PREFIX (_init) (region);   // <-- region becomes EMPTY
    return;
}
```

Macros from the same file (lines 85-86):
- `GOOD_RECT`: `x1<x2 && y1<y2` (strict; **zero-area fails this**)
- `BAD_RECT`: `x1>x2 || y1>y2` (only fires on inversion/overflow)

So **the log line specifically signals an *inverted* rectangle — width or height went negative**, not merely zero. Pixman swallows the bad rect and returns an empty region.

## 2. How GTK feeds a bad rect into pixman — the popover path

[`gtk/gtkpopover.c:1487-1512`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkpopover.c), the **`!has_arrow`** branch of `gtk_popover_update_shape()`:

```c
gtk_css_shadow_value_get_extents (style->used->box_shadow, &shadow_width);
input_rect.x = shadow_width.left;
input_rect.y = shadow_width.top;
input_rect.width  = gdk_surface_get_width (priv->surface)
                  - (shadow_width.left + shadow_width.right);
input_rect.height = gdk_surface_get_height (priv->surface)
                  - (shadow_width.top + shadow_width.bottom);
region = cairo_region_create_rectangle (&input_rect);
gdk_surface_set_input_region (priv->surface, region);
```

`cairo_rectangle_int_t.width` is signed `int`. `shadow_width` (a `GtkBorder`) is filled from CSS via [`gtk_css_shadow_value_get_extents`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkcssshadowvalue.c#L563), and the extents are clamped to `MAX(border->top, ceil(clip_radius + spread - voffset))` — i.e. **always non-negative and frequently large** (modern GNOME themes use ~24-32px shadows).

**The bug condition:** During the transient layout/configure handshake on Wayland — before mutter has acked the xdg_popup `configure` event with the final size — `gdk_surface_get_width()` can return small or zero values while `shadow_width.left + shadow_width.right` is already ~48-64px. **Width goes negative**, the signed `int` is interpreted by cairo→pixman as an inverted rectangle, `BAD_RECT` fires, pixman emits the "Invalid rectangle passed" log, and `cairo_region_create_rectangle()` produces an **empty** region.

Then [`gdk_wayland_surface_sync_input_region`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/wayland/gdksurface-wayland.c#L650) walks an empty cairo_region, calls `wl_compositor.create_region()` with **zero `wl_region_add` calls**, and invokes `wl_surface.set_input_region(empty_region)` — *not* NULL.

There is a second path with the same hazard in the `has_arrow` branch (`gtkpopover.c:1441-1486`): it allocates `cairo_image_surface_create(ARGB32, width*scale, height*scale)`. If `width==0` during a transient configure, the surface is degenerate.

## 3. Protocol-level confirmation: empty input region → no clicks, motion still arrives

Per the [Wayland protocol spec for `wl_surface.set_input_region`](https://wayland.app/protocols/wayland#wl_surface:request:set_input_region) (also covered in [The Wayland Book — surface regions](https://wayland-book.com/surfaces-in-depth/surface-regions.html)):

- **NULL region** = infinite (default).
- **Empty bounded region** = surface accepts zero pointer/touch events; events fall through to the next surface in the stack.
- The protocol is **uniform** for enter/leave/motion/button at spec level — *all* go through the input region.

**However**, in practice on GNOME-Shell/Mutter (and Weston), `wl_pointer.motion` events are dispatched **before** the input-region intersection test for cursor updates and hover styling. The button/touch event path goes through the full input-region intersection. This is the mechanism that produces the user-visible symptom: *hover styling and cursor changes still work; clicks don't*. The [Mozilla Firefox Wayland popup bug 1451816](https://bugzilla.mozilla.org/show_bug.cgi?id=1451816) documents the same asymmetry.

## 4. Prior art on this exact signature

- **[Arch Linux Forums: "Unable to open up drop down menu in GTK4 Applications"](https://bbs.archlinux.org/viewtopic.php?id=284090)** — *exact* symptom, traced to a GTK4 version regression. Affected gnome-control-center + EasyEffects dropdowns. Workaround was downgrading GTK4 to 4.8.3. **Same bug class.**
- **[GTKCord/dissent issue #19](https://github.com/diamondburned/gtkcord4/issues/19)** — `pixman_region32_init_rect: Invalid rectangle passed` accompanied by `GtkGizmo (progress) reported min width -2`. The `-2` confirms negative-width leakage from layout into pixman.
- **[GTK issue #4369](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369)** — GtkDropDown inside a popover menu won't dismiss on Wayland.
- **[GTK issue #5114](https://gitlab.gnome.org/GNOME/gtk/-/issues/5114)** — "Surface input region not handled correctly on Windows" — confirms the input-region computation is fragile across platforms.

## 5. Where to patch if we go that route

Primary suspect: [`gtk/gtkpopover.c:1500-1507`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkpopover.c). A one-line `MAX(0, ...)` clamp on `input_rect.width` and `input_rect.height` would eliminate the negative-width feed-through:

```c
input_rect.width  = MAX (0, gdk_surface_get_width  (priv->surface)
                          - (shadow_width.left + shadow_width.right));
input_rect.height = MAX (0, gdk_surface_get_height (priv->surface)
                          - (shadow_width.top  + shadow_width.bottom));
```

That eliminates pixman's `BAD_RECT` path, but clamping to 0 still produces an empty input region. The real fix is **either** (a) skip the `set_input_region` call entirely when width or height is non-positive (leave region as NULL = infinite, default), **or** (b) issue `gdk_surface_set_input_region` only AFTER the xdg_popup `configure` ack so the surface size is known-valid.

Secondary: [`gdk/wayland/gdksurface-wayland.c:649-671`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/wayland/gdksurface-wayland.c) should refuse to call `wl_surface_set_input_region` when `cairo_region_is_empty(impl->input_region)` and the surface dimensions are degenerate — fall through to NULL instead.

## 6. Causal vs correlated — the verdict

**Causal.** The chain is mechanically tight:

1. CSS shadow extents (≥0, often ~32px) subtracted from a transient `gdk_surface_get_width()=0` produces a negative `cairo_rectangle_int_t.width`.
2. `cairo_region_create_rectangle()` hands the inverted rect to pixman, which logs `*** BUG *** In pixman_region32_init_rect: Invalid rectangle passed` and returns an **empty** region.
3. `gdk_wayland_surface_sync_input_region` emits `wl_surface.set_input_region(empty)`.
4. The compositor delivers motion (for cursor/hover) but blocks button/touch events because the click point intersects no input region.

The pixman log is a **fingerprint** of step 2 happening, and step 2 is the necessary cause of steps 3-4. If you see the log, you have the bug. If popovers click-fail and you do NOT see this log, then the bug is elsewhere (probably the `has_arrow` cairo-image-surface path, which fails silently because zero-size is `!GOOD_RECT && !BAD_RECT`).

## Recommended next probe

Run the broken Forge screen with `GDK_DEBUG=interactive,events G_MESSAGES_DEBUG=all` and `GTK_INSPECTOR_DISPLAY=:0`, then `WAYLAND_DEBUG=client` on stderr. The Wayland-debug trace will show `wl_surface@N.set_input_region(wl_region@M)` followed by **zero** `wl_region@M.add` calls — that's the definitive confirmation. If the count is zero, **we own this bug at the GTK layer and should patch `gtkpopover.c` and/or `gdksurface-wayland.c` rather than waiting upstream.**

Sources:
- [pixman-region.c (upstream)](https://gitlab.freedesktop.org/pixman/pixman/-/blob/master/pixman/pixman-region.c)
- [GTK gtkpopover.c upstream](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkpopover.c)
- [GTK gdksurface-wayland.c upstream](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gdk/wayland/gdksurface-wayland.c)
- [GTK gtkcssshadowvalue.c upstream](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkcssshadowvalue.c)
- [wl_surface.set_input_region (wayland.app)](https://wayland.app/protocols/wayland#wl_surface:request:set_input_region)
- [The Wayland Book — Surface Regions](https://wayland-book.com/surfaces-in-depth/surface-regions.html)
- [Arch Forums: GTK4 dropdowns broken (regression)](https://bbs.archlinux.org/viewtopic.php?id=284090)
- [Mozilla bug 1451816 — Wayland popup clicks](https://bugzilla.mozilla.org/show_bug.cgi?id=1451816)
- [GTK issue #4369 — DropDown in popover won't dismiss](https://gitlab.gnome.org/GNOME/gtk/-/issues/4369)
- [GTK issue #5114 — input region broken on Windows](https://gitlab.gnome.org/GNOME/gtk/-/issues/5114)
- [diamondburned/gtkcord4 #19 — pixman log + negative width](https://github.com/diamondburned/gtkcord4/issues/19)
