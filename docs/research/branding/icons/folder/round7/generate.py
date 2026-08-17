#!/usr/bin/env python3
"""
Round 7 — BREAK THE MOLD. 128px only.

Stop drawing rectangles with bumps. Start designing InterGenOS containers.

Every previous round was the same shape with different paint.
This round: each concept is fundamentally different from the others.
"""

import cairosvg
from pathlib import Path

OUT = Path(__file__).parent / "png"
W, H = 256, 256
BG = "#000000"


def render(name, svg_str):
    OUT.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg_str.encode(),
                     write_to=str(OUT / f"{name}.png"),
                     output_width=128, output_height=128)
    print(f"  {name}")


def a_pulse_crease():
    """A — The Pulse Folder.
    The crease between back and front panels IS the ECG heartbeat.
    Every folder on the system carries InterGenOS's pulse."""
    # Simplified pulse path scaled to folder width (44 to 212)
    # Baseline at y=118, pulse centered around x=100
    pulse = ("M 44 118 L 80 118 "        # flat lead-in
             "L 88 124 "                   # Q dip
             "L 98 90 "                    # R peak (UP)
             "L 108 140 "                  # S trough (DOWN)
             "L 114 112 "                  # T peak
             "L 122 118 "                  # return to baseline
             "L 212 118")                  # flat trail

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="back" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#005599"/>
    <stop offset="100%" stop-color="#002a55"/></linearGradient>
  <linearGradient id="front" x1="0" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0099FF"/>
    <stop offset="7%" stop-color="#3ab0ff"/>
    <stop offset="15%" stop-color="#0095f5"/>
    <stop offset="85%" stop-color="#0080dd"/>
    <stop offset="94%" stop-color="#3ab0ff"/>
    <stop offset="100%" stop-color="#0075cc"/></linearGradient>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.12"/>
    <stop offset="30%" stop-color="#fff" stop-opacity="0.02"/>
    <stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="6"
    flood-color="#001133" flood-opacity="0.55"/></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="3" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <clipPath id="frontClip">
    <path d="M 44 118 L 212 118 L 212 200 C 212 208, 204 214, 196 214
             L 60 214 C 52 214, 44 208, 44 200 Z"/>
  </clipPath>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel (dark blue, no visible tab — just slightly taller on left) -->
<path d="M 60 82 C 52 82, 44 90, 44 98
         L 44 200 C 44 208, 52 214, 60 214
         L 196 214 C 204 214, 212 208, 212 200
         L 212 98 C 212 90, 204 82, 196 82
         Z"
      fill="url(#back)" filter="url(#sh)"/>

<!-- Front panel (clipped to below pulse line) -->
<g clip-path="url(#frontClip)">
  <rect x="44" y="100" width="168" height="120" fill="url(#front)"/>
  <path d="M 44 118 L 130 118 L 44 178 Z" fill="url(#gloss)"/>
</g>

<!-- THE PULSE — the crease IS the heartbeat -->
<path d="{pulse}" fill="none" stroke="#44ccff" stroke-width="2.5"
      stroke-opacity="0.8" stroke-linecap="round" stroke-linejoin="round"
      filter="url(#glow)"/>
