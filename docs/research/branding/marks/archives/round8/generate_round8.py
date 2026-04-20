#!/usr/bin/env python3
"""Round 8 — S→T as one continuous line, mirroring Q→R structure."""

import cairosvg
from pathlib import Path

OUT_SVG = Path("/home/christopher/intergenos/research/branding/marks/round8/svg")
OUT_PNG = Path("/home/christopher/intergenos/research/branding/marks/round8/png")

BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
W, H = 512, 512


def render(name, inner):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{BG_DARK}"/>{inner}</svg>'
    (OUT_SVG / f"{name}.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=512, output_height=512)
    print(f"  {name}")


def continuous_pulse(name, qr_span=32, q_dip=30, t_height=30,
                     peak_height=176, s_depth_pct=0.90, r_x=140,
                     left=40, right=472, baseline=256, stroke=10):
    """
    Q→R and S→T are each one continuous line (no flat gap, no intermediate).
    Q→R rises at q_slope, S→T also rises at q_slope — parallel and mirrored.
    R→S falls at rs_slope (steeper, the dramatic center spike).
    Entry drop and T→baseline recovery both use q_slope.
    """
    s_depth = int(peak_height * s_depth_pct)
    s_x = r_x + 40
    rs_slope = (peak_height + s_depth) / (s_x - r_x)

    q_x = r_x - qr_span
    q_slope = (q_dip + peak_height) / qr_span
    entry_span = q_dip / q_slope
    entry_flat_end = q_x - entry_span

    # S→T is one continuous line at q_slope, crossing baseline mid-flight
    # Rises from S at +s_depth up through baseline to T at -t_height
    # Total rise = s_depth + t_height
    st_span = (s_depth + t_height) / q_slope
    t_peak_x = s_x + st_span

    # T drops back to baseline at q_slope
    t_fall_span = t_height / q_slope
    right_flat_start = t_peak_x + t_fall_span

    d = (
        f"M {left} {baseline} "
        f"L {entry_flat_end:.1f} {baseline} "
        f"L {q_x:.1f} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_depth} "
        f"L {t_peak_x:.1f} {baseline - t_height} "
        f"L {right_flat_start:.1f} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="{stroke}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render(name, inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 8 — continuous S→T:")
    # Varying T height to find the right balance
    continuous_pulse("08a_t_h30", qr_span=32, q_dip=30, t_height=30)
    continuous_pulse("08b_t_h45", qr_span=32, q_dip=30, t_height=45)
    continuous_pulse("08c_t_h60", qr_span=32, q_dip=30, t_height=60)
    continuous_pulse("08d_t_h20", qr_span=32, q_dip=30, t_height=20)
    continuous_pulse("08e_t_h15", qr_span=32, q_dip=30, t_height=15)

    print("Done.")
