#!/usr/bin/env python3
"""
InterGenOS Logo Mark — FINAL assets (hybrid adaptive approach)

Three distinct deliverables, all sharing the same visual identity:

  ICON (full)    — spike with full Q/R/S/T detail, stroke 10.
                   Square canvas, left-aligned layout (short lead-in + spike + trail).
                   Used for rendering at 64 px and above.

  ICON (simple) — simplified spike (Q and T dips removed), stroke 32.
                   Same square canvas and layout.
                   Used for rendering at 48 px and below, where sub-pixel stroke
                   would make the full detail version unreadable.

  LOGO          — wide asymmetric layout (short lead-in + spike + long trail),
                   for the full brand lockup where "InterGenOS" wordmark will
                   sit on top of the right trail baseline.

Geometry (locked in from round 9e):
  qr_span = 32      Q dip → R peak horizontal span
  q_dip = 45        Q dip depth below baseline (= T peak height above)
  peak_height = 176 R peak height above baseline (= S trough depth below)
  All diagonal segments share slope = 6.906
  Left and right halves of the spike are true 180° mirrors of each other.
"""

import cairosvg
import shutil
from pathlib import Path

ROOT = Path("/home/christopher/intergenos/research/branding/marks/final")
SVG_DIR = ROOT / "svg"
PNG_DIR = ROOT / "png"

# Brand palette
BG_BLACK = "#000000"
BLUE = "#0099FF"
WHITE = "#e2e8f0"

# Locked-in geometry
QR_SPAN = 32
Q_DIP = 45
PEAK_HEIGHT = 176
Q_SLOPE = (Q_DIP + PEAK_HEIGHT) / QR_SPAN            # 6.906
ENTRY_SPAN = Q_DIP / Q_SLOPE                          # 6.516
S_DEPTH = PEAK_HEIGHT
DELTA = (2 * PEAK_HEIGHT) / Q_SLOPE                   # 50.967
SPIKE_LEFT_OFFSET = -(QR_SPAN + ENTRY_SPAN)           # -38.52
SPIKE_RIGHT_OFFSET = DELTA + QR_SPAN + ENTRY_SPAN     # +89.48
SPIKE_WIDTH = SPIKE_RIGHT_OFFSET - SPIKE_LEFT_OFFSET  # 128.0
SPIKE_HEIGHT = 2 * PEAK_HEIGHT                         # 352.0

# Icon layout constants
ICON_LEFT_FLAT = 80
LOGO_LEFT_FLAT = 80
LOGO_RIGHT_TRAIL = 780


def full_path(r_x, baseline, left_flat, right_trail):
    """Full Q/R/S/T path — the canonical spike."""
    entry_flat_end = r_x + SPIKE_LEFT_OFFSET
    exit_flat_start = r_x + SPIKE_RIGHT_OFFSET
    return (
        f"M {entry_flat_end - left_flat:.2f} {baseline} "
        f"L {entry_flat_end:.2f} {baseline} "
        f"L {r_x - QR_SPAN:.2f} {baseline + Q_DIP} "
        f"L {r_x:.2f} {baseline - PEAK_HEIGHT} "
        f"L {r_x + DELTA:.2f} {baseline + S_DEPTH} "
        f"L {r_x + DELTA + QR_SPAN:.2f} {baseline - Q_DIP} "
        f"L {exit_flat_start:.2f} {baseline} "
        f"L {exit_flat_start + right_trail:.2f} {baseline}"
    )


def simple_path(r_x, baseline, left_flat, right_trail):
    """Simplified path — Q and T dips removed. For small-size rendering."""
    entry_flat_end = r_x + SPIKE_LEFT_OFFSET
    exit_flat_start = r_x + SPIKE_RIGHT_OFFSET
    return (
        f"M {entry_flat_end - left_flat:.2f} {baseline} "
        f"L {entry_flat_end:.2f} {baseline} "
        f"L {r_x:.2f} {baseline - PEAK_HEIGHT} "
        f"L {r_x + DELTA:.2f} {baseline + S_DEPTH} "
        f"L {exit_flat_start:.2f} {baseline} "
        f"L {exit_flat_start + right_trail:.2f} {baseline}"
    )


