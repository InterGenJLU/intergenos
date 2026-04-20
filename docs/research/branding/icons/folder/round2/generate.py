#!/usr/bin/env python3
"""
InterGenOS Folder Icon — Round 2
Completely rethought — refined curves, subtle depth, premium feel.

Problems with Round 1:
  - Tab was a crude rectangular bump (Windows 3.1 feel)
  - Shapes were plain rectangles with overlaid lines
  - No sense of volume, thickness, or sophistication
  - "Flat" was interpreted as "lifeless" instead of "restrained depth"

Round 2 approach:
  - Smooth Bezier curves throughout — the tab flows from the body
  - Subtle gradients on surfaces (visual language allows this on surfaces)
  - A sense of thickness — visible folder edge/spine
  - The folder feels like a refined dark container, not a flat cutout
  - Blue accent is PART of the shape, not painted on top
  - Proportions studied from macOS/Windows 11 (smaller tab, wider body)
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path(__file__).parent / "svg"
OUT_PNG = Path(__file__).parent / "png"

# Palette
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


def svg(inner):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <!-- Reusable glow filter -->
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
  <filter id="softglow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="6" result="blur"/>
    <feMerge>
      <feMergeNode in="blur"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>

  <!-- Back panel gradient (top lighter, bottom darker) -->
  <linearGradient id="backGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0c1220"/>
    <stop offset="100%" stop-color="#070a14"/>
  </linearGradient>

  <!-- Front panel gradient (slightly lighter than back) -->
  <linearGradient id="frontGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#141c30"/>
    <stop offset="100%" stop-color="#0e1422"/>
  </linearGradient>

  <!-- Front panel with blue top-edge glow -->
  <linearGradient id="frontGlowGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0e1a30"/>
    <stop offset="8%" stop-color="#111828"/>
    <stop offset="100%" stop-color="#0c1220"/>
  </linearGradient>

  <!-- Tab gradient -->
  <linearGradient id="tabGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#101828"/>
    <stop offset="100%" stop-color="#0a0e1a"/>
  </linearGradient>

  <!-- Blue accent gradient for tab fill -->
  <linearGradient id="blueTabGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.25"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0.08"/>
  </linearGradient>

  <!-- Thickness edge gradient -->
  <linearGradient id="edgeGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#0a0e1a"/>
    <stop offset="100%" stop-color="#050810"/>
  </linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>
{inner}
</svg>'''


# ============================================================
# Shared folder path builders using smooth Bezier curves
# ============================================================

# Folder dimensions (refined proportions)
# Body: x 48-208, y 72-198  (160w x 126h, ratio ~1.27:1)
# Tab: smooth curve rising from body, ~40% of width
# Bottom edge: 6px thickness visible

def back_panel_path():
    """Back panel with smoothly integrated tab.
    Tab rises as a gentle hill from the left side."""
    return (
        "M 56 90 "
        # Left wall up to tab start
        "L 56 78 "
        # Tab: smooth curve up and over
        "C 56 68, 62 62, 72 60 "   # curve up-left
        "L 104 60 "                 # tab top
        "C 114 60, 120 66, 124 76 " # curve down-right
        "C 128 86, 132 90, 140 90 " # smooth merge back into top edge
        # Top edge continues to right
        "L 200 90 "
        # Right wall down
        "C 206 90, 208 94, 208 100 "
        # Down to bottom-right
        "L 208 190 "
        # Bottom-right corner
        "C 208 196, 204 198, 200 198 "
        # Bottom edge
        "L 56 198 "
        # Bottom-left corner
        "C 52 198, 48 196, 48 190 "
        # Left wall back up (using offset for thickness appearance)
        "L 48 100 "
        "C 48 94, 50 90, 56 90 "
        "Z"
    )


def front_panel_path():
    """Front panel overlapping the bottom portion."""
    return (
        "M 48 120 "
        "L 208 120 "
        "L 208 190 "
        "C 208 196, 204 198, 200 198 "
        "L 56 198 "
        "C 52 198, 48 196, 48 190 "
        "Z"
    )


def thickness_path():
    """Bottom edge suggesting folder thickness/spine."""
    return (
        "M 52 198 "
        "L 52 204 "
        "C 52 207, 55 209, 58 209 "
        "L 202 209 "
        "C 205 209, 208 207, 208 204 "
        "L 208 198 "
    )


def tab_path():
    """Just the tab portion for separate accent treatment."""
    return (
        "M 56 90 "
        "L 56 78 "
        "C 56 68, 62 62, 72 60 "
        "L 104 60 "
        "C 114 60, 120 66, 124 76 "
        "C 128 86, 132 90, 140 90 "
        "L 56 90 Z"
    )


def crease_path():
    """The crease line where front panel meets back panel."""
    return "M 48 120 L 208 120"


# ============================================================
# Concepts
# ============================================================

def concept_a():
    """A — Premium dark with glowing crease.
    Refined dark body with gradient depth, smooth tab, blue crease glow."""
    inner = f'''
<!-- Back panel -->
<path d="{back_panel_path()}" fill="url(#backGrad)"/>

<!-- Front panel (slightly lighter, overlaps) -->
<path d="{front_panel_path()}" fill="url(#frontGrad)"/>

<!-- Thickness edge -->
<path d="{thickness_path()}" fill="url(#edgeGrad)" stroke="none"/>

<!-- Structural border (barely visible) -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.1"/>

<!-- Crease glow -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Tab top accent (subtle) -->
<path d="M 62 64 C 62 62, 66 60, 72 60 L 104 60 C 110 60, 116 62, 120 68"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.25"
      stroke-linecap="round"/>
'''
    render("A_premium_crease", svg(inner))


def concept_b():
    """B — Blue-lit tab, dark body.
    Tab has a blue gradient fill that fades into the dark body.
    The tab is the color signature."""
    inner = f'''
<!-- Back panel -->
<path d="{back_panel_path()}" fill="url(#backGrad)"/>

<!-- Tab with blue gradient fill -->
<path d="{tab_path()}" fill="url(#blueTabGrad)"/>

<!-- Front panel -->
<path d="{front_panel_path()}" fill="url(#frontGrad)"/>

<!-- Thickness edge -->
<path d="{thickness_path()}" fill="url(#edgeGrad)"/>

<!-- Structural border -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.1"/>

<!-- Tab edge accent -->
<path d="M 58 78 C 58 68, 64 63, 73 61 L 103 61 C 113 61, 119 67, 123 76"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.4"
      stroke-linecap="round"/>

<!-- Subtle crease -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.2"/>
'''
    render("B_blue_tab", svg(inner))


def concept_c():
    """C — Energy borders, dark interior.
    The entire folder has a subtle blue border that intensifies
    at the crease and tab. Like a dark container with energy running
    through its seams."""
    inner = f'''
<!-- Back panel -->
<path d="{back_panel_path()}" fill="url(#backGrad)"/>

<!-- Front panel -->
<path d="{front_panel_path()}" fill="url(#frontGlowGrad)"/>

<!-- Thickness edge -->
<path d="{thickness_path()}" fill="url(#edgeGrad)"/>

<!-- Border glow (soft, wide) -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="3" stroke-opacity="0.06"
      filter="url(#softglow)"/>

<!-- Structural border (visible) -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.2"
      stroke-linejoin="round"/>

<!-- Front panel border (slightly brighter) -->
<path d="{front_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.25"
      stroke-linejoin="round"/>

<!-- Crease (brightest seam) -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.6"
      filter="url(#glow)"/>

<!-- Tab accent (second brightest) -->
<path d="M 58 78 C 58 68, 64 63, 73 61 L 103 61 C 113 61, 119 67, 123 76"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.45"
      stroke-linecap="round" filter="url(#glow)"/>

<!-- Bottom edge accent -->
<path d="M 56 198 L 200 198" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.2"/>
'''
    render("C_energy_borders", svg(inner))


def concept_d():
    """D — Glass dark with inner light.
    The front panel has a subtle inner illumination — as if blue light
    is leaking through from inside the folder. The folder contains energy."""
    inner = f'''
<defs>
  <!-- Inner glow on front panel -->
  <radialGradient id="innerGlow" cx="0.5" cy="0.3" r="0.7">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.06"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/>
  </radialGradient>
</defs>

<!-- Back panel -->
<path d="{back_panel_path()}" fill="url(#backGrad)"/>

<!-- Front panel base -->
<path d="{front_panel_path()}" fill="url(#frontGrad)"/>

<!-- Inner glow overlay on front panel -->
<path d="{front_panel_path()}" fill="url(#innerGlow)"/>

<!-- Thickness edge -->
<path d="{thickness_path()}" fill="url(#edgeGrad)"/>

<!-- Structural border -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.12"/>

<!-- Crease — light leaks here -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.45"
      filter="url(#glow)"/>

<!-- Tab subtle border -->
<path d="M 58 78 C 58 68, 64 63, 73 61 L 103 61 C 113 61, 119 67, 123 76"
      fill="none" stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.2"
      stroke-linecap="round"/>

<!-- Light leak at the gap between tab and front panel top edge -->
<path d="M 140 90 L 200 90" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.15"/>
'''
    render("D_inner_light", svg(inner))


def concept_e():
    """E — Refined outline with accent depth.
    Outline-forward but with sophisticated Bezier curves, variable
    stroke opacity, and the tab as a blue accent shape. Not flat —
    the line weight and opacity changes suggest depth."""
    inner = f'''
<!-- Back panel outline (dimmer) -->
<path d="{back_panel_path()}" fill="none"
      stroke="#e2e8f0" stroke-width="2" stroke-opacity="0.15"
      stroke-linejoin="round"/>

<!-- Front panel outline (brighter — it's in front) -->
<path d="{front_panel_path()}" fill="none"
      stroke="#e2e8f0" stroke-width="2.5" stroke-opacity="0.3"
      stroke-linejoin="round"/>

<!-- Tab — blue accent with slight fill -->
<path d="{tab_path()}" fill="{ACCENT}" fill-opacity="0.08"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      stroke-linejoin="round"/>

<!-- Crease accent -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.4"/>

<!-- Thickness suggestion -->
<path d="{thickness_path()}" fill="none"
      stroke="#e2e8f0" stroke-width="1.5" stroke-opacity="0.1"
      stroke-linejoin="round"/>
'''
    render("E_refined_outline", svg(inner))


def concept_f():
    """F — The living folder.
    Dark body with blue energy tracing every seam, brightest at the crease.
    The folder feels like it's powered — energy circulates through its structure.
    Thicker accent lines, more glow, more presence."""
    inner = f'''
<!-- Back panel -->
<path d="{back_panel_path()}" fill="url(#backGrad)"/>

<!-- Front panel -->
<path d="{front_panel_path()}" fill="url(#frontGrad)"/>

<!-- Thickness -->
<path d="{thickness_path()}" fill="url(#edgeGrad)"/>

<!-- Energy trace: full outline (dim base) -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.15"
      stroke-linejoin="round"/>

<!-- Energy trace: front panel (slightly brighter) -->
<path d="{front_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.22"
      stroke-linejoin="round"/>

<!-- Energy trace: thickness edge -->
<path d="{thickness_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.15"
      stroke-linejoin="round"/>

<!-- Bright crease (the pulse point) -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="2.5" stroke-opacity="0.7"
      filter="url(#glow)"/>

<!-- Tab energy (second brightest) -->
<path d="M 58 78 C 58 68, 64 63, 73 61 L 103 61 C 113 61, 119 67, 123 76
         C 127 84, 131 88, 138 90"
      fill="none" stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.5"
      stroke-linecap="round" filter="url(#glow)"/>

<!-- Bottom edge energy -->
<path d="M 56 198 C 52 198, 52 200, 52 204 C 52 207, 55 209, 58 209
         L 202 209 C 205 209, 208 207, 208 204 L 208 198"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.25"
      stroke-linecap="round"/>
'''
    render("F_living_folder", svg(inner))


def concept_g():
    """G — Holographic projection.
    The folder is barely there — a dark translucent shape with blue
    holographic edges, like a UI element projected in space. Maximum
    integration with the shell theme's 'frosted glass' surfaces."""
    inner = f'''
<!-- Back panel (very transparent dark) -->
<path d="{back_panel_path()}"
      fill="#0a0e1a" fill-opacity="0.4"/>

<!-- Front panel (slightly more opaque) -->
<path d="{front_panel_path()}"
      fill="#0f1525" fill-opacity="0.5"/>

<!-- Holographic edge — full outline with glow -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.3"
      stroke-linejoin="round" filter="url(#softglow)"/>

<!-- Sharper inner outline -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.5"
      stroke-linejoin="round"/>

<!-- Front panel edge -->
<path d="{front_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.4"
      stroke-linejoin="round"/>

<!-- Crease (bright) -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT_BRIGHT}" stroke-width="1.5" stroke-opacity="0.6"
      filter="url(#glow)"/>

<!-- Tab highlight -->
<path d="M 58 78 C 58 68, 64 63, 73 61 L 103 61 C 113 61, 119 67, 123 76"
      fill="none" stroke="{ACCENT_BRIGHT}" stroke-width="1" stroke-opacity="0.5"
      stroke-linecap="round"/>
'''
    render("G_holographic", svg(inner))


