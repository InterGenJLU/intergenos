#!/usr/bin/env python3
"""
InterGenOS Folder Icon — Round 4
RADICAL RETHINK. Not "a dark traditional folder."
An InterGenOS container that says "I'm a folder."

The shell theme took GNOME and made it unmistakably InterGenOS.
The folder must do the same: unmistakably a folder, unmistakably InterGenOS.

New rules:
  - The tab is NOT a protruding bump. It's a design element.
  - The shape is NOT a rectangle. It's a container with personality.
  - The blue is NOT painted on. It's the energy inside.
  - Depth comes from layering, highlights, and material quality.
"""

import cairosvg
from pathlib import Path

OUT_SVG = Path(__file__).parent / "svg"
OUT_PNG = Path(__file__).parent / "png"

BG = "#000000"
ACCENT = "#0099FF"
ACCENT_B = "#33b1ff"

W, H = 256, 256


def render(name, svg_str):
    (OUT_SVG / f"{name}.svg").write_text(svg_str)
    for size in [256, 128, 64, 48]:
        suffix = "" if size == 256 else f"_{size}"
        cairosvg.svg2png(bytestring=svg_str.encode(),
                         write_to=str(OUT_PNG / f"{name}{suffix}.png"),
                         output_width=size, output_height=size)
    print(f"  {name}")


def concept_a():
    """A — Overlapping Panels.
    Two generously rounded dark panels slightly offset. The back one
    peeks out behind the front one at the top-left. The offset IS the tab —
    no bump, no protrusion. Blue energy glows in the gap between them."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000" flood-opacity="0.5"/></filter>

  <linearGradient id="backA" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#0e1628"/>
    <stop offset="100%" stop-color="#070b14"/></linearGradient>
  <linearGradient id="frontA" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#151e36"/>
    <stop offset="5%" stop-color="#121a2e"/>
    <stop offset="100%" stop-color="#0a1020"/></linearGradient>
  <linearGradient id="highlightA" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.06"/>
    <stop offset="40%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel — peeks out at top and left -->
<rect x="38" y="64" width="172" height="138" rx="16"
      fill="url(#backA)" filter="url(#shadow)"/>

<!-- Blue energy in the gap between panels -->
<rect x="40" y="98" width="168" height="10" rx="3"
      fill="{ACCENT}" opacity="0.2"/>
<line x1="48" y1="104" x2="200" y2="104"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Front panel — overlaps, slightly smaller, offset down-right -->
<rect x="44" y="108" width="172" height="100" rx="14"
      fill="url(#frontA)"/>
<!-- Front highlight -->
<rect x="44" y="108" width="172" height="40" rx="14"
      fill="url(#highlightA)"/>

<!-- Subtle edge accent on front panel -->
<rect x="44" y="108" width="172" height="100" rx="14"
      fill="none" stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.1"/>

<!-- Back panel top-left exposure — the "tab" via negative space -->
<rect x="38" y="64" width="100" height="44" rx="16"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.12"/>
</svg>'''
    render("A_overlapping_panels", svg)


def concept_b():
    """B — The Glassy Container.
    A dark rounded container with visible depth. The top portion is a
    translucent 'lid' area where you can see blue energy inside.
    The bottom is solid dark. Like a dark glass box."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="5" stdDeviation="8" flood-color="#000" flood-opacity="0.5"/></filter>

  <linearGradient id="bodyB" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#12192e"/>
    <stop offset="50%" stop-color="#0d1424"/>
    <stop offset="100%" stop-color="#080c18"/></linearGradient>
  <linearGradient id="lidB" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#141c32" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="#0e1628" stop-opacity="0.6"/></linearGradient>
  <radialGradient id="innerGlowB" cx="0.5" cy="0.6" r="0.5">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.18"/>
    <stop offset="70%" stop-color="{ACCENT}" stop-opacity="0.04"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>
  <linearGradient id="reflectB" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.07"/>
    <stop offset="30%" stop-color="#ffffff" stop-opacity="0.02"/>
    <stop offset="50%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Main body -->
<rect x="38" y="62" width="180" height="146" rx="18"
      fill="url(#bodyB)" filter="url(#shadow)"/>

