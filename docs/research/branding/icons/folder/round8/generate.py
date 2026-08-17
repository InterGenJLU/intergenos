#!/usr/bin/env python3
"""
Round 8 — PUSH HARD. Rich, layered, bold. 128px only.

What Amy-Dark taught me:
  - Main body at 0.6 OPACITY (semi-transparent, shows depth)
  - Tab highlight is SOLID WHITE (not 5% — full catch-light)
  - Gradient from #0088ff to #73d9ff (blue to bright cyan, bottom-to-top)
  - Blurred shadow layer creates real 3D lift
  - 8+ visual layers, not 3-4

What the user wants:
  - "Wow" factor. Not placeholder art.
  - Something that says "this OS contains an AI"
  - Premium, futuristic, alive. Not Windows 2000.
  - Creative and unique to InterGenOS.

Approach: Layer density. Multiple overlapping gradients. Real opacity.
Real highlights. Strong shadows. Personality elements.
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent / "png"
W, H = 256, 256


def render(name, svg_str):
    OUT.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg_str.encode(),
                     write_to=str(OUT / f"{name}.png"),
                     output_width=128, output_height=128)
    print(f"  {name}")


def a_amy_style():
    """A — Amy-style construction with InterGenOS palette.
    8 layers. Semi-transparent body. White tab highlight.
    Gradient from navy to cyan. Proper 3D shadows."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="mainGrad" x1="0.5" y1="1" x2="0.5" y2="0">
    <stop offset="0%" stop-color="#0066cc"/>
    <stop offset="100%" stop-color="#55ddff"/></linearGradient>
  <linearGradient id="tabGrad" x1="0" y1="1" x2="0.5" y2="0">
    <stop offset="0%" stop-color="#004488"/>
    <stop offset="100%" stop-color="#0099FF"/></linearGradient>

  <filter id="shadow1" x="-8%" y="-5%" width="116%" height="125%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="5"/>
    <feOffset dx="0" dy="5"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.6"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="shadow2" x="-5%" y="-5%" width="110%" height="120%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="3"/>
    <feOffset dx="0" dy="3"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.5"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>

  <clipPath id="tabClip">
    <path d="M 44 90 L 44 74 C 44 66, 52 60, 62 60
             L 118 60 C 126 60, 130 64, 136 74
             C 142 84, 148 88, 158 88 L 44 88 Z"/>
  </clipPath>
  <clipPath id="bodyClip">
    <path d="M 44 88 L 212 88 L 212 200 C 212 208, 204 214, 196 214
             L 60 214 C 52 214, 44 208, 44 200 Z"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="#000000"/>

<!-- Layer 1: Tab back (darker blue fill) -->
<path d="M 44 90 L 44 74 C 44 66, 52 60, 62 60
         L 118 60 C 126 60, 130 64, 136 74
         C 142 84, 148 88, 158 88 L 44 88 Z"
      fill="url(#tabGrad)" filter="url(#shadow2)"/>

<!-- Layer 2: Tab label strip (dark bar for detail) -->
<rect x="54" y="66" width="50" height="8" rx="4"
      fill="#0a1020" opacity="0.7"/>

<!-- Layer 3: Dark shadow behind front body (blurred, creates depth) -->
<path d="M 46 92 L 210 92 L 210 198 C 210 206, 204 212, 196 212
         L 60 212 C 52 212, 46 206, 46 198 Z"
      fill="#000000" opacity="0.6" filter="url(#shadow1)"/>

<!-- Layer 4: Bright shadow/highlight behind front (white, blurred, adds glow) -->
<path d="M 50 94 L 206 94 L 206 196 C 206 204, 200 208, 194 208
         L 62 208 C 56 208, 50 204, 50 196 Z"
      fill="#0066aa" opacity="0.2" filter="url(#shadow2)"/>

<!-- Layer 5: Main front body (SEMI-TRANSPARENT — 0.65 opacity) -->
<path d="M 44 92 L 212 92 L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#mainGrad)" opacity="0.65" filter="url(#shadow2)"/>

<!-- Layer 6: Tab white highlight (BOLD — this is what Amy does) -->
<g clip-path="url(#tabClip)">
  <rect x="44" y="56" width="120" height="36"
        fill="#ffffff" opacity="0.85"/>
</g>

<!-- Layer 7: Diagonal light ray 1 (strong) -->
<g clip-path="url(#bodyClip)">
  <path d="M 44 92 L 150 92 L 44 172 Z"
        fill="#c8e8ff" opacity="0.12"/>
</g>

<!-- Layer 8: Diagonal light ray 2 (dimmer, wider) -->
<g clip-path="url(#bodyClip)">
  <path d="M 72 92 L 180 92 L 72 192 Z"
        fill="#d0f0ff" opacity="0.05"/>
