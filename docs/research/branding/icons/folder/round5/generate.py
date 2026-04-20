#!/usr/bin/env python3
"""
InterGenOS Folder Icon — Round 5
INFORMED BY REAL THEMES. Not guessing anymore.

What the reference themes taught us:
  1. Gradient color washes across the surface — the folder IS color, not dark-with-trim
  2. Diagonal light ray reflections — triangular highlights for glossy premium feel
  3. Spine/depth on the left edge — instant 3D without skeuomorphism
  4. The tab is part of the back panel path — never a separate bolted-on element
  5. The folder should GLOW against a dark desktop — visible, alive, present
  6. Each folder variant gets a symbolic icon on the face (home, documents, etc.)

Our voice:
  - ECG Blue gradient (#0099FF to #33b1ff) as the primary color wash
  - Deep navy (#050810 to #0a0e1a) as the dark base under the gradient
  - "Light emerging from dark" — the gradient should feel like blue energy
    illuminating a dark container from within
  - Clean construction: minimal paths, no Inkscape bloat, pure SVG primitives
  - Modeled after Adwaita's 3-path efficiency, Amy/Gradient's visual richness
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path(__file__).parent / "svg"
OUT_PNG = Path(__file__).parent / "png"

W, H = 256, 256
BG = "#000000"


def render(name, svg_str):
    (OUT_SVG / f"{name}.svg").write_text(svg_str)
    for size in [256, 128, 64, 48]:
        suffix = "" if size == 256 else f"_{size}"
        cairosvg.svg2png(bytestring=svg_str.encode(),
                         write_to=str(OUT_PNG / f"{name}{suffix}.png"),
                         output_width=size, output_height=size)
    print(f"  {name}")


def concept_a():
    """A — Blue Energy Gradient.
    Adwaita-efficient construction (back + highlight + front = 3 layers).
    But with our blue gradient and dark undertones. The folder is a luminous
    blue-gradient object sitting on darkness. Diagonal light reflection
    adds premium polish."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <!-- Back panel: darker blue -->
  <linearGradient id="backA" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0066aa"/>
    <stop offset="100%" stop-color="#003366"/>
  </linearGradient>

  <!-- Front panel: brighter blue gradient with edge highlights -->
  <linearGradient id="frontA" x1="0" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0099FF"/>
    <stop offset="0.06" stop-color="#40aaff"/>
    <stop offset="0.12" stop-color="#0099FF"/>
    <stop offset="0.88" stop-color="#0088ee"/>
    <stop offset="0.95" stop-color="#33a8ff"/>
    <stop offset="1" stop-color="#0080dd"/>
  </linearGradient>

  <!-- Highlight band between panels -->
  <linearGradient id="highlightA" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#66ccff"/>
    <stop offset="50%" stop-color="#55bbff"/>
    <stop offset="100%" stop-color="#66ccff"/>
  </linearGradient>

  <!-- Light reflection (diagonal gloss) -->
  <linearGradient id="glossA" x1="0" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.15"/>
    <stop offset="40%" stop-color="#ffffff" stop-opacity="0.03"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.5"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- BACK PANEL (darker blue, includes tab) -->
<path d="M 54 90
         C 54 82, 48 74, 48 74
         L 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 48 88, 54 90 Z"
      fill="url(#backA)" filter="url(#shadow)"/>

<!-- HIGHLIGHT BAND (thin bright strip between panels) -->
<path d="M 44 116 L 212 116 L 212 120 L 44 120 Z"
      fill="url(#highlightA)" opacity="0.5"/>

<!-- FRONT PANEL (brighter blue gradient) -->
<path d="M 44 120
         L 212 120
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         Z"
      fill="url(#frontA)"/>

<!-- DIAGONAL LIGHT REFLECTION -->
<path d="M 44 120 L 130 120 L 44 180 Z"
      fill="url(#glossA)"/>

<!-- SPINE (left edge depth — darker strip) -->
<path d="M 44 100 C 44 92, 48 88, 54 90
         C 54 82, 48 74, 48 74 L 48 72
         C 48 64, 54 58, 62 58 L 64 58
         L 48 58 C 48 58, 44 64, 44 72
         L 44 192 C 44 200, 50 206, 58 206
         L 44 206 L 44 100 Z"
      fill="#003060" opacity="0.6"/>