def concept_h():
    """H — The dark monolith.
    Maximum dark, maximum restraint. The folder is a very dark, nearly
    invisible refined shape. The ONLY blue is a thin crease line and
    a whisper of accent at the tab curve. The folder asserts its presence
    through its SHAPE and ABSENCE of color, not through decoration.
    When hovered in the file manager, the shell theme's glow effect
    would illuminate it — but at rest, it's nearly invisible."""
    inner = f'''
<!-- Back panel (very dark, near-void) -->
<path d="{back_panel_path()}" fill="#080c18"/>

<!-- Front panel (one step lighter) -->
<path d="{front_panel_path()}" fill="#0c1220"/>

<!-- Thickness -->
<path d="{thickness_path()}" fill="#050810"/>

<!-- Barely visible structural border -->
<path d="{back_panel_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.06"/>

<!-- The only blue: crease line -->
<path d="{crease_path()}" fill="none"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.35"/>

<!-- The only other blue: whisper at tab curve -->
<path d="M 72 61 L 103 61 C 112 61, 118 66, 122 74"
      fill="none" stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.15"
      stroke-linecap="round"/>
'''
    render("H_dark_monolith", svg(inner))


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 2 — refined folder concepts:")
    concept_a()
    concept_b()
    concept_c()
    concept_d()
    concept_e()
    concept_f()
    concept_g()
    concept_h()
    print("Done.")
