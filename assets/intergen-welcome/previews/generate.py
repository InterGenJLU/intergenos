#!/usr/bin/env python3
"""
Welcomer appearance-picker thumbnail generator.

Generates 10 deterministic 1280×800 PNGs (one per theme combo) showing a
stylized mini GNOME desktop scene in the respective theme's palette + icon
family. Each thumbnail uses the SAME composition layout — only colors +
icon-family geometry change between them. That's what gives the picker its
"curated set" cohesion.

Pipeline:
    THEMES (dict) -> render_svg(theme) -> cairosvg -> PNG

Outputs land at ~/intergenos/welcomer-thumbnails/<file>.png.

Run:
    python3 generate.py
"""

from pathlib import Path
import cairosvg

OUT_DIR = Path.home() / "intergenos" / "welcomer-thumbnails"
W, H = 1280, 800

# ---------- THEMES ----------

THEMES = [
    {
        "name": "InterGenOS",
        "file": "intergenos",
        "wallpaper_top": "#1a1a2e",
        "wallpaper_bottom": "#16213e",
        "topbar_bg": "#0f0f1e",
        "topbar_text": "#e0e0e0",
        "window_chrome": "#1a1a2e",
        "window_content": "#0f1419",
        "window_text": "#e0e0e0",
        "sidebar_bg": "#16213e",
        "accent": "#0099FF",
        "selected_bg": "#0099FF",
        "selected_text": "#ffffff",
        "icon_family": "intergenos",
    },
    {
        "name": "Orchis Dark",
        "file": "orchis-dark",
        "wallpaper_top": "#3a3a3c",
        "wallpaper_bottom": "#2a2a2c",
        "topbar_bg": "#2c2c2e",
        "topbar_text": "#e0e0e0",
        "window_chrome": "#3a3a3c",
        "window_content": "#1c1c1e",
        "window_text": "#e0e0e0",
        "sidebar_bg": "#2c2c2e",
        "accent": "#1a73e8",
        "selected_bg": "#1a73e8",
        "selected_text": "#ffffff",
        "icon_family": "papirus_dark",
    },
    {
        "name": "WhiteSur",
        "file": "whitesur",
        "wallpaper_top": "#2a2a2d",
        "wallpaper_bottom": "#1d1d1f",
        "topbar_bg": "#28282b",
        "topbar_text": "#f0f0f0",
        "window_chrome": "#323236",
        "window_content": "#1d1d1f",
        "window_text": "#f0f0f0",
        "sidebar_bg": "#28282b",
        "accent": "#0a84ff",
        "selected_bg": "#0a84ff",
        "selected_text": "#ffffff",
        "icon_family": "whitesur_dark",
    },
    {
        "name": "Catppuccin Mocha",
        "file": "catppuccin-mocha",
        "wallpaper_top": "#1e1e2e",
        "wallpaper_bottom": "#181825",
        "topbar_bg": "#181825",
        "topbar_text": "#cdd6f4",
        "window_chrome": "#1e1e2e",
        "window_content": "#11111b",
        "window_text": "#cdd6f4",
        "sidebar_bg": "#181825",
        "accent": "#89b4fa",
        "selected_bg": "#89b4fa",
        "selected_text": "#1e1e2e",
        "icon_family": "papirus_dark",
    },
    {
        "name": "Nordic",
        "file": "nordic",
        "wallpaper_top": "#3b4252",
        "wallpaper_bottom": "#2e3440",
        "topbar_bg": "#2e3440",
        "topbar_text": "#eceff4",
        "window_chrome": "#3b4252",
        "window_content": "#2e3440",
        "window_text": "#eceff4",
        "sidebar_bg": "#434c5e",
        "accent": "#88c0d0",
        "selected_bg": "#88c0d0",
        "selected_text": "#2e3440",
        "icon_family": "papirus_dark",
    },
    {
        "name": "Graphite",
        "file": "graphite",
        "wallpaper_top": "#4a4a4a",
        "wallpaper_bottom": "#383838",
        "topbar_bg": "#2e2e2e",
        "topbar_text": "#d0d0d0",
        "window_chrome": "#383838",
        "window_content": "#222222",
        "window_text": "#d0d0d0",
        "sidebar_bg": "#2e2e2e",
        "accent": "#787878",
        "selected_bg": "#787878",
        "selected_text": "#ffffff",
        "icon_family": "papirus_dark",
    },
    {
        "name": "Dracula",
        "file": "dracula",
        "wallpaper_top": "#2c2e3a",
        "wallpaper_bottom": "#282a36",
        "topbar_bg": "#21222c",
        "topbar_text": "#f8f8f2",
        "window_chrome": "#282a36",
        "window_content": "#1e1f29",
        "window_text": "#f8f8f2",
        "sidebar_bg": "#21222c",
        "accent": "#bd93f9",
        "selected_bg": "#bd93f9",
        "selected_text": "#282a36",
        "icon_family": "papirus_dark",
        # Dracula has signature secondary accents (pink + cyan + green)
        "icon_accent_pink": "#ff79c6",
        "icon_accent_cyan": "#8be9fd",
        "icon_accent_green": "#50fa7b",
    },
    {
        "name": "Fluent",
        "file": "fluent",
        "wallpaper_top": "#2b2b2b",
        "wallpaper_bottom": "#1f1f1f",
        "topbar_bg": "#1f1f1f",
        "topbar_text": "#ffffff",
        "window_chrome": "#2b2b2b",
        "window_content": "#1f1f1f",
        "window_text": "#ffffff",
        "sidebar_bg": "#252525",
        "accent": "#0078d4",
        "selected_bg": "#0078d4",
        "selected_text": "#ffffff",
        "icon_family": "fluent",
    },
    {
        "name": "Orchis Light",
        "file": "orchis-light",
        "wallpaper_top": "#fafafa",
        "wallpaper_bottom": "#e8e8e8",
        "topbar_bg": "#f0f0f0",
        "topbar_text": "#202020",
        "window_chrome": "#f8f8f7",
        "window_content": "#ffffff",
        "window_text": "#202020",
        "sidebar_bg": "#f0f0f0",
        "accent": "#1a73e8",
        "selected_bg": "#1a73e8",
        "selected_text": "#ffffff",
        "icon_family": "papirus_light",
    },
    {
        "name": "Cybernetic Blue",
        "file": "cybernetic-blue",
        "wallpaper_top": "#0a0e1f",
        "wallpaper_bottom": "#050818",
        "topbar_bg": "#070a18",
        "topbar_text": "#0099FF",
        "window_chrome": "#0d1224",
        "window_content": "#050818",
        "window_text": "#7ec4ff",
        "sidebar_bg": "#0a0e1f",
        "accent": "#00CCFF",
        "selected_bg": "#0099FF",
        "selected_text": "#ffffff",
        "icon_family": "cybernetic_blue",
    },
]