</svg>'''
    render("A_blue_energy", svg)


def concept_b():
    """B — Dark-to-Blue Emergence.
    The folder transitions from near-black at the bottom to vivid blue
    at the top — literally 'light emerging from dark.' The tab area is
    the brightest, the base is the darkest. The energy rises."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="backB" x1="0" y1="1" x2="0" y2="0">
    <stop offset="0%" stop-color="#050810"/>
    <stop offset="60%" stop-color="#0a2040"/>
    <stop offset="100%" stop-color="#0066aa"/>
  </linearGradient>

  <linearGradient id="frontB" x1="0" y1="1" x2="0.3" y2="0">
    <stop offset="0%" stop-color="#080c18"/>
    <stop offset="50%" stop-color="#0a2848"/>
    <stop offset="100%" stop-color="#0088dd"/>
  </linearGradient>

  <linearGradient id="highlightB" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.6"/>
    <stop offset="50%" stop-color="#33b1ff" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="#0099FF" stop-opacity="0.6"/>
  </linearGradient>

  <linearGradient id="glossB" x1="0" y1="0" x2="0.6" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.12"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.5"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 72 C 44 64, 48 64, 48 72 Z"
      fill="url(#backB)" filter="url(#shadow)"/>

<!-- Highlight band -->
<path d="M 44 118 L 212 118 L 212 122 L 44 122 Z"
      fill="url(#highlightB)"/>

<!-- Front panel -->
<path d="M 44 122 L 212 122
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="url(#frontB)"/>

<!-- Light reflection -->
<path d="M 44 122 L 140 122 L 44 185 Z"
      fill="url(#glossB)"/>

<!-- Tab top highlight -->
<path d="M 58 60 L 112 60 C 116 60, 120 62, 124 68"
      fill="none" stroke="#33b1ff" stroke-width="1.5" stroke-opacity="0.4"
      stroke-linecap="round"/>
</svg>'''
    render("B_dark_to_blue", svg)


def concept_c():
    """C — Vivid Blue Glass.
    Inspired by Vivid-Glassy: semi-transparent blue with visible
    depth. The folder feels like tinted glass — you can almost see
    through it. Gradient from deep blue to bright cyan. Strong
    highlight band at the crease. Premium polish."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="backC" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0077cc"/>
    <stop offset="100%" stop-color="#004488"/>
  </linearGradient>

  <linearGradient id="frontC" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.85"/>
    <stop offset="0.5" stop-color="#0088ee" stop-opacity="0.9"/>
    <stop offset="1" stop-color="#0070cc" stop-opacity="0.85"/>
  </linearGradient>

  <linearGradient id="shineC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.18"/>
    <stop offset="50%" stop-color="#ffffff" stop-opacity="0.04"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <linearGradient id="edgeC" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#33bbff" stop-opacity="0.6"/>
    <stop offset="50%" stop-color="#66ddff" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="#33bbff" stop-opacity="0.6"/>
  </linearGradient>

  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="7" flood-color="#002244" flood-opacity="0.6"/>
  </filter>
  <filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="2"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 50 86, 54 86 Z"
      fill="url(#backC)" filter="url(#shadow)"/>

<!-- Crease highlight band -->
<path d="M 44 115 L 212 115 L 212 121 L 44 121 Z"
      fill="url(#edgeC)"/>

<!-- Crease glow -->
<line x1="60" y1="118" x2="196" y2="118"
      stroke="#66ddff" stroke-width="1" stroke-opacity="0.4"
      filter="url(#glow)"/>

<!-- Front panel -->
<path d="M 44 120 L 212 120
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="url(#frontC)"/>

<!-- Front panel top shine -->
<path d="M 44 120 L 212 120 L 212 155 L 44 155 Z"
      fill="url(#shineC)"/>

<!-- Diagonal light ray -->
<path d="M 44 120 L 120 120 L 44 175 Z"
      fill="#ffffff" opacity="0.06"/>
<path d="M 60 120 L 145 120 L 60 182 Z"
      fill="#ffffff" opacity="0.03"/>

<!-- Spine depth (left edge darker) -->
<path d="M 44 100 L 48 100 L 48 192 C 48 198, 52 204, 58 206
         L 44 206 L 44 192 L 44 100 Z"
      fill="#003366" opacity="0.5"/>
</svg>'''
    render("C_vivid_glass", svg)


def concept_d():
    """D — Tron-Line Folder.
    Dark navy body with bright blue edge lines tracing the structure.
    Like Infinity-Dark's outline approach but with our palette.
    The body is filled dark, but every structural edge glows blue.
    The outline IS the energy."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bodyD" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0c1628"/>
    <stop offset="100%" stop-color="#060a14"/>
  </linearGradient>

  <linearGradient id="frontD" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#101c34"/>
    <stop offset="100%" stop-color="#080e1c"/>
  </linearGradient>

  <linearGradient id="edgeGrad" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0099FF"/>
    <stop offset="50%" stop-color="#33b1ff"/>
    <stop offset="100%" stop-color="#0099FF"/>
  </linearGradient>

  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="2.5" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="shadow">
    <feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="#000" flood-opacity="0.5"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel fill (dark) -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 50 86, 54 86 Z"
      fill="url(#bodyD)" filter="url(#shadow)"/>

