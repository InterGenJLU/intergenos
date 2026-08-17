#!/usr/bin/env python3
"""Round 5 — finding the median Q position between R1/R2 and R4."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("docs/research/branding/marks/round5/svg")
OUT_PNG = Path("docs/research/branding/marks/round5/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def median_pulse(name, qr_span, q_dip=30, peak_height=176, s_depth_pct=0.90,
                 r_x=140, left=40, right=472, baseline=256, stroke=10):
    """
    Q has its own angular family (gentler), R/S shares the steep family.
    The Q entrance line is parallel to the Q→R climb — so Q's geometry
    is internally consistent, even if different from R→S.
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40  # fixed R→S span (defines the R/S slope)
    rs_slope = (peak_height + s_depth) / (s_x - r_x)

    # Q→R uses its own slope derived from the chosen span
    q_x = r_x - qr_span
    qr_slope = (q_dip + peak_height) / qr_span

    # Entry line slope matches Q→R slope (so Q entry and climb align)
    entry_span = q_dip / qr_slope
    entry_flat_end = q_x - entry_span

    # Exit line slope matches R→S slope (so S descent and recovery align)
    exit_span = s_depth / rs_slope
    exit_flat_start = s_x + exit_span

    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_depth} "
        f"L {exit_flat_start:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 5 — median Q positions:")
    # Round 1/2 had qr_span = 40
    # Round 4 had qr_span = 25 (aligned/tight)
    # Try 28, 30, 32, 34, 36
    median_pulse("05u_qr28", qr_span=28)
    median_pulse("05v_qr30", qr_span=30)
    median_pulse("05w_qr32", qr_span=32)
    median_pulse("05x_qr34", qr_span=34)
    median_pulse("05y_qr36", qr_span=36)

    print("Done.")
