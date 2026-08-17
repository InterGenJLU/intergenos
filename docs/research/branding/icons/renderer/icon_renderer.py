#!/usr/bin/env python3
"""
InterGenOS Icon Renderer — Programmatic bitmap generation.

Renders dark glass icons with blue edge glow matching the approved
concept and locked render spec. No AI generation, no background
removal — pure engineering.

Approach:
  1. Draw the icon shape as a filled path (Cairo)
  2. Create multi-radius gaussian blur glow from the edge
  3. Add diagonal glass reflection
  4. Add edge highlight line
  5. Composite on transparent/black background

Every parameter from the render spec is directly in code.
"""

import cairo
import numpy as np
from scipy.ndimage import gaussian_filter
from PIL import Image
from pathlib import Path

# ================================================================
# Render Spec Constants (from the locked spec)
# ================================================================

# Canvas
SIZE = 512  # render at 2x, downscale for anti-aliasing

# Colors (render spec §2 + §3)
BODY_TOP = (0.07, 0.10, 0.18)       # #121a2f — slight lift
BODY_MID = (0.04, 0.07, 0.13)       # #0b1222 — main body
BODY_BOT = (0.024, 0.04, 0.07)      # #060a12 — deep void

BLUE = (0.0, 0.6, 1.0)              # #0099FF
BLUE_BRIGHT = (0.2, 0.69, 1.0)      # #33b1ff
BLUE_HIGHLIGHT = (0.4, 0.8, 1.0)    # #66ccff

# Glow stack (render spec §3)
GLOW_WIDE_BLUR = 28                  # wide ambient (stddev in pixels at 512)
GLOW_WIDE_OPACITY = 0.07
GLOW_WIDE_WIDTH = 12

GLOW_MID_BLUR = 14
GLOW_MID_OPACITY = 0.16
GLOW_MID_WIDTH = 5

GLOW_TIGHT_BLUR = 5
GLOW_TIGHT_OPACITY = 0.6
GLOW_TIGHT_WIDTH = 2.5

# Structural edge (render spec §4)
STRUCT_EDGE_OPACITY = 0.40
STRUCT_EDGE_WIDTH = 1.5

# Reflection (render spec §5)
REFLECTION_ANGLE = 12        # degrees
REFLECTION_PEAK_OPACITY = 0.25
REFLECTION_EDGE_LINE_OPACITY = 0.5
REFLECTION_EDGE_LINE_WIDTH = 1.5

# Energy hierarchy (render spec §6)
ENERGY_LEVELS = {
    0: 0.20,  # idle
    1: 0.35,  # standard (Edge-Lit)
    2: 0.50,  # special (Pulse-Core)
}

# Directionality (render spec §3) — bottom is brightest
DIRECTION_TOP = 0.30
DIRECTION_MID = 0.65
DIRECTION_BOTTOM = 1.0


