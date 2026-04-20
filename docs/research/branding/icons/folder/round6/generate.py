#!/usr/bin/env python3
"""
Round 6 — TAB REHAB. 128px only. Rapid iteration.

THE TAB PROBLEM: It's been 28px tall on a 256 canvas — 11% of the icon
height devoted to a protruding bump. In reference themes, the tab is
barely 4-8px — a gentle asymmetry in the top edge, not a landmark.

Fix: the tab is now a SUBTLE height difference between the left and
right sides of the back panel's top edge. Just enough to say "folder,"
not enough to dominate the silhouette.
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent / "png"
W, H = 256, 256
BG = "#000000"
ACCENT = "#0099FF"


def render(name, svg_str):
    OUT.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg_str.encode(),
                     write_to=str(OUT / f"{name}.png"),
                     output_width=128, output_height=128)
    print(f"  {name}")


def _spine(bl, bt, bb):
    return (f'<path d="M {bl-4} {bt-2} L {bl-4} {bb+2} '
            f'C {bl-4} {bb+6}, {bl} {bb+8}, {bl+6} {bb+8} '
            f'L {bl+4} {bb+6} '
            f'C {bl} {bb+6}, {bl-2} {bb+4}, {bl-2} {bb} '
            f'L {bl-2} {bt} Z" fill="url(#spineGrad_)"/>')


def make_folder(name, back_grad, front_grad, crease_color="#44ccff",
                crease_opacity=0.5, gloss=True, spine=True,
                tab_rise=10, tab_width_pct=0.38,
                body_top=88, body_bottom=206, body_left=44, body_right=212,
                front_top_offset=30, corner_r=14, extra_elements=""):
    """Parameterized folder builder with controllable tab subtlety."""

    # Tab geometry
    tab_left = body_left
    tab_right = body_left + (body_right - body_left) * tab_width_pct
    tab_top = body_top - tab_rise
    # Smooth transition from tab height to body height
    transition_x = tab_right + 20

    crease_y = body_top + front_top_offset
    front_y = crease_y + 2

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  {back_grad}
  {front_grad}
  <linearGradient id="glossGrad" x1="0" y1="0" x2="0.6" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.12"/>
    <stop offset="30%" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="spineGrad_" x1="1" y1="0" x2="0" y2="0.3">
    <stop offset="0%" stop-color="#0a1628"/>
    <stop offset="100%" stop-color="#040810"/>
  </linearGradient>
  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#001133" flood-opacity="0.55"/>
  </filter>
  <filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="2"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

{_spine(body_left, body_top, body_bottom) if spine else ""}

<!-- Back panel with subtle tab -->
<path d="M {body_left} {body_top}
         L {body_left} {tab_top + corner_r}
         C {body_left} {tab_top}, {body_left + corner_r} {tab_top}, {body_left + corner_r} {tab_top}
         L {tab_right - 8} {tab_top}
         C {tab_right} {tab_top}, {tab_right + 4} {tab_top + 4}, {tab_right + 8} {tab_top + tab_rise * 0.6:.0f}
         C {tab_right + 14} {body_top - 2}, {tab_right + 18} {body_top}, {tab_right + 24} {body_top}
         L {body_right - corner_r} {body_top}
         C {body_right} {body_top}, {body_right} {body_top + corner_r}, {body_right} {body_top + corner_r}
         L {body_right} {body_bottom - corner_r}
         C {body_right} {body_bottom}, {body_right - corner_r} {body_bottom}, {body_right - corner_r} {body_bottom}
         L {body_left + corner_r} {body_bottom}
         C {body_left} {body_bottom}, {body_left} {body_bottom - corner_r}, {body_left} {body_bottom - corner_r}
         Z"
      fill="url(#backGrad)" filter="url(#shadow)"/>

<!-- Crease highlight -->
<line x1="{body_left + 2}" y1="{crease_y}" x2="{body_right - 2}" y2="{crease_y}"
      stroke="{crease_color}" stroke-width="2" stroke-opacity="{crease_opacity}"/>

<!-- Front panel -->
<path d="M {body_left} {front_y}
         L {body_right} {front_y}
         L {body_right} {body_bottom - corner_r}
         C {body_right} {body_bottom}, {body_right - corner_r} {body_bottom}, {body_right - corner_r} {body_bottom}
         L {body_left + corner_r} {body_bottom}
         C {body_left} {body_bottom}, {body_left} {body_bottom - corner_r}, {body_left} {body_bottom - corner_r}
         Z"
      fill="url(#frontGrad)"/>

{f'<!-- Diagonal gloss --><path d="M {body_left} {front_y} L {body_left + 90} {front_y} L {body_left} {front_y + 60} Z" fill="url(#glossGrad)"/>' if gloss else ''}

{extra_elements}
</svg>'''
    render(name, svg)


def run():
    # ================================================================
    # A — Full blue, minimal tab (10px rise)
    # ================================================================
    make_folder("A_full_blue_subtle",
        back_grad='''<linearGradient id="backGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0066aa"/>
            <stop offset="100%" stop-color="#003366"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0099FF"/>
            <stop offset="6%" stop-color="#40aaff"/>
            <stop offset="14%" stop-color="#0099FF"/>
            <stop offset="86%" stop-color="#0088ee"/>
            <stop offset="95%" stop-color="#33a8ff"/>
            <stop offset="100%" stop-color="#0080dd"/></linearGradient>''',
        tab_rise=10)

    # ================================================================
    # B — Same but even subtler tab (6px rise)
    # ================================================================
    make_folder("B_full_blue_whisper",
        back_grad='''<linearGradient id="backGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0066aa"/>
            <stop offset="100%" stop-color="#003366"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0099FF"/>
            <stop offset="6%" stop-color="#40aaff"/>
            <stop offset="14%" stop-color="#0099FF"/>
            <stop offset="86%" stop-color="#0088ee"/>
            <stop offset="95%" stop-color="#33a8ff"/>
            <stop offset="100%" stop-color="#0080dd"/></linearGradient>''',
        tab_rise=6)

    # ================================================================
    # C — Emergence gradient (dark bottom, blue top), subtle tab
    # ================================================================
    make_folder("C_emergence_subtle",
        back_grad='''<linearGradient id="backGrad" x1="0.2" y1="1" x2="0.5" y2="0">
            <stop offset="0%" stop-color="#0a1628"/>
            <stop offset="50%" stop-color="#003870"/>
            <stop offset="100%" stop-color="#0066aa"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0.2" y1="1" x2="0.5" y2="0">
            <stop offset="0%" stop-color="#0a1628"/>
            <stop offset="40%" stop-color="#003060"/>
            <stop offset="85%" stop-color="#0088dd"/>
            <stop offset="94%" stop-color="#33aaff"/>
            <stop offset="100%" stop-color="#0099FF"/></linearGradient>''',
        tab_rise=8)

    # ================================================================
    # D — Vivid glass, subtle tab
    # ================================================================
    make_folder("D_glass_subtle",
        back_grad='''<linearGradient id="backGrad" x1="0.2" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0077cc"/>
            <stop offset="100%" stop-color="#004488"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0099FF" stop-opacity="0.9"/>
            <stop offset="50%" stop-color="#0088ee" stop-opacity="0.95"/>
            <stop offset="100%" stop-color="#0070cc" stop-opacity="0.9"/></linearGradient>''',
        crease_color="#66ddff", crease_opacity=0.6,
        tab_rise=8)

    # ================================================================
    # E — No visible tab at all — just a flat-topped rounded rect
    #     with the crease creating the "folder" read
    # ================================================================
    make_folder("E_no_tab",
        back_grad='''<linearGradient id="backGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0066aa"/>
            <stop offset="100%" stop-color="#003366"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0099FF"/>
            <stop offset="6%" stop-color="#40aaff"/>
            <stop offset="14%" stop-color="#0099FF"/>
            <stop offset="86%" stop-color="#0088ee"/>
            <stop offset="95%" stop-color="#33a8ff"/>
            <stop offset="100%" stop-color="#0080dd"/></linearGradient>''',
        tab_rise=0)

    # ================================================================
    # F — Tron lines with subtle tab
    # ================================================================
    make_folder("F_tron_subtle",
        back_grad='''<linearGradient id="backGrad" x1="0.3" y1="0" x2="0.7" y2="1">
            <stop offset="0%" stop-color="#0c1628"/>
            <stop offset="100%" stop-color="#060a14"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0.3" y1="0" x2="0.7" y2="1">
            <stop offset="0%" stop-color="#101c34"/>
            <stop offset="100%" stop-color="#080e1c"/></linearGradient>''',
        tab_rise=8, gloss=False, spine=False,
        crease_color="#33b1ff", crease_opacity=0.8,
        extra_elements=f'''
<!-- Energy edge outline -->
<path d="M 44 88 L 44 78 C 44 70, 58 70, 58 70
         L 100 70 C 108 70, 112 74, 116 80 C 120 86, 124 88, 132 88
         L 198 88 C 212 88, 212 102, 212 102
         L 212 192 C 212 206, 198 206, 198 206
         L 58 206 C 44 206, 44 192, 44 192 Z"
      fill="none" stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.6"
      stroke-linejoin="round" filter="url(#glow)"/>
<path d="M 44 120 L 212 120 L 212 192
         C 212 206, 198 206, 198 206
         L 58 206 C 44 206, 44 192, 44 192 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.4"
      stroke-linejoin="round"/>''')

    # ================================================================
    # G — Wide tab (50% width), barely rises (6px). More Adwaita-like.
    # ================================================================
    make_folder("G_wide_whisper",
        back_grad='''<linearGradient id="backGrad" x1="0.2" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0077cc"/>
            <stop offset="100%" stop-color="#003d6b"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0.2" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0099FF"/>
            <stop offset="8%" stop-color="#3ab0ff"/>
            <stop offset="16%" stop-color="#0099FF"/>
            <stop offset="84%" stop-color="#0085dd"/>
            <stop offset="94%" stop-color="#3ab0ff"/>
            <stop offset="100%" stop-color="#007acc"/></linearGradient>''',
        tab_rise=6, tab_width_pct=0.50,
        crease_color="#55ccff", crease_opacity=0.4)

    # ================================================================
    # H — Narrow tab (28%), barely rises (6px), strong crease, no spine
    # ================================================================
    make_folder("H_narrow_whisper",
        back_grad='''<linearGradient id="backGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#005599"/>
            <stop offset="100%" stop-color="#002a55"/></linearGradient>''',
        front_grad='''<linearGradient id="frontGrad" x1="0" y1="0" x2="0.8" y2="1">
            <stop offset="0%" stop-color="#0099FF"/>
            <stop offset="6%" stop-color="#44bbff"/>
            <stop offset="14%" stop-color="#0099FF"/>
            <stop offset="86%" stop-color="#0085dd"/>
            <stop offset="96%" stop-color="#44bbff"/>
            <stop offset="100%" stop-color="#0080cc"/></linearGradient>''',
        tab_rise=6, tab_width_pct=0.28, spine=False,
        crease_color="#66ddff", crease_opacity=0.6)


if __name__ == "__main__":
    run()
    print("Done — 8 concepts at 128px.")