</svg>'''
    render("A_pulse_crease", svg)


def b_energy_gap():
    """B — Energy Gap.
    Two dark panels offset, with blue plasma streaming through the gap.
    Not a traditional folder — a container made of two surfaces with
    energy between them."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="panel1" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0e1830"/>
    <stop offset="100%" stop-color="#060c18"/></linearGradient>
  <linearGradient id="panel2" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#121e38"/>
    <stop offset="100%" stop-color="#0a1224"/></linearGradient>
  <linearGradient id="plasma" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.15"/>
    <stop offset="30%" stop-color="#33bbff" stop-opacity="0.5"/>
    <stop offset="70%" stop-color="#33bbff" stop-opacity="0.5"/>
    <stop offset="100%" stop-color="#0099FF" stop-opacity="0.15"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="6"
    flood-color="#000" flood-opacity="0.5"/></filter>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4"/></filter>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.08"/>
    <stop offset="25%" stop-color="#fff" stop-opacity="0.01"/>
    <stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel -->
<rect x="38" y="58" width="172" height="138" rx="16"
      fill="url(#panel1)" filter="url(#sh)"/>

<!-- Energy plasma in the gap -->
<rect x="42" y="110" width="164" height="18" rx="4"
      fill="url(#plasma)" filter="url(#glow)"/>
<line x1="52" y1="119" x2="196" y2="119"
      stroke="#44ddff" stroke-width="1.5" stroke-opacity="0.6"
      filter="url(#glow)"/>

<!-- Front panel (offset down) -->
<rect x="46" y="124" width="172" height="92" rx="14"
      fill="url(#panel2)" filter="url(#sh)"/>
<rect x="46" y="124" width="172" height="30" rx="14"
      fill="url(#gloss)"/>

<!-- Front panel subtle edge -->
<rect x="46" y="124" width="172" height="92" rx="14"
      fill="none" stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.12"/>
</svg>'''
    render("B_energy_gap", svg)


def c_frosted_panel():
    """C — Frosted Dark Panel.
    A dark glass panel with a frosted surface. Blue energy visible at
    the edges and through the frost. No traditional tab or crease.
    The folder reads as a dark container by its depth and opening."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="body" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0e1830"/>
    <stop offset="100%" stop-color="#060c18"/></linearGradient>
  <linearGradient id="frost" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#1a2a4a" stop-opacity="0.6"/>
    <stop offset="100%" stop-color="#0a1224" stop-opacity="0.3"/></linearGradient>
  <radialGradient id="inner" cx="0.5" cy="0.3" r="0.6">
    <stop offset="0%" stop-color="#0099FF" stop-opacity="0.15"/>
    <stop offset="100%" stop-color="#0099FF" stop-opacity="0"/></radialGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="7"
    flood-color="#001133" flood-opacity="0.5"/></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="3"/></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Main body -->
<rect x="38" y="56" width="180" height="152" rx="20"
      fill="url(#body)" filter="url(#sh)"/>

<!-- Inner blue glow (energy inside the container) -->
<rect x="42" y="60" width="172" height="144" rx="18"
      fill="url(#inner)"/>

<!-- Frosted overlay (upper portion — the "lid") -->
<path d="M 58 56 L 198 56 C 210 56, 218 64, 218 76 L 218 110 L 38 110
         L 38 76 C 38 64, 46 56, 58 56 Z"
      fill="url(#frost)"/>

<!-- Blue energy at the opening seam -->
<line x1="42" y1="110" x2="214" y2="110"
      stroke="#0099FF" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Blue edge glow -->
<rect x="38" y="56" width="180" height="152" rx="20"
      fill="none" stroke="#0099FF" stroke-width="1" stroke-opacity="0.15"
      filter="url(#glow)"/>

<!-- Top edge highlight -->
<path d="M 60 57 L 196 57" fill="none"
      stroke="#33bbff" stroke-width="0.75" stroke-opacity="0.2"
      stroke-linecap="round"/>
</svg>'''
    render("C_frosted_panel", svg)


def d_circuit_folder():
    """D — Circuit Trace Folder.
    The folder surface has visible circuit trace patterns — connecting
    back to the original InterGenOS branding (circuit board macro).
    Dark body with blue circuit traces etched into the surface."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="body" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0c1628"/>
    <stop offset="100%" stop-color="#060a14"/></linearGradient>
  <linearGradient id="front" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#101c34"/>
    <stop offset="100%" stop-color="#080e1c"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="6"
    flood-color="#000" flood-opacity="0.5"/></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="2" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Body -->
<rect x="40" y="68" width="176" height="146" rx="16"
      fill="url(#body)" filter="url(#sh)"/>

<!-- Crease -->
<line x1="40" y1="116" x2="216" y2="116"
      stroke="#0099FF" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Front panel -->
<rect x="40" y="118" width="176" height="96" rx="0"
      fill="url(#front)"/>
