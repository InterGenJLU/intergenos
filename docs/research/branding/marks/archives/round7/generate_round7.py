#!/usr/bin/env python3
"""Round 7 — T positioned so its distance from S matches Q's distance from R."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("docs/research/branding/marks/round7/svg")
OUT_PNG = Path("docs/research/branding/marks/round7/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def pulse_aligned_t(name, qr_span=32, q_dip=30, t_height=30,
                    peak_height=176, s_depth_pct=0.90, r_x=140,
                    left=40, right=472, baseline=256, stroke=10):
    """
    T peak is positioned at s_x + qr_span — so its horizontal distance
    from S matches Q dip's horizontal distance from R.
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40
    rs_slope = (peak_height + s_depth) / (s_x - r_x)

    q_x = r_x - qr_span
    q_slope = (q_dip + peak_height) / qr_span
    entry_span = q_dip / q_slope
    entry_flat_end = q_x - entry_span

    s_baseline_x = s_x + s_depth / rs_slope

    # Position T peak at the same distance from S that Q dip is from R
    t_peak_x = s_x + qr_span

    # Symmetric T using Q-family slope
    t_half_width = t_height / q_slope
    t_start_x = t_peak_x - t_half_width
    t_end_x = t_peak_x + t_half_width

    # Between s_baseline_x and t_start_x there's a small flat segment
    # that creates the spacing match with Q
    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_depth} "
        f"L {s_baseline_x:.1f} {baseline} "
        f"L {t_start_x:.1f} {baseline} "
        f"L {t_peak_x:.1f} {baseline - t_height} "
        f"L {t_end_x:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


def pulse_full_mirror_t(name, qr_span=32, q_dip=30,
                        peak_height=176, s_depth_pct=0.90, r_x=140,
                        left=40, right=472, baseline=256, stroke=10):
    """
    T is the full geometric mirror of Q: same height as Q depth (30),
    rise segment matches Q's entry_span, fall segment matches Q's qr_span.
    So T's RIGHT side (fall) is gentle and wide, matching the gentle Q→R climb.
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40
    rs_slope = (peak_height + s_depth) / (s_x - r_x)

    q_x = r_x - qr_span
    q_slope = (q_dip + peak_height) / qr_span
    entry_span = q_dip / q_slope
    entry_flat_end = q_x - entry_span

    s_baseline_x = s_x + s_depth / rs_slope

    # Mirror Q's shape: short sharp rise (entry_span wide) + long gentle fall (qr_span wide)
    # But keep height = q_dip so the T height matches Q depth
    # Sharp rise uses q_slope (height = entry_span * q_slope = q_dip) ✓
    # Gentle fall slope = q_dip / qr_span (much gentler than q_slope)
    t_rise_width = entry_span
    t_fall_width = qr_span
    t_peak_x = s_baseline_x + t_rise_width
    t_end_x = t_peak_x + t_fall_width
    t_height = q_dip  # same as Q depth

    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_depth} "
        f"L {s_baseline_x:.1f} {baseline} "
        f"L {t_peak_x:.1f} {baseline - t_height} "
        f"L {t_end_x:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 7 — spacing-matched T:")
    # Approach 1: T peak at s_x + qr_span (mirror distance), symmetric small T
    pulse_aligned_t("07a_aligned_h30", qr_span=32, q_dip=30, t_height=30)
    pulse_aligned_t("07b_aligned_h45", qr_span=32, q_dip=30, t_height=45)
    pulse_aligned_t("07c_aligned_h60", qr_span=32, q_dip=30, t_height=60)

    # Approach 2: full geometric mirror of Q shape (asymmetric T)
    pulse_full_mirror_t("07d_full_mirror", qr_span=32, q_dip=30)

    # Same approaches with qr_span=30 in case user prefers slightly tighter
    pulse_aligned_t("07e_qr30_h30", qr_span=30, q_dip=30, t_height=30)
    pulse_aligned_t("07f_qr30_h45", qr_span=30, q_dip=30, t_height=45)

    print("Done.")
