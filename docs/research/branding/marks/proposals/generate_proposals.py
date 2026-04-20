#!/usr/bin/env python3
"""
InterGenOS icon proposals — three clean options for the small-size problem.

Each proposal is a complete icon set rendered at 16, 24, 32, 48, 64, 128.
All use the SAME spike geometry. They differ only in how stroke width and
geometry detail are handled at smaller sizes.

Proposal 1 — UNIFORM
  One SVG with fixed stroke 10 (the current approach).
  Detail preserved, but small sizes are faint/blurry.
  Side-by-side consistency is perfect; small sizes suffer.

Proposal 2 — CHUNKY
  One SVG with heavier stroke 20. Same geometry.
  Small sizes are more visible, large sizes look bolder/thicker.
  Single asset, simple workflow.

Proposal 3 — HYBRID (adaptive)
  Two SVGs: full-detail for large sizes, simplified (no Q/T) for small sizes.
  Small sizes have thick bold strokes and render clearly.
  Large sizes preserve the Q and T detail.
  Two assets, rendered at different size buckets.
"""

import cairosvg
from pathlib import Path
import shutil

ROOT = Path("/home/christopher/intergenos/research/branding/marks/proposals")

BG = "#000000"
BLUE = "#0099FF"

# Shared spike geometry
QR_SPAN = 32
Q_DIP = 45
PEAK_HEIGHT = 176
Q_SLOPE = (Q_DIP + PEAK_HEIGHT) / QR_SPAN
ENTRY_SPAN = Q_DIP / Q_SLOPE
S_DEPTH = PEAK_HEIGHT
DELTA = (2 * PEAK_HEIGHT) / Q_SLOPE

# Icon layout
CANVAS = 512
LEFT_FLAT = 80


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
    """Simplified — drops Q and T. Just flat → R → S → flat."""
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


def make_svg(path_d, stroke):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}">'
        f'<rect width="{CANVAS}" height="{CANVAS}" fill="{BG}"/>'
        f'<path d="{path_d}" stroke="{BLUE}" stroke-width="{stroke}" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def render(svg_str, out_path, size):
    cairosvg.svg2png(bytestring=svg_str.encode(), write_to=str(out_path),
                     output_width=size, output_height=size)


def setup_proposal(name):
    """Clean and create a proposal directory."""
    d = ROOT / name
    if d.exists():
        shutil.rmtree(d)
    (d / "svg").mkdir(parents=True)
    (d / "png").mkdir(parents=True)
    return d


def main():
    # Shared layout math
    r_x = LEFT_FLAT - (-(QR_SPAN + ENTRY_SPAN)) + 5
    baseline = CANVAS / 2
    exit_flat_start = r_x + DELTA + QR_SPAN + ENTRY_SPAN
    right_trail = CANVAS - exit_flat_start - 5

    sizes = [16, 24, 32, 48, 64, 128]

    # -------------------------------------------------------------------
    # PROPOSAL 1 — UNIFORM (current approach, baseline for comparison)
    # -------------------------------------------------------------------
    d1 = setup_proposal("proposal_1_uniform")
    path = full_path(r_x, baseline, LEFT_FLAT, right_trail)
    svg = make_svg(path, stroke=10)
    (d1 / "svg" / "intergenos_icon.svg").write_text(svg)
    for s in sizes:
        render(svg, d1 / "png" / f"icon_{s:03d}.png", s)
    (d1 / "README.txt").write_text(
        "PROPOSAL 1 — UNIFORM\n\n"
        "One SVG, stroke 10, full Q/R/S/T geometry. This is what we had before.\n"
        "Large sizes look great and detailed. Small sizes are faint and blurry\n"
        "because the stroke becomes sub-pixel.\n\n"
        "Best at: 128+\n"
        "Weak at: 16-64\n"
    )
    print(f"✓ {d1}")

    # -------------------------------------------------------------------
    # PROPOSAL 2 — CHUNKY (single asset, thicker stroke)
    # -------------------------------------------------------------------
    d2 = setup_proposal("proposal_2_chunky")
    svg = make_svg(path, stroke=20)
    (d2 / "svg" / "intergenos_icon.svg").write_text(svg)
    for s in sizes:
        render(svg, d2 / "png" / f"icon_{s:03d}.png", s)
    (d2 / "README.txt").write_text(
        "PROPOSAL 2 — CHUNKY\n\n"
        "One SVG, stroke 20, full Q/R/S/T geometry. Same mark, thicker line.\n"
        "Small sizes render more visibly. Large sizes look bolder than Proposal 1.\n"
        "Simplest workflow: one asset works across all sizes.\n\n"
        "Best at: 48+\n"
        "Acceptable at: 24-32\n"
        "Weak at: 16\n"
    )
    print(f"✓ {d2}")

    # -------------------------------------------------------------------
    # PROPOSAL 3 — HYBRID (two assets, size-aware rendering)
    # -------------------------------------------------------------------
    d3 = setup_proposal("proposal_3_hybrid")
    # Full detail for large sizes
    svg_full = make_svg(path, stroke=10)
    (d3 / "svg" / "intergenos_icon_full.svg").write_text(svg_full)
    # Simplified for small sizes (no Q/T, thicker stroke)
    simple = simple_path(r_x, baseline, LEFT_FLAT, right_trail)
    svg_simple = make_svg(simple, stroke=32)
    (d3 / "svg" / "intergenos_icon_simple.svg").write_text(svg_simple)
    # Render: small sizes from simple SVG, large sizes from full SVG
    # Transition at 64: 48 and below use simple/chunky, 64 and above use full
    small_sizes = [16, 24, 32, 48]
    large_sizes = [64, 128]
    for s in small_sizes:
        render(svg_simple, d3 / "png" / f"icon_{s:03d}.png", s)
    for s in large_sizes:
        render(svg_full, d3 / "png" / f"icon_{s:03d}.png", s)
    (d3 / "README.txt").write_text(
        "PROPOSAL 3 — HYBRID (adaptive)\n\n"
        "Two SVG assets, rendered based on target size:\n"
        "  - intergenos_icon_simple.svg : no Q/T dips, stroke 32 (for 16-48 px)\n"
        "  - intergenos_icon_full.svg   : full Q/R/S/T, stroke 10 (for 64+ px)\n\n"
        "Small sizes render cleanly because they use a simpler geometry and\n"
        "a proportionally thicker stroke. Large sizes preserve the Q and T\n"
        "details that make the mark unique.\n\n"
        "Both versions read as 'heartbeat pulse' — visually consistent.\n"
        "Similar approach to Apple/Google/most major OS icons.\n\n"
        "Best at: every size\n"
        "Tradeoff: two assets instead of one\n"
    )
    print(f"✓ {d3}")


if __name__ == "__main__":
    main()
    print("\nThree proposals in /home/christopher/intergenos/research/branding/marks/proposals/")