</g>

<!-- Layer 9: Top edge catch light on front panel -->
<line x1="48" y1="92.5" x2="208" y2="92.5"
      stroke="#ffffff" stroke-width="1" stroke-opacity="0.15"/>
</svg>'''
    render("A_amy_rich", svg)


def b_emergence_rich():
    """B — Emergence with Amy-level richness.
    Dark at bottom, bright at top. Semi-transparent. White tab.
    Strong light rays. Double shadow. 'Light emerging from dark'
    with actual visual weight."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bodyGrad" x1="0.3" y1="1" x2="0.7" y2="0">
    <stop offset="0%" stop-color="#001a33"/>
    <stop offset="35%" stop-color="#004488"/>
    <stop offset="65%" stop-color="#0088dd"/>
    <stop offset="85%" stop-color="#0099FF"/>
    <stop offset="100%" stop-color="#44ccff"/></linearGradient>
  <linearGradient id="tabGrad" x1="0" y1="1" x2="0.3" y2="0">
    <stop offset="0%" stop-color="#003366"/>
    <stop offset="100%" stop-color="#0077cc"/></linearGradient>

  <filter id="lift" x="-8%" y="-5%" width="116%" height="125%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="5"/>
    <feOffset dx="0" dy="6"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="3" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>

  <clipPath id="tabClip">
    <path d="M 44 90 L 44 76 C 44 68, 50 62, 60 62
             L 116 62 C 124 62, 128 66, 134 74
             C 140 82, 146 86, 154 86 L 44 86 Z"/>
  </clipPath>
  <clipPath id="bodyClip">
    <rect x="44" y="90" width="168" height="130" rx="14"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="#000000"/>

<!-- Tab (darker, behind) -->
<path d="M 44 90 L 44 76 C 44 68, 50 62, 60 62
         L 116 62 C 124 62, 128 66, 134 74
         C 140 82, 146 86, 154 86 L 44 86 Z"
      fill="url(#tabGrad)"/>

<!-- Tab highlight (white wash — bright catch-light) -->
<g clip-path="url(#tabClip)">
  <rect x="44" y="58" width="115" height="34"
        fill="#ffffff" opacity="0.75"/>
</g>

<!-- Tab label -->
<rect x="54" y="68" width="46" height="7" rx="3.5"
      fill="#0a1628" opacity="0.6"/>

<!-- Shadow layer behind body -->
<path d="M 46 94 L 210 94 L 210 198 C 210 206, 202 212, 194 212
         L 62 212 C 54 212, 46 206, 46 198 Z"
      fill="#000" opacity="0.5" filter="url(#lift)"/>

<!-- Main body (semi-transparent, rich gradient) -->
<path d="M 44 90 L 212 90 L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#bodyGrad)" opacity="0.7" filter="url(#lift)"/>

<!-- Crease highlight (bright cyan band) -->
<line x1="48" y1="90" x2="208" y2="90"
      stroke="#66eeff" stroke-width="2" stroke-opacity="0.4"
      filter="url(#glow)"/>

<!-- Strong diagonal gloss -->
<g clip-path="url(#bodyClip)">
  <path d="M 44 90 L 160 90 L 44 175 Z"
        fill="#c8e8ff" opacity="0.1"/>
  <path d="M 70 90 L 190 90 L 70 195 Z"
        fill="#d8f0ff" opacity="0.04"/>
</g>
</svg>'''
    render("B_emergence_rich", svg)


