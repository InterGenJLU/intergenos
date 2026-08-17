#!/usr/bin/env python3
"""
InterGenOS Folder Icon — Round 3
COMPLETE REDESIGN. Surfaces, not outlines. Objects, not diagrams.

What was wrong with R1/R2:
  - Tab was a crude protruding bump (filing cabinet from the 90s)
  - Shapes were flat rectangles with lines overlaid
  - No sense of volume, depth, or modernity
  - Looked like technical diagrams, not objects

R3 approach:
  - The folder is a SURFACE-BASED object, not an outlined shape
  - Multiple overlapping filled layers create depth
  - The tab is barely there — a gentle swell in the back panel
  - The "opening" between back and front panels reveals blue from INSIDE
  - Complex gradients suggest light and material
  - The overall shape is ROUND, ORGANIC, GENEROUS
  - Wider aspect ratio (~1.5:1), much more modern proportions
  - Think: dark glass container with blue energy inside
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path(__file__).parent / "svg"
OUT_PNG = Path(__file__).parent / "png"

BG = "#000000"
ACCENT = "#0099FF"
ACCENT_BRIGHT = "#33b1ff"

W, H = 256, 256


def render(name, svg_content):
    (OUT_SVG / f"{name}.svg").write_text(svg_content)
    for size in [256, 128, 64, 48]:
        suffix = "" if size == 256 else f"_{size}"
        cairosvg.svg2png(bytestring=svg_content.encode(),
                         write_to=str(OUT_PNG / f"{name}{suffix}.png"),
                         output_width=size, output_height=size)
    print(f"  {name}")


def concept_a():
    """A — Dark glass container.
    The folder is a dark, rounded, padded shape. The back panel barely
    rises into a subtle tab swell. The gap between back and front panels
    emits a blue glow — you can see energy inside."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="innershadow" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="3" result="blur"/>
    <feOffset dx="0" dy="2" result="offset"/>
    <feComposite in="offset" in2="SourceAlpha" operator="in" result="shadow"/>
    <feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>

  <!-- Back panel body gradient -->
  <linearGradient id="backBody" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#111a2e"/>
    <stop offset="40%" stop-color="#0c1222"/>
    <stop offset="100%" stop-color="#080c18"/>
  </linearGradient>

  <!-- Front panel gradient — slightly lighter, surface catch -->
  <linearGradient id="frontBody" x1="0" y1="0" x2="0.3" y2="1">
    <stop offset="0%" stop-color="#151f35"/>
    <stop offset="15%" stop-color="#121a2c"/>
    <stop offset="100%" stop-color="#0a1020"/>
  </linearGradient>

  <!-- Interior glow (visible through the gap) -->
  <linearGradient id="interiorGlow" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.35"/>
    <stop offset="60%" stop-color="{ACCENT}" stop-opacity="0.12"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0.03"/>
  </linearGradient>

  <!-- Top highlight on front panel -->
  <linearGradient id="topHighlight" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.04"/>
    <stop offset="30%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>

  <!-- Bottom spine gradient -->
  <linearGradient id="spineGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Shadow beneath folder -->
<ellipse cx="128" cy="210" rx="72" ry="6"
         fill="black" opacity="0.4" filter="url(#glow)"/>

<!-- BACK PANEL — the container, with a very gentle tab swell -->
<path d="M 42 96
         C 42 86, 46 80, 56 78
         L 82 74
         C 88 72, 94 68, 98 66
         C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68
         C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88
         L 200 88
         C 210 88, 216 94, 216 104
         L 216 188
         C 216 196, 210 202, 200 202
         L 56 202
         C 46 202, 42 196, 42 188
         Z"
      fill="url(#backBody)"/>

<!-- INTERIOR GLOW — visible in the gap between back and front panels -->
<rect x="46" y="108" width="166" height="18" rx="4"
      fill="url(#interiorGlow)"/>

<!-- Interior glow line (the bright crease) -->
<line x1="52" y1="118" x2="206" y2="118"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.3"
      filter="url(#glow)"/>

<!-- FRONT PANEL — overlaps lower portion, rounded -->
<path d="M 42 122
         L 216 122
         L 216 188
         C 216 196, 210 202, 200 202
         L 56 202
         C 46 202, 42 196, 42 188
         Z"
      fill="url(#frontBody)"/>

<!-- Top highlight on front panel -->
<path d="M 42 122 L 216 122 L 216 142 L 42 142 Z"
      fill="url(#topHighlight)"/>

<!-- SPINE / bottom thickness -->
<path d="M 48 202 C 48 206, 50 210, 58 210
         L 200 210 C 208 210, 212 206, 212 202"
      fill="url(#spineGrad)" stroke="none"/>