# ---------- ICON FAMILY GEOMETRY ----------
#
# Each family is a small SVG fragment representing one icon in the dock.
# The function takes (cx, cy) of the icon center + an accent color + theme
# dict, returns an SVG snippet for one icon.

def icon_papirus_dark(cx, cy, accent, theme):
    """Papirus-Dark: flat rounded square with subtle colored badge."""
    r = 38  # icon half-size
    return f'''
    <rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" rx="14"
          fill="#2a2a2e" stroke="#3a3a3e" stroke-width="1.5"/>
    <rect x="{cx-r+8}" y="{cy-r+8}" width="{2*r-16}" height="{2*r-16}" rx="8"
          fill="{accent}" opacity="0.85"/>
    <circle cx="{cx}" cy="{cy}" r="12" fill="#3a3a3e"/>
    '''


def icon_papirus_light(cx, cy, accent, theme):
    """Papirus-Light: flat rounded square light variant."""
    r = 38
    return f'''
    <rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" rx="14"
          fill="#e8e8e8" stroke="#c0c0c0" stroke-width="1.5"/>
    <rect x="{cx-r+8}" y="{cy-r+8}" width="{2*r-16}" height="{2*r-16}" rx="8"
          fill="{accent}" opacity="0.85"/>
    <circle cx="{cx}" cy="{cy}" r="12" fill="#ffffff"/>
    '''