<path d="M 40 118 L 216 118 L 216 198 C 216 208, 208 214, 200 214
         L 56 214 C 48 214, 40 208, 40 198 Z"
      fill="url(#front)"/>

<!-- CIRCUIT TRACES on front panel surface -->
<g stroke="#0099FF" stroke-opacity="0.25" stroke-width="1.2"
   stroke-linecap="round" fill="none">
  <!-- Horizontal traces -->
  <line x1="56" y1="138" x2="100" y2="138"/>
  <line x1="120" y1="138" x2="156" y2="138"/>
  <line x1="56" y1="158" x2="84" y2="158"/>
  <line x1="140" y1="168" x2="200" y2="168"/>
  <!-- Vertical traces -->
  <line x1="100" y1="138" x2="100" y2="158"/>
  <line x1="156" y1="128" x2="156" y2="148"/>
  <line x1="84" y1="148" x2="84" y2="168"/>
  <!-- Connection nodes (small circles) -->
  <circle cx="100" cy="138" r="2" fill="#0099FF" fill-opacity="0.3" stroke="none"/>
  <circle cx="156" cy="138" r="2" fill="#0099FF" fill-opacity="0.3" stroke="none"/>
  <circle cx="84" cy="158" r="2" fill="#0099FF" fill-opacity="0.3" stroke="none"/>
  <circle cx="100" cy="158" r="2" fill="#0099FF" fill-opacity="0.3" stroke="none"/>
  <circle cx="140" cy="168" r="2" fill="#0099FF" fill-opacity="0.3" stroke="none"/>
</g>

<!-- Circuit traces on back panel too (dimmer) -->
<g stroke="#0099FF" stroke-opacity="0.12" stroke-width="1"
   stroke-linecap="round" fill="none">
  <line x1="56" y1="88" x2="120" y2="88"/>
  <line x1="140" y1="88" x2="180" y2="88"/>
  <line x1="120" y1="82" x2="120" y2="98"/>
  <line x1="80" y1="96" x2="160" y2="96"/>
</g>

<!-- Outer edge trace -->
<rect x="40" y="68" width="176" height="146" rx="16"
      fill="none" stroke="#0099FF" stroke-width="1" stroke-opacity="0.2"
      filter="url(#glow)"/>
</svg>'''
    render("D_circuit_folder", svg)


def e_gradient_sweep():
    """E — Bold Gradient Sweep.
    Inspired by Amy/Gradient themes. A beautiful diagonal gradient
    sweep from deep navy to bright cyan across the folder surface.
    Diagonal light rays for polish. Bold and present."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="back" x1="0" y1="1" x2="1" y2="0">
    <stop offset="0%" stop-color="#003060"/>
    <stop offset="100%" stop-color="#0077cc"/></linearGradient>
  <linearGradient id="front" x1="0" y1="1" x2="1" y2="0">
    <stop offset="0%" stop-color="#004488"/>
    <stop offset="30%" stop-color="#0088dd"/>
    <stop offset="70%" stop-color="#0099FF"/>
    <stop offset="100%" stop-color="#33ccff"/></linearGradient>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.6" y2="0.8">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.15"/>
    <stop offset="25%" stop-color="#fff" stop-opacity="0.04"/>
    <stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="6"
    flood-color="#001133" flood-opacity="0.55"/></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel -->
<path d="M 60 78 C 52 78, 44 86, 44 94
         L 44 200 C 44 208, 52 214, 60 214
         L 196 214 C 204 214, 212 208, 212 200
         L 212 94 C 212 86, 204 78, 196 78 Z"
      fill="url(#back)" filter="url(#sh)"/>

<!-- Crease highlight -->
<line x1="48" y1="118" x2="208" y2="118"
      stroke="#66ddff" stroke-width="2" stroke-opacity="0.4"/>

<!-- Front panel -->
<path d="M 44 120 L 212 120
         L 212 200 C 212 208, 204 214, 196 214
         L 60 214 C 52 214, 44 208, 44 200 Z"
      fill="url(#front)"/>

<!-- Diagonal light ray 1 -->
<path d="M 44 120 L 130 120 L 44 185 Z"
      fill="url(#gloss)"/>

<!-- Diagonal light ray 2 (fainter) -->
<path d="M 70 120 L 165 120 L 70 195 Z"
      fill="#ffffff" opacity="0.025"/>

<!-- Top edge catch light -->
<line x1="52" y1="120.5" x2="204" y2="120.5"
      stroke="#ffffff" stroke-width="0.75" stroke-opacity="0.08"/>
</svg>'''
    render("E_gradient_sweep", svg)