<!-- Subtle edge highlights -->
<path d="M 42 96
         C 42 86, 46 80, 56 78 L 82 74 C 88 72, 94 68, 98 66
         C 102 64, 108 62, 114 62 C 120 62, 124 64, 126 68
         C 128 72, 130 76, 134 80 C 138 84, 142 86, 148 88
         L 200 88 C 210 88, 216 94, 216 104
         L 216 188 C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.08"/>

<!-- Front panel top edge — subtle blue accent -->
<line x1="44" y1="122" x2="214" y2="122"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.15"/>
</svg>'''
    render("A_dark_glass", svg)


def concept_b():
    """B — Glowing interior container.
    Same refined shape but with stronger interior glow visible through
    the gap. The folder looks like it contains blue energy."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="bigglow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="8" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>

  <linearGradient id="backB" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#111a2e"/>
    <stop offset="100%" stop-color="#080c18"/>
  </linearGradient>
  <linearGradient id="frontB" x1="0" y1="0" x2="0.2" y2="1">
    <stop offset="0%" stop-color="#151f35"/>
    <stop offset="100%" stop-color="#0a1020"/>
  </linearGradient>
  <linearGradient id="frontHighB" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.05"/>
    <stop offset="25%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="spineB" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>

  <!-- Strong interior glow -->
  <radialGradient id="interiorGlowB" cx="0.5" cy="0.5" r="0.6">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.5"/>
    <stop offset="50%" stop-color="{ACCENT}" stop-opacity="0.2"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/>
  </radialGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Shadow -->
<ellipse cx="128" cy="212" rx="70" ry="5" fill="black" opacity="0.35"/>

<!-- Back panel -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="url(#backB)"/>

<!-- Interior glow (radial, visible through gap) -->
<rect x="44" y="104" width="170" height="22" rx="6"
      fill="url(#interiorGlowB)"/>

<!-- Bright interior line -->
<line x1="52" y1="116" x2="206" y2="116"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Atmospheric glow spilling up from gap -->
<rect x="60" y="100" width="140" height="24" rx="8"
      fill="{ACCENT}" opacity="0.04" filter="url(#bigglow)"/>

<!-- Front panel -->
<path d="M 42 122 L 216 122 L 216 188
         C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="url(#frontB)"/>

<!-- Front highlight -->
<path d="M 42 122 L 216 122 L 216 138 L 42 138 Z"
      fill="url(#frontHighB)"/>

<!-- Spine -->
<path d="M 48 202 C 48 206, 52 210, 58 210
         L 200 210 C 206 210, 212 206, 212 202"
      fill="url(#spineB)"/>

<!-- Subtle structural edge -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.1"/>
</svg>
'''
    render("B_glowing_interior", svg)


def concept_c():
    """C — Frosted dark container with blue light leak.
    The front panel is slightly translucent. Blue light from inside
    bleeds through the top edge of the front panel and the gap."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>

  <linearGradient id="backC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#10182a"/>
    <stop offset="100%" stop-color="#080c18"/>
  </linearGradient>
  <linearGradient id="frontC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#182040"/>
    <stop offset="8%" stop-color="#141c30"/>
    <stop offset="100%" stop-color="#0c1222"/>
  </linearGradient>
  <!-- Blue light bleed at top of front panel -->
  <linearGradient id="bleedC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.12"/>
    <stop offset="20%" stop-color="{ACCENT}" stop-opacity="0.04"/>
    <stop offset="40%" stop-color="{ACCENT}" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="spineC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Shadow -->
<ellipse cx="128" cy="212" rx="68" ry="5" fill="black" opacity="0.3"/>

<!-- Back panel -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="url(#backC)"/>

<!-- Interior — blue glow strip visible in the gap -->
<rect x="46" y="108" width="166" height="16" rx="3"
      fill="{ACCENT}" opacity="0.15"/>
<line x1="50" y1="116" x2="208" y2="116"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.4"
      filter="url(#glow)"/>

<!-- Front panel -->
<path d="M 42 120 L 216 120 L 216 188
         C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="url(#frontC)"/>

<!-- Blue light bleed through top of front panel -->
<path d="M 42 120 L 216 120 L 216 160 L 42 160 Z"
      fill="url(#bleedC)"/>

<!-- Spine -->
<path d="M 48 202 C 48 206, 52 210, 58 210
         L 200 210 C 206 210, 212 206, 212 202"
      fill="url(#spineC)"/>

<!-- Front panel subtle top highlight -->
<line x1="46" y1="121" x2="212" y2="121"
      stroke="#ffffff" stroke-width="0.5" stroke-opacity="0.06"/>
</svg>'''
    render("C_light_bleed", svg)


def concept_d():
    """D — Layered dark with accent crease.
    Clean modern folder. The tab is just a gentle bump in the back panel's
    top edge. Strong separation between panels via the illuminated crease.
    Most 'conventional' of the set but with premium dark finish."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3.5" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>

  <linearGradient id="backD" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#111a2e"/>
    <stop offset="100%" stop-color="#070b16"/>
  </linearGradient>
  <linearGradient id="frontD" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#161e34"/>
    <stop offset="15%" stop-color="#121a2c"/>
    <stop offset="100%" stop-color="#0b1020"/>
  </linearGradient>
  <linearGradient id="spineD" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Shadow -->
