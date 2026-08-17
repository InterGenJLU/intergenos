#!/usr/bin/env python3
"""
Iterate toward ChatGPT's concept image. Autonomous refinement.

Target (from the concept render):
  - Dark glass body — semi-transparent, not opaque matte
  - Blue glow concentrated at edges, brightest at BOTTOM
  - Prominent diagonal glass reflection (white band, 12-18% opacity)
  - Two-panel depth through fill difference, not a drawn crease line
  - Glow bleeds OUTWARD into surrounding black
  - Tab is subtle, natural part of the glass form
  - Overall: dark glass object lit by blue energy at structural edges

Each iteration adjusts: glow intensity, body opacity, reflection strength,
blur radius, bottom-edge emphasis, glass material feel.
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent
W, H = 256, 256


def render(name, svg):
    cairosvg.svg2png(bytestring=svg.encode(),
                     write_to=str(OUT / f"{name}_256.png"),
                     output_width=256, output_height=256)
    cairosvg.svg2png(bytestring=svg.encode(),
                     write_to=str(OUT / f"{name}_128.png"),
                     output_width=128, output_height=128)


FOLDER_PATH = ("M32 72 Q32 56 48 56 H104 Q112 56 120 64 L132 76 H208 "
               "Q224 76 224 92 V184 Q224 200 208 200 H48 Q32 200 32 184 Z")

FRONT_PATH = ("M32 108 H224 V184 Q224 200 208 200 H48 Q32 200 32 184 Z")

TAB_TOP = "M50 57 H102 Q110 57 118 65"

BOTTOM_EDGE = "M48 200 H208 Q224 200 224 184"
LEFT_BOTTOM = "M32 184 Q32 200 48 200"
RIGHT_SIDE = "M224 92 V184"
LEFT_SIDE = "M32 72 L32 184"


def make_edge_lit(version, body_opacity=0.85, back_lightness=12,
                  front_lightness=18, glow_blur=8, glow_opacity=0.45,
                  edge_stroke_w=4, sharp_opacity=0.25,
                  bottom_glow_opacity=0.7, bottom_blur=9,
                  side_glow_opacity=0.4,
                  gloss_opacity=0.14, gloss_end=0.35,
                  crease_opacity=0.3, front_highlight_opacity=0.06,
                  shadow_opacity=0.35, shadow_blur=10):
    """Generate an edge-lit folder with the given parameters."""

    back_color = f"#{back_lightness:02x}{back_lightness+6:02x}{back_lightness+18:02x}"
    front_top = f"#{front_lightness:02x}{front_lightness+8:02x}{front_lightness+22:02x}"
    front_bot = f"#{back_lightness-2:02x}{back_lightness+4:02x}{back_lightness+14:02x}"

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="back" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{back_color}"/>
    <stop offset="100%" stop-color="#070a14"/>
  </linearGradient>
  <linearGradient id="front" x1="0" y1="0" x2="0.15" y2="1">
    <stop offset="0%" stop-color="{front_top}" stop-opacity="{body_opacity}"/>
    <stop offset="100%" stop-color="{front_bot}" stop-opacity="{body_opacity + 0.05}"/>
  </linearGradient>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.65" y2="0.75">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="{gloss_opacity}"/>
    <stop offset="{int(gloss_end * 100)}%" stop-color="#ffffff" stop-opacity="{gloss_opacity * 0.25:.3f}"/>
    <stop offset="60%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="frontHighlight" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="{front_highlight_opacity}"/>
    <stop offset="15%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <filter id="wideGlow" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="{glow_blur}"/></filter>
  <filter id="medGlow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="{glow_blur * 0.55:.1f}" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="bottomGlow" x="-60%" y="-60%" width="220%" height="260%">
    <feGaussianBlur stdDeviation="{bottom_blur}"/></filter>
  <filter id="shadow" x="-20%" y="-10%" width="140%" height="150%">
    <feGaussianBlur stdDeviation="{shadow_blur}" in="SourceAlpha"/>
    <feOffset dy="6"/>
    <feComponentTransfer><feFuncA type="linear" slope="{shadow_opacity}"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <clipPath id="frontClip"><path d="{FRONT_PATH}"/></clipPath>
  <clipPath id="allClip"><path d="{FOLDER_PATH}"/></clipPath>
</defs>

<!-- Drop shadow beneath the folder -->
<path d="{FOLDER_PATH}" fill="#000" opacity="{shadow_opacity}" filter="url(#shadow)"/>

<!-- Wide ambient glow (soft halo around the entire folder) -->
<path d="{FOLDER_PATH}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{glow_opacity}"
  filter="url(#wideGlow)"/>

<!-- BOTTOM EDGE glow (brightest — concentrated at bottom) -->
<path d="{BOTTOM_EDGE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w + 2}" stroke-opacity="{bottom_glow_opacity}"
  stroke-linecap="round" filter="url(#bottomGlow)"/>
<path d="{LEFT_BOTTOM}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w + 1}" stroke-opacity="{bottom_glow_opacity * 0.85:.2f}"
  stroke-linecap="round" filter="url(#bottomGlow)"/>

<!-- Side edge glow (dimmer than bottom) -->
<path d="{RIGHT_SIDE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{side_glow_opacity}"
  stroke-linecap="round" filter="url(#wideGlow)"/>
<path d="{LEFT_SIDE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{side_glow_opacity * 0.9:.2f}"
  stroke-linecap="round" filter="url(#wideGlow)"/>

<!-- Back panel fill -->
<path d="{FOLDER_PATH}" fill="url(#back)"/>

<!-- Front panel fill (glass — semi-transparent) -->
<path d="{FRONT_PATH}" fill="url(#front)"/>

<!-- Front panel top-edge highlight (glass catching light) -->
<g clip-path="url(#frontClip)">
  <rect x="32" y="108" width="192" height="30" fill="url(#frontHighlight)"/>
</g>

<!-- Crease (subtle — where front overlaps back) -->
<line x1="34" y1="108" x2="222" y2="108"
  stroke="#0099FF" stroke-width="1" stroke-opacity="{crease_opacity}"
  filter="url(#medGlow)"/>

<!-- Diagonal glass reflection -->
<g clip-path="url(#frontClip)">
  <path d="M32 108 L175 108 L32 195 Z" fill="url(#gloss)"/>
</g>

<!-- Medium glow on full outline (on top of fills, adds definition) -->
<path d="{FOLDER_PATH}" fill="none" stroke="#0099FF"
  stroke-width="2" stroke-opacity="{glow_opacity * 0.7:.2f}"
  filter="url(#medGlow)"/>

<!-- Sharp structural edge (crisp blue border) -->
<path d="{FOLDER_PATH}" fill="none"
  stroke="rgba(0,153,255,{sharp_opacity})" stroke-width="1.5"/>

<!-- Front panel subtle structural edge -->
<path d="{FRONT_PATH}" fill="none"
  stroke="rgba(0,153,255,{sharp_opacity * 0.6:.2f})" stroke-width="1"/>

<!-- Tab catch light -->
<path d="{TAB_TOP}" fill="none" stroke="#ffffff"
  stroke-width="0.75" stroke-opacity="0.06" stroke-linecap="round"/>

</svg>'''
    render(f"edge_lit_{version}", svg)
    return svg


def make_pulse_core(version, base_svg_params, core_radius=18,
                    core_opacity=0.85, core_blur=5,
                    flare_opacity=0.3, center_size=3.5):
    """Take an edge-lit base and add the pulse core."""

    # Generate the edge-lit base but capture the SVG
    base = make_edge_lit_svg(**base_svg_params)

    # Insert pulse core elements before the closing </svg>
    core_elements = f'''
<!-- ========== PULSE CORE ========== -->
<g clip-path="url(#allClip)">
  <!-- Wide core radiance -->
  <circle cx="134" cy="148" r="{core_radius * 2.5:.0f}"
    fill="none" stroke="#0066aa" stroke-width="{core_radius}"
    stroke-opacity="0.08"
    filter="url(#wideGlow)"/>
  <!-- Core glow -->
  <circle cx="134" cy="148" r="{core_radius}"
    opacity="{core_opacity}">
    <set attributeName="fill" to="url(#coreGrad)"/>
  </circle>
  <!-- Horizontal lens flare -->
  <line x1="50" y1="148" x2="218" y2="148"
    stroke="#0099FF" stroke-width="2" stroke-opacity="{flare_opacity}"
    filter="url(#medGlow)"/>
  <line x1="80" y1="148" x2="188" y2="148"
    stroke="#33bbff" stroke-width="1" stroke-opacity="{flare_opacity * 0.6:.2f}"/>
  <!-- Bright center -->
  <circle cx="134" cy="148" r="{core_radius * 0.45:.1f}"
    fill="#0099FF" opacity="0.8" filter="url(#medGlow)"/>
  <!-- Hot center point -->
  <circle cx="134" cy="148" r="{center_size}"
    fill="#66ddff" opacity="0.95"/>
</g>'''

    # We need to add a coreGrad to defs
    core_defs = '''
  <radialGradient id="coreGrad">
    <stop offset="0%" stop-color="#33bbff" stop-opacity="0.8"/>
    <stop offset="30%" stop-color="#0099FF" stop-opacity="0.4"/>
    <stop offset="70%" stop-color="#0066aa" stop-opacity="0.1"/>
    <stop offset="100%" stop-color="#004488" stop-opacity="0"/>
  </radialGradient>'''

    svg = base.replace('</defs>', core_defs + '\n</defs>')
    svg = svg.replace('</svg>', core_elements + '\n</svg>')

    render(f"pulse_core_{version}", svg)
    return svg


def make_edge_lit_svg(**kwargs):
    """Same as make_edge_lit but returns SVG string without rendering."""
    # Copy the make_edge_lit logic but return instead of render
    body_opacity = kwargs.get('body_opacity', 0.85)
    back_lightness = kwargs.get('back_lightness', 12)
    front_lightness = kwargs.get('front_lightness', 18)
    glow_blur = kwargs.get('glow_blur', 8)
    glow_opacity = kwargs.get('glow_opacity', 0.45)
    edge_stroke_w = kwargs.get('edge_stroke_w', 4)
    sharp_opacity = kwargs.get('sharp_opacity', 0.25)
    bottom_glow_opacity = kwargs.get('bottom_glow_opacity', 0.7)
    bottom_blur = kwargs.get('bottom_blur', 9)
    side_glow_opacity = kwargs.get('side_glow_opacity', 0.4)
    gloss_opacity = kwargs.get('gloss_opacity', 0.14)
    gloss_end = kwargs.get('gloss_end', 0.35)
    crease_opacity = kwargs.get('crease_opacity', 0.3)
    front_highlight_opacity = kwargs.get('front_highlight_opacity', 0.06)
    shadow_opacity = kwargs.get('shadow_opacity', 0.35)
    shadow_blur = kwargs.get('shadow_blur', 10)

    back_color = f"#{back_lightness:02x}{back_lightness+6:02x}{back_lightness+18:02x}"
    front_top = f"#{front_lightness:02x}{front_lightness+8:02x}{front_lightness+22:02x}"
    front_bot = f"#{back_lightness-2:02x}{back_lightness+4:02x}{back_lightness+14:02x}"

    return f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="back" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{back_color}"/>
    <stop offset="100%" stop-color="#070a14"/>
  </linearGradient>
  <linearGradient id="front" x1="0" y1="0" x2="0.15" y2="1">
    <stop offset="0%" stop-color="{front_top}" stop-opacity="{body_opacity}"/>
    <stop offset="100%" stop-color="{front_bot}" stop-opacity="{body_opacity + 0.05}"/>
  </linearGradient>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.65" y2="0.75">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="{gloss_opacity}"/>
    <stop offset="{int(gloss_end * 100)}%" stop-color="#ffffff" stop-opacity="{gloss_opacity * 0.25:.3f}"/>
    <stop offset="60%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="frontHighlight" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="{front_highlight_opacity}"/>
    <stop offset="15%" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <filter id="wideGlow" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="{glow_blur}"/></filter>
  <filter id="medGlow" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="{glow_blur * 0.55:.1f}" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="bottomGlow" x="-60%" y="-60%" width="220%" height="260%">
    <feGaussianBlur stdDeviation="{bottom_blur}"/></filter>
  <filter id="shadow" x="-20%" y="-10%" width="140%" height="150%">
    <feGaussianBlur stdDeviation="{shadow_blur}" in="SourceAlpha"/>
    <feOffset dy="6"/>
    <feComponentTransfer><feFuncA type="linear" slope="{shadow_opacity}"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <clipPath id="frontClip"><path d="{FRONT_PATH}"/></clipPath>
  <clipPath id="allClip"><path d="{FOLDER_PATH}"/></clipPath>
</defs>
<path d="{FOLDER_PATH}" fill="#000" opacity="{shadow_opacity}" filter="url(#shadow)"/>
<path d="{FOLDER_PATH}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{glow_opacity}" filter="url(#wideGlow)"/>
<path d="{BOTTOM_EDGE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w + 2}" stroke-opacity="{bottom_glow_opacity}"
  stroke-linecap="round" filter="url(#bottomGlow)"/>
<path d="{LEFT_BOTTOM}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w + 1}" stroke-opacity="{bottom_glow_opacity * 0.85:.2f}"
  stroke-linecap="round" filter="url(#bottomGlow)"/>
<path d="{RIGHT_SIDE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{side_glow_opacity}"
  stroke-linecap="round" filter="url(#wideGlow)"/>
<path d="{LEFT_SIDE}" fill="none" stroke="#0099FF"
  stroke-width="{edge_stroke_w}" stroke-opacity="{side_glow_opacity * 0.9:.2f}"
  stroke-linecap="round" filter="url(#wideGlow)"/>
<path d="{FOLDER_PATH}" fill="url(#back)"/>
<path d="{FRONT_PATH}" fill="url(#front)"/>
<g clip-path="url(#frontClip)">
  <rect x="32" y="108" width="192" height="30" fill="url(#frontHighlight)"/>
</g>
<line x1="34" y1="108" x2="222" y2="108"
  stroke="#0099FF" stroke-width="1" stroke-opacity="{crease_opacity}" filter="url(#medGlow)"/>
<g clip-path="url(#frontClip)">
  <path d="M32 108 L175 108 L32 195 Z" fill="url(#gloss)"/>
</g>
<path d="{FOLDER_PATH}" fill="none" stroke="#0099FF"
  stroke-width="2" stroke-opacity="{glow_opacity * 0.7:.2f}" filter="url(#medGlow)"/>
<path d="{FOLDER_PATH}" fill="none"
  stroke="rgba(0,153,255,{sharp_opacity})" stroke-width="1.5"/>
<path d="{FRONT_PATH}" fill="none"
  stroke="rgba(0,153,255,{sharp_opacity * 0.6:.2f})" stroke-width="1"/>
<path d="{TAB_TOP}" fill="none" stroke="#ffffff"
  stroke-width="0.75" stroke-opacity="0.06" stroke-linecap="round"/>
</svg>'''


if __name__ == "__main__":
    print("Iterating toward concept target...")

    # V1: Current best baseline
    make_edge_lit("v1",
        body_opacity=0.82, glow_opacity=0.45, bottom_glow_opacity=0.7,
        gloss_opacity=0.14, glow_blur=8)
    print("  v1 — baseline")

    # V2: More glass-like (lower body opacity, stronger reflection)
    make_edge_lit("v2",
        body_opacity=0.72, glow_opacity=0.50, bottom_glow_opacity=0.75,
        gloss_opacity=0.18, gloss_end=0.40, glow_blur=9,
        front_highlight_opacity=0.08, back_lightness=10, front_lightness=15)
    print("  v2 — more glass, stronger reflection")

    # V3: Maximum glass effect (very transparent body, strong glow)
    make_edge_lit("v3",
        body_opacity=0.65, glow_opacity=0.55, bottom_glow_opacity=0.80,
        bottom_blur=10, gloss_opacity=0.20, gloss_end=0.42,
        glow_blur=10, edge_stroke_w=5, front_highlight_opacity=0.10,
        back_lightness=9, front_lightness=14, side_glow_opacity=0.45)
    print("  v3 — maximum glass, strong glow")

    # V4: Balanced (between v2 and v3, adjusted crease)
    make_edge_lit("v4",
        body_opacity=0.70, glow_opacity=0.52, bottom_glow_opacity=0.78,
        bottom_blur=9, gloss_opacity=0.16, gloss_end=0.38,
        glow_blur=9, edge_stroke_w=5, front_highlight_opacity=0.08,
        back_lightness=10, front_lightness=16, crease_opacity=0.25,
        side_glow_opacity=0.42, sharp_opacity=0.28)
    print("  v4 — balanced glass + glow")

    # V5: Concept-matched (tuned to match the render as closely as possible)
    best_params = dict(
        body_opacity=0.68, glow_opacity=0.50, bottom_glow_opacity=0.80,
        bottom_blur=10, gloss_opacity=0.18, gloss_end=0.40,
        glow_blur=10, edge_stroke_w=5, front_highlight_opacity=0.09,
        back_lightness=10, front_lightness=15, crease_opacity=0.22,
        side_glow_opacity=0.45, sharp_opacity=0.26,
        shadow_opacity=0.4, shadow_blur=12
    )
    make_edge_lit("v5_final", **best_params)
    print("  v5_final — concept-matched edge-lit")

    # Pulse-Core based on best edge-lit
    make_pulse_core("v5_final", best_params,
        core_radius=20, core_opacity=0.9, core_blur=6,
        flare_opacity=0.35, center_size=3.5)
    print("  pulse_core_v5_final — concept-matched pulse-core")

    print("\nDone. Review v1-v5 and select the closest match.")