def f_hologram():
    """F — The Hologram.
    The folder is a dark glass panel with a holographic blue edge.
    Iridescent glow at the borders, translucent body, like a
    holographic projection of a container. Futuristic."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="body" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0e1830" stop-opacity="0.7"/>
    <stop offset="100%" stop-color="#060c18" stop-opacity="0.5"/></linearGradient>
  <linearGradient id="holo" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#0066cc"/>
    <stop offset="25%" stop-color="#0099FF"/>
    <stop offset="50%" stop-color="#33ccff"/>
    <stop offset="75%" stop-color="#0099FF"/>
    <stop offset="100%" stop-color="#0066cc"/></linearGradient>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="bigglow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="6" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Outer holographic glow (wide, dim) -->
<rect x="38" y="60" width="180" height="148" rx="20"
      fill="none" stroke="url(#holo)" stroke-width="3" stroke-opacity="0.15"
      filter="url(#bigglow)"/>

<!-- Body fill (dark, translucent) -->
<rect x="38" y="60" width="180" height="148" rx="20"
      fill="url(#body)"/>

<!-- Crease line -->
<line x1="46" y1="112" x2="210" y2="112"
      stroke="#33ccff" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Inner holographic edge (sharp) -->
<rect x="38" y="60" width="180" height="148" rx="20"
      fill="none" stroke="url(#holo)" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Front panel (slightly more opaque) -->
<path d="M 38 112 L 218 112 L 218 190
         C 218 200, 210 208, 200 208
         L 58 208 C 48 208, 38 200, 38 190 Z"
      fill="#0c1628" fill-opacity="0.4"/>

<!-- Scan line effect (very subtle horizontal lines) -->
<g stroke="#0099FF" stroke-opacity="0.04" stroke-width="0.5">
  <line x1="42" y1="75" x2="214" y2="75"/>
  <line x1="42" y1="85" x2="214" y2="85"/>
  <line x1="42" y1="95" x2="214" y2="95"/>
  <line x1="42" y1="125" x2="214" y2="125"/>
  <line x1="42" y1="135" x2="214" y2="135"/>
  <line x1="42" y1="145" x2="214" y2="145"/>
  <line x1="42" y1="155" x2="214" y2="155"/>
  <line x1="42" y1="165" x2="214" y2="165"/>
  <line x1="42" y1="175" x2="214" y2="175"/>
  <line x1="42" y1="185" x2="214" y2="185"/>
  <line x1="42" y1="195" x2="214" y2="195"/>
</g>
</svg>'''
    render("F_hologram", svg)


def g_split_surface():
    """G — Split Surface.
    A dark container split horizontally. The top half has a
    subtle blue gradient, the bottom half is deeper navy. The split
    glows with energy. Like a clamshell case for data."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="top" x1="0" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0077cc"/>
    <stop offset="100%" stop-color="#004477"/></linearGradient>
  <linearGradient id="bot" x1="0" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0099FF"/>
    <stop offset="8%" stop-color="#3ab0ff"/>
    <stop offset="16%" stop-color="#0095f5"/>
    <stop offset="84%" stop-color="#0080dd"/>
    <stop offset="94%" stop-color="#3ab0ff"/>
    <stop offset="100%" stop-color="#0075cc"/></linearGradient>
  <linearGradient id="gloss" x1="0" y1="0" x2="0.5" y2="1">
    <stop offset="0%" stop-color="#fff" stop-opacity="0.14"/>
    <stop offset="25%" stop-color="#fff" stop-opacity="0.03"/>
    <stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="4" stdDeviation="6"
    flood-color="#001133" flood-opacity="0.55"/></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="2.5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Top half (darker, the "lid") -->