<ellipse cx="128" cy="211" rx="68" ry="5" fill="black" opacity="0.35"/>

<!-- Back panel with gentle tab swell -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="url(#backD)"/>

<!-- Gap accent — blue energy at the crease -->
<line x1="46" y1="120" x2="212" y2="120"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.55"
      filter="url(#glow)"/>

<!-- Front panel -->
<path d="M 42 122 L 216 122 L 216 188
         C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="url(#frontD)"/>

<!-- Front panel top edge highlight (white catch-light) -->
<line x1="46" y1="122.5" x2="212" y2="122.5"
      stroke="#ffffff" stroke-width="0.75" stroke-opacity="0.06"/>

<!-- Spine -->
<path d="M 48 202 C 48 206, 52 210, 58 210
         L 200 210 C 206 210, 212 206, 212 202"
      fill="url(#spineD)"/>

<!-- Tab top edge — subtle accent trace -->
<path d="M 60 76 C 66 72, 76 68, 88 66
         C 96 64, 106 62, 114 62
         C 120 62, 124 64, 126 68"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.2"
      stroke-linecap="round"/>
</svg>'''
    render("D_premium_modern", svg)


def concept_e():
    """E — Energy-seamed container.
    Every structural seam of the folder carries a subtle blue trace.
    The overall body is dark and premium. The blue traces are like
    veins of energy running through the structure's joints."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>

  <linearGradient id="backE" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#10182a"/>
    <stop offset="100%" stop-color="#070b16"/>
  </linearGradient>
  <linearGradient id="frontE" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#141c30"/>
    <stop offset="100%" stop-color="#0b1020"/>
  </linearGradient>
  <linearGradient id="spineE" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Shadow -->
<ellipse cx="128" cy="211" rx="68" ry="5" fill="black" opacity="0.3"/>

<!-- Back panel -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="url(#backE)"/>

<!-- Front panel -->
<path d="M 42 120 L 216 120 L 216 188
         C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="url(#frontE)"/>

<!-- Spine -->
<path d="M 48 202 C 48 206, 52 210, 58 210
         L 200 210 C 206 210, 212 206, 212 202"
      fill="url(#spineE)"/>

<!-- ENERGY SEAMS -->

<!-- Seam 1: outer edge of whole folder (dim) -->
<path d="M 42 96 C 42 86, 46 80, 56 78 L 82 74
         C 88 72, 94 68, 98 66 C 102 64, 108 62, 114 62
         C 120 62, 124 64, 126 68 C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88 L 200 88
         C 210 88, 216 94, 216 104 L 216 188
         C 216 196, 210 202, 200 202 L 56 202
         C 46 202, 42 196, 42 188 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.15"/>

<!-- Seam 2: front panel edge (brighter — it's closer to viewer) -->
<path d="M 42 120 L 216 120 L 216 188
         C 216 196, 210 202, 200 202
         L 56 202 C 46 202, 42 196, 42 188 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.25"/>

<!-- Seam 3: the crease (brightest — the energy junction) -->
<line x1="44" y1="120" x2="214" y2="120"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.6"
      filter="url(#glow)"/>

<!-- Seam 4: tab curve (second brightest) -->
<path d="M 58 78 C 64 74, 76 70, 88 68
         C 96 66, 106 63, 114 63
         C 120 63, 124 65, 126 68
         C 128 72, 130 76, 134 80
         C 138 84, 142 86, 148 88"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.35"
      stroke-linecap="round" filter="url(#glow)"/>

<!-- Seam 5: spine bottom -->
<path d="M 48 202 C 48 206, 52 210, 58 210
         L 200 210 C 206 210, 212 206, 212 202"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.15"/>
</svg>'''
    render("E_energy_seams", svg)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 3 — redesigned folders:")
    concept_a()
    concept_b()
    concept_c()
    concept_d()
    concept_e()
    print("Done.")