<!-- Inner blue glow (the energy inside) -->
<rect x="42" y="66" width="172" height="138" rx="16"
      fill="url(#innerGlowB)"/>

<!-- Lid / top section (slightly translucent, shows energy) -->
<path d="M 38 80 L 38 78 C 38 68, 44 62, 56 62
         L 108 62 C 114 62, 118 62, 120 66
         L 124 74 C 126 78, 130 80, 136 80
         L 200 80 C 212 80, 218 86, 218 96 L 218 80
         L 38 80 Z"
      fill="url(#lidB)"/>

<!-- Divider line (where lid meets body) -->
<line x1="42" y1="106" x2="214" y2="106"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.35"
      filter="url(#glow)"/>

<!-- Front body surface below divider -->
<rect x="38" y="106" width="180" height="102" rx="0"
      fill="url(#bodyB)" opacity="0.7"/>
<path d="M 38 106 L 218 106 L 218 190 C 218 200, 212 208, 200 208
         L 56 208 C 44 208, 38 200, 38 190 Z"
      fill="url(#bodyB)"/>

<!-- Surface reflection -->
<path d="M 38 106 L 218 106 L 218 140 L 38 140 Z"
      fill="url(#reflectB)"/>

<!-- Outer edge accent -->
<rect x="38" y="62" width="180" height="146" rx="18"
      fill="none" stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.1"/>
</svg>'''
    render("B_glassy_container", svg)


def concept_c():
    """C — Layered Stack.
    The folder looks like two sheets of dark glass stacked with a slight
    offset. Blue light bleeds between the layers. No traditional tab —
    the offset IS the folder metaphor (back page behind front page)."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-25%" y="-25%" width="150%" height="150%">
    <feGaussianBlur stdDeviation="5"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="#000" flood-opacity="0.45"/></filter>

  <linearGradient id="backC" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#0e1628"/>
    <stop offset="100%" stop-color="#080c16"/></linearGradient>
  <linearGradient id="frontC" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#161f38"/>
    <stop offset="8%" stop-color="#121a2e"/>
    <stop offset="100%" stop-color="#0b1222"/></linearGradient>
  <linearGradient id="highlightC" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.06"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back sheet — slightly larger, offset up-left -->
<rect x="36" y="60" width="176" height="130" rx="16"
      fill="url(#backC)" filter="url(#shadow)"/>

<!-- Blue energy between the layers -->
<rect x="44" y="110" width="164" height="14" rx="4"
      fill="{ACCENT}" opacity="0.08" filter="url(#glow)"/>
<line x1="50" y1="116" x2="202" y2="116"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.45"
      filter="url(#glow)"/>

<!-- Front sheet — slightly smaller, offset down-right -->
<rect x="44" y="98" width="176" height="112" rx="14"
      fill="url(#frontC)" filter="url(#shadow)"/>

<!-- Front sheet highlight -->
<rect x="44" y="98" width="176" height="36" rx="14"
      fill="url(#highlightC)"/>

<!-- Front sheet subtle border -->
<rect x="44" y="98" width="176" height="112" rx="14"
      fill="none" stroke="{ACCENT}" stroke-width="0.75" stroke-opacity="0.12"/>

<!-- Back sheet top-left exposure (visible "behind" the front) -->
<path d="M 52 60 L 196 60 C 206 60, 212 66, 212 76 L 212 98"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.15"
      stroke-linecap="round"/>
</svg>'''
    render("C_layered_stack", svg)