<path d="M 58 62 C 48 62, 40 70, 40 80
         L 40 118 L 216 118
         L 216 80 C 216 70, 208 62, 198 62 Z"
      fill="url(#top)" filter="url(#sh)"/>

<!-- Energy split line -->
<line x1="44" y1="118" x2="212" y2="118"
      stroke="#44ddff" stroke-width="2.5" stroke-opacity="0.7"
      filter="url(#glow)"/>

<!-- Bottom half (brighter, the "body") -->
<path d="M 40 120 L 216 120
         L 216 196 C 216 206, 208 214, 198 214
         L 58 214 C 48 214, 40 206, 40 196 Z"
      fill="url(#bot)" filter="url(#sh)"/>

<!-- Diagonal gloss on bottom -->
<path d="M 40 120 L 140 120 L 40 185 Z"
      fill="url(#gloss)"/>

<!-- Top panel catch light -->
<path d="M 60 63 L 196 63" fill="none"
      stroke="#ffffff" stroke-width="0.75" stroke-opacity="0.06"
      stroke-linecap="round"/>
</svg>'''
    render("G_split_surface", svg)


def h_book_binder():
    """H — The Book Binder.
    Vertical spine on the left with pages visible. Like a dark leather
    binder with a blue spine. Different metaphor — container of pages,
    not container of files."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="cover" x1="0" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0c1628"/>
    <stop offset="100%" stop-color="#060a14"/></linearGradient>
  <linearGradient id="spine" x1="1" y1="0" x2="0" y2="0">
    <stop offset="0%" stop-color="#0077cc"/>
    <stop offset="40%" stop-color="#0066aa"/>
    <stop offset="100%" stop-color="#004488"/></linearGradient>
  <linearGradient id="pages" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#1a2a4a"/>
    <stop offset="100%" stop-color="#0e1830"/></linearGradient>
  <filter id="sh"><feDropShadow dx="0" dy="3" stdDeviation="5"
    flood-color="#000" flood-opacity="0.5"/></filter>
  <filter id="glow" x="-15%" y="-15%" width="130%" height="130%">
    <feGaussianBlur stdDeviation="2" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Pages (visible behind cover on the right edge) -->
<rect x="78" y="66" width="136" height="144" rx="4"
      fill="url(#pages)"/>
<line x1="80" y1="84" x2="206" y2="84"
      stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.08"/>
<line x1="80" y1="104" x2="206" y2="104"
      stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.08"/>
<line x1="80" y1="124" x2="206" y2="124"
      stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.08"/>

<!-- Cover -->
<rect x="44" y="58" width="160" height="152" rx="6"
      fill="url(#cover)" filter="url(#sh)"/>

<!-- Spine (blue vertical bar on left edge) -->
<path d="M 44 64 C 44 60, 48 58, 52 58
         L 66 58 L 66 210 L 52 210
         C 48 210, 44 208, 44 204 Z"
      fill="url(#spine)"/>

<!-- Spine glow -->
<path d="M 66 62 L 66 206"
      stroke="#33bbff" stroke-width="1.5" stroke-opacity="0.4"
      filter="url(#glow)"/>

<!-- Spine edge highlight -->
<path d="M 54 59 L 54 209"
      stroke="#44ccff" stroke-width="0.5" stroke-opacity="0.2"/>

<!-- Cover subtle edge -->
<rect x="44" y="58" width="160" height="152" rx="6"
      fill="none" stroke="#0099FF" stroke-width="0.5" stroke-opacity="0.08"/>
</svg>'''
    render("H_book_binder", svg)


if __name__ == "__main__":
    a_pulse_crease()
    b_energy_gap()
    c_frosted_panel()
    d_circuit_folder()
    e_gradient_sweep()
    f_hologram()
    g_split_surface()
    h_book_binder()
    print("Done — 8 concepts, all different.")
