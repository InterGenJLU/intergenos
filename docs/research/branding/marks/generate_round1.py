#!/usr/bin/env python3
"""
InterGenOS Logo Mark Generator — Round 1
Explores the ECG-pulse-as-logo concept across simple variations.
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path("docs/research/branding/marks/round1/svg")
OUT_PNG = Path("docs/research/branding/marks/round1/png")

# Brand colors
BG_DARK = "#0a0e1a"
BLUE = "#0099FF"
BLUE_DIM = "#0066AA"

# Canvas
W, H = 512, 512


def wrap_svg(inner, bg=BG_DARK):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{bg}"/>
{inner}
</svg>'''


def render(name, inner, bg=BG_DARK):
    svg = wrap_svg(inner, bg=bg)
    svg_path = OUT_SVG / f"{name}.svg"
    png_path = OUT_PNG / f"{name}.png"
    svg_path.write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(png_path),
                     output_width=512, output_height=512)
    print(f"  {name}")


# ---------- Path helpers ----------

def qrs_path(cx=256, baseline=256, width=412, peak_height=176, q_dip=34, s_dip=54):
    """Generate QRS spike path centered at (cx, baseline).
    Returns SVG path d-attribute."""
    half = width // 2
    left = cx - half
    right = cx + half
    # The QRS spike occupies the middle third of the width
    spike_span = width // 3
    q_x = cx - spike_span // 4
    r_x = cx
    s_x = cx + spike_span // 4
    spike_start = cx - spike_span // 2
    spike_end = cx + spike_span // 2
    return (
        f"M {left} {baseline} "
        f"L {spike_start} {baseline} "
        f"L {q_x} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_dip} "
        f"L {spike_end} {baseline} "
        f"L {right} {baseline}"
    )


def pqrst_path(cx=256, baseline=256, width=432, peak_height=176, q_dip=34, s_dip=54,
               p_height=22, t_height=26):
    """Full PQRST waveform."""
    half = width // 2
    left = cx - half
    right = cx + half
    spike_span = width // 3
    spike_start = cx - spike_span // 2
    spike_end = cx + spike_span // 2
    q_x = cx - spike_span // 4
    r_x = cx
    s_x = cx + spike_span // 4
    # P wave (small bump) before Q
    p_peak_x = spike_start - 50
    # T wave (rounded bump) after S
    t_peak_x = spike_end + 60
    return (
        f"M {left} {baseline} "
        f"L {p_peak_x - 30} {baseline} "
        f"Q {p_peak_x} {baseline - p_height} {p_peak_x + 30} {baseline} "
        f"L {spike_start} {baseline} "
        f"L {q_x} {baseline + q_dip} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + s_dip} "
        f"L {spike_end} {baseline} "
        f"L {t_peak_x - 40} {baseline} "
        f"Q {t_peak_x} {baseline - t_height} {t_peak_x + 40} {baseline} "
        f"L {right} {baseline}"
    )


# ---------- Variations ----------

def mark_01_pure_qrs_thin():
    """Minimalist QRS spike, thin single stroke."""
    d = qrs_path()
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render("01_pure_qrs_thin", inner)


def mark_02_pure_qrs_bold():
    """QRS spike, bold stroke."""
    d = qrs_path()
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="18" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render("02_pure_qrs_bold", inner)


def mark_03_full_pqrst():
    """Full heartbeat waveform — P, Q, R, S, T."""
    d = pqrst_path()
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render("03_full_pqrst", inner)


def mark_04_qrs_in_circle():
    """QRS inside a circle frame."""
    d = qrs_path(cx=256, baseline=256, width=320, peak_height=130, q_dip=26, s_dip=40)
    inner = f'''
<circle cx="256" cy="256" r="200" stroke="{BLUE}" stroke-width="8" fill="none"/>
<path d="{d}" stroke="{BLUE}" stroke-width="10" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'''
    render("04_qrs_in_circle", inner)


def mark_05_perspective():
    """Asymmetric — user perspective, peak on the left, long trail to the right."""
    # Short left baseline leading quickly into the peak, long right baseline fading
    left = 40
    right = 472
    baseline = 256
    peak_height = 176
    q_x = 100
    r_x = 140
    s_x = 180
    d = (
        f"M {left} {baseline} "
        f"L {q_x - 20} {baseline} "
        f"L {q_x} {baseline + 30} "
        f"L {r_x} {baseline - peak_height} "
        f"L {s_x} {baseline + 50} "
        f"L {s_x + 20} {baseline} "
        f"L {right} {baseline}"
    )
    inner = f'<path d="{d}" stroke="{BLUE}" stroke-width="10" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    render("05_perspective", inner)


def mark_06_dual_helix():
    """Two parallel strokes hinting at DNA, forming the QRS."""
    d_upper = qrs_path(cx=256, baseline=248, width=412, peak_height=170, q_dip=30, s_dip=50)
    d_lower = qrs_path(cx=256, baseline=264, width=412, peak_height=170, q_dip=30, s_dip=50)
    inner = f'''
<path d="{d_upper}" stroke="{BLUE}" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
<path d="{d_lower}" stroke="{BLUE_DIM}" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'''
    render("06_dual_helix", inner)


def mark_07_peak_only():
    """Just the sharp R peak — maximum minimalism."""
    # Three-segment: quick down, sharp up, quick down
    baseline = 300
    peak_height = 220
    inner = f'''
<path d="M 156 {baseline} L 220 {baseline + 30} L 256 {baseline - peak_height} L 292 {baseline + 30} L 356 {baseline}"
stroke="{BLUE}" stroke-width="14" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'''
    render("07_peak_only", inner)


def mark_08_pulse_i():
    """Stylized 'i' where the dot is a miniature QRS spike — for InterGen."""
    # Lowercase 'i' body
    stem_x = 256
    stem_top = 200
    stem_bot = 400
    # Mini QRS where the dot would be
    d = (
        f"M 196 160 "
        f"L 226 160 "
        f"L 238 180 "
        f"L 256 100 "
        f"L 274 180 "
        f"L 286 160 "
        f"L 316 160"
    )
    inner = f'''
<rect x="{stem_x - 14}" y="{stem_top}" width="28" height="{stem_bot - stem_top}" rx="14" fill="{BLUE}"/>
<path d="{d}" stroke="{BLUE}" stroke-width="12" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'''
    render("08_pulse_i", inner)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Generating Round 1 marks:")
    mark_01_pure_qrs_thin()
    mark_02_pure_qrs_bold()
    mark_03_full_pqrst()
    mark_04_qrs_in_circle()
    mark_05_perspective()
    mark_06_dual_helix()
    mark_07_peak_only()
    mark_08_pulse_i()
    print(f"\nDone. SVGs in {OUT_SVG}, PNGs in {OUT_PNG}")
