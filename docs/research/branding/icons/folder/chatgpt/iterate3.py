#!/usr/bin/env python3
"""
Third pass — focusing on:
1. Making diagonal reflection CLEARLY visible (bright diagonal band)
2. Bottom edge approaching white-blue intensity
3. More contrast between back and front panels
4. Subtle inner luminosity in the body
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent

FOLDER = ("M32 72 Q32 56 48 56 H104 Q112 56 120 64 L132 76 H208 "
          "Q224 76 224 92 V184 Q224 200 208 200 H48 Q32 200 32 184 Z")
FRONT = "M32 108 H224 V184 Q224 200 208 200 H48 Q32 200 32 184 Z"


def render(name, svg):
    for size, s in [(256, ""), (128, "_128")]:
        cairosvg.svg2png(bytestring=svg.encode(),
                         write_to=str(OUT / f"{name}{s}.png"),
                         output_width=size, output_height=size)


def final_edge_lit():
    svg = '''<svg width="256" height="256" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
<defs>
  <!-- Back panel: very dark -->
  <linearGradient id="bk" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a1020"/>
    <stop offset="100%" stop-color="#050810"/></linearGradient>

  <!-- Front panel: slightly lighter, glass-like -->
  <linearGradient id="ft" x1="0" y1="0" x2="0.1" y2="1">
    <stop offset="0%" stop-color="#0f172a" stop-opacity="0.55"/>
    <stop offset="100%" stop-color="#080c18" stop-opacity="0.60"/></linearGradient>

  <!-- Subtle inner luminosity (very faint blue radial in the body) -->
  <radialGradient id="inner" cx="0.5" cy="0.55" r="0.5">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.03"/>
    <stop offset="100%" stop-color="#0099FF" stop-opacity="0"/></radialGradient>

  <!-- DIAGONAL REFLECTION — a bright band, not just a fading triangle -->
  <!-- Using two overlapping shapes for a band effect -->
  <linearGradient id="glossA" x1="0.1" y1="0" x2="0.55" y2="0.65">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.22"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0.06"/>
    <stop offset="55%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
  <linearGradient id="glossB" x1="0.15" y1="0" x2="0.5" y2="0.6">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0"/>
    <stop offset="15%" stop-color="#ffffff" stop-opacity="0.10"/>
    <stop offset="40%" stop-color="#ffffff" stop-opacity="0.03"/>
    <stop offset="60%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>

  <!-- Front panel top-edge glass highlight -->
  <linearGradient id="tophi" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.10"/>
    <stop offset="15%" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="25%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>

  <!-- Glow filters -->
  <filter id="w" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="14"/></filter>
  <filter id="m" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="bot" x="-80%" y="-40%" width="260%" height="320%">
    <feGaussianBlur stdDeviation="16"/></filter>
  <filter id="botSharp" x="-50%" y="-30%" width="200%" height="260%">
    <feGaussianBlur stdDeviation="8" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>

  <clipPath id="fc"><path d="''' + FRONT + '''"/></clipPath>
  <clipPath id="ac"><path d="''' + FOLDER + '''"/></clipPath>
</defs>

<!-- ======================================================= -->
<!-- BOTTOM GLOW POOL — the dominant light source             -->
<!-- ======================================================= -->
<!-- Ultra-wide soft bottom glow -->
<path d="M48 200 H208 Q224 200 224 184" fill="none"
  stroke="#0099FF" stroke-width="10" stroke-opacity="0.7"
  stroke-linecap="round" filter="url(#bot)"/>
<!-- Bottom-left corner -->
<path d="M32 184 Q32 200 48 200" fill="none"
  stroke="#0099FF" stroke-width="9" stroke-opacity="0.6"
  stroke-linecap="round" filter="url(#bot)"/>
<!-- Sharper bottom edge on top of the wide glow -->
<path d="M48 200 H208 Q224 200 224 186" fill="none"
  stroke="#33bbff" stroke-width="3" stroke-opacity="0.5"
  stroke-linecap="round" filter="url(#botSharp)"/>

<!-- ======================================================= -->
<!-- SIDE GLOW — dimmer than bottom                           -->
<!-- ======================================================= -->
<path d="M224 92 V180" fill="none" stroke="#0099FF"
  stroke-width="5" stroke-opacity="0.35" stroke-linecap="round" filter="url(#w)"/>
<path d="M32 76 V180" fill="none" stroke="#0099FF"
  stroke-width="5" stroke-opacity="0.30" stroke-linecap="round" filter="url(#w)"/>

<!-- ======================================================= -->
<!-- TAB GLOW — dimmest (energy fades toward top)             -->
<!-- ======================================================= -->
<path d="M48 56 H104 Q112 56 120 64 L132 76 H208 Q224 76 224 88" fill="none"
  stroke="#0099FF" stroke-width="4" stroke-opacity="0.14"
  stroke-linecap="round" filter="url(#w)"/>
<path d="M32 72 Q32 56 48 56" fill="none" stroke="#0099FF"
  stroke-width="4" stroke-opacity="0.14" stroke-linecap="round" filter="url(#w)"/>

<!-- ======================================================= -->
<!-- BODY FILLS                                               -->
<!-- ======================================================= -->
<!-- Back panel (very dark) -->
<path d="''' + FOLDER + '''" fill="url(#bk)"/>

<!-- Inner luminosity (very subtle blue glow inside the body) -->
<g clip-path="url(#ac)">
  <rect x="32" y="56" width="192" height="144" fill="url(#inner)"/>
</g>

<!-- Front panel (glass — semi-transparent, lighter than back) -->
<path d="''' + FRONT + '''" fill="url(#ft)"/>

<!-- Front panel top highlight (glass catching light) -->
<g clip-path="url(#fc)">
  <rect x="32" y="108" width="192" height="32" fill="url(#tophi)"/>
</g>

<!-- ======================================================= -->
<!-- CREASE (where front overlaps back — subtle)              -->
<!-- ======================================================= -->
<line x1="35" y1="108" x2="221" y2="108"
  stroke="#0099FF" stroke-width="1" stroke-opacity="0.18" filter="url(#m)"/>

<!-- ======================================================= -->
<!-- DIAGONAL GLASS REFLECTION — the premium signature        -->
<!-- Two overlapping layers creating a visible bright band    -->
<!-- ======================================================= -->
<g clip-path="url(#fc)">
  <!-- Primary reflection (bright, wide) -->
  <path d="M32 108 L190 108 L32 200 Z" fill="url(#glossA)"/>
  <!-- Secondary reflection (offset band, reinforces the diagonal) -->
  <path d="M55 108 L210 108 L55 198 Z" fill="url(#glossB)"/>
</g>

<!-- ======================================================= -->
<!-- EDGE DEFINITION — medium glow + sharp structural edge    -->
<!-- ======================================================= -->
<!-- Medium glow (adds soft edge definition) -->
<path d="''' + FOLDER + '''" fill="none" stroke="#0099FF"
  stroke-width="2" stroke-opacity="0.22" filter="url(#m)"/>

<!-- Sharp structural edge -->
<path d="''' + FOLDER + '''" fill="none"
  stroke="rgba(0,153,255,0.24)" stroke-width="1.5"/>

<!-- Front panel edge (dimmer) -->
<path d="''' + FRONT + '''" fill="none"
  stroke="rgba(0,153,255,0.12)" stroke-width="0.75"/>

<!-- Tab catch light (very subtle) -->
<path d="M50 57 H102 Q110 57 118 65" fill="none"
  stroke="#ffffff" stroke-width="0.75" stroke-opacity="0.05"
  stroke-linecap="round"/>

</svg>'''
    render("final_edge_lit", svg)
    return svg


def final_pulse_core(base_svg):
    extra_defs = '''
  <radialGradient id="cg">
    <stop offset="0%" stop-color="#55ddff" stop-opacity="0.9"/>
    <stop offset="18%" stop-color="#0099FF" stop-opacity="0.55"/>
    <stop offset="50%" stop-color="#0066aa" stop-opacity="0.15"/>
    <stop offset="100%" stop-color="#003366" stop-opacity="0"/></radialGradient>
  <filter id="cb" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="6"/></filter>'''

    core = '''
<g clip-path="url(#ac)">
  <!-- Wide ambient radiance from core -->
  <circle cx="134" cy="150" r="55" fill="none" stroke="#003366"
    stroke-width="20" stroke-opacity="0.05" filter="url(#w)"/>
  <!-- Core glow -->
  <circle cx="134" cy="150" r="22" fill="url(#cg)" opacity="0.88" filter="url(#cb)"/>
  <!-- Horizontal lens flare -->
  <line x1="45" y1="150" x2="222" y2="150" stroke="#0088dd"
    stroke-width="2" stroke-opacity="0.35" filter="url(#m)"/>
  <line x1="70" y1="150" x2="198" y2="150" stroke="#33bbff"
    stroke-width="1" stroke-opacity="0.18"/>
  <!-- Bright core -->
  <circle cx="134" cy="150" r="9" fill="#0099FF" opacity="0.8" filter="url(#m)"/>
  <!-- Hot center -->
  <circle cx="134" cy="150" r="3.5" fill="#88eeff" opacity="0.95"/>
</g>'''

    svg = base_svg.replace('</defs>', extra_defs + '\n</defs>')
    svg = svg.replace('</svg>', core + '\n</svg>')
    render("final_pulse_core", svg)


if __name__ == "__main__":
    print("Final pass...")
    base = final_edge_lit()
    print("  final_edge_lit")
    final_pulse_core(base)
    print("  final_pulse_core")
    print("Done.")
