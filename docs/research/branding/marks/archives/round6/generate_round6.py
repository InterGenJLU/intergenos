#!/usr/bin/env python3
"""Round 6 — adding a T bump after S to balance the Q dip."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("/home/christopher/intergenos/research/branding/marks/round6/svg")
OUT_PNG = Path("/home/christopher/intergenos/research/branding/marks/round6/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def pulse_with_t(name, qr_span=32, q_dip=30, t_height=30, t_style="symmetric",
                 peak_height=176, s_depth_pct=0.90, r_x=140,
                 left=40, right=472, baseline=256, stroke=10):
    """
    QRS with a T bump after S.

    t_style options:
      - "symmetric"  : T is a small symmetric ^ using Q-family slope
      - "mirror_q"   : T is a shape mirror of Q (sharp rise, gentle fall)
      - "steep"      : T is symmetric but uses R/S-family slope (narrower)
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40
    rs_slope = (peak_height + s_depth) / (s_x - r_x)

    q_x = r_x - qr_span
    q_slope = (q_dip + peak_height) / qr_span

    entry_span = q_dip / q_slope
    entry_flat_end = q_x - entry_span

    # S recovers back to baseline at R/S slope
    s_baseline_x = s_x + s_depth / rs_slope

    # T bump construction
    if t_style == "symmetric":
        # Symmetric bump, both halves at Q-family slope
        t_half_width = t_height / q_slope
        t_peak_x = s_baseline_x + t_half_width
        t_end_x = t_peak_x + t_half_width
    elif t_style == "mirror_q":
        # T's rise = Q's drop width (sharp), T's fall = Q's climb width (gentle)
        t_rise_width = entry_span
        t_fall_width = qr_span
        t_peak_x = s_baseline_x + t_rise_width
        t_end_x = t_peak_x + t_fall_width
        # Height determined by slope and rise width
        t_height = q_slope * t_rise_width
    elif t_style == "steep":
        # Symmetric bump at R/S slope (same angular family as the big spike)
        t_half_width = t_height / rs_slope
        t_peak_x = s_baseline_x + t_half_width
        t_end_x = t_peak_x + t_half_width

    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_depth} "
        f"L {s_baseline_x:.1f} {baseline} "
        f"L {t_peak_x:.1f} {baseline - t_height:.0f} "
        f"L {t_end_x:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 6 — symmetric T bump after S:")
    # Using qr_span=32 as the median from Round 5
    pulse_with_t("06a_t_sym_h30", qr_span=32, q_dip=30, t_height=30, t_style="symmetric")
    pulse_with_t("06b_t_sym_h45", qr_span=32, q_dip=30, t_height=45, t_style="symmetric")
    pulse_with_t("06c_t_sym_h60", qr_span=32, q_dip=30, t_height=60, t_style="symmetric")
    pulse_with_t("06d_t_mirror_q", qr_span=32, q_dip=30, t_style="mirror_q")
    pulse_with_t("06e_t_steep_h30", qr_span=32, q_dip=30, t_height=30, t_style="steep")
    pulse_with_t("06f_t_steep_h45", qr_span=32, q_dip=30, t_height=45, t_style="steep")

    print("Done.")