def concept_d():
    """D — The Notched Container.
    A rounded dark rectangle where the tab is defined by a NOTCH cut
    into the top-right, not a bump on the top-left. The tab area is
    the LEFT portion of the top. The notch creates the visual break.
    This inverts the traditional metaphor while still reading as folder."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3.5"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="4" stdDeviation="7" flood-color="#000" flood-opacity="0.5"/></filter>

  <linearGradient id="bodyD" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#131b30"/>
    <stop offset="40%" stop-color="#0e1626"/>
    <stop offset="100%" stop-color="#080c18"/></linearGradient>
  <linearGradient id="frontD" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#171f38"/>
    <stop offset="10%" stop-color="#131a2e"/>
    <stop offset="100%" stop-color="#0b1222"/></linearGradient>
  <linearGradient id="highlightD" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.07"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Main body with notch — the notch on the right creates the tab on the left -->
<path d="M 56 62 C 46 62, 38 70, 38 80
         L 38 192 C 38 202, 46 210, 56 210
         L 200 210 C 210 210, 218 202, 218 192
         L 218 100 C 218 90, 210 82, 200 82
         L 148 82 C 140 82, 136 78, 134 74
         L 130 66 C 128 62, 124 62, 120 62
         Z"
      fill="url(#bodyD)" filter="url(#shadow)"/>

<!-- Crease / divider line -->
<line x1="42" y1="118" x2="214" y2="118"
      stroke="{ACCENT}" stroke-width="1" stroke-opacity="0.4"
      filter="url(#glow)"/>

<!-- Front panel below crease -->
<path d="M 38 118 L 218 118
         L 218 192 C 218 202, 210 210, 200 210
         L 56 210 C 46 210, 38 202, 38 192 Z"
      fill="url(#frontD)"/>

<!-- Front highlight -->
<path d="M 38 118 L 218 118 L 218 145 L 38 145 Z"
      fill="url(#highlightD)"/>

<!-- Notch accent — blue energy at the notch curve -->
<path d="M 148 82 C 140 82, 136 78, 134 74 L 130 66 C 128 62, 124 62, 120 62"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.35"
      stroke-linecap="round" filter="url(#glow)"/>

<!-- Subtle outer edge -->
<path d="M 56 62 C 46 62, 38 70, 38 80
         L 38 192 C 38 202, 46 210, 56 210
         L 200 210 C 210 210, 218 202, 218 192
         L 218 100 C 218 90, 210 82, 200 82
         L 148 82 C 140 82, 136 78, 134 74
         L 130 66 C 128 62, 124 62, 120 62 Z"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.08"/>
</svg>'''
    render("D_notched_container", svg)


def concept_e():
    """E — The Living Container.
    Completely modern rounded shape. The 'tab' is a subtle step-down
    in the top edge — the left side is higher than the right, creating
    an asymmetric top. Blue energy lives at the step and the crease.
    Generous radii, premium feel, alive."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="3.5"/></filter>
  <filter id="bigglow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="7"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="4" stdDeviation="7" flood-color="#000" flood-opacity="0.5"/></filter>

  <linearGradient id="bodyE" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#111a2e"/>
    <stop offset="100%" stop-color="#070b14"/></linearGradient>
  <linearGradient id="frontE" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#161f38"/>
    <stop offset="10%" stop-color="#121a2e"/>
    <stop offset="100%" stop-color="#0a1020"/></linearGradient>
  <linearGradient id="highlightE" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.06"/>
    <stop offset="35%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
  <radialGradient id="innerE" cx="0.45" cy="0.35" r="0.5">
    <stop offset="0%" stop-color="{ACCENT}" stop-opacity="0.12"/>
    <stop offset="100%" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Back panel — asymmetric top: left side is taller -->
<path d="M 56 66 C 46 66, 38 74, 38 84
         L 38 192 C 38 202, 46 210, 56 210
         L 200 210 C 210 210, 218 202, 218 192
         L 218 96 C 218 86, 210 78, 200 78
         L 146 78
         C 138 78, 132 74, 130 70
         C 128 66, 124 66, 118 66
         Z"
      fill="url(#bodyE)" filter="url(#shadow)"/>

<!-- Inner glow -->
<path d="M 56 66 C 46 66, 38 74, 38 84
         L 38 192 C 38 202, 46 210, 56 210
         L 200 210 C 210 210, 218 202, 218 192
         L 218 96 C 218 86, 210 78, 200 78
         L 146 78 C 138 78, 132 74, 130 70 C 128 66, 124 66, 118 66 Z"
      fill="url(#innerE)"/>

<!-- Energy at the step (where tab meets lower top edge) -->
<path d="M 146 78 C 138 78, 132 74, 130 70 C 128 66, 124 66, 118 66"
      fill="none" stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.4"
      stroke-linecap="round" filter="url(#glow)"/>