def make_svg(viewbox, path_d, stroke, stroke_color=BLUE, bg=None):
    vb_x, vb_y, vb_w, vb_h = viewbox
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x} {vb_y} {vb_w} {vb_h}">']
    if bg is not None:
        parts.append(f'<rect x="{vb_x}" y="{vb_y}" width="{vb_w}" height="{vb_h}" fill="{bg}"/>')
    parts.append(
        f'<path d="{path_d}" stroke="{stroke_color}" stroke-width="{stroke}" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    parts.append('</svg>')
    return "\n".join(parts)


def render(svg_str, out_path, width=None, height=None):
    kwargs = {"bytestring": svg_str.encode(), "write_to": str(out_path)}
    if width is not None:
        kwargs["output_width"] = width
    if height is not None:
        kwargs["output_height"] = height
    cairosvg.svg2png(**kwargs)


def generate():
    # Clean slate
    if SVG_DIR.exists():
        shutil.rmtree(SVG_DIR)
    if PNG_DIR.exists():
        shutil.rmtree(PNG_DIR)
    SVG_DIR.mkdir(parents=True)
    PNG_DIR.mkdir(parents=True)

    # =========================================================================
    # ICON — 512x512 square, left-aligned asymmetric
    # =========================================================================
    icon_side = 512
    baseline_icon = icon_side / 2
    r_x_icon = ICON_LEFT_FLAT - SPIKE_LEFT_OFFSET + 5
    right_trail_icon = icon_side - (r_x_icon + SPIKE_RIGHT_OFFSET) - 5

    path_full_icon = full_path(r_x_icon, baseline_icon, ICON_LEFT_FLAT, right_trail_icon)
    path_simple_icon = simple_path(r_x_icon, baseline_icon, ICON_LEFT_FLAT, right_trail_icon)

    # ICON — full detail, stroke 10 (for 64+)
    svg_icon_full_black = make_svg((0, 0, icon_side, icon_side), path_full_icon,
                                     stroke=10, stroke_color=BLUE, bg=BG_BLACK)
    (SVG_DIR / "intergenos_icon_full.svg").write_text(svg_icon_full_black)
    print("  intergenos_icon_full.svg  (full detail, stroke 10, 512x512, black bg)")

    svg_icon_full_transparent = make_svg((0, 0, icon_side, icon_side), path_full_icon,
                                           stroke=10, stroke_color=BLUE, bg=None)
    (SVG_DIR / "intergenos_icon_full_transparent.svg").write_text(svg_icon_full_transparent)
    print("  intergenos_icon_full_transparent.svg")

    svg_icon_full_white = make_svg((0, 0, icon_side, icon_side), path_full_icon,
                                     stroke=10, stroke_color=WHITE, bg=None)
    (SVG_DIR / "intergenos_icon_full_white.svg").write_text(svg_icon_full_white)
    print("  intergenos_icon_full_white.svg")

    # ICON — simplified, stroke 32 (for 16-48)
    svg_icon_simple_black = make_svg((0, 0, icon_side, icon_side), path_simple_icon,
                                       stroke=32, stroke_color=BLUE, bg=BG_BLACK)
    (SVG_DIR / "intergenos_icon_simple.svg").write_text(svg_icon_simple_black)
    print("  intergenos_icon_simple.svg  (simplified, stroke 32, for small sizes)")

    svg_icon_simple_transparent = make_svg((0, 0, icon_side, icon_side), path_simple_icon,
                                             stroke=32, stroke_color=BLUE, bg=None)
    (SVG_DIR / "intergenos_icon_simple_transparent.svg").write_text(svg_icon_simple_transparent)
    print("  intergenos_icon_simple_transparent.svg")

    svg_icon_simple_white = make_svg((0, 0, icon_side, icon_side), path_simple_icon,
                                       stroke=32, stroke_color=WHITE, bg=None)
    (SVG_DIR / "intergenos_icon_simple_white.svg").write_text(svg_icon_simple_white)
    print("  intergenos_icon_simple_white.svg")

    # =========================================================================
    # LOGO — wide asymmetric, short lead-in, long trail for wordmark
    # =========================================================================
    logo_h = 512
    baseline_logo = logo_h / 2
    r_x_logo = LOGO_LEFT_FLAT - SPIKE_LEFT_OFFSET + 5
    logo_w = (LOGO_LEFT_FLAT + SPIKE_WIDTH + LOGO_RIGHT_TRAIL) + 10

    path_full_logo = full_path(r_x_logo, baseline_logo, LOGO_LEFT_FLAT, LOGO_RIGHT_TRAIL)

    svg_logo_black = make_svg((0, 0, logo_w, logo_h), path_full_logo,
                                stroke=10, stroke_color=BLUE, bg=BG_BLACK)
    (SVG_DIR / "intergenos_logo.svg").write_text(svg_logo_black)
    print(f"\n  intergenos_logo.svg  ({logo_w:.0f} x {logo_h}, wide asymmetric, black bg)")

    svg_logo_transparent = make_svg((0, 0, logo_w, logo_h), path_full_logo,
                                      stroke=10, stroke_color=BLUE, bg=None)
    (SVG_DIR / "intergenos_logo_transparent.svg").write_text(svg_logo_transparent)
    print("  intergenos_logo_transparent.svg")

    svg_logo_white = make_svg((0, 0, logo_w, logo_h), path_full_logo,
                                stroke=10, stroke_color=WHITE, bg=None)
    (SVG_DIR / "intergenos_logo_white.svg").write_text(svg_logo_white)
    print("  intergenos_logo_white.svg")

    # =========================================================================
    # PNG renders — hybrid size routing
    # =========================================================================
    # 16, 24, 32, 48 render from the simplified SVG
    # 64, 128, 256, 512, 1024 render from the full detail SVG
    small_sizes = [16, 24, 32, 48]
    large_sizes = [64, 128, 256, 512, 1024]

    print("\nPNG renders — ICON (hybrid):")
    for s in small_sizes:
        render(svg_icon_simple_black, PNG_DIR / f"intergenos_icon_{s:03d}.png", width=s)
        print(f"  intergenos_icon_{s:03d}.png  (from simple, {s}x{s})")
    for s in large_sizes:
        render(svg_icon_full_black, PNG_DIR / f"intergenos_icon_{s:03d}.png", width=s)
        print(f"  intergenos_icon_{s:03d}.png  (from full, {s}x{s})")

    print("\nPNG renders — ICON transparent (hybrid):")
    for s in small_sizes:
        render(svg_icon_simple_transparent, PNG_DIR / f"intergenos_icon_transparent_{s:03d}.png", width=s)
    for s in large_sizes:
        render(svg_icon_full_transparent, PNG_DIR / f"intergenos_icon_transparent_{s:03d}.png", width=s)

    print("\nPNG renders — LOGO (full wide, black bg):")
    for w in [512, 1024, 1536, 2048]:
        render(svg_logo_black, PNG_DIR / f"intergenos_logo_{w}.png", width=w)
        print(f"  intergenos_logo_{w}.png")

    print("\nPNG renders — LOGO transparent:")
    for w in [1024, 2048]:
        render(svg_logo_transparent, PNG_DIR / f"intergenos_logo_transparent_{w}.png", width=w)
        print(f"  intergenos_logo_transparent_{w}.png")


if __name__ == "__main__":
    generate()
    print("\nDone.")
