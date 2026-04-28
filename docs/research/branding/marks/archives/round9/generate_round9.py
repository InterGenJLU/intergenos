#!/usr/bin/env python3
"""Round 9 — true mirror-inverted construction.

Take everything left of R, rotate it 180° around the point (r_x, baseline),
shift right by some amount to create the R→S line, extend the tail.
Result: Q↔T and R↔S are true geometric mirrors.
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path("docs/research/branding/marks/round9/svg")
OUT_PNG = Path("docs/research/branding/marks/round9/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def mirror_inverted_pulse(name, qr_span=32, q_dip=30, rs_slope_target=None,
                          peak_height=176, r_x=140, left=40, right=472,
                          baseline=256, stroke=10):
    """
    Left side built normally. Right side is 180° rotation of the left side
    around (r_x, baseline), then shifted right by Δ.

    Δ is chosen to give the R→S line a target slope. By default it matches
    q_slope (making ALL diagonals the same angle — maximum symmetry).
    """
    q_slope = (q_dip + peak_height) / qr_span
    entry_span = q_dip / q_slope
    q_x = r_x - qr_span
    entry_flat_end = q_x - entry_span

    # S depth = peak height (mirror of R below baseline)
    s_depth = peak_height

    # Determine the horizontal shift Δ from the desired R→S slope
    # R at (r_x, baseline - peak_height); S at (r_x + Δ, baseline + peak_height)
    # Total vertical drop = 2 * peak_height
    # rs_slope_target = 2 * peak_height / Δ
    # Default: match q_slope (everything aligned)
    if rs_slope_target is None:
        rs_slope_target = q_slope
    delta = (2 * peak_height) / rs_slope_target

    # Mirrored points (180° rotation of left side around (r_x, baseline), shifted by Δ)
    # R (r_x, baseline - peak_height) → (r_x, baseline + peak_height) → shifted: (r_x + Δ, baseline + peak_height) = S
    # Q (q_x, baseline + q_dip) → (r_x + qr_span, baseline - q_dip) → shifted: (r_x + qr_span + Δ, baseline - q_dip) = T peak
    # entry_flat_end (x, baseline) → (r_x + entry_span + qr_span, baseline) → shifted: exit_flat_start
    s_x = r_x + delta
    t_peak_x = r_x + qr_span + delta
    exit_flat_start = r_x + entry_span + qr_span + delta

    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x:.1f} {baseline + s_depth} "
        f"L {t_peak_x:.1f} {baseline - q_dip} "
        f"L {exit_flat_start:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 9 — true mirror-inverted:")
    # Default: all slopes equal (R→S at q_slope)
    mirror_inverted_pulse("09a_all_equal", qr_span=32, q_dip=30)

    # R→S at 8.35 (the previous rs_slope — steeper center spike)
    mirror_inverted_pulse("09b_rs_835", qr_span=32, q_dip=30, rs_slope_target=8.35)

    # R→S slightly steeper than q_slope — compromise
    mirror_inverted_pulse("09c_rs_725", qr_span=32, q_dip=30, rs_slope_target=7.25)

    # Tighter Q→R span for a different balance
    mirror_inverted_pulse("09d_qr28", qr_span=28, q_dip=30)

    # Deeper Q
    mirror_inverted_pulse("09e_q45", qr_span=32, q_dip=45)

    print("Done.")
