#!/usr/bin/env python3
"""Test different stroke weights and simplified geometries at small sizes."""

import cairosvg
from pathlib import Path

OUT = Path("docs/research/branding/marks/final/test_small")
OUT.mkdir(parents=True, exist_ok=True)

BG = "#000000"
BLUE = "#0099FF"

QR_SPAN = 32
Q_DIP = 45
PEAK_HEIGHT = 176
Q_SLOPE = (Q_DIP + PEAK_HEIGHT) / QR_SPAN
ENTRY_SPAN = Q_DIP / Q_SLOPE
S_DEPTH = PEAK_HEIGHT
DELTA = (2 * PEAK_HEIGHT) / Q_SLOPE
SPIKE_W = (QR_SPAN + ENTRY_SPAN) * 2 + DELTA
SPIKE_H = 2 * PEAK_HEIGHT


def full_path(r_x, baseline, left_flat, right_trail):
    """Full Q/R/S/T path."""
    entry_flat_end = r_x - QR_SPAN - ENTRY_SPAN
    exit_flat_start = r_x + DELTA + QR_SPAN + ENTRY_SPAN
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
    """Simplified path — no Q dip, no T peak. Just flat→R→S→flat."""
    # R and S positions stay the same for shape consistency
    entry_flat_end = r_x - QR_SPAN - ENTRY_SPAN
    exit_flat_start = r_x + DELTA + QR_SPAN + ENTRY_SPAN
    return (
        f"M {entry_flat_end - left_flat:.2f} {baseline} "
        f"L {entry_flat_end:.2f} {baseline} "
        f"L {r_x:.2f} {baseline - PEAK_HEIGHT} "
        f"L {r_x + DELTA:.2f} {baseline + S_DEPTH} "
        f"L {exit_flat_start:.2f} {baseline} "
        f"L {exit_flat_start + right_trail:.2f} {baseline}"
    )


def make_svg(viewbox, path, stroke, color=BLUE, bg=BG):
    vb_x, vb_y, vb_w, vb_h = viewbox
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x} {vb_y} {vb_w} {vb_h}">'
        f'<rect x="{vb_x}" y="{vb_y}" width="{vb_w}" height="{vb_h}" fill="{bg}"/>'
        f'<path d="{path}" stroke="{color}" stroke-width="{stroke}" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def test():
    # 512x512 canvas, left-aligned spike, short trail
    side = 512
    baseline = side / 2
    left_flat = 40
    right_trail_target = 0  # fill to right edge
    r_x = left_flat - (-(QR_SPAN + ENTRY_SPAN))
    # Calculate right trail to reach the right edge
    exit_flat_start = r_x + DELTA + QR_SPAN + ENTRY_SPAN
    right_trail = side - exit_flat_start - 5  # small margin

    # Variant A: full geometry, stroke 10 (current)
    svgA = make_svg((0, 0, side, side), full_path(r_x, baseline, left_flat, right_trail), 10)
    (OUT / "A_full_s10.svg").write_text(svgA)
    # Variant B: full geometry, stroke 20
    svgB = make_svg((0, 0, side, side), full_path(r_x, baseline, left_flat, right_trail), 20)
    (OUT / "B_full_s20.svg").write_text(svgB)
    # Variant C: full geometry, stroke 32
    svgC = make_svg((0, 0, side, side), full_path(r_x, baseline, left_flat, right_trail), 32)
    (OUT / "C_full_s32.svg").write_text(svgC)
    # Variant D: simplified, stroke 20
    svgD = make_svg((0, 0, side, side), simple_path(r_x, baseline, left_flat, right_trail), 20)
    (OUT / "D_simple_s20.svg").write_text(svgD)
    # Variant E: simplified, stroke 32
    svgE = make_svg((0, 0, side, side), simple_path(r_x, baseline, left_flat, right_trail), 32)
    (OUT / "E_simple_s32.svg").write_text(svgE)
    # Variant F: simplified, stroke 48
    svgF = make_svg((0, 0, side, side), simple_path(r_x, baseline, left_flat, right_trail), 48)
    (OUT / "F_simple_s48.svg").write_text(svgF)

    variants = {"A": svgA, "B": svgB, "C": svgC, "D": svgD, "E": svgE, "F": svgF}

    # Render each variant at problem sizes
    for size in [16, 24, 32, 48, 64]:
        for name, svg in variants.items():
            out = OUT / f"size{size:03d}_{name}.png"
            cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out),
                             output_width=size)
            print(f"  {out.name}")


if __name__ == "__main__":
    test()