def create_surface(size=SIZE):
    """Create a Cairo ARGB surface."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.set_antialias(cairo.ANTIALIAS_BEST)
    return surface, ctx


def surface_to_numpy(surface):
    """Convert Cairo surface to numpy RGBA array."""
    buf = surface.get_data()
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(
        surface.get_height(), surface.get_width(), 4).copy()
    # Cairo is BGRA, convert to RGBA
    arr[:, :, [0, 2]] = arr[:, :, [2, 0]]
    return arr


def numpy_to_surface(arr):
    """Convert numpy RGBA array back to Cairo surface."""
    h, w = arr.shape[:2]
    rgba = arr.copy()
    rgba[:, :, [0, 2]] = rgba[:, :, [2, 0]]  # RGBA -> BGRA
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    buf = np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(h, w, 4)
    buf[:] = rgba
    surface.mark_dirty()
    return surface


def draw_folder_path(ctx, s=SIZE, margin=0.12):
    """Draw the canonical folder shape path.
    Returns the path for reuse."""
    m = s * margin  # margin from edges
    w = s - 2 * m   # body width
    h_body = s * 0.52  # body height
    r = s * 0.04    # corner radius
    tab_w = w * 0.35
    tab_h = s * 0.04   # tab height (subtle!)

    # Starting positions
    x0 = m
    y_tab = m + (s * 0.12)  # top of tab area
    y_body = y_tab + tab_h  # top of main body
    y_bottom = y_tab + h_body
    x1 = m + w

    ctx.new_path()

    # Start at left side, at body top
    ctx.move_to(x0, y_body)

    # Up to tab area (left side climbs up)
    ctx.line_to(x0, y_tab + r)
    ctx.curve_to(x0, y_tab, x0 + r, y_tab, x0 + r, y_tab)

    # Tab top edge
    ctx.line_to(x0 + tab_w - r, y_tab)

    # Tab curves down to body top
    ctx.curve_to(x0 + tab_w, y_tab, x0 + tab_w + r, y_tab + tab_h * 0.6,
                 x0 + tab_w + r * 2, y_body)

    # Body top edge continues to right
    ctx.line_to(x1 - r, y_body)

    # Top-right corner
    ctx.curve_to(x1, y_body, x1, y_body + r, x1, y_body + r)

    # Right side down
    ctx.line_to(x1, y_bottom - r)

    # Bottom-right corner
    ctx.curve_to(x1, y_bottom, x1 - r, y_bottom, x1 - r, y_bottom)

    # Bottom edge
    ctx.line_to(x0 + r, y_bottom)

    # Bottom-left corner
    ctx.curve_to(x0, y_bottom, x0, y_bottom - r, x0, y_bottom - r)

    # Left side back up
    ctx.close_path()

    return y_body, y_bottom


def draw_front_panel_path(ctx, y_crease, y_bottom, s=SIZE, margin=0.12):
    """Draw the front panel (below the crease)."""
    m = s * margin
    w = s - 2 * m
    r = s * 0.04
    x0, x1 = m, m + w

    ctx.new_path()
    ctx.move_to(x0, y_crease)
    ctx.line_to(x1, y_crease)
    ctx.line_to(x1, y_bottom - r)
    ctx.curve_to(x1, y_bottom, x1 - r, y_bottom, x1 - r, y_bottom)
    ctx.line_to(x0 + r, y_bottom)
    ctx.curve_to(x0, y_bottom, x0, y_bottom - r, x0, y_bottom - r)
    ctx.close_path()


def render_glow_layer(shape_func, args, blur_radius, line_width,
                      color=BLUE, opacity=1.0, size=SIZE,
                      directional=True):
    """Render a single glow layer: draw shape outline, then blur."""
    surface, ctx = create_surface(size)

    # Draw the shape outline
    shape_func(ctx, *args)
    ctx.set_source_rgba(color[0], color[1], color[2], opacity)
    ctx.set_line_width(line_width)
    ctx.stroke()

    # Convert to numpy and apply gaussian blur
    arr = surface_to_numpy(surface)

    # Blur each channel
    for c in range(4):
        arr[:, :, c] = gaussian_filter(arr[:, :, c].astype(float),
                                        sigma=blur_radius).astype(np.uint8)

    # Apply directional bias (bottom brighter, top dimmer)
    if directional:
        h = arr.shape[0]
        for y in range(h):
            t = y / h  # 0 at top, 1 at bottom
            # Interpolate from DIRECTION_TOP to DIRECTION_BOTTOM
            factor = DIRECTION_TOP + (DIRECTION_BOTTOM - DIRECTION_TOP) * t
            arr[y, :, 3] = (arr[y, :, 3].astype(float) * factor).clip(0, 255).astype(np.uint8)

    return arr


def render_body(y_body, y_bottom, size=SIZE, margin=0.12, body_opacity=0.95):
    """Render the folder body fill with gradient."""
    surface, ctx = create_surface(size)
    m = size * margin

    # Back panel gradient
    draw_folder_path(ctx, size, margin)
    grad = cairo.LinearGradient(0, m, 0, y_bottom)
    grad.add_color_stop_rgba(0.0, *BODY_TOP, body_opacity)
    grad.add_color_stop_rgba(0.5, *BODY_MID, body_opacity)
    grad.add_color_stop_rgba(1.0, *BODY_BOT, body_opacity)
    ctx.set_source(grad)
    ctx.fill()

    # Front panel (slightly lighter, semi-transparent for glass effect)
    draw_front_panel_path(ctx, y_body + (y_bottom - y_body) * 0.22, y_bottom, size, margin)
    front_grad = cairo.LinearGradient(0, y_body, 0, y_bottom)
    front_grad.add_color_stop_rgba(0.0, BODY_TOP[0] + 0.02, BODY_TOP[1] + 0.02,
                                    BODY_TOP[2] + 0.04, body_opacity * 0.7)
    front_grad.add_color_stop_rgba(1.0, *BODY_MID, body_opacity * 0.8)
    ctx.set_source(front_grad)
    ctx.fill()

    return surface_to_numpy(surface)


def render_reflection(y_crease, y_bottom, size=SIZE, margin=0.12):
    """Render the diagonal glass reflection + edge highlight."""
    surface, ctx = create_surface(size)
    m = size * margin
    w = size - 2 * m
    x0, x1 = m, m + w

    # Clip to front panel
    draw_front_panel_path(ctx, y_crease, y_bottom, size, margin)
    ctx.clip()

    # Diagonal reflection band
    # Angle ~12 degrees, covering upper ~25% of front panel
    ref_height = (y_bottom - y_crease) * 0.30
    ctx.move_to(x0, y_crease)
    ctx.line_to(x1, y_crease)
    ctx.line_to(x0, y_crease + ref_height * 2.5)
    ctx.close_path()

    grad = cairo.LinearGradient(x0, y_crease, x0 + w * 0.6, y_crease + ref_height * 2)
    grad.add_color_stop_rgba(0.0, 1, 1, 1, REFLECTION_PEAK_OPACITY)
    grad.add_color_stop_rgba(0.35, 1, 1, 1, REFLECTION_PEAK_OPACITY * 0.2)
    grad.add_color_stop_rgba(0.6, 1, 1, 1, 0)
    ctx.set_source(grad)
    ctx.fill()

    # Edge highlight line at top of reflection (CRITICAL for glass read)
    ctx.move_to(x0 + 4, y_crease + 1)
    ctx.line_to(x1 - 4, y_crease + 1)
    ctx.set_source_rgba(1, 1, 1, REFLECTION_EDGE_LINE_OPACITY)
    ctx.set_line_width(REFLECTION_EDGE_LINE_WIDTH)
    ctx.stroke()

    return surface_to_numpy(surface)


def render_crease_glow(y_crease, size=SIZE, margin=0.12):
    """Render the crease line with glow."""
    surface, ctx = create_surface(size)
    m = size * margin
    w = size - 2 * m

    ctx.move_to(m + 4, y_crease)
    ctx.line_to(m + w - 4, y_crease)
    ctx.set_source_rgba(*BLUE, 0.5)
    ctx.set_line_width(2)
    ctx.stroke()

    arr = surface_to_numpy(surface)
    for c in range(4):
        arr[:, :, c] = gaussian_filter(arr[:, :, c].astype(float),
                                        sigma=8).astype(np.uint8)

    # Also draw the sharp crease on top
    surface2, ctx2 = create_surface(size)
    ctx2.move_to(m + 4, y_crease)
    ctx2.line_to(m + w - 4, y_crease)
    ctx2.set_source_rgba(*BLUE, 0.25)
    ctx2.set_line_width(1)
    ctx2.stroke()

    sharp = surface_to_numpy(surface2)
    arr = composite_arrays(arr, sharp)

    return arr


def render_structural_edge(y_body, y_bottom, size=SIZE, margin=0.12):
    """Render the thin structural edge."""
    surface, ctx = create_surface(size)
    draw_folder_path(ctx, size, margin)
    ctx.set_source_rgba(*BLUE, STRUCT_EDGE_OPACITY)
    ctx.set_line_width(STRUCT_EDGE_WIDTH)
    ctx.stroke()
    return surface_to_numpy(surface)


def composite_arrays(base, overlay):
    """Alpha composite overlay onto base using PIL (reliable)."""
    base_img = Image.fromarray(base, 'RGBA')
    over_img = Image.fromarray(overlay, 'RGBA')
    base_img = Image.alpha_composite(base_img, over_img)
    return np.array(base_img)


def render_pulse_core(y_crease, y_bottom, size=SIZE, margin=0.12):
    """Render the Pulse-Core energy center for Level 2 folders."""
    surface, ctx = create_surface(size)
    m = size * margin
    w = size - 2 * m

    cx = m + w * 0.52  # slightly right of center
    cy = (y_crease + y_bottom) / 2 + (y_bottom - y_crease) * 0.05

    # Clip to folder shape
    draw_folder_path(ctx, size, margin)
    ctx.clip()

    # Outer radiance
    grad = cairo.RadialGradient(cx, cy, 0, cx, cy, size * 0.15)
    grad.add_color_stop_rgba(0.0, *BLUE_HIGHLIGHT, 0.7)
    grad.add_color_stop_rgba(0.3, *BLUE, 0.3)
    grad.add_color_stop_rgba(0.7, *BLUE, 0.08)
    grad.add_color_stop_rgba(1.0, *BLUE, 0.0)
    ctx.set_source(grad)
    ctx.paint()

    # Horizontal flare
    ctx.move_to(m + 10, cy)
    ctx.line_to(m + w - 10, cy)
    ctx.set_source_rgba(*BLUE_BRIGHT, 0.3)
    ctx.set_line_width(3)
    ctx.stroke()

    arr = surface_to_numpy(surface)
    # Blur the outer radiance and flare
    for c in range(4):
        arr[:, :, c] = gaussian_filter(arr[:, :, c].astype(float), sigma=6).astype(np.uint8)

    # Draw bright center on top (no blur)
    surface2, ctx2 = create_surface(size)
    draw_folder_path(ctx2, size, margin)
    ctx2.clip()

    # Mid core
    grad2 = cairo.RadialGradient(cx, cy, 0, cx, cy, size * 0.03)
    grad2.add_color_stop_rgba(0.0, *BLUE_HIGHLIGHT, 0.9)
    grad2.add_color_stop_rgba(1.0, *BLUE, 0.4)
    ctx2.set_source(grad2)
    ctx2.arc(cx, cy, size * 0.03, 0, 2 * 3.14159)
    ctx2.fill()

    # Hot center
    ctx2.arc(cx, cy, size * 0.008, 0, 2 * 3.14159)
    ctx2.set_source_rgba(0.5, 0.9, 1.0, 0.95)
    ctx2.fill()

    sharp = surface_to_numpy(surface2)
    return composite_arrays(arr, sharp)


def render_folder(output_path, energy_level=1, pulse_core=False,
                  output_sizes=[256, 128, 64, 48]):
    """Render a complete folder icon."""

    s = SIZE
    margin = 0.12

    # Get shape coordinates
    temp_surface, temp_ctx = create_surface(s)
    y_body, y_bottom = draw_folder_path(temp_ctx, s, margin)
    y_crease = y_body + (y_bottom - y_body) * 0.22

    energy_mult = ENERGY_LEVELS.get(energy_level, 0.35)

    # Build the glow stack (bottom-up compositing)
    # Start with empty canvas
    result = np.zeros((s, s, 4), dtype=np.uint8)

    # 1. Wide ambient glow
    wide_glow = render_glow_layer(
        draw_folder_path, (s, margin),
        blur_radius=GLOW_WIDE_BLUR,
        line_width=GLOW_WIDE_WIDTH,
        opacity=GLOW_WIDE_OPACITY * (energy_mult / 0.35),
        size=s)
    result = composite_arrays(result, wide_glow)

    # 2. Mid glow
    mid_glow = render_glow_layer(
        draw_folder_path, (s, margin),
        blur_radius=GLOW_MID_BLUR,
        line_width=GLOW_MID_WIDTH,
        opacity=GLOW_MID_OPACITY * (energy_mult / 0.35),
        size=s)
    result = composite_arrays(result, mid_glow)

    # 3. Tight glow
    tight_glow = render_glow_layer(
        draw_folder_path, (s, margin),
        blur_radius=GLOW_TIGHT_BLUR,
        line_width=GLOW_TIGHT_WIDTH,
        color=BLUE_BRIGHT,
        opacity=GLOW_TIGHT_OPACITY * (energy_mult / 0.35),
        size=s)
    result = composite_arrays(result, tight_glow)

    # 4. Body fill
    body = render_body(y_body, y_bottom, s, margin)
    result = composite_arrays(result, body)

    # 5. Crease glow
    crease = render_crease_glow(y_crease, s, margin)
    result = composite_arrays(result, crease)

    # 6. Diagonal reflection
    reflection = render_reflection(y_crease, y_bottom, s, margin)
    result = composite_arrays(result, reflection)

    # 7. Structural edge
    edge = render_structural_edge(y_body, y_bottom, s, margin)
    result = composite_arrays(result, edge)

    # 8. Pulse core (if Level 2)
    if pulse_core:
        core = render_pulse_core(y_crease, y_bottom, s, margin)
        result = composite_arrays(result, core)

    # Convert to PIL and save at requested sizes
    img = Image.fromarray(result)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for size in output_sizes:
        resized = img.resize((size, size), Image.LANCZOS)
        suffix = f"_{size}" if size != output_sizes[0] else ""
        path = output_path.parent / f"{output_path.stem}{suffix}.png"
        resized.save(path)
        print(f"  {path.name} ({size}x{size})")


if __name__ == "__main__":
    out = Path("docs/research/branding/icons/renderer/output")

    print("Rendering Edge-Lit folder (Level 1)...")
    render_folder(out / "folder-edge-lit.png", energy_level=1)

    print("\nRendering Pulse-Core folder (Level 2)...")
    render_folder(out / "folder-pulse-core.png", energy_level=2, pulse_core=True)

    print("\nDone.")