<!-- Front panel fill (slightly lighter dark) -->
<path d="M 44 120 L 212 120
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="url(#frontD)"/>

<!-- ENERGY EDGES — the blue tron lines -->
<!-- Back panel outline -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 50 86, 54 86 Z"
      fill="none" stroke="url(#edgeGrad)" stroke-width="2" stroke-opacity="0.7"
      stroke-linejoin="round" filter="url(#glow)"/>

<!-- Front panel outline -->
<path d="M 44 120 L 212 120
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="none" stroke="url(#edgeGrad)" stroke-width="1.5" stroke-opacity="0.5"
      stroke-linejoin="round" filter="url(#glow)"/>

<!-- Crease (brightest line) -->
<line x1="44" y1="120" x2="212" y2="120"
      stroke="#33b1ff" stroke-width="2" stroke-opacity="0.8"
      filter="url(#glow)"/>
</svg>'''
    render("D_tron_lines", svg)


def concept_e():
    """E — Deep Blue Gradient with Spine.
    Inspired by Slot-Beauty's spine effect. The folder has a visible
    3D spine on the left. The body sweeps from deep navy to bright
    blue. Clean, premium, dimensional."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="backE" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#005599"/>
    <stop offset="100%" stop-color="#002244"/>
  </linearGradient>

  <linearGradient id="frontE" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0099FF"/>
    <stop offset="0.06" stop-color="#44bbff"/>
    <stop offset="0.15" stop-color="#0099FF"/>
    <stop offset="0.85" stop-color="#0088ee"/>
    <stop offset="0.96" stop-color="#44bbff"/>
    <stop offset="1" stop-color="#0080dd"/>
  </linearGradient>

  <linearGradient id="spineE" x1="1" y1="0" x2="0" y2="0.5">
    <stop offset="0%" stop-color="#004477"/>
    <stop offset="100%" stop-color="#002244"/>
  </linearGradient>

  <linearGradient id="glossE" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.14"/>
    <stop offset="30%" stop-color="#ffffff" stop-opacity="0.03"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#001133" flood-opacity="0.6"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- SPINE (3D left edge — drawn first, behind everything) -->
<path d="M 40 72 C 40 64, 44 58, 50 58
         L 52 58 L 48 62 C 44 66, 42 70, 42 76
         L 42 194 C 42 200, 46 206, 52 208
         L 50 210 C 42 210, 38 204, 38 196
         L 38 80 C 38 72, 40 68, 40 72 Z"
      fill="url(#spineE)"/>

<!-- Back panel -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 112 58 C 118 58, 122 60, 126 66
         L 132 78 C 136 84, 140 86, 148 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 50 86, 54 86 Z"
      fill="url(#backE)" filter="url(#shadow)"/>

<!-- Highlight band at crease -->
<path d="M 44 116 L 212 116 L 212 122 L 44 122 Z"
      fill="#44bbff" opacity="0.35"/>

<!-- Front panel -->
<path d="M 44 122 L 212 122
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="url(#frontE)"/>

<!-- Diagonal gloss -->
<path d="M 44 122 L 135 122 L 44 185 Z"
      fill="url(#glossE)"/>

<!-- Second lighter gloss ray -->
<path d="M 65 122 L 160 122 L 65 190 Z"
      fill="#ffffff" opacity="0.02"/>
</svg>'''
    render("E_deep_blue_spine", svg)


