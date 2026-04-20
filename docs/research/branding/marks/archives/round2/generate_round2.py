#!/usr/bin/env python3
"""
InterGenOS Logo Mark — Round 2
Iterating on #05 (perspective) with deeper S dip.
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path("/home/christopher/intergenos/research/branding/marks/round2/svg")
OUT_PNG = Path("/home/christopher/intergenos/research/branding/marks/round2/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"

W, H = 512, 512


def wrap_svg(inner, bg=BG_DARK):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{bg}"/>
{inner}
</svg>'''


def render(name, inner, bg=BG_DARK):
    svg = wrap_svg(inner, bg=bg)
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def perspective(name, s_dip, peak_height=176, stroke=10):
    """Perspective QRS. s_dip = how far below baseline the S wave goes."""
    left = 40
    right = 472
    baseline = 256
    q_x = 100
    r_x = 140
    s_x = 180
    # Small Q dip before the big R
    q_dip = 30
    d = (
        f"M {left} {baseline} "
        f"L {q_x - 20} {baseline} "
        f"L {q_x} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_dip} "
        f"L {s_x + 20} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 2 — perspective with deeper S:")
    # R peak is 176px above baseline.
    # Half = 88. Try a range from "exactly half" to deeper.
    perspective("05a_s_half",     s_dip=88)    # exactly half of R
    perspective("05b_s_60pct",    s_dip=106)   # 60% of R
    perspective("05c_s_70pct",    s_dip=123)   # 70% of R
    perspective("05d_s_half_bold", s_dip=88, stroke=14)     # same as a but bolder
    perspective("05e_s_60pct_bold", s_dip=106, stroke=14)   # same as b but bolder

    print(f"\nDone.")
