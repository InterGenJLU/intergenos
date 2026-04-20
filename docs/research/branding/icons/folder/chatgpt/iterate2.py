#!/usr/bin/env python3
"""
Second iteration pass — pushing harder toward the concept.

What's still wrong:
  - Bottom glow not bright/wide enough — concept has a POOL of light below
  - Diagonal reflection invisible — concept shows a clear bright diagonal band
  - Glow too uniform — concept has bottom MUCH brighter than top
  - Body too opaque — concept has clear glass transparency
  - Not enough contrast between dark body and bright glow
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent
W, H = 256, 256

FOLDER = ("M32 72 Q32 56 48 56 H104 Q112 56 120 64 L132 76 H208 "
          "Q224 76 224 92 V184 Q224 200 208 200 H48 Q32 200 32 184 Z")
FRONT = "M32 108 H224 V184 Q224 200 208 200 H48 Q32 200 32 184 Z"


def render(name, svg):
    for size in [256, 128]:
        s = "" if size == 256 else f"_{size}"
        cairosvg.svg2png(bytestring=svg.encode(),
                         write_to=str(OUT / f"{name}{s}.png"),
                         output_width=size, output_height=size)


def edge_lit(name, **k):
    bo = k.get('body_op', 0.60)       # body opacity — lower = more glass
    bk = k.get('back_l', 8)           # back panel lightness
    ft = k.get('front_l', 13)         # front panel lightness
    gb = k.get('glow_blur', 12)       # glow blur radius — bigger = wider bleed
    go = k.get('glow_op', 0.40)       # overall glow opacity
    bgo = k.get('bottom_glow', 0.85)  # bottom edge glow opacity
    bgb = k.get('bottom_blur', 14)    # bottom edge blur radius
    sgo = k.get('side_glow', 0.35)    # side glow opacity
    tgo = k.get('tab_glow', 0.20)     # tab area glow (dimmer)
    gl = k.get('gloss', 0.22)         # diagonal reflection strength
    ge = k.get('gloss_end', 0.45)     # where reflection fades
    so = k.get('sharp', 0.22)         # sharp edge opacity
    co = k.get('crease', 0.20)        # crease line opacity
    fh = k.get('front_hi', 0.08)      # front panel top highlight

    bc = f"#{bk:02x}{bk+5:02x}{bk+15:02x}"
    fc1 = f"#{ft:02x}{ft+6:02x}{ft+18:02x}"
    fc2 = f"#{bk:02x}{bk+4:02x}{bk+12:02x}"

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="bk" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{bc}"/><stop offset="100%" stop-color="#050810"/></linearGradient>
  <linearGradient id="ft" x1="0" y1="0" x2="0.12" y2="1">
    <stop offset="0%" stop-color="{fc1}" stop-opacity="{bo}"/>
    <stop offset="100%" stop-color="{fc2}" stop-opacity="{bo+0.05}"/></linearGradient>
  <linearGradient id="gl" x1="0" y1="0" x2="0.6" y2="0.7">
    <stop offset="0%" stop-color="#fff" stop-opacity="{gl}"/>
    <stop offset="{int(ge*100)}%" stop-color="#fff" stop-opacity="{gl*0.15:.3f}"/>
    <stop offset="65%" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <linearGradient id="fh" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#fff" stop-opacity="{fh}"/>
    <stop offset="20%" stop-color="#fff" stop-opacity="0"/></linearGradient>

  <filter id="w" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="{gb}"/></filter>
  <filter id="m" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="{gb*0.5:.0f}" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="b" x="-80%" y="-60%" width="260%" height="300%">
    <feGaussianBlur stdDeviation="{bgb}"/></filter>

  <clipPath id="fc"><path d="{FRONT}"/></clipPath>
</defs>

<!-- Bottom glow pool (the brightest element — light pools BELOW the folder) -->
<path d="M48 200 H208 Q224 200 224 184" fill="none" stroke="#0099FF"
  stroke-width="8" stroke-opacity="{bgo}" stroke-linecap="round" filter="url(#b)"/>
<path d="M32 184 Q32 200 48 200" fill="none" stroke="#0099FF"
  stroke-width="7" stroke-opacity="{bgo*0.8:.2f}" stroke-linecap="round" filter="url(#b)"/>

<!-- Side glow (dimmer than bottom) -->
<path d="M224 92 V180" fill="none" stroke="#0099FF"
  stroke-width="5" stroke-opacity="{sgo}" stroke-linecap="round" filter="url(#w)"/>
<path d="M32 76 V180" fill="none" stroke="#0099FF"
  stroke-width="5" stroke-opacity="{sgo*0.85:.2f}" stroke-linecap="round" filter="url(#w)"/>

<!-- Tab glow (dimmest) -->
<path d="M48 56 H104 Q112 56 120 64 L132 76 H208 Q224 76 224 88" fill="none"
  stroke="#0099FF" stroke-width="4" stroke-opacity="{tgo}" stroke-linecap="round" filter="url(#w)"/>
<path d="M32 72 Q32 56 48 56" fill="none" stroke="#0099FF"
  stroke-width="4" stroke-opacity="{tgo}" stroke-linecap="round" filter="url(#w)"/>

<!-- Back panel fill (very dark) -->
<path d="{FOLDER}" fill="url(#bk)"/>

<!-- Front panel fill (glass — semi-transparent) -->
<path d="{FRONT}" fill="url(#ft)"/>

<!-- Front panel top highlight (glass catching light) -->
<g clip-path="url(#fc)"><rect x="32" y="108" width="192" height="28" fill="url(#fh)"/></g>

<!-- Crease (subtle separator) -->
<line x1="35" y1="108" x2="221" y2="108" stroke="#0099FF"
  stroke-width="1" stroke-opacity="{co}" filter="url(#m)"/>

<!-- Diagonal glass reflection (THE PROMINENT BAND) -->
<g clip-path="url(#fc)"><path d="M32 108 L185 108 L32 200 Z" fill="url(#gl)"/></g>

<!-- Medium glow outline (adds edge definition) -->
<path d="{FOLDER}" fill="none" stroke="#0099FF"
  stroke-width="2" stroke-opacity="{go*0.6:.2f}" filter="url(#m)"/>

<!-- Sharp structural edge -->
<path d="{FOLDER}" fill="none" stroke="rgba(0,153,255,{so})" stroke-width="1.5"/>
<path d="{FRONT}" fill="none" stroke="rgba(0,153,255,{so*0.5:.2f})" stroke-width="0.75"/>

<!-- Tab catch light -->
<path d="M50 57 H102 Q110 57 118 65" fill="none"
  stroke="#fff" stroke-width="0.75" stroke-opacity="0.05" stroke-linecap="round"/>
</svg>'''
    render(name, svg)
    return svg


def pulse_core(name, base_svg, **k):
    cr = k.get('core_r', 22)
    co = k.get('core_op', 0.85)
    fl = k.get('flare', 0.35)
    cs = k.get('center', 3.5)

    core = f'''
<defs>
  <radialGradient id="cg">
    <stop offset="0%" stop-color="#44ccff" stop-opacity="0.85"/>
    <stop offset="20%" stop-color="#0099FF" stop-opacity="0.5"/>
    <stop offset="55%" stop-color="#0066aa" stop-opacity="0.15"/>
    <stop offset="100%" stop-color="#003366" stop-opacity="0"/></radialGradient>
  <filter id="cb" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="6"/></filter>
  <clipPath id="ac"><path d="{FOLDER}"/></clipPath>
</defs>
<g clip-path="url(#ac)">
  <circle cx="134" cy="150" r="{cr*2.2:.0f}" fill="none" stroke="#004488"
    stroke-width="{cr*0.8:.0f}" stroke-opacity="0.06" filter="url(#w)"/>
  <circle cx="134" cy="150" r="{cr}" fill="url(#cg)" opacity="{co}" filter="url(#cb)"/>
  <line x1="45" y1="150" x2="222" y2="150" stroke="#0088dd"
    stroke-width="2" stroke-opacity="{fl}" filter="url(#m)"/>
  <line x1="75" y1="150" x2="193" y2="150" stroke="#33bbff"
    stroke-width="1" stroke-opacity="{fl*0.5:.2f}"/>
  <circle cx="134" cy="150" r="{cr*0.4:.0f}" fill="#0099FF" opacity="0.75" filter="url(#m)"/>
  <circle cx="134" cy="150" r="{cs}" fill="#77eeff" opacity="0.95"/>
</g>'''

    # Insert before </svg> — need to handle the defs merging
    # Simple approach: insert the core group and its defs right before closing
    svg = base_svg.replace('</svg>', core + '\n</svg>')
    # Add the extra defs by finding first </defs> and inserting before it
    # Actually the core defs use filters from the base, and adds its own
    # Let's just inject the radialGradient and filter into existing defs
    extra_defs = '''<radialGradient id="cg">
    <stop offset="0%" stop-color="#44ccff" stop-opacity="0.85"/>
    <stop offset="20%" stop-color="#0099FF" stop-opacity="0.5"/>
    <stop offset="55%" stop-color="#0066aa" stop-opacity="0.15"/>
    <stop offset="100%" stop-color="#003366" stop-opacity="0"/></radialGradient>
  <filter id="cb" x="-60%" y="-60%" width="220%" height="220%">
    <feGaussianBlur stdDeviation="6"/></filter>
  <clipPath id="ac"><path d="''' + FOLDER + '''"/></clipPath>'''

    svg = base_svg.replace('</defs>', extra_defs + '\n</defs>')
    # Now add just the group (without wrapping defs)
    core_group = f'''<g clip-path="url(#ac)">
  <circle cx="134" cy="150" r="{cr*2.2:.0f}" fill="none" stroke="#004488"
    stroke-width="{cr*0.8:.0f}" stroke-opacity="0.06" filter="url(#w)"/>
  <circle cx="134" cy="150" r="{cr}" fill="url(#cg)" opacity="{co}" filter="url(#cb)"/>
  <line x1="45" y1="150" x2="222" y2="150" stroke="#0088dd"
    stroke-width="2" stroke-opacity="{fl}" filter="url(#m)"/>
  <line x1="75" y1="150" x2="193" y2="150" stroke="#33bbff"
    stroke-width="1" stroke-opacity="{fl*0.5:.2f}"/>
  <circle cx="134" cy="150" r="{cr*0.4:.0f}" fill="#0099FF" opacity="0.75" filter="url(#m)"/>
  <circle cx="134" cy="150" r="{cs}" fill="#77eeff" opacity="0.95"/>
</g>'''
    svg = svg.replace('</svg>', core_group + '\n</svg>')
    render(name, svg)


if __name__ == "__main__":
    print("Pass 2 — pushing toward concept...")

    # V6: Darker body, brighter bottom, wider glow bleed, bigger diagonal reflection
    s6 = edge_lit("v6",
        body_op=0.55, back_l=7, front_l=11,
        glow_blur=13, glow_op=0.38,
        bottom_glow=0.90, bottom_blur=16,
        side_glow=0.38, tab_glow=0.18,
        gloss=0.24, gloss_end=0.48,
        sharp=0.24, crease=0.18, front_hi=0.09)
    print("  v6 — darker body, brighter bottom pool")

    # V7: Even more extreme — concept matching attempt
    s7 = edge_lit("v7",
        body_op=0.50, back_l=6, front_l=10,
        glow_blur=14, glow_op=0.35,
        bottom_glow=0.95, bottom_blur=18,
        side_glow=0.40, tab_glow=0.15,
        gloss=0.26, gloss_end=0.50,
        sharp=0.26, crease=0.15, front_hi=0.10)
    print("  v7 — maximum push")

    # V8: Refined from v6/v7 — finding the sweet spot
    s8 = edge_lit("v8_final",
        body_op=0.52, back_l=7, front_l=11,
        glow_blur=14, glow_op=0.36,
        bottom_glow=0.92, bottom_blur=16,
        side_glow=0.40, tab_glow=0.16,
        gloss=0.25, gloss_end=0.46,
        sharp=0.25, crease=0.16, front_hi=0.10)
    print("  v8_final — refined sweet spot")

    # Pulse-Core on v8 base
    pulse_core("pulse_v8_final", s8,
        core_r=22, core_op=0.88, flare=0.38, center=3.5)
    print("  pulse_v8_final — pulse core on best base")

    print("\nDone.")