def icon_whitesur_dark(cx, cy, accent, theme):
    """WhiteSur: macOS-rounded squircle (very rounded corners)."""
    r = 38
    return f'''
    <rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" rx="22"
          fill="url(#whitesur-gradient-{cx})" stroke="#444448" stroke-width="1"/>
    <defs>
        <linearGradient id="whitesur-gradient-{cx}" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="{accent}" stop-opacity="0.95"/>
            <stop offset="100%" stop-color="{accent}" stop-opacity="0.7"/>
        </linearGradient>
    </defs>
    <circle cx="{cx}" cy="{cy-3}" r="14" fill="#ffffff" opacity="0.95"/>
    '''


def icon_fluent(cx, cy, accent, theme):
    """Fluent: dimensional rounded rect with diagonal sheen."""
    r = 38
    return f'''
    <rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" rx="10"
          fill="url(#fluent-gradient-{cx})" stroke="{accent}" stroke-width="1.5"/>
    <defs>
        <linearGradient id="fluent-gradient-{cx}" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="{accent}" stop-opacity="1"/>
            <stop offset="100%" stop-color="{accent}" stop-opacity="0.55"/>
        </linearGradient>
    </defs>
    <rect x="{cx-r+12}" y="{cy-r+12}" width="{2*r-24}" height="{2*r-24}" rx="4"
          fill="#ffffff" opacity="0.18"/>
    '''


def icon_intergenos(cx, cy, accent, theme):
    """InterGenOS: the shipped icon theme's line-art register at mini-scene scale —
    stroke-drawn glyphs with no tile plate (every other family here is a filled
    plate; the system default is line-forward), sized to the icon box per the
    theme's optical-size law, squared functional details, accent-colored strokes.
    The three dock positions carry the default desktop's first three marks
    (settings gear, files folder, terminal) rather than one repeated shape."""
    variant = 0 if cx < W // 2 else (1 if cx == W // 2 else 2)
    if variant == 0:
        # settings gear: stroked ring + 8 square-capped radial teeth + hub dot.
        # 45-degree tooth offsets precomputed (cos45 = 0.70711) — no runtime trig,
        # the fragment stays a pure deterministic literal like its siblings.
        i, o = 19.1, 25.5     # 27 * 0.70711, 36 * 0.70711
        teeth = "".join(
            f'<line x1="{cx + dx1:.1f}" y1="{cy + dy1:.1f}" '
            f'x2="{cx + dx2:.1f}" y2="{cy + dy2:.1f}"/>'
            for (dx1, dy1, dx2, dy2) in [
                (27, 0, 36, 0), (-27, 0, -36, 0), (0, 27, 0, 36), (0, -27, 0, -36),
                (i, i, o, o), (-i, i, -o, o), (i, -i, o, -o), (-i, -i, -o, -o)])
        return f'''
    <g stroke="{accent}" stroke-width="6" fill="none" stroke-linecap="square">
        <circle cx="{cx}" cy="{cy}" r="27" stroke-linecap="round"/>
        {teeth}
    </g>
    <circle cx="{cx}" cy="{cy}" r="7" fill="{accent}"/>
    '''
    if variant == 1:
        # files folder: single clean silhouette with a straight left edge and a
        # squared tab, plus one interior divider stroke — the shipped folder shape.
        return f'''
    <g stroke="{accent}" stroke-width="6" fill="none"
       stroke-linecap="round" stroke-linejoin="round">
        <path d="M {cx-34} {cy+27} L {cx-34} {cy-27} L {cx-8} {cy-27}
                 L {cx-2} {cy-19} L {cx+34} {cy-19} L {cx+34} {cy+27} Z"/>
        <line x1="{cx-34}" y1="{cy+6}" x2="{cx+34}" y2="{cy+6}"/>
    </g>
    '''
    # terminal: outlined frame + prompt chevron + cursor stroke.
    return f'''
    <g stroke="{accent}" stroke-width="6" fill="none"
       stroke-linecap="round" stroke-linejoin="round">
        <rect x="{cx-34}" y="{cy-28}" width="68" height="56" rx="6"/>
        <path d="M {cx-20} {cy-8} L {cx-8} {cy+2} L {cx-20} {cy+12}"/>
        <line x1="{cx+2}" y1="{cy+12}" x2="{cx+18}" y2="{cy+12}"/>
    </g>
    '''


