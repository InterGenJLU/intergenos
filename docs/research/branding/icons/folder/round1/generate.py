#!/usr/bin/env python3
"""
InterGenOS Folder Icon — Round 1
Exploring the "dark body, glowing seams" concept.

Design principles from the visual language:
  - Canvas: 256x256
  - Primary stroke: 12px (4.7%)
  - Margin: 40px (16% padding)
  - Body: --bg-card (#0f1525) or similar dark
  - Accent: --accent (#0099FF) at structural edges only
  - "Glow, don't tint" — blue is light emission at seams, not color fill
  - Rounded corners per the radius scale
  - Front-facing, wider than tall, tab on top-left
  - Two-plane: back panel (with tab) + front panel (flap)

Variations explore:
  A — Outlined folder, blue accent at seams only
  B — Filled folder, blue glow at edges (box-shadow style)
  C — Filled folder, blue accent line at the crease between panels
  D — Outlined folder, tab accent in blue, body in off-white
  E — Filled dark body, blue energy at tab + bottom edge
  F — Minimal outline, blue only at the tab
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path(__file__).parent / "svg"
OUT_PNG = Path(__file__).parent / "png"

# Palette (from visual language)
BG = "#000000"
BODY_DARK = "#0f1525"
BODY_MID = "#141c30"
BODY_BACK = "#0a0e1a"
ACCENT = "#0099FF"
ACCENT_GLOW = "rgba(0, 153, 255, 0.3)"
TEXT = "#e2e8f0"
BORDER_DIM = "rgba(0, 153, 255, 0.15)"

W, H = 256, 256
MARGIN = 40


def render(name, svg_content):
    (OUT_SVG / f"{name}.svg").write_text(svg_content)
    cairosvg.svg2png(bytestring=svg_content.encode(),
                     write_to=str(OUT_PNG / f"{name}.png"),
                     output_width=256, output_height=256)
    # Also render at 64 and 128 for size testing
    cairosvg.svg2png(bytestring=svg_content.encode(),
                     write_to=str(OUT_PNG / f"{name}_128.png"),
                     output_width=128, output_height=128)
    cairosvg.svg2png(bytestring=svg_content.encode(),
                     write_to=str(OUT_PNG / f"{name}_64.png"),
                     output_width=64, output_height=64)
    print(f"  {name}")


def svg(inner):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{BG}"/>
{inner}
</svg>'''


def folder_a():
    """A — Outlined folder, blue accent at seams/creases only.
    Body is off-white outline, but the seam where panels meet glows blue."""
    inner = f'''
<!-- Back panel with tab -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{TEXT}" stroke-width="3" stroke-opacity="0.3"
      stroke-linejoin="round"/>

<!-- Front panel -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{TEXT}" stroke-width="3" stroke-opacity="0.5"
      stroke-linejoin="round"/>

<!-- Accent: the crease where front meets back (blue glow) -->
<line x1="58" y1="105" x2="198" y2="105"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.6"/>

<!-- Accent: tab bottom edge -->
<path d="M 55 75 L 130 75" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.4"/>
'''
    render("A_outlined_seam", svg(inner))


def folder_b():
    """B — Filled dark body, blue glow emanating from edges.
    The folder is a solid dark shape with a soft blue corona."""
    inner = f'''
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>

<!-- Glow layer (blue outline, blurred) -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Back panel solid fill -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_BACK}"/>

<!-- Front panel (slightly lighter) -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_DARK}"/>

<!-- Crease accent -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.35"/>

<!-- Outer border (very subtle) -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.15"/>
'''
    render("B_filled_glow", svg(inner))


def folder_c():
    """C — Filled dark body, prominent blue crease line.
    The crease between back and front panel is the hero element."""
    inner = f'''
<!-- Back panel -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_BACK}"/>

<!-- Front panel -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_DARK}"/>

<!-- Outer border -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.12"/>

<!-- HERO: bright crease line -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="2.5" stroke-opacity="0.7"/>

<!-- Crease glow -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="8" stroke-opacity="0.08"/>
'''
    render("C_crease_hero", svg(inner))


def folder_d():
    """D — Outlined off-white body, tab filled with blue accent.
    The tab is the color signature, the body is neutral."""
    inner = f'''
<!-- Back panel outline -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{TEXT}" stroke-width="3" stroke-opacity="0.4"
      stroke-linejoin="round"/>

<!-- Tab fill (blue accent) -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 Z"
      fill="{ACCENT}" fill-opacity="0.25"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.5"/>

<!-- Front panel outline -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{TEXT}" stroke-width="3" stroke-opacity="0.5"
      stroke-linejoin="round"/>
'''
    render("D_blue_tab", svg(inner))


def folder_e():
    """E — Filled dark body, blue energy at tab + bottom edge + crease.
    Three points of blue light on a dark container."""
    inner = f'''
<defs>
  <filter id="glow2" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="3" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>

<!-- Back panel -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_BACK}"/>

<!-- Front panel -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_DARK}"/>

<!-- Blue accent 1: tab top edge -->
<path d="M 58 60 Q 58 54 64 54 L 104 54 Q 111 54 114 60 L 118 68"
      fill="none" stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.6"
      stroke-linecap="round" filter="url(#glow2)"/>

<!-- Blue accent 2: crease -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      filter="url(#glow2)"/>

<!-- Blue accent 3: bottom edge -->
<path d="M 63 203 Q 55 203 55 195 L 55 195 M 201 195 Q 201 203 193 203"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.3"/>
<line x1="63" y1="203" x2="193" y2="203"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.3"
      filter="url(#glow2)"/>

<!-- Subtle outer border -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.08"/>
'''
    render("E_three_accents", svg(inner))


def folder_f():
    """F — Ultra minimal outline, blue only on the tab.
    The most restrained version — barely there."""
    inner = f'''
<!-- Whole folder as a single thin outline -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{TEXT}" stroke-width="2" stroke-opacity="0.25"
      stroke-linejoin="round"/>

<!-- Front panel line only -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{TEXT}" stroke-width="1.5" stroke-opacity="0.2"/>

<!-- Tab highlighted in blue -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75"
      fill="none" stroke="{ACCENT}" stroke-width="2.5" stroke-opacity="0.7"
      stroke-linecap="round" stroke-linejoin="round"/>
'''
    render("F_minimal_tab", svg(inner))


def folder_g():
    """G — The shell theme folder.
    Treats the folder exactly like a shell UI element: dark fill surface,
    structural borders at --border opacity, accent glow on the crease.
    This is what a folder would look like if it were a quick-toggle button."""
    inner = f'''
<defs>
  <filter id="glow3" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="3" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>

<!-- Back panel — bg-card fill, structural border -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_BACK}"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.08"/>

<!-- Front panel — slightly elevated -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_MID}"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.08"/>

<!-- Crease glow — the seam emits blue light -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.45"
      filter="url(#glow3)"/>

<!-- Tab top edge — subtle blue trace -->
<path d="M 57 62 Q 57 55 64 55 L 104 55 Q 111 55 114 61 L 118 69"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.25"
      stroke-linecap="round"/>
'''
    render("G_shell_element", svg(inner))


def folder_h():
    """H — Energy trace folder.
    The folder body is dark and almost invisible. A continuous blue
    energy line traces the entire folder outline like a circuit trace,
    with the crease and tab glowing brightest."""
    inner = f'''
<defs>
  <filter id="glow4" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>

<!-- Very subtle dark fill -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_BACK}" fill-opacity="0.5"/>

<!-- Front panel fill -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="{BODY_DARK}" fill-opacity="0.4"/>

<!-- Energy trace — full outline (dim) -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75 L 193 75 Q 201 75 201 83 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 Z"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.2"
      stroke-linejoin="round"/>

<!-- Energy trace — front panel (slightly brighter) -->
<path d="M 55 105 L 201 105 L 201 195 Q 201 203 193 203
         L 63 203 Q 55 203 55 195 L 55 105"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.3"
      stroke-linejoin="round"/>

<!-- Bright crease (the brightest point) -->
<line x1="55" y1="105" x2="201" y2="105"
      stroke="{ACCENT}" stroke-width="2.5" stroke-opacity="0.7"
      filter="url(#glow4)"/>

<!-- Bright tab trace -->
<path d="M 55 75 L 55 60 Q 55 52 63 52 L 105 52 Q 113 52 116 60 L 120 68
         Q 123 75 130 75"
      fill="none" stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      stroke-linecap="round" stroke-linejoin="round"
      filter="url(#glow4)"/>
'''
    render("H_energy_trace", svg(inner))


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 1 — folder concepts:")
    folder_a()
    folder_b()
    folder_c()
    folder_d()
    folder_e()
    folder_f()
    folder_g()
    folder_h()
    print("Done.")
