#!/usr/bin/env python3
"""Round 3 — S almost matching R depth."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("docs/research/branding/marks/round3/svg")
OUT_PNG = Path("docs/research/branding/marks/round3/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def perspective(name, s_dip, peak_height=176, stroke=10):
    left, right, baseline = 40, 472, 256
    q_x, r_x, s_x = 100, 140, 180
    q_dip = 30
    d = (f"M {left} {baseline} L {q_x - 20} {baseline} L {q_x} {baseline + q_dip} "
         f"L {r_x} {baseline - peak_height} L {s_x} {baseline + s_dip} "
         f"L {s_x + 20} {baseline} L {right} {baseline}")
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 3 — S approaching R:")
    # R peak = 176. Try 80% → equal.
    perspective("05f_s_80pct",  s_dip=141)  # 80%
    perspective("05g_s_85pct",  s_dip=150)  # 85%
    perspective("05h_s_90pct",  s_dip=158)  # 90%
    perspective("05i_s_95pct",  s_dip=167)  # 95%
    perspective("05j_s_equal",  s_dip=176)  # 100% — symmetric depth
    # Bold versions of the most promising
    perspective("05k_s_85pct_bold", s_dip=150, stroke=14)
    perspective("05l_s_90pct_bold", s_dip=158, stroke=14)
    perspective("05m_s_equal_bold", s_dip=176, stroke=14)

    print("Done.")