def c_circuit_premium():
    """C — Circuit traces on a premium blue surface.
    The folder has VISIBLE circuit traces AND the rich gradient treatment.
    Technology lives in the surface."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bg" x1="0.3" y1="1" x2="0.7" y2="0">
    <stop offset="0%" stop-color="#003060"/>
    <stop offset="50%" stop-color="#0077cc"/>
    <stop offset="100%" stop-color="#0099FF"/></linearGradient>
  <linearGradient id="tabGrad" x1="0" y1="1" x2="0.3" y2="0">
    <stop offset="0%" stop-color="#002244"/>
    <stop offset="100%" stop-color="#005599"/></linearGradient>

  <filter id="lift" x="-8%" y="-5%" width="116%" height="125%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="5"/>
    <feOffset dx="0" dy="5"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>

  <clipPath id="tabClip">
    <path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
             L 114 62 C 122 62, 126 66, 132 74
             C 138 82, 144 86, 152 86 L 44 86 Z"/>
  </clipPath>
  <clipPath id="bodyClip">
    <rect x="44" y="88" width="168" height="130" rx="14"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="#000000"/>

<!-- Tab -->
<path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
         L 114 62 C 122 62, 126 66, 132 74
         C 138 82, 144 86, 152 86 L 44 86 Z"
      fill="url(#tabGrad)"/>
<g clip-path="url(#tabClip)">
  <rect x="44" y="58" width="112" height="32" fill="#fff" opacity="0.7"/>
</g>
<rect x="54" y="68" width="44" height="7" rx="3.5" fill="#0a1628" opacity="0.5"/>

<!-- Shadow -->
<path d="M 46 92 L 210 92 L 210 198 C 210 206, 202 212, 194 212
         L 62 212 C 54 212, 46 206, 46 198 Z"
      fill="#000" opacity="0.5" filter="url(#lift)"/>

<!-- Body -->
<path d="M 44 88 L 212 88 L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#bg)" opacity="0.7" filter="url(#lift)"/>

<!-- Gloss -->
<g clip-path="url(#bodyClip)">
  <path d="M 44 88 L 148 88 L 44 168 Z" fill="#c8e8ff" opacity="0.1"/>
</g>

<!-- CIRCUIT TRACES (the personality element) -->
<g clip-path="url(#bodyClip)" stroke="#88ddff" stroke-linecap="round"
   fill="none" stroke-opacity="0.2" stroke-width="1.2">
  <path d="M 60 110 L 100 110 L 100 130 L 140 130"/>
  <path d="M 160 105 L 160 125 L 195 125"/>
  <path d="M 75 150 L 75 170 L 120 170 L 120 185"/>
  <path d="M 155 145 L 190 145 L 190 170"/>
  <path d="M 60 190 L 95 190"/>
  <!-- Nodes -->
  <circle cx="100" cy="110" r="2.5" fill="#88ddff" fill-opacity="0.25" stroke="none"/>
  <circle cx="140" cy="130" r="2.5" fill="#88ddff" fill-opacity="0.25" stroke="none"/>
  <circle cx="160" cy="125" r="2.5" fill="#88ddff" fill-opacity="0.25" stroke="none"/>
  <circle cx="120" cy="170" r="2.5" fill="#88ddff" fill-opacity="0.25" stroke="none"/>
  <circle cx="75" cy="170" r="2.5" fill="#88ddff" fill-opacity="0.25" stroke="none"/>
</g>
</svg>'''
    render("C_circuit_premium", svg)


def d_pulse_alive():
    """D — The pulse lives in the folder.
    Premium blue gradient body with the ECG pulse waveform visible
    as a subtle embossed line across the lower portion. The folder
    is alive — it has a heartbeat."""
    pulse_d = "M 55 165 L 95 165 L 104 172 L 116 148 L 128 186 L 136 158 L 144 165 L 200 165"

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bodyGrad" x1="0.4" y1="1" x2="0.6" y2="0">
    <stop offset="0%" stop-color="#002244"/>
    <stop offset="40%" stop-color="#005599"/>
    <stop offset="75%" stop-color="#0099FF"/>
    <stop offset="100%" stop-color="#33ccff"/></linearGradient>
  <linearGradient id="tabGrad" x1="0" y1="1" x2="0.3" y2="0">
    <stop offset="0%" stop-color="#002244"/>
    <stop offset="100%" stop-color="#006699"/></linearGradient>

  <filter id="lift" x="-8%" y="-5%" width="116%" height="125%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="5"/>
    <feOffset dx="0" dy="5"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="2.5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>

  <clipPath id="tabClip">
    <path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
             L 114 62 C 122 62, 126 66, 132 74
             C 138 82, 144 86, 152 86 L 44 86 Z"/>
  </clipPath>
  <clipPath id="bodyClip">
    <rect x="44" y="88" width="168" height="130" rx="14"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="#000000"/>

<!-- Tab -->
<path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
         L 114 62 C 122 62, 126 66, 132 74
         C 138 82, 144 86, 152 86 L 44 86 Z"
      fill="url(#tabGrad)"/>
<g clip-path="url(#tabClip)">
  <rect x="44" y="58" width="112" height="32" fill="#fff" opacity="0.7"/>
</g>
<rect x="54" y="68" width="44" height="7" rx="3.5" fill="#0a1628" opacity="0.5"/>

<!-- Shadow -->
<path d="M 46 92 L 210 92 L 210 198 C 210 206, 202 212, 194 212
         L 62 212 C 54 212, 46 206, 46 198 Z"
      fill="#000" opacity="0.5" filter="url(#lift)"/>

<!-- Body -->
<path d="M 44 88 L 212 88 L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#bodyGrad)" opacity="0.7" filter="url(#lift)"/>

<!-- Gloss -->
<g clip-path="url(#bodyClip)">
  <path d="M 44 88 L 150 88 L 44 165 Z" fill="#c8e8ff" opacity="0.1"/>
</g>

<!-- THE PULSE — embossed into the folder surface -->
<g clip-path="url(#bodyClip)">
  <!-- Pulse shadow (dark, offset down) -->
  <path d="{pulse_d}" fill="none"
        stroke="#001133" stroke-width="2.5" stroke-opacity="0.3"
        stroke-linecap="round" stroke-linejoin="round"
        transform="translate(0, 1.5)"/>
  <!-- Pulse highlight (bright, on top) -->
  <path d="{pulse_d}" fill="none"
        stroke="#88eeff" stroke-width="1.5" stroke-opacity="0.35"
        stroke-linecap="round" stroke-linejoin="round"
        filter="url(#glow)"/>
</g>
</svg>'''
    render("D_pulse_alive", svg)


def e_holographic_premium():
    """E — Holographic dark glass with scan lines and edge glow.
    The folder is a projection — dark glass with holographic edges,
    faint scan lines, and blue energy. Premium futuristic."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bodyGrad" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0e1830" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="#060c18" stop-opacity="0.6"/></linearGradient>
  <linearGradient id="holo" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0055aa"/>
    <stop offset="25%" stop-color="#0099FF"/>
    <stop offset="50%" stop-color="#33ddff"/>
    <stop offset="75%" stop-color="#0099FF"/>
    <stop offset="100%" stop-color="#0055aa"/></linearGradient>
  <linearGradient id="tabGrad" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#0e1830" stop-opacity="0.6"/>
    <stop offset="100%" stop-color="#060c18" stop-opacity="0.4"/></linearGradient>

  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="softglow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="7"/></filter>

  <clipPath id="allClip">
    <path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
             L 114 62 C 122 62, 126 66, 132 74
             C 138 82, 144 86, 152 86
             L 196 86 C 204 86, 212 94, 212 102
             L 212 200 C 212 208, 204 214, 196 214
             L 60 214 C 52 214, 44 208, 44 200 Z"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="#000000"/>