def concept_f():
    """F — Emergence.
    The definitive InterGenOS folder. Combines everything learned:
    - Dark navy base with blue gradient emergence (bottom dark, top bright)
    - Spine for 3D depth
    - Diagonal light reflections for polish
    - Bright crease band
    - The tab as organic part of the shape
    - The folder GLOWS on the dark desktop"""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <!-- Back: deep navy to medium blue, bottom-to-top emergence -->
  <linearGradient id="backF" x1="0.2" y1="1" x2="0.5" y2="0">
    <stop offset="0%" stop-color="#0a1628"/>
    <stop offset="50%" stop-color="#003870"/>
    <stop offset="100%" stop-color="#0066aa"/>
  </linearGradient>

  <!-- Front: darker at bottom, brighter at top — light emerges at the crease -->
  <linearGradient id="frontF" x1="0.2" y1="1" x2="0.5" y2="0">
    <stop offset="0%" stop-color="#0a1628"/>
    <stop offset="40%" stop-color="#003060"/>
    <stop offset="85%" stop-color="#0088dd"/>
    <stop offset="0.92" stop-color="#33aaff"/>
    <stop offset="1" stop-color="#0099FF"/>
  </linearGradient>

  <!-- Spine: darker version of back -->
  <linearGradient id="spineF" x1="1" y1="0" x2="0" y2="0.3">
    <stop offset="0%" stop-color="#0a1628"/>
    <stop offset="100%" stop-color="#060e1c"/>
  </linearGradient>

  <!-- Crease band: bright blue -->
  <linearGradient id="creaseF" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.5"/>
    <stop offset="50%" stop-color="#44ccff" stop-opacity="0.7"/>
    <stop offset="100%" stop-color="#0099FF" stop-opacity="0.5"/>
  </linearGradient>

  <!-- Diagonal gloss -->
  <linearGradient id="glossF" x1="0" y1="0" x2="0.6" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.1"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <filter id="shadow">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#001133" flood-opacity="0.55"/>
  </filter>
  <filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="2"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Spine (3D left edge) -->
<path d="M 38 76 C 38 66, 42 60, 50 58
         L 52 58 L 46 62 C 42 66, 40 72, 40 78
         L 40 194 C 40 202, 44 208, 52 210
         L 48 210 C 40 208, 36 202, 36 194
         L 36 84 C 36 76, 38 72, 38 76 Z"
      fill="url(#spineF)"/>

<!-- Back panel with tab -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 114 58 C 120 58, 124 60, 128 66
         L 134 78 C 138 84, 142 86, 150 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 48 88, 54 88 Z"
      fill="url(#backF)" filter="url(#shadow)"/>

<!-- Crease highlight band -->
<path d="M 44 116 L 212 116 L 212 122 L 44 122 Z"
      fill="url(#creaseF)"/>

<!-- Crease glow line -->
<line x1="52" y1="119" x2="204" y2="119"
      stroke="#44ccff" stroke-width="1" stroke-opacity="0.3"
      filter="url(#glow)"/>

<!-- Front panel -->
<path d="M 44 122 L 212 122
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192 Z"
      fill="url(#frontF)"/>

<!-- Diagonal light reflection -->
<path d="M 44 122 L 130 122 L 44 180 Z"
      fill="url(#glossF)"/>

<!-- Tab top edge highlight -->
<path d="M 60 60 L 112 60 C 118 60, 122 62, 126 68"
      fill="none" stroke="#33bbff" stroke-width="1" stroke-opacity="0.3"
      stroke-linecap="round"/>

<!-- Very subtle overall outer edge -->
<path d="M 48 72 C 48 64, 54 58, 62 58
         L 114 58 C 120 58, 124 60, 128 66
         L 134 78 C 138 84, 142 86, 150 86
         L 198 86 C 206 86, 212 92, 212 100
         L 212 192 C 212 200, 206 206, 198 206
         L 58 206 C 50 206, 44 200, 44 192
         L 44 100 C 44 92, 48 88, 54 88 Z"
      fill="none" stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.1"/>
</svg>'''
    render("F_emergence", svg)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 5 — informed by reference themes:")
    concept_a()
    concept_b()
    concept_c()
    concept_d()
    concept_e()
    concept_f()
    print("Done.")