def icon_cybernetic_blue(cx, cy, accent, theme):
    """Cybernetic - Blue: glowing hexagonal HUD-style icon."""
    r = 38
    # Hexagonal path
    import math
    pts = []
    for i in range(6):
        a = math.radians(60 * i - 30)
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    hex_path = " ".join(pts)
    return f'''
    <polygon points="{hex_path}" fill="#050818"
             stroke="{accent}" stroke-width="2"
             filter="url(#cyber-glow)"/>
    <polygon points="{hex_path}" fill="none"
             stroke="{accent}" stroke-width="1" opacity="0.4"
             transform="scale(0.7) translate({cx*0.43:.1f} {cy*0.43:.1f})"/>
    <circle cx="{cx}" cy="{cy}" r="6" fill="{accent}" filter="url(#cyber-glow)"/>
    '''


ICON_FAMILIES = {
    "papirus_dark": icon_papirus_dark,
    "papirus_light": icon_papirus_light,
    "whitesur_dark": icon_whitesur_dark,
    "fluent": icon_fluent,
    "cybernetic_blue": icon_cybernetic_blue,
    "intergenos": icon_intergenos,
}


# ---------- SVG TEMPLATE RENDERING ----------

def render_svg(theme):
    """Generate the full SVG for one theme."""
    icon_fn = ICON_FAMILIES[theme["icon_family"]]

    # Three dock icons, evenly spaced near bottom center
    dock_y = H - 105
    dock_centers = [W // 2 - 130, W // 2, W // 2 + 130]

    # For Dracula, use signature pink/cyan/green for 3 icons
    if theme["name"] == "Dracula":
        icon_accents = [
            theme.get("icon_accent_pink", theme["accent"]),
            theme["accent"],
            theme.get("icon_accent_cyan", theme["accent"]),
        ]
    else:
        icon_accents = [theme["accent"]] * 3

    icons_svg = "\n".join(
        icon_fn(cx, dock_y, icon_accents[i], theme)
        for i, cx in enumerate(dock_centers)
    )

    # Window geometry (centered, 70% width × 65% height)
    win_w = int(W * 0.70)
    win_h = int(H * 0.62)
    win_x = (W - win_w) // 2
    win_y = 70  # below top bar
    sidebar_w = int(win_w * 0.28)

    # Sidebar items (5 horizontal bars representing menu entries)
    sidebar_items = ""
    for i in range(5):
        item_y = win_y + 70 + i * 38
        item_bg = theme["selected_bg"] if i == 1 else "none"
        item_text_fill = theme["selected_text"] if i == 1 else theme["window_text"]
        sidebar_items += f'''
        <rect x="{win_x + 16}" y="{item_y}" width="{sidebar_w - 32}" height="28" rx="6"
              fill="{item_bg}" opacity="{0.95 if i==1 else 0.0}"/>
        <rect x="{win_x + 30}" y="{item_y + 9}" width="{sidebar_w - 80}" height="10" rx="3"
              fill="{item_text_fill}" opacity="0.85"/>
        '''

    # Content area lines (representing text/content placeholders)
    content_x = win_x + sidebar_w
    content_w = win_w - sidebar_w
    content_lines = ""
    for i in range(7):
        line_y = win_y + 80 + i * 36
        line_w = content_w - 60 - (i * 12 % 80)  # vary widths
        content_lines += f'''
        <rect x="{content_x + 30}" y="{line_y}" width="{line_w}" height="10" rx="3"
              fill="{theme["window_text"]}" opacity="{0.6 if i < 3 else 0.35}"/>
        '''

    # Top bar: activities button (left) + clock (center) + status (right)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}">

    <defs>
        <!-- Wallpaper gradient -->
        <linearGradient id="wallpaper" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="{theme['wallpaper_top']}"/>
            <stop offset="100%" stop-color="{theme['wallpaper_bottom']}"/>
        </linearGradient>

        <!-- Window drop shadow -->
        <filter id="winshadow" x="-10%" y="-10%" width="120%" height="125%">
            <feGaussianBlur in="SourceAlpha" stdDeviation="10"/>
            <feOffset dx="0" dy="8" result="offsetblur"/>
            <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
            <feMerge>
                <feMergeNode/>
                <feMergeNode in="SourceGraphic"/>
            </feMerge>
        </filter>

        <!-- Cybernetic icon glow -->
        <filter id="cyber-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4"/>
            <feMerge>
                <feMergeNode/>
                <feMergeNode in="SourceGraphic"/>
            </feMerge>
        </filter>
    </defs>

    <!-- Wallpaper -->
    <rect width="{W}" height="{H}" fill="url(#wallpaper)"/>

    <!-- GNOME top bar -->
    <rect x="0" y="0" width="{W}" height="42" fill="{theme['topbar_bg']}" opacity="0.92"/>
    <!-- Activities button -->
    <rect x="22" y="11" width="100" height="20" rx="4" fill="{theme['topbar_text']}" opacity="0.18"/>
    <rect x="38" y="17" width="68" height="8" rx="2" fill="{theme['topbar_text']}" opacity="0.7"/>
    <!-- Clock -->
    <rect x="{W//2 - 50}" y="11" width="100" height="20" rx="4" fill="{theme['topbar_text']}" opacity="0.15"/>
    <rect x="{W//2 - 35}" y="17" width="70" height="8" rx="2" fill="{theme['topbar_text']}" opacity="0.7"/>
    <!-- Status (right) -->
    <circle cx="{W - 60}" cy="21" r="5" fill="{theme['topbar_text']}" opacity="0.55"/>
    <circle cx="{W - 42}" cy="21" r="5" fill="{theme['topbar_text']}" opacity="0.55"/>
    <circle cx="{W - 24}" cy="21" r="5" fill="{theme['accent']}" opacity="0.9"/>

    <!-- Application window -->
    <g filter="url(#winshadow)">
        <!-- Window background -->
        <rect x="{win_x}" y="{win_y}" width="{win_w}" height="{win_h}" rx="14"
              fill="{theme['window_chrome']}" stroke="{theme['accent']}" stroke-width="0.8" opacity="0.98"/>

        <!-- Title bar -->
        <rect x="{win_x}" y="{win_y}" width="{win_w}" height="48" rx="14"
              fill="{theme['window_chrome']}" opacity="0.94"/>
        <!-- Title bar bottom-edge (mask the rounded corners on the title bar) -->
        <rect x="{win_x}" y="{win_y + 34}" width="{win_w}" height="14"
              fill="{theme['window_chrome']}" opacity="0.94"/>

        <!-- Traffic-light buttons (close/min/max) -->
        <circle cx="{win_x + 22}" cy="{win_y + 24}" r="7" fill="#ff5555"/>
        <circle cx="{win_x + 44}" cy="{win_y + 24}" r="7" fill="#ffaa00"/>
        <circle cx="{win_x + 66}" cy="{win_y + 24}" r="7" fill="#50c878"/>
        <!-- Window title -->
        <rect x="{win_x + 100}" y="{win_y + 19}" width="180" height="10" rx="2"
              fill="{theme['window_text']}" opacity="0.6"/>

        <!-- Sidebar -->
        <rect x="{win_x}" y="{win_y + 48}" width="{sidebar_w}" height="{win_h - 48}"
              fill="{theme['sidebar_bg']}" opacity="0.95"/>
        {sidebar_items}

        <!-- Content area -->
        <rect x="{content_x}" y="{win_y + 48}" width="{content_w}" height="{win_h - 48}"
              fill="{theme['window_content']}" opacity="0.98"/>
        {content_lines}
    </g>

    <!-- Dock backdrop -->
    <rect x="{W//2 - 220}" y="{dock_y - 50}" width="440" height="100" rx="22"
          fill="{theme['topbar_bg']}" opacity="0.75"
          stroke="{theme['accent']}" stroke-width="0.5"/>

    <!-- Dock icons -->
    {icons_svg}

</svg>
'''


# ---------- MAIN ----------

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        svg = render_svg(theme)
        out_path = OUT_DIR / f"{theme['file']}.png"
        cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                         output_width=W, output_height=H,
                         write_to=str(out_path))
        print(f"  -> {out_path}  ({theme['name']})")
    print(f"\n10 thumbnails rendered at {OUT_DIR}")


if __name__ == "__main__":
    main()
