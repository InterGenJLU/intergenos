#!/usr/bin/env python3
"""Round 4 — aligning Q entrance angle with R and S slopes."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("/home/christopher/intergenos/research/branding/marks/round4/svg")
OUT_PNG = Path("/home/christopher/intergenos/research/branding/marks/round4/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def aligned_pulse(name, q_dip=30, peak_height=176, s_depth_pct=0.90,
                  r_x=140, left=40, right=472, baseline=256, stroke=10):
    """
    Build a QRS where every diagonal segment shares the same slope.

    Slope is derived from R→S (the steepest required segment):
        slope = (peak_height + s_depth) / (s_x - r_x)

    Then all other diagonals (entry→Q, Q→R, S→exit) use that same slope.
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40  # fixed R→S horizontal span — establishes the slope
    slope = (peak_height + s_depth) / (s_x - r_x)

    # Q→R span to achieve the same slope
    qr_span = (q_dip + peak_height) / slope
    q_x = r_x - qr_span

    # Entry drop span (flat baseline → Q)
    entry_span = q_dip / slope
    entry_flat_end = q_x - entry_span

    # Exit rise span (S → flat baseline)
    exit_span = s_depth / slope
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


def no_q_pulse(name, peak_height=176, s_depth_pct=0.90, r_x=140, stroke=10):
    """Variant: remove the Q dip entirely — just baseline → R → S → baseline."""
    left, right, baseline = 40, 472, 256
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40
    slope = (peak_height + s_depth) / (s_x - r_x)
    # Entry rises directly to R
    entry_span = peak_height / slope
    entry_flat_end = r_x - entry_span
    exit_span = s_depth / slope
    exit_flat_start = s_x + exit_span
    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
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

    print("Round 4 — aligned angles:")
    # S at 90% of R (the depth we settled on)
    aligned_pulse("05n_aligned_q30",  q_dip=30)   # shallow Q, all slopes matched
    aligned_pulse("05o_aligned_q50",  q_dip=50)   # slightly deeper Q
    aligned_pulse("05p_aligned_q70",  q_dip=70)   # deeper Q
    aligned_pulse("05q_aligned_q30_bold", q_dip=30, stroke=14)
    aligned_pulse("05r_aligned_q50_bold", q_dip=50, stroke=14)
    no_q_pulse("05s_no_q")              # no Q dip at all
    no_q_pulse("05t_no_q_bold", stroke=14)

    print("Done.")