<!-- Crease — blue energy line -->
<line x1="42" y1="120" x2="214" y2="120"
      stroke="{ACCENT}" stroke-width="1.5" stroke-opacity="0.5"
      filter="url(#glow)"/>

<!-- Atmospheric glow at crease -->
<line x1="60" y1="120" x2="200" y2="120"
      stroke="{ACCENT}" stroke-width="6" stroke-opacity="0.06"
      filter="url(#bigglow)"/>

<!-- Front panel -->
<path d="M 38 120 L 218 120
         L 218 192 C 218 202, 210 210, 200 210
         L 56 210 C 46 210, 38 202, 38 192 Z"
      fill="url(#frontE)"/>

<!-- Front panel highlight -->
<path d="M 38 120 L 218 120 L 218 148 L 38 148 Z"
      fill="url(#highlightE)"/>

<!-- Very subtle outer edge -->
<path d="M 56 66 C 46 66, 38 74, 38 84
         L 38 192 C 38 202, 46 210, 56 210
         L 200 210 C 210 210, 218 202, 218 192
         L 218 96 C 218 86, 210 78, 200 78
         L 146 78 C 138 78, 132 74, 130 70 C 128 66, 124 66, 118 66 Z"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.06"/>
</svg>'''
    render("E_living_container", svg)


def concept_f():
    """F — The Glow Line.
    Maximum modern: a beautifully rounded dark container with ONE
    horizontal glow line across the upper third. No traditional tab
    at all. Just a dark shape with a single blue energy crease that
    says 'this opens here.' The simplest possible statement."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">
<defs>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="4"/></filter>
  <filter id="bigglow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="8"/></filter>
  <filter id="shadow" x="-10%" y="-5%" width="120%" height="130%">
    <feDropShadow dx="0" dy="4" stdDeviation="7" flood-color="#000" flood-opacity="0.5"/></filter>

  <linearGradient id="bodyF" x1="0.3" y1="0" x2="0.7" y2="1">
    <stop offset="0%" stop-color="#12192e"/>
    <stop offset="40%" stop-color="#0e1424"/>
    <stop offset="100%" stop-color="#080c18"/></linearGradient>
  <linearGradient id="frontF" x1="0.2" y1="0" x2="0.8" y2="1">
    <stop offset="0%" stop-color="#161f38"/>
    <stop offset="8%" stop-color="#131a2e"/>
    <stop offset="100%" stop-color="#0a1020"/></linearGradient>
  <linearGradient id="highlightF" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.06"/>
    <stop offset="30%" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>

<!-- Body — one unified rounded rectangle -->
<rect x="38" y="62" width="180" height="148" rx="20"
      fill="url(#bodyF)" filter="url(#shadow)"/>

<!-- THE glow line — the single defining feature -->
<line x1="46" y1="112" x2="210" y2="112"
      stroke="{ACCENT}" stroke-width="2" stroke-opacity="0.6"
      filter="url(#glow)"/>

<!-- Atmospheric glow around the line -->
<line x1="60" y1="112" x2="196" y2="112"
      stroke="{ACCENT}" stroke-width="10" stroke-opacity="0.05"
      filter="url(#bigglow)"/>

<!-- Front panel below the line -->
<path d="M 38 112 L 218 112 L 218 192
         C 218 202, 210 210, 200 210
         L 56 210 C 46 210, 38 202, 38 192 Z"
      fill="url(#frontF)"/>

<!-- Front highlight -->
<path d="M 38 112 L 218 112 L 218 138 L 38 138 Z"
      fill="url(#highlightF)"/>

<!-- Subtle outer border -->
<rect x="38" y="62" width="180" height="148" rx="20"
      fill="none" stroke="{ACCENT}" stroke-width="0.5" stroke-opacity="0.06"/>
</svg>'''
    render("F_glow_line", svg)


if __name__ == "__main__":
    OUT_SVG.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)

    print("Round 4 — radical rethink:")
    concept_a()
    concept_b()
    concept_c()
    concept_d()
    concept_e()
    concept_f()
    print("Done.")