<!-- Wide holographic glow (ambient) -->
<path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
         L 114 62 C 122 62, 126 66, 132 74
         C 138 82, 144 86, 152 86
         L 196 86 C 204 86, 212 94, 212 102
         L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="none" stroke="url(#holo)" stroke-width="4" stroke-opacity="0.15"
      filter="url(#softglow)"/>

<!-- Body fill (dark glass) -->
<path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
         L 114 62 C 122 62, 126 66, 132 74
         C 138 82, 144 86, 152 86
         L 196 86 C 204 86, 212 94, 212 102
         L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#bodyGrad)"/>

<!-- Crease separator -->
<line x1="44" y1="116" x2="212" y2="116"
      stroke="#33ddff" stroke-width="1.5" stroke-opacity="0.45"
      filter="url(#glow)"/>

<!-- Front panel (slightly different opacity) -->
<path d="M 44 116 L 212 116 L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="#0c1628" opacity="0.35"/>

<!-- Holographic edge (sharp, bright) -->
<path d="M 44 88 L 44 76 C 44 68, 50 62, 60 62
         L 114 62 C 122 62, 126 66, 132 74
         C 138 82, 144 86, 152 86
         L 196 86 C 204 86, 212 94, 212 102
         L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="none" stroke="url(#holo)" stroke-width="2" stroke-opacity="0.55"
      filter="url(#glow)"/>

<!-- Scan lines (subtle horizontal lines — holographic texture) -->
<g clip-path="url(#allClip)" stroke="#0099FF" stroke-opacity="0.04" stroke-width="0.5">
  <line x1="44" y1="70" x2="212" y2="70"/>
  <line x1="44" y1="80" x2="212" y2="80"/>
  <line x1="44" y1="90" x2="212" y2="90"/>
  <line x1="44" y1="100" x2="212" y2="100"/>
  <line x1="44" y1="110" x2="212" y2="110"/>
  <line x1="44" y1="126" x2="212" y2="126"/>
  <line x1="44" y1="136" x2="212" y2="136"/>
  <line x1="44" y1="146" x2="212" y2="146"/>
  <line x1="44" y1="156" x2="212" y2="156"/>
  <line x1="44" y1="166" x2="212" y2="166"/>
  <line x1="44" y1="176" x2="212" y2="176"/>
  <line x1="44" y1="186" x2="212" y2="186"/>
  <line x1="44" y1="196" x2="212" y2="196"/>
  <line x1="44" y1="206" x2="212" y2="206"/>
</g>

<!-- Top edge catch light -->
<path d="M 62 63 L 112 63" fill="none"
      stroke="#ffffff" stroke-width="1" stroke-opacity="0.2"
      stroke-linecap="round"/>
</svg>'''
    render("E_holographic_premium", svg)


if __name__ == "__main__":
    a_amy_style()
    b_emergence_rich()
    c_circuit_premium()
    d_pulse_alive()
    e_holographic_premium()
    print("Done — 5 rich concepts.")
