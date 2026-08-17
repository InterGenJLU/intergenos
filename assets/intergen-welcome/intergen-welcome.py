#!/usr/bin/env python3
"""
InterGenOS Welcome — First-boot greeter
GTK4 / libadwaita native application

Flows naturally from the boot animation:
  ECG pulse → "Hello." → "Shall we get started?" → fade → this greeter
"""

import gi
import importlib.util
import subprocess
import threading
import json
import os

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango


# ---------------------------------------------------------------------------
# The shared "why can't this machine reach the download sources" module
# ---------------------------------------------------------------------------

def _load_net_diagnostics():
    """Load net_diagnostics.py — the one place that decides what a failed
    name lookup means and what to tell the user about it.

    This application deliberately does not import the intergen package (it is
    a separate GTK application with its own package), so it cannot say
    ``from intergen.net_diagnostics import ...``. Instead the file itself is
    staged into this package by the source-tarball generator and installed
    beside this script, and it is loaded from there.

    Two locations are searched, and they are the only two that exist:
      1. beside this script — the installed layout,
         /usr/libexec/intergen-welcome/net_diagnostics.py;
      2. <repo>/intergen/net_diagnostics.py — the source tree, where this
         script is two directories below the repository root. That is the
         layout the tests and the local render harness run in.

    Failing to find it raises, loudly, rather than falling back to a private
    copy of the logic. A private copy is exactly the thing this module exists
    to prevent, and on a built system the file cannot be missing: the package
    declares it in verify_paths, so a build that did not ship it halts at the
    pre-squashfs audit rather than reaching a user.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(here, 'net_diagnostics.py'),
        os.path.join(here, os.pardir, os.pardir, 'intergen',
                     'net_diagnostics.py'),
    )
    for path in candidates:
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(
                'intergen_welcome_net_diagnostics', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(
        'net_diagnostics.py was not found beside this script '
        f'({candidates[0]}) or in the source tree ({candidates[1]}). '
        'The intergen-welcome package installs it into '
        '/usr/libexec/intergen-welcome/ and declares it in verify_paths.')


netdiag = _load_net_diagnostics()


# ---------------------------------------------------------------------------
# CSS — background images, gradients, and visual polish
# ---------------------------------------------------------------------------

# Image paths — replace with FLUX-generated art when ready.
# For now, uses gradient backgrounds as placeholders.
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backgrounds')

CUSTOM_CSS = """
/* ============================================================
   InterGenOS Welcomer — visual-language layer
   "Light emerging from dark": void canvas, ECG-blue glow, no tint.
   Vocabulary aligned with the Forge installer (style.py).
   ============================================================ */

window.welcome-window {
    background-color: #050810;
    color: #e2e8f0;
    font-family: 'Inter', 'Cantarell', sans-serif;
}

/* Unified dark canvas with a soft ECG wash from the top — consistent
   across every page (no arbitrary per-page color tints). */
.welcome-bg, .appearance-bg, .extensions-bg, .shortcuts-bg, .community-bg {
    background:
        radial-gradient(120% 75% at 50% -8%,
            rgba(0, 153, 255, 0.10) 0%,
            rgba(0, 153, 255, 0.03) 34%,
            rgba(5, 8, 16, 0.0) 68%),
        #050810;
}
/* Brand-moment pages carry a centered pulse of blue behind the content. */
.welcome-bg, .intergen-bg, .done-bg {
    background:
        radial-gradient(85% 65% at 50% 40%,
            rgba(0, 153, 255, 0.15) 0%,
            rgba(0, 153, 255, 0.045) 42%,
            rgba(5, 8, 16, 0.0) 72%),
        #050810;
}

.welcome-header, headerbar.welcome-header {
    background: transparent; box-shadow: none; border: none;
}

/* ---- Typography (the type scale) ---- */
.welcome-pulse { margin-bottom: 4px; }
.welcome-title { font-size: 30px; font-weight: 700; color: #ffffff; letter-spacing: -0.01em; }
.welcome-subtitle { font-size: 15px; font-weight: 400; color: #b6c3d2; line-height: 1.6; }
.page-title { font-size: 25px; font-weight: 700; color: #ffffff; letter-spacing: -0.01em; margin-bottom: 2px; }
.page-subtitle { font-size: 14px; color: #7a8ba8; line-height: 1.5; margin-bottom: 8px; }

/* ---- Boxed groups (row containers): card tone + glow border ---- */
.transparent-group {
    background-color: #0f1525;
    border: 1px solid rgba(0, 153, 255, 0.18);
    border-radius: 14px;
    padding: 0;
}
.transparent-group row {
    background-color: transparent;
    border-bottom: 1px solid rgba(0, 153, 255, 0.06);
    min-height: 46px;
    padding: 2px 6px;
}
.transparent-group row:last-child { border-bottom: none; }
.transparent-group row:hover { background-color: rgba(0, 153, 255, 0.06); }
.transparent-group row label.title { color: #e2e8f0; font-weight: 600; }
.transparent-group row label.subtitle { color: #7a8ba8; }
/* The card's border is drawn around the whole group, header included, so a
   group title lands on the border itself (measured x=1 in window coordinates)
   while the row titles below it start at x=33. This inset gives the title the
   same start position as those row titles. 32px is the measured row-title
   inset on GTK 4.20.3 / libadwaita 1.8.4, not a chosen value; the labels box
   is targeted so a group description moves with its title. */
.transparent-group box.labels { margin-left: 32px; }
.heading { color: #ffffff; font-weight: 700; }
.dim-label { color: #7a8ba8; }

/* ---- Theme picker ---- */
.theme-row-box { padding: 10px 12px; }
.theme-preview {
    border-radius: 10px; border: 1px solid rgba(0, 153, 255, 0.18);
    min-width: 160px; min-height: 100px; background-color: #0a0e1a;
}
.theme-preview-active { border-color: #0099FF; border-width: 2px; }

/* ---- Prompt page: monospace example chip ---- */
.prompt-example-frame {
    background-color: #050810;
    border: 1px solid rgba(0, 153, 255, 0.22);
    border-radius: 8px;
}
.prompt-example-text {
    font-family: 'JetBrains Mono', 'Noto Sans Mono', monospace;
    font-size: 0.85em; color: #b6c3d2;
}

/* ---- Meet InterGen ---- */
.intergen-name { font-size: 20px; font-weight: 700; color: #ffffff; }
.intergen-example {
    background-color: rgba(0, 153, 255, 0.05);
    border: 1px solid rgba(0, 153, 255, 0.14);
    border-left: 2px solid rgba(0, 153, 255, 0.55);
    border-radius: 8px; padding: 8px 12px; margin: 3px 0;
}
.intergen-example-text { color: #cdd8e6; font-size: 0.92em; }
.intergen-summon {
    background: linear-gradient(135deg, rgba(0,153,255,0.10) 0%, rgba(0,153,255,0.02) 60%, transparent 100%), #0a0e1a;
    border: 1px solid rgba(0, 153, 255, 0.22);
    border-radius: 12px; padding: 11px 20px; margin-top: 8px;
}
.intergen-summon-key { font-size: 13px; font-weight: 700; color: #0099FF; font-family: 'JetBrains Mono', monospace; }
.intergen-summon-text { font-size: 12px; color: #7a8ba8; }
/* Driver advisory — the ONE thing on this page that must not be missed.
   Deliberately loud: amber warning colour, a border, a filled background and a
   font two steps up from body copy. Guidance a user is meant to ACT on cannot
   be set in the same 12px grey as the surrounding prose — nobody reads that,
   and an advisory nobody reads is the same as no advisory (decided 2026-07-31,
   after the first build put this text in .intergen-summon-text and it was
   invisible in practice on the hardware it was written for). */
.intergen-advisory {
    background-color: rgba(255, 193, 7, 0.14);
    border: 2px solid #ffc107;
    border-radius: 12px;
    padding: 16px 22px;
    margin-top: 6px;
    margin-bottom: 2px;
}
.intergen-advisory-title { font-size: 19px; font-weight: 800; color: #ffc107; }
.intergen-advisory-text { font-size: 15px; color: #f6e7bf; }
.intergen-advisory-action { font-size: 15px; font-weight: 700; color: #ffd54f; }
/* Panel-icon preview — exactly what the user should hunt for in the top bar.
   Brand-blue (matches the live panel indicator), seated in a panel-like pill. */
.intergen-icon-preview {
    color: #0099FF;
    background-color: rgba(5, 8, 16, 0.55);
    border: 1px solid rgba(0, 153, 255, 0.22);
    border-radius: 8px;
    padding: 5px;
}

/* ---- Buttons: glow, not tint ---- */
.nav-next { padding: 8px 24px; }
button {
    background-color: rgba(15, 21, 37, 0.5); color: #e2e8f0;
    border: 1px solid rgba(0, 153, 255, 0.08); border-radius: 8px;
    padding: 6px 16px; font-weight: 500;
}
button:hover { border-color: rgba(0, 153, 255, 0.22); background-color: rgba(0, 153, 255, 0.06); color: #ffffff; }
button.suggested-action { background-color: rgba(0, 153, 255, 0.18); border-color: rgba(0, 153, 255, 0.40); color: #ffffff; font-weight: 600; }
button.suggested-action:hover { background-color: rgba(0, 153, 255, 0.30); border-color: rgba(0, 153, 255, 0.65); }

checkbutton check, checkbutton radio { border: 1px solid rgba(0, 153, 255, 0.30); background-color: #0f1525; }
checkbutton:checked check, checkbutton:checked radio { background-color: #0099FF; border-color: #0099FF; }

link, button.link { color: #0099FF; }
link:hover { color: #66bfff; }

/* ---- Community page: branded link cards (G3-14) ---- */
button.community-card {
    background-color: rgba(15, 21, 37, 0.55);
    border: 1px solid rgba(0, 153, 255, 0.12);
    border-radius: 12px;
    padding: 14px 18px;
    transition: all 160ms ease;
}
button.community-card:hover {
    background-color: rgba(0, 153, 255, 0.08);
    border-color: rgba(0, 153, 255, 0.45);
}
button.community-card .community-card-icon { color: #0099FF; }
button.community-card .community-card-heading {
    color: #ffffff; font-weight: 700; font-size: 15px;
}
button.community-card .community-card-desc {
    color: #7a8ba8; font-size: 12.5px;
}
button.community-card .community-card-arrow {
    color: rgba(0, 153, 255, 0.55); font-size: 16px; font-weight: 700;
}
button.community-card:hover .community-card-arrow { color: #66bfff; }
button.community-card:hover .community-card-heading { color: #66bfff; }
.community-creed {
    color: #4a5872; font-size: 12.5px; font-style: italic;
    letter-spacing: 0.02em;
}
"""


def load_css():
    """Load custom CSS into the GTK display."""
    provider = Gtk.CssProvider()
    provider.load_from_string(CUSTOM_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


# ---------------------------------------------------------------------------
# Theme / Extension / Cursor definitions
# ---------------------------------------------------------------------------

THEME_COMBOS = [
    {
        'name': 'InterGenOS',
        'description': 'ECG blue on deep navy — the native InterGenOS look',
        'gtk_theme': 'InterGenOS',
        'shell_theme': 'InterGenOS',
        'icon_theme': 'InterGenOS',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Orchis Dark',
        'description': 'Clean, modern Material Design',
        'gtk_theme': 'adw-gtk3-dark',
        'shell_theme': 'Orchis-Dark',
        'icon_theme': 'Papirus-Dark',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'WhiteSur',
        'description': 'macOS-inspired elegance',
        'gtk_theme': 'WhiteSur-Dark',
        'shell_theme': 'WhiteSur-Dark',
        'icon_theme': 'WhiteSur-dark',
        'cursor_theme': 'macOS',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Catppuccin Mocha',
        'description': 'Warm pastel dark theme',
        'gtk_theme': 'catppuccin-mocha-blue-standard+default',
        'shell_theme': 'Orchis-Dark',
        'icon_theme': 'Papirus-Dark',
        'cursor_theme': 'Bibata-Modern-Ice',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Nordic',
        'description': 'Cool blue-grey Nord palette',
        'gtk_theme': 'Nordic',
        'shell_theme': 'Nordic',
        'icon_theme': 'Papirus-Dark',
        'cursor_theme': 'phinger-cursors-dark',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Graphite',
        'description': 'Minimal flat design',
        'gtk_theme': 'Graphite-Dark',
        'shell_theme': 'Graphite-Dark',
        'icon_theme': 'Tela-dark',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Dracula',
        'description': 'The classic dark color scheme',
        'gtk_theme': 'Dracula',
        'shell_theme': 'Orchis-Dark',
        'icon_theme': 'Papirus-Dark',
        'cursor_theme': 'Bibata-Modern-Amber',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Fluent',
        'description': 'Microsoft Fluent Design language',
        'gtk_theme': 'Fluent-Dark',
        'shell_theme': 'Fluent-Dark',
        'icon_theme': 'Fluent-dark',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-dark',
    },
    {
        'name': 'Orchis Light',
        'description': 'Clean and bright',
        'gtk_theme': 'adw-gtk3',
        'shell_theme': 'Orchis-Light',
        'icon_theme': 'Papirus',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-light',
    },
    {
        'name': 'Cybernetic Blue',
        'description': 'Full cybernetic aesthetic — featured alternate to default',
        'gtk_theme': 'InterGenOS',
        'shell_theme': 'InterGenOS',
        'icon_theme': 'Cybernetic - Blue',
        'cursor_theme': 'Bibata-Modern-Classic',
        'color_scheme': 'prefer-dark',
    },
]

EXTENSION_GROUPS = {
    'Appearance': [
        ('blur-my-shell@aunetx', 'Blur my Shell',
         'Blur effects on panel, overview, and lockscreen'),
        ('burn-my-windows@schneegans.github.com', 'Burn My Windows',
         'Stylish window open/close animations'),
        ('rounded-window-corners@fxgn', 'Rounded Window Corners',
         'Add rounded corners to all windows'),
        ('desktop-cube@schneegans.github.com', 'Desktop Cube',
         '3D cube workspace switching'),
        ('nightthemeswitcher@romainvigier.fr', 'Night Theme Switcher',
         'Auto-switch light/dark by time of day'),
    ],
    'Productivity': [
        ('CoverflowAltTab@palatis.blogspot.com', 'Coverflow Alt-Tab',
         '3D window switcher for Alt-Tab'),
        ('clipboard-indicator@tudmotu.com', 'Clipboard Indicator',
         'Clipboard history with search'),
        ('tilingshell@ferrarodomenico.com', 'Tiling Shell',
         'Windows-style snap and custom layouts'),
        ('forge@jmmaranan.com', 'Forge',
         'Auto-tiling window manager (i3-style)'),
        ('ddterm@amezin.github.com', 'ddterm',
         'Drop-down terminal (Quake-style)'),
        ('AlphabeticalAppGrid@stuarthayhurst', 'Alphabetical App Grid',
         'Sort the app grid alphabetically'),
    ],
    'Layout': [
        ('dash-to-dock@micxgx.gmail.com', 'Dash to Dock',
         'Persistent dock on any screen edge'),
        ('dash-to-panel@jderose9.github.com', 'Dash to Panel',
         'Windows/KDE-style taskbar'),
        ('arcmenu@arcmenu.com', 'ArcMenu',
         'Full app menu with search and layouts'),
        ('show-desktop-button@amivaleo', 'Show Desktop Button',
         'One-click minimize all windows'),
    ],
    'Utilities': [
        ('appindicatorsupport@rgcjonas.gmail.com', 'AppIndicator Support',
         'System tray icons for apps'),
        ('bluetooth-quick-connect@bjarosze.gmail.com', 'Bluetooth Quick Connect',
         'Pair and connect Bluetooth from the panel'),
        ('caffeine@patapon.info', 'Caffeine',
         'Disable auto-suspend with a toggle'),
        ('Vitals@CoreCoding.com', 'Vitals',
         'CPU, memory, temperature in the panel'),
        ('mediacontrols@cliffniff.github.com', 'Media Controls',
         'Now-playing info in the panel'),
        ('gsconnect@andyholmes.github.io', 'GSConnect',
         'Connect your phone — notifications, files, clipboard'),
        ('just-perfection-desktop@just-perfection', 'Just Perfection',
         'Tweak every aspect of the GNOME Shell'),
        ('ding@rastersoft.com', 'Desktop Icons NG',
         'Desktop icons with drag and drop'),
    ],
}

# Extensions enabled by default
DEFAULT_EXTENSIONS = {
    'user-theme@gnome-shell-extensions.gcampax.github.com',
    'appindicatorsupport@rgcjonas.gmail.com',
    'CoverflowAltTab@palatis.blogspot.com',
    'blur-my-shell@aunetx',
    'bluetooth-quick-connect@bjarosze.gmail.com',
    'burn-my-windows@schneegans.github.com',
}


# ---------------------------------------------------------------------------
# Helper: apply a theme combo via gsettings
# ---------------------------------------------------------------------------

def apply_theme(combo):
    """Apply a theme combination to the current session."""
    settings_if = Gio.Settings.new('org.gnome.desktop.interface')
    settings_if.set_string('gtk-theme', combo['gtk_theme'])
    settings_if.set_string('icon-theme', combo['icon_theme'])
    settings_if.set_string('cursor-theme', combo['cursor_theme'])
    settings_if.set_string('color-scheme', combo['color_scheme'])

    try:
        settings_ut = Gio.Settings.new('org.gnome.shell.extensions.user-theme')
        settings_ut.set_string('name', combo['shell_theme'])
    except Exception:
        pass


def get_enabled_extensions():
    """Get the current list of enabled extensions."""
    settings = Gio.Settings.new('org.gnome.shell')
    return set(settings.get_strv('enabled-extensions'))


def set_enabled_extensions(uuids):
    """Set the list of enabled extensions."""
    settings = Gio.Settings.new('org.gnome.shell')
    settings.set_strv('enabled-extensions', list(uuids))


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------

def wrap_with_background(content, css_class, scroll=False):
    """Wrap a content widget in a background overlay container.

    When scroll=True, the content is placed inside a vertically-scrolling
    GtkScrolledWindow so a page whose natural height exceeds the window
    scrolls instead of clipping. Without it, a tall valign=CENTER page
    (the text-heavy Meet InterGen page) is centered past both edges — the
    title is pushed up under the header bar and the final paragraph is cut
    off the bottom. The ScrolledWindow is transparent, so the background
    overlay shows through, identical to the in-page scrolled lists on the
    Appearance and Extensions pages.
    """
    overlay = Gtk.Overlay()
    overlay.set_vexpand(True)
    overlay.set_hexpand(True)

    # Background layer (receives the gradient/image CSS)
    bg = Gtk.Box()
    bg.set_vexpand(True)
    bg.set_hexpand(True)
    bg.add_css_class(css_class)
    overlay.set_child(bg)

    # Content on top — optionally in a vertical scroller for tall pages.
    if scroll:
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(content)
        overlay.add_overlay(scroller)
    else:
        overlay.add_overlay(content)

    return overlay


def build_welcome_page():
    """Page 1: Welcome — brand moment."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)
    box.set_margin_top(40)
    box.set_margin_bottom(48)

    # Brand moment — the ECG pulse hero (the system has a heartbeat).
    # Shipped at /usr/share/intergenos; repo path is the dev/render fallback.
    for _hero in ('/usr/share/intergenos/intergenos_pulse_forge_hero.png',
                  '/mnt/intergenos/assets/intergen-mark/png/intergenos_pulse_forge_hero.png'):
        if os.path.exists(_hero):
            pulse = Gtk.Picture.new_for_filename(_hero)
            pulse.set_content_fit(Gtk.ContentFit.CONTAIN)
            pulse.set_size_request(460, 150)
            pulse.set_halign(Gtk.Align.CENTER)
            pulse.add_css_class('welcome-pulse')
            box.append(pulse)
            break

    title = Gtk.Label(label='Welcome to InterGenOS')
    title.add_css_class('welcome-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='You\'re now running a system built from source — every '
              'package compiled deliberately, every default chosen by '
              'someone who actually uses it.\n\n'
              'The next few steps make it yours: appearance, extensions, '
              'terminal prompt, your local AI assistant.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    subtitle.set_wrap(True)
    subtitle.set_max_width_chars(70)
    box.append(subtitle)

    return wrap_with_background(box, 'welcome-bg', scroll=True)


def build_appearance_page():
    """Page 2: Theme gallery with curated combos and thumbnails."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Choose Your Look')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(label='Pick a style. You can change this anytime in Settings.')
    subtitle.add_css_class('page-subtitle')
    box.append(subtitle)

    # Scrollable theme list
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    list_box.set_margin_top(8)
    scrolled.set_child(list_box)
    box.append(scrolled)

    current_theme = [0]
    preview_images = []
    first_check = None

    # Previews directory — FLUX images go here later
    preview_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'previews')

    for i, combo in enumerate(THEME_COMBOS):
        # Each theme is a horizontal box: [radio + text] [preview thumbnail]
        row_frame = Gtk.Frame()
        row_frame.add_css_class('transparent-group')

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row_box.add_css_class('theme-row-box')
        row_frame.set_child(row_box)

        # Left side: radio + text
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        check = Gtk.CheckButton()
        if i == 0:
            check.set_active(True)
            first_check = check
        else:
            check.set_group(first_check)

        left.append(check)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(label=combo['name'], xalign=0)
        name_label.add_css_class('heading')
        text_box.append(name_label)

        desc_label = Gtk.Label(label=combo['description'], xalign=0)
        desc_label.add_css_class('dim-label')
        desc_label.add_css_class('caption')
        text_box.append(desc_label)

        # Show what's in the combo
        details = f"{combo['icon_theme']}  •  {combo['cursor_theme']}"
        detail_label = Gtk.Label(label=details, xalign=0)
        detail_label.add_css_class('dim-label')
        detail_label.add_css_class('caption')
        text_box.append(detail_label)

        left.append(text_box)
        row_box.append(left)

        # Right side: preview thumbnail
        # Check for preview image: previews/<theme_name>.png
        safe_name = combo['name'].lower().replace(' ', '-')
        preview_path = os.path.join(preview_dir, f'{safe_name}.png')

        preview = Gtk.Box()
        preview.add_css_class('theme-preview')
        preview.set_size_request(160, 100)
        preview.set_valign(Gtk.Align.CENTER)

        if os.path.exists(preview_path):
            picture = Gtk.Picture.new_for_filename(preview_path)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_size_request(160, 100)
            preview.append(picture)
        else:
            # Placeholder — show color swatch based on theme
            placeholder = Gtk.Label(label='Preview')
            placeholder.add_css_class('dim-label')
            placeholder.set_halign(Gtk.Align.CENTER)
            placeholder.set_valign(Gtk.Align.CENTER)
            preview.append(placeholder)
            preview.set_halign(Gtk.Align.CENTER)

        preview_images.append(preview)
        row_box.append(preview)

        def on_toggled(button, idx=i):
            if button.get_active():
                current_theme[0] = idx
                apply_theme(THEME_COMBOS[idx])
                # Update preview borders
                for j, p in enumerate(preview_images):
                    if j == idx:
                        p.add_css_class('theme-preview-active')
                    else:
                        p.remove_css_class('theme-preview-active')

        check.connect('toggled', on_toggled)

        # Make entire row clickable
        gesture = Gtk.GestureClick()
        def on_row_click(g, n, x, y, chk=check):
            chk.set_active(True)
        gesture.connect('pressed', on_row_click)
        row_frame.add_controller(gesture)

        list_box.append(row_frame)

    # Set initial active border
    if preview_images:
        preview_images[0].add_css_class('theme-preview-active')

    return wrap_with_background(box, 'appearance-bg')


# ── Layout choice (Windows-style taskbar vs GNOME top bar + dock) ──────────
# Greenlit 2026-06-10 (G3-20): the page AFTER the Theme page. Windows-style is
# the default (more adoptable for newcomers, per the Zorin/Mint/Plasma prior
# art). The choice APPLIES live by toggling the layout extensions. The dock's
# *look* ships as a dconf system db (intergenos-default-settings) so it stays
# authoritative + updatable; this page only flips which layout extensions are
# enabled — it does not write per-user dconf overrides.
LAYOUT_DTP = 'dash-to-panel@jderose9.github.com'
LAYOUT_ARCMENU = 'arcmenu@arcmenu.com'
LAYOUT_DTD = 'dash-to-dock@micxgx.gmail.com'

LAYOUT_CHOICES = [
    {
        'id': 'windows',
        'name': 'Windows-style taskbar',
        'description': 'A familiar bottom taskbar with a start button — the '
                       'easiest move from Windows.',
        'detail': 'Bottom bar  •  start menu  •  system tray',
    },
    {
        'id': 'gnome',
        'name': 'GNOME top bar + dock',
        'description': 'The classic GNOME layout: a top bar with Activities and '
                       'a dock for your apps.',
        'detail': 'Top bar  •  app dock  •  Activities overview',
    },
]


def apply_layout(choice_id):
    """Apply a desktop layout live by toggling the layout extensions.

    Order is preserved and intergen-no-overview stays first (its load order is
    load-bearing for the no-overview-flash race — see the 91 gschema override).
    """
    settings = Gio.Settings.new('org.gnome.shell')
    enabled = list(settings.get_strv('enabled-extensions'))

    def ensure(uuid, on):
        if on and uuid not in enabled:
            enabled.append(uuid)
        elif not on and uuid in enabled:
            enabled.remove(uuid)

    if choice_id == 'windows':
        ensure(LAYOUT_DTP, True)
        ensure(LAYOUT_ARCMENU, True)
        ensure(LAYOUT_DTD, False)
    else:  # gnome — classic top bar + a persistent dock (dash-to-dock)
        ensure(LAYOUT_DTP, False)
        ensure(LAYOUT_ARCMENU, False)
        ensure(LAYOUT_DTD, True)

    settings.set_strv('enabled-extensions', enabled)


def build_layout_page():
    """Layout page (after Appearance): Windows-style taskbar vs GNOME top bar."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Choose Your Layout')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='How should your desktop be arranged? You can change this '
              'anytime in Settings.')
    subtitle.add_css_class('page-subtitle')
    box.append(subtitle)

    list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    list_box.set_margin_top(8)
    list_box.set_vexpand(True)
    box.append(list_box)

    preview_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'previews')
    preview_widgets = []
    first_check = None

    for i, choice in enumerate(LAYOUT_CHOICES):
        row_frame = Gtk.Frame()
        row_frame.add_css_class('transparent-group')
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row_box.add_css_class('theme-row-box')
        row_frame.set_child(row_box)

        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        check = Gtk.CheckButton()
        if i == 0:
            check.set_active(True)
            first_check = check
        else:
            check.set_group(first_check)
        left.append(check)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(label=choice['name'], xalign=0)
        name_label.add_css_class('heading')
        text_box.append(name_label)
        desc_label = Gtk.Label(label=choice['description'], xalign=0)
        desc_label.add_css_class('dim-label')
        desc_label.add_css_class('caption')
        desc_label.set_wrap(True)
        desc_label.set_max_width_chars(42)
        text_box.append(desc_label)
        detail_label = Gtk.Label(label=choice['detail'], xalign=0)
        detail_label.add_css_class('dim-label')
        detail_label.add_css_class('caption')
        text_box.append(detail_label)
        left.append(text_box)
        row_box.append(left)

        preview = Gtk.Box()
        preview.add_css_class('theme-preview')
        preview.set_size_request(200, 125)
        preview.set_valign(Gtk.Align.CENTER)
        preview_path = os.path.join(preview_dir, f"layout-{choice['id']}.png")
        if os.path.exists(preview_path):
            picture = Gtk.Picture.new_for_filename(preview_path)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_size_request(200, 125)
            preview.append(picture)
        else:
            placeholder = Gtk.Label(label='Preview')
            placeholder.add_css_class('dim-label')
            placeholder.set_halign(Gtk.Align.CENTER)
            placeholder.set_valign(Gtk.Align.CENTER)
            preview.append(placeholder)
        preview_widgets.append(preview)
        row_box.append(preview)

        def on_toggled(button, cid=choice['id'], idx=i):
            if button.get_active():
                apply_layout(cid)
                for j, p in enumerate(preview_widgets):
                    if j == idx:
                        p.add_css_class('theme-preview-active')
                    else:
                        p.remove_css_class('theme-preview-active')
        check.connect('toggled', on_toggled)

        gesture = Gtk.GestureClick()
        def on_row_click(g, n, x, y, chk=check):
            chk.set_active(True)
        gesture.connect('pressed', on_row_click)
        row_frame.add_controller(gesture)

        list_box.append(row_frame)

    if preview_widgets:
        preview_widgets[0].add_css_class('theme-preview-active')

    return wrap_with_background(box, 'appearance-bg')


def build_extensions_page():
    """Page 3: Extension picker by category."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Extensions')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='These are pre-installed and ready to go. Toggle what you want.'
    )
    subtitle.add_css_class('page-subtitle')
    box.append(subtitle)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    scrolled.set_child(inner)
    box.append(scrolled)

    enabled = get_enabled_extensions()
    switches = {}

    for category, extensions in EXTENSION_GROUPS.items():
        group = Adw.PreferencesGroup()
        group.set_title(category)
        inner.append(group)

        for uuid, name, description in extensions:
            row = Adw.SwitchRow()
            row.set_title(name)
            row.set_subtitle(description)
            row.set_active(uuid in enabled)
            switches[uuid] = row
            group.add(row)

    # Extensions this page shows a toggle for. Everything else that is
    # currently enabled (the InterGenOS infrastructure extensions — the
    # intergen AI panel, intergen-no-overview, intergen-firstboot,
    # pkm-notifier, appindicator — none of which appear on this page) must be
    # PRESERVED, not wiped by replacing the whole enabled-extensions list with
    # only this page's picks.
    managed = {uuid for _cat, _exts in EXTENSION_GROUPS.items()
               for (uuid, _n, _d) in _exts}

    def collect_extensions():
        # Base = every currently-enabled extension this page does not manage,
        # so a user's theme/appearance choices here never disable the AI panel
        # or the other infra extensions enabled elsewhere.
        result = {e for e in get_enabled_extensions() if e not in managed}
        result.add('user-theme@gnome-shell-extensions.gcampax.github.com')
        for uuid, row in switches.items():
            if row.get_active():
                result.add(uuid)
        return result

    box._collect_extensions = collect_extensions
    wrapped = wrap_with_background(box, 'extensions-bg')
    wrapped._collect_extensions = collect_extensions
    return wrapped


PROMPT_OPTIONS = [
    {
        'id': 'stock',
        'name': 'Stock',
        'description': 'Clean classic bash prompt — minimal, universal, '
                       'no extra processing on each line.',
        'example': 'user@intergenos:~/projects$ ',
    },
    {
        'id': 'starship',
        'name': 'Starship',
        'description': 'Minimal, fast, contextual — shows git branch, '
                       'language version, exit code, command duration.',
        'example': '~/projects on  main [!?] via  v22 ❯ ',
    },
]


def apply_prompt(prompt_id):
    """Append or strip the starship init block in ~/.bashrc. Idempotent."""
    import pathlib
    bashrc = pathlib.Path.home() / '.bashrc'
    starship_line = 'eval "$(starship init bash)"'
    marker_start = '# >>> intergen-welcome: starship >>>'
    marker_end = '# <<< intergen-welcome: starship <<<'

    content = bashrc.read_text() if bashrc.exists() else ''

    # Strip any existing intergen-welcome starship block (idempotent both ways).
    in_block = False
    new_lines = []
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped == marker_start:
            in_block = True
            continue
        if stripped == marker_end:
            in_block = False
            continue
        if not in_block:
            new_lines.append(line)
    content = '\n'.join(new_lines).rstrip('\n')

    # Append the starship block iff the user chose starship.
    if prompt_id == 'starship':
        content += (f'\n\n{marker_start}\n'
                    f'{starship_line}\n'
                    f'{marker_end}\n')
    else:
        content += '\n'

    bashrc.write_text(content)


def build_prompt_page():
    """Page 5: Choose terminal prompt — Stock or Starship.

    Aligns with the D-014 (2026-05-20) ratification: starship ships
    ISO-resident specifically because the first-login welcomer presents
    this toggle.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Choose Your Prompt')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='Pick your terminal prompt style. You can change this '
              'anytime by re-running this welcomer or editing ~/.bashrc.'
    )
    subtitle.add_css_class('page-subtitle')
    box.append(subtitle)

    # Two option cards
    cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    cards_box.set_margin_top(8)

    first_check = None
    for i, opt in enumerate(PROMPT_OPTIONS):
        row_frame = Gtk.Frame()
        row_frame.add_css_class('transparent-group')

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row_box.add_css_class('theme-row-box')
        row_frame.set_child(row_box)

        # Left: radio + name + description
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        check = Gtk.CheckButton()
        if i == 0:
            check.set_active(True)
            first_check = check
        else:
            check.set_group(first_check)
        left.append(check)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_label = Gtk.Label(label=opt['name'], xalign=0)
        name_label.add_css_class('heading')
        text_box.append(name_label)

        desc_label = Gtk.Label(label=opt['description'], xalign=0)
        desc_label.add_css_class('dim-label')
        desc_label.add_css_class('caption')
        desc_label.set_wrap(True)
        desc_label.set_max_width_chars(60)
        text_box.append(desc_label)
        left.append(text_box)
        row_box.append(left)

        # Right: monospace example
        example_frame = Gtk.Frame()
        example_frame.add_css_class('prompt-example-frame')
        example_label = Gtk.Label(label=opt['example'], xalign=0)
        example_label.add_css_class('prompt-example-text')
        example_label.set_wrap(True)
        example_label.set_max_width_chars(24)
        example_label.set_margin_top(10)
        example_label.set_margin_bottom(10)
        example_label.set_margin_start(14)
        example_label.set_margin_end(14)
        example_frame.set_child(example_label)
        example_frame.set_hexpand(False)
        example_frame.set_halign(Gtk.Align.END)
        example_frame.set_valign(Gtk.Align.CENTER)
        row_box.append(example_frame)

        def on_toggled(button, prompt_id=opt['id']):
            if button.get_active():
                apply_prompt(prompt_id)
        check.connect('toggled', on_toggled)

        # Make entire row clickable.
        gesture = Gtk.GestureClick()
        def on_row_click(g, n, x, y, chk=check):
            chk.set_active(True)
        gesture.connect('pressed', on_row_click)
        row_frame.add_controller(gesture)

        cards_box.append(row_frame)

    box.append(cards_box)

    # Footer hint: prompt activates in new shells.
    hint = Gtk.Label(
        label='Open a new terminal after selecting to see your prompt.'
    )
    hint.add_css_class('dim-label')
    hint.add_css_class('caption')
    hint.set_margin_top(16)
    box.append(hint)

    return wrap_with_background(box, 'shortcuts-bg', scroll=True)


# ── The privileged helper ───────────────────────────────────────────────────
# One helper carries every privileged action this application can take: the
# opt-in service toggles below (the services InterGenOS ships OFF by default,
# security-first) and the name-server selection on the Finding Websites page.
# Each call goes through pkexec — polkit prompts for the admin password (the
# advisory above the toggles says so) — the helper does the whole action, and
# every action is reversible by its counterpart.
PRIVHELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'intergen-welcome-privhelper')

# toggle key -> the systemd unit whose enabled-state the switch reflects
_SERVICE_UNITS = {
    'printing':  'cups.socket',
    'discovery': 'avahi-daemon.service',
    'ssh':       'sshd.service',
}


def _service_enabled(unit):
    """True if the unit is enabled (persists across boot). Read-only — needs no
    privilege, so the page reflects live state without a password prompt."""
    try:
        r = subprocess.run(['systemctl', 'is-enabled', unit],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and r.stdout.strip() in ('enabled',
                                                           'enabled-runtime')
    except Exception:
        return False


def _apply_service(key, want_on):
    """Run the privileged helper via pkexec. pkexec authenticates the caller as
    an administrator (password prompt); a cancelled prompt or a helper error
    yields a non-zero exit -> False, and the caller reverts the row."""
    verb = ('enable-' if want_on else 'disable-') + key
    try:
        r = subprocess.run(['pkexec', PRIVHELPER, verb], timeout=180)
        return r.returncode == 0
    except Exception:
        return False


def build_services_page():
    """Enable Services: opt-in privileged toggles (print / discovery / SSH)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Enable Services')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='These ship OFF for security. Turn on what you need — each is '
              'set up for you and can be turned back off any time.')
    subtitle.add_css_class('page-subtitle')
    subtitle.set_wrap(True)
    subtitle.set_justify(Gtk.Justification.CENTER)
    # Cap the width + center the label so it wraps into a tidy column instead of
    # running edge-to-edge and stranding a single word on line 2.
    subtitle.set_max_width_chars(48)
    subtitle.set_halign(Gtk.Align.CENTER)
    box.append(subtitle)

    # Required advisory, directly above the toggles.
    advisory = Gtk.Label(
        label='You will be prompted to provide your root password if you '
              'choose to enable the services with the toggles below.')
    advisory.add_css_class('page-subtitle')
    advisory.set_wrap(True)
    advisory.set_justify(Gtk.Justification.CENTER)
    advisory.set_max_width_chars(48)
    advisory.set_halign(Gtk.Align.CENTER)
    advisory.set_margin_top(4)
    box.append(advisory)

    group = Adw.PreferencesGroup()
    group.set_margin_top(12)
    box.append(group)

    rows = [
        ('printing', 'Enable Print Services',
         'Start the CUPS print service and let you add and manage printers in '
         'Settings. Needed for USB and network printers.'),
        ('discovery', 'Enable Network Discovery',
         'Find printers, media servers, and other devices on your local '
         'network (mDNS / Avahi). Opens udp/5353 on the LAN.'),
        ('ssh', 'Enable SSH Server',
         'Allow incoming SSH connections to this machine (key-only). Opens '
         'tcp/22.'),
    ]

    # Guard so the revert-on-failure set_active() does not re-fire the handler.
    guard = {'busy': False}

    for key, name, desc in rows:
        row = Adw.SwitchRow()
        row.set_title(name)
        row.set_subtitle(desc)
        row.set_active(_service_enabled(_SERVICE_UNITS[key]))

        def on_active(sw, _pspec, k=key):
            if guard['busy']:
                return
            want = sw.get_active()
            # Run pkexec off the UI thread so an ignored auth dialog can't
            # freeze the Welcomer; disable the row meanwhile.
            sw.set_sensitive(False)

            def worker():
                ok = _apply_service(k, want)

                def finish():
                    sw.set_sensitive(True)
                    if not ok:
                        guard['busy'] = True
                        sw.set_active(not want)
                        guard['busy'] = False
                    return False

                GLib.idle_add(finish)

            threading.Thread(target=worker, daemon=True).start()

        row.connect('notify::active', on_active)
        group.add(row)

    return wrap_with_background(box, 'extensions-bg', scroll=True)


# ── Finding Websites (name-server) page ─────────────────────────────────────
# Why this page exists: a network whose name server does not answer leaves a
# machine that is connected to everything and can find nothing. Package
# updates fail, InterGen's model never downloads, and every message the system
# produced before this page said "you are offline — connect to WiFi", which is
# advice a user on a working network cannot act on. The page shows which name
# server is in use, changes nothing unless the user chooses to, and is put in
# front of the user by itself when name lookups are what failed.
#
# Everything it reads is read-only and unprivileged (resolvectl reports state
# to any user). Everything it writes goes through the same privileged helper
# the service toggles use, and is read back out of systemd-resolved afterwards
# before anything is described as applied.

# The file the helper writes. Its presence is how this page knows the current
# name servers were chosen here rather than handed out by the network.
_RESOLVER_DROPIN = '/etc/systemd/resolved.conf.d/50-intergen-welcome-dns.conf'

# The addresses the helper writes for each named provider. Kept here as well
# so the page can recognise them in what systemd-resolved reports back, which
# is what turns "we ran the helper" into "the machine is actually using this".
_PROVIDER_ADDRESSES = {
    'cloudflare': ('1.1.1.1', '1.0.0.1',
                   '2606:4700:4700::1111', '2606:4700:4700::1001'),
    'quad9': ('9.9.9.9', '149.112.112.112',
              '2620:fe::fe', '2620:fe::9'),
}


# Installed by the application once its navigation exists. Any part of the
# interface that discovers name lookups are what failed calls
# _request_dns_page(cause) to have the Finding Websites page put in front of
# the user with that cause named on it. It is a module-level hook because
# every page is built before the navigation that would carry it, so a page
# cannot hold a reference to something that does not exist yet.
_surface_dns_page = None


def _request_dns_page(cause):
    """Ask for the Finding Websites page to be shown, naming ``cause``.

    Safe to call before the application has installed its hook (nothing
    happens) and safe to call from a worker thread only via GLib.idle_add —
    it touches widgets.
    """
    if _surface_dns_page is not None:
        _surface_dns_page(cause)
    return False


def _resolver_state(run=None, dropin_exists=None):
    """What systemd-resolved is doing right now, as a plain dictionary.

    Reads ``resolvectl status --json=short``, which any user may run — showing
    the current name server needs no privilege, so the page reflects live
    state without a password prompt (the same rule the service toggles follow
    with ``systemctl is-enabled``).

    Returns::

        {'read': bool,          # was the state actually read
         'error': str | None,   # why not, when read is False
         'managed': bool,       # the drop-in this page writes is present
         'entries': [ {...} ]}  # one per scope, global first

    Each entry::

        {'scope': 'global' | 'link',
         'ifname': str | None,
         'default_route': bool,
         'servers': [{'address': str, 'name': str | None}],
         'current': str | None,
         'over_tls': str}       # 'yes' | 'no' | 'opportunistic'

    ``read`` False is reported to the user as "this could not be read", never
    smoothed into a guess: a page about what the machine is doing is worthless
    if it invents the answer when it cannot find out.

    ``run`` is the subprocess runner and ``dropin_exists`` the test for this
    page's own configuration file — both injectable, so tests can present any
    resolver state without needing a machine configured that way and without
    writing into the machine's own configuration.
    """
    if run is None:
        run = subprocess.run
    if dropin_exists is None:
        def dropin_exists():  # noqa: E306 — local default
            return os.path.exists(_RESOLVER_DROPIN)
    try:
        proc = run(['resolvectl', 'status', '--json=short'],
                   capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {'read': False, 'error': str(exc), 'managed': False,
                'entries': []}
    if proc.returncode != 0:
        return {'read': False,
                'error': (proc.stderr or '').strip() or
                         f'resolvectl exited {proc.returncode}',
                'managed': False, 'entries': []}
    try:
        raw = json.loads(proc.stdout)
    except Exception as exc:
        return {'read': False, 'error': f'unreadable resolvectl output: {exc}',
                'managed': False, 'entries': []}
    if not isinstance(raw, list):
        return {'read': False, 'error': 'unexpected resolvectl output shape',
                'managed': False, 'entries': []}

    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        servers = []
        for server in item.get('servers') or ():
            if not isinstance(server, dict):
                continue
            address = server.get('addressString')
            if address:
                servers.append({'address': address, 'name': server.get('name')})
        current = (item.get('currentServer') or {}).get('addressString') \
            if isinstance(item.get('currentServer'), dict) else None
        entries.append({
            'scope': 'link' if item.get('ifindex') is not None else 'global',
            'ifname': item.get('ifname'),
            'default_route': bool(item.get('defaultRoute')),
            'servers': servers,
            'current': current,
            'over_tls': str(item.get('dnsOverTLS') or 'no'),
        })

    return {'read': True, 'error': None,
            'managed': bool(dropin_exists()),
            'entries': entries}


def _effective_resolver(state):
    """Which name servers this machine actually uses for internet names.

    systemd-resolved keeps servers per network interface and, separately, a
    global set — and the ORDER matters. A network interface that was handed
    servers uses those; the global set is consulted when no interface has any.
    The one thing that overrides that is the routing rule this page writes
    alongside its servers, which sends every name to the global set. So:

      1. this page's own file is present -> the global servers, because the
         file that put them there also carries the rule that makes them win;
      2. otherwise the interface carrying the default route, then any other
         interface with servers;
      3. otherwise the global set.

    Getting that order the other way round would have this panel name servers
    the machine is not actually using, which is worse than saying nothing.

    ``also_global`` carries a global set that exists but is NOT the answer, so
    the panel can mention it rather than pretend it is not there.

    Known boundary, stated rather than papered over: routing domains are not
    read here. systemd-resolved sends a query to every scope whose routing
    domain matches it best, so a network interface that independently carries
    the "all names" routing domain would be queried alongside the servers this
    page wrote. Nothing InterGenOS installs configures that, and the panel does
    not claim exclusivity — it names the servers in use, which stays true — but
    a reader extending this function should know the difference.

    Returns ``{'known': False, ...}`` when there is nothing to report, rather
    than an empty list that reads like "no name server configured".
    """
    if not state.get('read'):
        return {'known': False, 'origin': 'unreadable', 'servers': [],
                'over_tls': 'no', 'ifname': None, 'also_global': []}

    entries = state.get('entries') or []
    global_entry = next((e for e in entries if e['scope'] == 'global'), None)
    global_servers = list(global_entry['servers']) if global_entry else []

    if state.get('managed') and global_servers:
        return {'known': True, 'origin': 'chosen-here',
                'servers': global_servers,
                'over_tls': global_entry['over_tls'], 'ifname': None,
                'also_global': []}

    link = next((e for e in entries
                 if e['scope'] == 'link' and e['default_route'] and e['servers']),
                None)
    if link is None:
        link = next((e for e in entries
                     if e['scope'] == 'link' and e['servers']), None)
    if link is not None:
        return {'known': True, 'origin': 'network',
                'servers': list(link['servers']),
                'over_tls': link['over_tls'], 'ifname': link['ifname'],
                'also_global': global_servers}

    if global_servers:
        return {'known': True, 'origin': 'system-wide',
                'servers': global_servers,
                'over_tls': global_entry['over_tls'], 'ifname': None,
                'also_global': []}

    return {'known': False, 'origin': 'none', 'servers': [],
            'over_tls': 'no', 'ifname': None, 'also_global': []}


def _selection_from_state(state):
    """Which of this page's four choices matches the machine's current state.

    Returns ``(selection, addresses, encrypted)`` where selection is one of
    ``'network'``, ``'cloudflare'``, ``'quad9'``, ``'custom'`` or
    ``'unknown'``. This is what preselects the radio button, so the page opens
    describing the machine rather than proposing a change to it, and it is
    what the read-back after an apply is compared against.

    A provider is recognised when every address in use belongs to that
    provider's set — not when the sets are equal — because a machine with no
    IPv6 route legitimately shows only the provider's IPv4 addresses.
    """
    effective = _effective_resolver(state)
    if not effective['known']:
        return ('unknown', [], False)

    addresses = [s['address'] for s in effective['servers']]
    encrypted = effective['over_tls'] == 'yes'

    if effective['origin'] not in ('chosen-here', 'system-wide'):
        return ('network', addresses, encrypted)

    in_use = set(addresses)
    for provider, known in _PROVIDER_ADDRESSES.items():
        if in_use and in_use <= set(known):
            return (provider, addresses, encrypted)
    return ('custom', addresses, encrypted)


def _address_is_valid(address, run=None):
    """True if the privileged helper would accept ``address``.

    Runs the helper's ``check-address`` verb, which validates and does nothing
    else — no privilege, no write, no side effect. Using the helper rather
    than a second parser here is the point: the check the user is shown and
    the check that gates the privileged write are the same code, so they
    cannot drift apart and let the interface promise something the boundary
    then refuses.
    """
    if run is None:
        run = subprocess.run
    try:
        proc = run([PRIVHELPER, 'check-address', address],
                   capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return proc.returncode == 0


def _apply_resolver(selection, addresses=(), encrypted=True, run=None):
    """Ask the privileged helper to make ``selection`` the machine's choice.

    Returns ``(ok, message)``. ``ok`` means only that the helper exited zero;
    whether the machine actually ended up in that state is a separate question
    the caller answers by reading systemd-resolved back afterwards. The two
    are kept apart deliberately — "the command succeeded" and "the machine
    changed" are different claims and only the second one is worth showing.

    Runs off the GTK main thread by its callers, like the service toggles, so
    an authentication dialog the user ignores cannot freeze the interface.
    """
    if run is None:
        run = subprocess.run
    if selection == 'network':
        argv = ['pkexec', PRIVHELPER, 'dns-use-network-default']
    elif selection in ('cloudflare', 'quad9'):
        argv = ['pkexec', PRIVHELPER, f'dns-use-{selection}']
    elif selection == 'custom':
        mode = 'encrypted' if encrypted else 'cleartext'
        argv = ['pkexec', PRIVHELPER, 'dns-use-custom', mode] + list(addresses)
    else:
        return (False, f'unknown selection {selection!r}')
    try:
        proc = run(argv, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return (False, str(exc))
    if proc.returncode == 0:
        return (True, '')
    detail = (proc.stderr or '').strip()
    if proc.returncode == 126 or proc.returncode == 127:
        # pkexec's own codes for "the user cancelled" / "authorisation was
        # not given". Saying so is more use than repeating an exit number.
        return (False, 'Nothing was changed — the password prompt was '
                       'dismissed or the authorisation was refused.')
    return (False, detail or f'the helper exited {proc.returncode}')


# What each choice does, and — the part that matters — what it does not do.
# Every entry says who can see the lookups afterwards, because a name server
# is a thing that watches you type; a page that offered "private" resolvers
# without saying that would be selling the feeling of privacy rather than the
# fact of it.
_DNS_CHOICES = (
    ('network', 'Use what this network provides',
     'The name servers your router or internet provider hands out. This is '
     'what the machine uses unless you change it, and choosing it here undoes '
     'any choice made on this page. Whether these lookups are encrypted is up '
     'to your network — the panel above says whether they are right now. When '
     'they are not, everyone between this machine and that server can see '
     'every name you look up, and can change the answers.'),
    ('cloudflare', 'Cloudflare — 1.1.1.1, encrypted',
     'Lookups go to Cloudflare inside an encrypted connection, so the network '
     'you are sitting on can no longer read them or alter the answers. '
     'Cloudflare itself can see every name you look up; their published '
     'policy says they do not retain it. Encryption is part of this choice '
     'and cannot be switched off: a privacy-branded name server queried in '
     'plain text gives you the appearance of protection and none of it.'),
    ('quad9', 'Quad9 — 9.9.9.9, encrypted',
     'The same encrypted arrangement, run by a Swiss non-profit foundation, '
     'which additionally declines to answer for names it knows to be used by '
     'malicious software. Quad9 itself can see every name you look up. '
     'Encryption is part of this choice and cannot be switched off, for the '
     'same reason.'),
    ('custom', 'A name server I choose',
     'For a name server you run yourself, or one your workplace or school '
     'gave you. It has to be an address rather than a name — this machine '
     'cannot look up a name until it has a name server that works.'),
)

_ENCRYPT_CUSTOM_TITLE = 'Require encryption for this name server'

_ENCRYPT_CUSTOM_DETAIL = (
    'Lookups are encrypted, and they FAIL rather than quietly falling back to '
    'plain text if this server does not offer encryption with a certificate '
    'matching the address you typed. If that happens, come back here and '
    'choose "Use what this network provides". Left off, the lookups are sent '
    'in plain text and anyone on the path can read them and change the '
    'answers.'
)


def _describe_current(effective):
    """The three plain sentences the "Right now" panel shows.

    Returns ``(servers_text, origin_text, encryption_text)``. Each is written
    to be true even when nothing could be read, because "could not be read" is
    itself a fact the user is entitled to.
    """
    if not effective['known']:
        if effective['origin'] == 'unreadable':
            return ('Could not be read',
                    'The system\'s resolver service did not answer, so this '
                    'panel is not describing your machine.',
                    'Could not be read')
        return ('None configured',
                'This machine has not been given a name server, which is why '
                'nothing with a name can be reached.',
                'Not applicable')

    addresses = [s['address'] for s in effective['servers']]
    servers_text = ', '.join(addresses) if addresses else 'None configured'

    origin_text = {
        'chosen-here': 'Chosen on this page. Selecting "Use what this network '
                       'provides" below puts it back the way your network had '
                       'it.',
        'system-wide': 'Set on this machine itself rather than handed out by '
                       'a network, and no network connection supplied one of '
                       'its own.',
        'network': ('Handed out by the network on ' + (effective['ifname'] or
                    'this machine\'s connection') + '.'),
    }.get(effective['origin'], 'Origin unknown.')

    # A machine-wide setting that exists but is not what gets used is worth a
    # sentence: leaving it out would make the panel look like the whole story
    # when it is not.
    also_global = effective.get('also_global') or []
    if also_global:
        origin_text += (' This machine also carries a machine-wide setting ('
                        + ', '.join(s['address'] for s in also_global)
                        + '), which is not what these lookups use.')

    if effective['over_tls'] == 'yes':
        encryption_text = ('Yes — encrypted (DNS over TLS). The network you '
                           'are on cannot read which names you look up or '
                           'change the answers.')
    elif effective['over_tls'] == 'opportunistic':
        encryption_text = ('Only when the server offers it. Because it falls '
                           'back to plain text without telling you, treat '
                           'these lookups as readable.')
    else:
        encryption_text = ('No — sent in plain text. Anyone between this '
                           'machine and the name server can see every name '
                           'you look up, and can change the answers that come '
                           'back.')

    return (servers_text, origin_text, encryption_text)


def build_dns_page():
    """Finding Websites: show the name server in use, and let the user change
    it — with the encryption question answered honestly for every option.

    The returned widget carries ``_show_resolution_cause(cause)``: the
    application calls it when its startup probe finds that name lookups are
    what failed, which reveals a banner naming the cause. Building the banner
    hidden and revealing it later is required because every page is built
    before the window is shown, long before any probe can have finished.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label='Finding Websites')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='Before this machine can open a website, install an update, or '
              'download InterGen\'s model, it has to turn a name such as '
              'repo.intergenos.org into an address. A name server does that. '
              'This page shows which one you are using, and lets you choose a '
              'different one.')
    subtitle.add_css_class('page-subtitle')
    subtitle.set_wrap(True)
    subtitle.set_justify(Gtk.Justification.CENTER)
    subtitle.set_max_width_chars(64)
    subtitle.set_halign(Gtk.Align.CENTER)
    box.append(subtitle)

    # The cause banner — hidden until the startup probe says name lookups are
    # what failed. Same loud treatment as the driver advisory, for the same
    # reason: this is the one thing on the page a user must not miss.
    cause_banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    cause_banner.add_css_class('intergen-advisory')
    cause_banner.set_halign(Gtk.Align.CENTER)
    cause_banner.set_visible(False)
    cause_headline = Gtk.Label()
    cause_headline.add_css_class('intergen-advisory-title')
    cause_headline.set_wrap(True)
    cause_headline.set_max_width_chars(56)
    cause_headline.set_justify(Gtk.Justification.CENTER)
    cause_banner.append(cause_headline)
    cause_detail = Gtk.Label()
    cause_detail.add_css_class('intergen-advisory-text')
    cause_detail.set_wrap(True)
    cause_detail.set_max_width_chars(80)
    cause_detail.set_xalign(0)
    cause_banner.append(cause_detail)
    box.append(cause_banner)

    # ---- Right now ----
    current_group = Adw.PreferencesGroup()
    current_group.set_title('Right now')
    current_group.add_css_class('transparent-group')
    current_group.set_margin_top(8)

    row_servers = Adw.ActionRow()
    row_servers.set_title('Name server in use')
    row_servers.set_subtitle_lines(0)
    row_origin = Adw.ActionRow()
    row_origin.set_title('Where it came from')
    row_origin.set_subtitle_lines(0)
    row_encrypted = Adw.ActionRow()
    row_encrypted.set_title('Are these lookups encrypted?')
    row_encrypted.set_subtitle_lines(0)
    for row in (row_servers, row_origin, row_encrypted):
        current_group.add(row)
    box.append(current_group)

    # ---- Choose ----
    choice_group = Adw.PreferencesGroup()
    choice_group.set_title('Choose a name server')
    choice_group.set_description(
        'Nothing changes until you press Apply, and the choice that matches '
        'your machine right now is the one already selected.')
    choice_group.set_margin_top(12)

    radios = {}
    first_radio = None
    for choice_id, label, detail in _DNS_CHOICES:
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        holder.set_margin_top(6)
        holder.set_margin_bottom(6)
        radio = Gtk.CheckButton(label=label)
        if first_radio is None:
            first_radio = radio
        else:
            radio.set_group(first_radio)
        radios[choice_id] = radio
        holder.append(radio)
        note = Gtk.Label(label=detail)
        note.add_css_class('intergen-summon-text')
        note.set_wrap(True)
        note.set_xalign(0)
        note.set_max_width_chars(78)
        note.set_margin_start(28)
        holder.append(note)
        row = Adw.PreferencesRow()
        row.set_activatable(False)
        row.set_child(holder)
        choice_group.add(row)

    # Custom address entry + its encryption switch, shown only for the custom
    # choice so the page does not ask for an address it is not going to use.
    custom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    custom_box.set_margin_start(28)
    custom_box.set_margin_end(12)
    custom_box.set_margin_bottom(8)
    custom_box.set_visible(False)

    custom_entry = Gtk.Entry()
    custom_entry.set_placeholder_text('for example 10.0.0.10, or two '
                                      'addresses separated by a space')
    custom_box.append(custom_entry)

    encrypt_switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=10)
    encrypt_switch = Gtk.Switch()
    encrypt_switch.set_active(True)
    encrypt_switch.set_valign(Gtk.Align.CENTER)
    encrypt_label = Gtk.Label(label=_ENCRYPT_CUSTOM_TITLE)
    encrypt_label.add_css_class('heading')
    encrypt_label.set_xalign(0)
    encrypt_switch_row.append(encrypt_switch)
    encrypt_switch_row.append(encrypt_label)
    custom_box.append(encrypt_switch_row)

    encrypt_note = Gtk.Label(label=_ENCRYPT_CUSTOM_DETAIL)
    encrypt_note.add_css_class('intergen-summon-text')
    encrypt_note.set_wrap(True)
    encrypt_note.set_xalign(0)
    encrypt_note.set_max_width_chars(78)
    custom_box.append(encrypt_note)

    custom_row = Adw.PreferencesRow()
    custom_row.set_activatable(False)
    custom_row.set_child(custom_box)
    choice_group.add(custom_row)
    box.append(choice_group)

    apply_btn = Gtk.Button(label='Apply this choice')
    apply_btn.add_css_class('suggested-action')
    apply_btn.set_halign(Gtk.Align.CENTER)
    apply_btn.set_margin_top(12)
    box.append(apply_btn)

    status = Gtk.Label(label='')
    status.add_css_class('intergen-summon-text')
    status.set_wrap(True)
    status.set_justify(Gtk.Justification.CENTER)
    status.set_max_width_chars(78)
    status.set_visible(False)
    status.set_margin_top(6)
    box.append(status)

    # `current` holds the selection the machine is actually in, so Apply can
    # stay switched off while the user is only looking at the page — the
    # strongest form of "this page changes nothing by itself".
    current = {'selection': 'unknown'}

    def _refresh_current():
        state = _resolver_state()
        effective = _effective_resolver(state)
        servers_text, origin_text, encryption_text = _describe_current(effective)
        row_servers.set_subtitle(servers_text)
        row_origin.set_subtitle(origin_text)
        row_encrypted.set_subtitle(encryption_text)

        selection, addresses, encrypted = _selection_from_state(state)
        current['selection'] = selection
        if selection in radios:
            radios[selection].set_active(True)
            if selection == 'custom':
                custom_entry.set_text(' '.join(addresses))
                encrypt_switch.set_active(encrypted)
        _sync_sensitivity()
        return selection

    def _picked():
        for choice_id, radio in radios.items():
            if radio.get_active():
                return choice_id
        return 'network'

    def _sync_sensitivity(*_args):
        picked = _picked()
        custom_box.set_visible(picked == 'custom')
        # Apply is live when the pick differs from the machine's state, and
        # for the custom choice also whenever an address is typed — the same
        # provider with a different address is still a change.
        differs = picked != current['selection']
        if picked == 'custom':
            differs = True
        apply_btn.set_sensitive(differs)

    for radio in radios.values():
        radio.connect('toggled', _sync_sensitivity)

    def _on_apply(btn):
        picked = _picked()
        addresses = []
        if picked == 'custom':
            addresses = custom_entry.get_text().replace(',', ' ').split()
            if not addresses:
                status.set_visible(True)
                status.set_text('Enter the address of the name server you '
                                'want to use, then press Apply.')
                return
            if len(addresses) > 3:
                status.set_visible(True)
                status.set_text('Enter at most three addresses.')
                return
            bad = [a for a in addresses if not _address_is_valid(a)]
            if bad:
                status.set_visible(True)
                status.set_text(
                    'This is not an address: ' + ', '.join(bad) + '. A name '
                    'server has to be given as an address such as '
                    '10.0.0.10 or 2001:db8::1, because this machine cannot '
                    'look up a name before it has a name server that works.')
                return

        encrypted = encrypt_switch.get_active() if picked == 'custom' else True
        btn.set_sensitive(False)
        status.set_visible(True)
        status.set_text('Applying…')

        def worker():
            ok, message = _apply_resolver(picked, addresses, encrypted)
            verified = None
            if ok:
                # Apply-then-verify: read the machine back rather than
                # trusting the exit code. A change that did not take is never
                # reported as applied.
                verified = _selection_from_state(_resolver_state())

            def finish():
                btn.set_sensitive(True)
                if not ok:
                    status.set_text(message or 'Nothing was changed.')
                    _refresh_current()
                    return False
                selection, addresses_now, encrypted_now = verified
                _refresh_current()
                if selection != picked:
                    status.set_text(
                        'The change did not take. The machine is still using '
                        + (', '.join(addresses_now) or 'its previous name '
                           'servers') + '. Nothing here is claiming otherwise '
                        '— the panel above is what the system reports.')
                elif picked == 'custom' and set(addresses_now) != set(addresses):
                    status.set_text(
                        'Applied, but the machine reports '
                        + ', '.join(addresses_now) + ' rather than what was '
                        'entered. The panel above is what the system reports.')
                elif picked == 'custom' and encrypted and not encrypted_now:
                    status.set_text(
                        'The addresses were applied, but encryption is not on. '
                        'Treat these lookups as readable until the panel above '
                        'says otherwise.')
                else:
                    status.set_text(
                        'Applied, and confirmed by reading the system back. '
                        'The panel above shows what it now reports.')
                return False

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    apply_btn.connect('clicked', _on_apply)

    def _show_resolution_cause(cause):
        """Reveal the banner naming why the user was brought here."""
        cause_headline.set_text(netdiag.cause_headline(cause))
        cause_detail.set_text(
            netdiag.cause_detail(cause)
            + '\n\nYou were brought to this page because that is what failed. '
              'Choosing a different name server below is the change that '
              'fixes it.')
        cause_banner.set_visible(True)

    _refresh_current()

    wrapped = wrap_with_background(box, 'extensions-bg', scroll=True)
    wrapped._show_resolution_cause = _show_resolution_cause
    wrapped._refresh_current = _refresh_current
    return wrapped


def build_shortcuts_page():
    """Page 4: Keyboard shortcuts reference — compact two-column layout."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(24)
    box.set_margin_end(24)

    # Title area — pinned toward top
    title = Gtk.Label(label='Keyboard Shortcuts')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(label='The essentials to get you moving fast.')
    subtitle.add_css_class('page-subtitle')
    subtitle.set_margin_bottom(20)
    box.append(subtitle)

    shortcuts_left = [
        ('Super', 'Activities overview'),
        ('Super + A', 'All applications'),
        ('Alt + Tab', 'Switch windows'),
        ('Ctrl + Alt + T', 'Open terminal'),
        ('Super + L', 'Lock screen'),
    ]

    shortcuts_right = [
        ('Super + Left/Right', 'Tile to half screen'),
        ('Super + Up', 'Maximize window'),
        ('Super + Down', 'Restore / minimize'),
        ('Super + D', 'Show desktop'),
        ('Super + Tab', 'Switch workspaces'),
    ]

    columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    columns.set_homogeneous(True)
    columns.set_margin_top(4)

    for col_shortcuts in [shortcuts_left, shortcuts_right]:
        group = Adw.PreferencesGroup()
        group.add_css_class('transparent-group')

        for key, desc in col_shortcuts:
            row = Adw.ActionRow()
            row.set_title(desc)

            label = Gtk.Label(label=key)
            label.add_css_class('dim-label')
            label.add_css_class('caption')
            label.set_xalign(1.0)
            row.add_suffix(label)
            group.add(row)

        columns.append(group)

    columns.set_margin_top(10)
    box.append(columns)

    return wrap_with_background(box, 'shortcuts-bg', scroll=True)


def _probe_download_sources(timeout=4.0, hosts=None):
    """Can a model download source be reached, and if not, WHY?

    Delegates to net_diagnostics, the module intergen.setup uses for the same
    question, so the command line and this application cannot end up telling
    the same user two different things about the same machine.

    Returns the module's ProbeResult: a plain boolean plus the cause. The
    cause is the reason this exists. The previous version of this function
    answered only True or False, and its own docstring recorded the mistake
    that followed: it described a machine with a route and no working name
    server as "correctly reported as offline", and the caller then told that
    user to connect to WiFi — a network they were already on. Reporting the
    cause is what lets the caller say something the user can act on.

    ``hosts`` defaults to the model download's own sources — the mirror first,
    the vendor second — which is right when the question is "can the download
    run". The start-up check passes a shorter list; see _STARTUP_PROBE_HOSTS.

    Always called off the GTK main thread; the connect timeouts would freeze
    the interface otherwise.
    """
    if hosts is None:
        hosts = netdiag.MODEL_SOURCE_HOSTS
    return netdiag.probe_hosts(hosts, timeout=timeout)


# The start-up check contacts the InterGenOS mirror and nothing else.
#
# It runs on every launch of this application, whether or not the user is
# going to set anything up, and its question is only "do name lookups work" —
# which one host answers as well as two. Reaching out to a third party on a
# question that does not need them, unasked, is not something to do because it
# happened to be the existing list.
_STARTUP_PROBE_HOSTS = ('repo.intergenos.org',)


def _model_offer():
    """What model tiers this box can run, or None if it could not be asked.

    Runs `intergen setup --show-offer`, which is read-only and unprivileged and
    prints JSON. Standalone by the same rule as _probe_download_sources: the
    Welcomer is a GTK app, not part of the intergen package, so it asks over a
    process boundary instead of importing it.

    Returns the parsed dict, or None on any failure — the caller then shows the
    plain one-click card, which is exactly the pre-existing behaviour. A missing
    offer must never block setup.
    """
    try:
        proc = subprocess.run(['intergen', 'setup', '--show-offer'],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except Exception:
        return None
    if not isinstance(data, dict) or 'tiers' not in data or data.get('error'):
        return None
    return data


# What each rung means, in the user's terms. The 2B is described as a real
# choice because it IS one — a smaller, faster model is a legitimate preference,
# not a consolation prize (decided 2026-07-31).
_TIER_CHOICE_LABELS = {
    1: ('InterGen 2B — fastest replies, smallest download (about 1 GB)',
        'Runs well on any machine.'),
    2: ('InterGen 9B — the full experience (about 5 GB)',
        'The model InterGen is designed around.'),
    3: ('InterGen 35B — the largest model (about 21 GB)',
        'For graphics cards with the memory to hold it.'),
}


# The driver advisory, in the two forms it is shown: a banner at the top of the
# page, and a dialog at the moment the user commits. Same words in both places.
_ADVISORY_HEADING = ('NVIDIA GPU DETECTED - PROPRIETARY DRIVERS ARE '
                     'RECOMMENDED FOR THE BEST USER EXPERIENCE')

# The one command, written once and used in three places: the sentence the user
# can copy, the button that runs it for them, and the dialog's detail text. One
# definition means the instruction and the action can never drift apart.
_ADVISORY_COMMAND = 'sudo pkm update && sudo pkm install nvidia'

_ADVISORY_BODY_BEFORE_CMD = (
    'This machine is currently running the Nouveau opensource GPU driver. For '
    'the best user experience, it is strongly recommended that you install the '
    'Nvidia graphics driver. To install it, run the command '
)

_ADVISORY_BODY_AFTER_CMD = (
    ' from a terminal, or click the button below to have this module open a '
    'terminal and run it for you. Note- you\'ll need to provide the sudo '
    'password in the authorization prompt that will open if you use the button '
    'below. With either installation method, you\'ll need to accept Nvidia\'s '
    'EULA (End User License Agreement) in the terminal in order to allow the '
    'installation to complete. Once the driver is installed, you\'ll be '
    'presented a choice of model sizes your GPU can accommodate. While '
    'InterGen runs incredibly well on the smallest 2B model, running a larger '
    'model increases his performance with more parameters available for more '
    'complex tasks.'
)

# Plain-text form for the dialog, which takes no markup.
_ADVISORY_BODY_PLAIN = (_ADVISORY_BODY_BEFORE_CMD + _ADVISORY_COMMAND
                        + _ADVISORY_BODY_AFTER_CMD)

_INSTALL_BUTTON_LABEL = 'Open a terminal and install the NVIDIA driver'

_INSTALL_RETRY_LABEL = 'Open the terminal again'

# Shown once the terminal window has closed. It deliberately does not say the
# install succeeded: the window wraps the command in a pause, so its exit status
# belongs to the pause, not to the installer, and claiming an outcome we cannot
# see would be a guess dressed as a result.
_TERMINAL_CLOSED_NOTICE = (
    'The terminal window has closed. If the driver did not finish installing — '
    'for example if the password was mistyped — open it again with the button '
    'above. Once it is installed, reboot; this page will be shown to you again '
    'so you can finish setting InterGen up.'
)

# Shown the moment the install button is pressed, at the same size as the
# advisory heading. It has to be readable in the few seconds before the
# terminal window appears and takes the foreground — at body size it was
# covered by that window before it could be read (decided 2026-07-31).
_TERMINAL_NOTICE = (
    'A TERMINAL WINDOW WILL OPEN MOMENTARILY - Enter your sudo password when '
    'prompted, ACCEPT the Nvidia EULA when prompted (by pressing \'Enter\'), '
    'and finally reboot the system once the driver is installed. You\'ll be '
    'shown the Welcomer again after reboot, so you can return to this page and '
    'continue InterGen\'s setup'
)

# How long the notice above is left alone on screen before the terminal opens.
_TERMINAL_OPEN_DELAY_SECONDS = 6

# The package the driver offer installs. Named once, so the command, the
# outcome check and the message cannot drift apart.
_DRIVER_PACKAGE = 'nvidia'

# Shown when the package database confirms the driver IS installed. This
# replaces the retry banner rather than sitting beside it: a user who has just
# succeeded must not be shown a retry that implies they have not.
_DRIVER_INSTALLED_NOTICE = (
    'The NVIDIA driver is installed. REBOOT NOW to start using it — the '
    'driver only takes effect after a restart. This page will be shown to you '
    'again after the reboot so you can finish setting InterGen up.'
)

# Shown when the package database says the driver is NOT installed. Distinct
# from the could-not-tell wording below, because a user who knows the install
# did not complete needs a different next step from one who is being asked to
# check.
_DRIVER_NOT_INSTALLED_NOTICE = (
    'The terminal window closed and the NVIDIA driver is still not installed. '
    'The most common causes are a mistyped password and a declined licence. '
    'Open the terminal again with the button above to retry; the output in '
    'that window says which it was.'
)


def _driver_leg_is_done():
    """Whether the graphics-driver step has been completed on this machine.

    True only when the machine was offered a driver AND that driver is now
    installed. Both halves matter: a machine that was never offered one has no
    driver leg to have finished, and reordering the page for it would move a
    call to action above the disclosure it is supposed to follow.

    A machine whose record cannot be read, or whose package state cannot be
    determined, answers False — the unchanged page order is the safe default,
    since being asked to scroll is a far smaller harm than being shown a button
    before the explanation of what it does.
    """
    record = _gpu_detection_record()
    if not record:
        return False
    if record.get('vendor') != 'nvidia':
        # The driver offer on this page is the NVIDIA proprietary one; no other
        # vendor has a driver leg here to complete.
        return False
    return _package_is_installed(_DRIVER_PACKAGE) is True


def _package_is_installed(name):
    """Whether ``name`` is installed, or None when that cannot be determined.

    Asked of the package database, because that is the thing that knows. The
    three-valued answer is deliberate: "not installed" and "I could not ask"
    lead to different messages, and collapsing them would either accuse a
    successful install of having failed or claim success on a machine where
    nothing could be checked.

    THE EXIT STATUS IS NOT THE ANSWER, and assuming it was would have made
    this check useless. Measured 2026-08-06 against the shipped pkm: `pkm info`
    exits 0 for an installed package, 0 for a known package that is not
    installed, AND 0 for a package name that does not exist at all. Reading
    "rc != 0" as "not installed" would therefore have reported every machine as
    installed. What actually distinguishes the cases is the output: an
    installed package prints an `install_date` record, and an absent one prints
    "is not installed".
    """
    try:
        proc = subprocess.run(['pkm', 'info', name],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or '') + (proc.stderr or '')
    if not text.strip():
        return None
    for line in text.splitlines():
        if line.strip().startswith('install_date'):
            return True
    if 'is not installed' in text:
        return False
    # Output that matches neither shape means pkm answered something this code
    # does not understand. Saying "not installed" would accuse a possibly
    # successful install of having failed; unknown is the honest answer.
    return None

# Written when a driver install is started from this page; the launcher reads
# it after this process exits. The path is built from $HOME rather than from
# GLib's config dir deliberately: the launcher hardcodes
# "${HOME}/.config/intergen-welcome", so on a machine with XDG_CONFIG_HOME set
# elsewhere the two would point at different files and the sentinel would never
# be seen — the writer must match the reader exactly.
_REARM_SENTINEL = os.path.join(
    os.path.expanduser('~'), '.config', 'intergen-welcome', 'rearm')


def _request_welcomer_rearm():
    """Ask the launcher to clear the "already seen" marker as it exits.

    The driver install needs a reboot, and the notice above promises the
    Welcomer will be back afterwards so setup can be finished — a promise that
    is only true if the first-login marker is cleared.

    The clearing is done by the LAUNCHER, not here, and that is the whole point
    of the sentinel: the launcher writes the done-marker on every clean exit,
    so a marker deleted from inside this process would be re-created by the
    launcher moments later and the promise would silently be false.

    Never raises — a machine where this cannot be written still installs the
    driver correctly; the user just opens the Welcomer from the app grid
    instead of being shown it.
    """
    try:
        os.makedirs(os.path.dirname(_REARM_SENTINEL), exist_ok=True)
        with open(_REARM_SENTINEL, 'w') as fh:
            fh.write('nvidia-driver-install\n')
        return True
    except OSError:
        return False


def _clear_welcomer_rearm():
    """Withdraw the request written by :func:`_request_welcomer_rearm`.

    Called when the install could not actually be started, so a request that
    no longer corresponds to anything does not survive to re-show the Welcomer
    for a reboot that is never going to happen. The launcher clears a stale one
    at startup as well; this is the same correction made at the moment it is
    known, rather than at the next launch.
    """
    try:
        os.remove(_REARM_SENTINEL)
    except OSError:
        pass


def _code_span(text):
    """Render ``text`` as a code span — monospace on a tinted chip.

    The same treatment markdown gives backticked text, and for the same reason:
    a command a user is expected to TYPE has to be visibly separate from the
    prose around it. Monospace alone (a bare <tt>) does not carry at body size.
    The hair spaces inside the span are the chip's padding — Pango attributes
    colour a run of text, they cannot add a margin.
    """
    return ('<span font_family="monospace" size="105%" '
            'background="#0d1117" foreground="#ffd54f"> '
            + GLib.markup_escape_text(text) + ' </span>')


def _open_terminal_running(command):
    """Open a terminal window running ``command``, leaving it open afterwards.

    The trailing pause is load-bearing: the NVIDIA install presents an EULA and
    then a summary, and a terminal that closes the instant the command returns
    would take both off the screen before they could be read.

    Returns the terminal's process handle so the caller can tell when the
    window closes. Returns None if no terminal emulator could be started — the
    caller then shows the command so the user can run it themselves, rather
    than leaving a dead button.
    """
    # The closing line reports the ACTUAL exit status. It used to say
    # "Installation finished." unconditionally, so a refused licence, a failed
    # download or an unresolvable dependency ended with a sentence stating the
    # opposite of what happened — and the user closed the window believing the
    # package was installed.
    script = (f'{command}; __rc=$?; echo; '
              'if [ "$__rc" -eq 0 ]; then '
              '  echo "Installation finished successfully."; '
              'else '
              '  echo "Installation FAILED (exit $__rc). Nothing above this '
              'line was necessarily completed — read the output for the '
              'reason."; '
              'fi; '
              'read -r -p "Press Enter to close this window."; '
              'exit "$__rc"')
    for argv in (['gnome-terminal', '--', 'bash', '-lc', script],
                 ['xdg-terminal-exec', 'bash', '-lc', script],
                 ['xterm', '-e', 'bash', '-lc', script]):
        try:
            return subprocess.Popen(argv)
        except (OSError, FileNotFoundError):
            continue
    return None


def _build_driver_advisory():
    """The advisory banner, shown at the top of the page the moment it opens.

    Deliberately loud and placed first: a user deciding whether to install
    graphics drivers needs to see this BEFORE they read anything else. The
    first build set this text in the same 12px grey as the surrounding prose,
    below the fold, and it was missed entirely on the hardware it was written
    for (decided 2026-07-31).
    """
    banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    banner.add_css_class('intergen-advisory')
    banner.set_halign(Gtk.Align.CENTER)

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    head.set_halign(Gtk.Align.CENTER)
    left_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')
    left_icon.set_pixel_size(30)
    head.append(left_icon)
    title = Gtk.Label(label=_ADVISORY_HEADING)
    title.add_css_class('intergen-advisory-title')
    title.set_justify(Gtk.Justification.CENTER)
    title.set_wrap(True)
    title.set_max_width_chars(64)
    head.append(title)
    right_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')
    right_icon.set_pixel_size(30)
    head.append(right_icon)
    banner.append(head)

    banner.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    # The command is set in monospace inside the sentence, so it reads as
    # something to type rather than as prose.
    body = Gtk.Label()
    body.set_markup(
        GLib.markup_escape_text(_ADVISORY_BODY_BEFORE_CMD)
        + _code_span(_ADVISORY_COMMAND)
        + GLib.markup_escape_text(_ADVISORY_BODY_AFTER_CMD))
    body.add_css_class('intergen-advisory-text')
    body.set_justify(Gtk.Justification.LEFT)
    body.set_xalign(0)
    body.set_wrap(True)
    body.set_max_width_chars(88)
    banner.append(body)

    banner.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    install_btn = Gtk.Button(label=_INSTALL_BUTTON_LABEL)
    install_btn.add_css_class('suggested-action')
    install_btn.set_halign(Gtk.Align.CENTER)
    banner.append(install_btn)

    # Heading-sized, not body-sized: this is the instruction the user has to
    # carry into the terminal window, and the terminal takes the foreground a
    # few seconds later.
    install_status = Gtk.Label(label='')
    install_status.add_css_class('intergen-advisory-title')
    install_status.set_justify(Gtk.Justification.CENTER)
    install_status.set_wrap(True)
    install_status.set_max_width_chars(64)
    install_status.set_visible(False)
    install_status.set_margin_top(4)
    banner.append(install_status)

    def _on_install_clicked(btn):
        # One install at a time — a second press would open a second terminal
        # running a second privileged package transaction.
        btn.set_sensitive(False)
        install_status.set_visible(True)
        install_status.set_text(_TERMINAL_NOTICE)
        _request_welcomer_rearm()

        def _open_terminal_after_delay():
            proc = _open_terminal_running(_ADVISORY_COMMAND)
            if proc is None:
                _no_terminal(btn)
            else:
                _watch_terminal(btn, proc)
            return GLib.SOURCE_REMOVE

        # The delay is the point: the notice above needs to be read BEFORE the
        # terminal window opens over it and takes the foreground.
        GLib.timeout_add_seconds(_TERMINAL_OPEN_DELAY_SECONDS,
                                 _open_terminal_after_delay)

    def _watch_terminal(btn, proc):
        """Report the OUTCOME when the terminal window closes.

        The window closing is not the outcome. The earlier version could only
        say the window had closed, and offered a retry regardless — so a user
        whose driver installed correctly was shown a retry banner suggesting it
        had not, and was told nothing about the reboot the driver needs.

        The outcome is now ASKED OF THE MACHINE: is the package installed? That
        is a fact the package database answers directly, and it is true whether
        the install ran here, in another window, or on a previous attempt. When
        it says yes, the retry banner is replaced by a success state naming the
        reboot requirement and what happens after it. When it says no, or
        cannot be determined, the retry stays — and the two are worded
        differently, because "it failed" and "I could not tell" are not the
        same thing to a user deciding what to do next.
        """
        def _poll():
            if btn.get_root() is None:      # window closed; stop the timer
                return GLib.SOURCE_REMOVE
            if proc.poll() is None:
                return GLib.SOURCE_CONTINUE
            installed = _package_is_installed(_DRIVER_PACKAGE)
            if installed is True:
                btn.set_visible(False)
                install_status.set_text(_DRIVER_INSTALLED_NOTICE)
                return GLib.SOURCE_REMOVE
            btn.set_sensitive(True)
            btn.set_label(_INSTALL_RETRY_LABEL)
            install_status.set_text(
                _TERMINAL_CLOSED_NOTICE if installed is None
                else _DRIVER_NOT_INSTALLED_NOTICE)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(2, _poll)

    def _no_terminal(btn):
        """No terminal could be started — say so, and give the command.

        The button is handed back so the user can try again, and the notice is
        replaced rather than left standing: a promise that a window is about to
        open must not survive the discovery that none can. The re-arm request
        is withdrawn too — no install was started, so there is no reboot for
        the Welcomer to come back after.
        """
        _clear_welcomer_rearm()
        install_status.set_markup(
            GLib.markup_escape_text(
                'A terminal could not be opened on this machine. Open one '
                'yourself and run: ')
            + _code_span(_ADVISORY_COMMAND))
        btn.set_sensitive(True)

    install_btn.connect('clicked', _on_install_clicked)
    return banner



# ---------------------------------------------------------------------------
# The GPU driver / compute-engine offer
# ---------------------------------------------------------------------------
#
# Decided 2026-08-05: this offer used to be a page in the installer. It was
# moved here because the installer can never perform it — the vendor driver
# and the per-vendor compute engines are mirror-only, the installer reaches no
# network, and all that page could produce was a command the user had to
# remember across a reboot. Here the package manager is present, the machine
# is on a network, and the vendor's own licence gate can run with its full
# text on the user's own machine.
#
# The installer still runs the vendor probe and writes what it found to the
# record below; nothing here re-derives the hardware or the engine ranking.

# Written by the installer (installer/backend/gpu_detect.py). Absent on a
# machine installed before this landed, or one where the write failed — both
# are handled by offering nothing, which is what hardware with no upgrade path
# gets anyway.
_GPU_RECORD_PATH = '/etc/intergen/gpu-detection.json'
_GPU_RECORD_VERSION = 2

# Every schema version this build can read. Version 2 only ADDED keys to
# version 1 — gfx_targets and upgrade_engine_supported — and every key is read
# through .get(), so a version-1 record still describes the machine correctly
# and its absent keys read as "not determined", which is exactly what they mean
# on a machine installed before the check existed.
#
# This is a SET rather than an equality test because the alternative silently
# withdraws the offer from every machine installed by an older medium. That
# failure would look identical to hardware having no upgrade path, which is the
# kind of silence this file is written to avoid. A version that genuinely
# CHANGED the meaning of a key would be left out of this set on purpose.
_GPU_RECORD_READABLE_VERSIONS = (1, 2)


def _gpu_detection_record(path=_GPU_RECORD_PATH):
    """The installer's display-controller record, or None.

    Returns None for every failure — absent, unreadable, malformed, or written
    by a schema version this build does not know. A missing offer is a silent,
    correct outcome; guessing at the hardware from a record we cannot parse is
    not. Never raises.
    """
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get('version') not in _GPU_RECORD_READABLE_VERSIONS:
        return None
    if 'vendor' not in data:
        return None
    return data


# The commands. The driver command is _ADVISORY_COMMAND above — the same one
# the banner tells the user to type — so the button and the sentence can never
# disagree. The engine packages pull their own chains: the CUDA engine pulls
# the driver and the toolkit's download helper, the HIP engine pulls the ROCm
# runtime.
#
# EVERY install here SYNCS THE PACKAGE INDEX FIRST. This page is shown during
# first boot, which is precisely when the index cache is empty — nothing has
# run `pkm update` on this machine yet — and an install against an empty index
# fails with the package "not found", which reads to a user as the package not
# existing rather than as the machine never having looked. That was the
# measured root cause of the offer failing on a fresh install.
#
# `&&` rather than `;` so a failed sync stops the chain: installing against a
# sync that just failed would produce the same confusing not-found error one
# step later.
_CUDA_ENGINE_COMMAND = 'sudo pkm update && sudo pkm install llama-cpp-cuda'
_HIP_ENGINE_COMMAND = 'sudo pkm update && sudo pkm install llama-cpp-hip'

# Said on every proprietary offer. It promises only what is true of both: the
# package manager runs the vendor's gate before it installs. The CUDA toolkit
# is fetched from the vendor and asks first, before any download; the driver
# comes from this project's mirror, so its archive is fetched and the gate runs
# before the install. The weaker true sentence is the one that covers both.
_VENDOR_LICENCE_NOTICE = (
    'This is proprietary software under a vendor licence. The package manager '
    'shows you the vendor\'s licence — its full text, on this machine — and '
    'installs nothing until you accept it in the terminal.'
)


def _gpu_offers(record):
    """The offers for this machine, in the order they are shown.

    Every per-vendor decision comes from the record the installer wrote, which
    derived it from the ratified engine preference table. Nothing here ranks
    engines or reads hardware.

    Each offer is a dict: key / title / detail / command / proprietary.
    An empty list means there is nothing to offer and the section is not built.
    """
    if not record:
        return []
    vendor = record.get('vendor')
    upgrade = record.get('upgrade_engine')
    outranks = bool(record.get('upgrade_outranks_shipped'))
    offers = []

    if vendor == 'nvidia':
        offers.append({
            'key': 'nvidia_driver',
            'title': 'NVIDIA proprietary graphics driver',
            'detail': (
                'Replaces the open source nouveau driver this machine is '
                'running now. On NVIDIA hardware this is what gives you the '
                'full performance of the card, and InterGen cannot read how '
                'much video memory the card has until it is installed. Needs '
                'one reboot to take effect.'),
            'command': _ADVISORY_COMMAND,
            'proprietary': True,
        })
    if upgrade == 'cuda':
        offers.append({
            'key': 'compute_engine',
            'title': 'CUDA compute engine for InterGen',
            'detail': (
                'Adds NVIDIA\'s CUDA build of the inference engine alongside '
                'the Vulkan one, and the CUDA toolkit it needs (about 4.1 GB '
                'to download, about 6.7 GB installed). It requires the '
                'proprietary driver above, so choosing it chooses that too. '
                + _cuda_speed_sentence(outranks)),
            'command': _CUDA_ENGINE_COMMAND,
            'proprietary': True,
        })
    elif upgrade == 'hip':
        supported = record.get('upgrade_engine_supported')
        # A measured "this build has no code for this GPU" removes the offer
        # entirely. The HIP build carries device code only for the
        # architectures it was compiled for, and on an AMD GPU outside that
        # list the inference server crashes at model load rather than refusing
        # cleanly — so installing it would replace a working setup with a
        # broken one. There is nothing to offer, so nothing is offered.
        if supported is not False:
            offers.append({
                'key': 'compute_engine',
                'title': 'HIP/ROCm compute engine for InterGen',
                'detail': (
                    'Adds AMD\'s HIP build of the inference engine and the '
                    'ROCm runtime it needs. '
                    + _hip_preference_sentence(supported)
                    + ' Open source; no vendor licence to accept.'),
                'command': _HIP_ENGINE_COMMAND,
                'proprietary': False,
            })
    return offers


def _hip_preference_sentence(supported):
    """What to say about HIP taking over from Vulkan.

    Scoped to what was actually measured, and on which hardware. The previous
    wording said "on this hardware it is the preferred engine" for every AMD
    machine, which stated a measurement taken on one card as though it were a
    fact about the machine in front of the user. Where the architecture could
    not be read, that is said plainly rather than papered over — a user reading
    an offer is entitled to know which parts of it were checked here and which
    were carried over from elsewhere.
    """
    measured = ('On the AMD hardware this project measured, the Vulkan driver '
                'kept the model\'s weights in system memory instead of the '
                'card\'s and the HIP build put them on the card.')
    if supported:
        return (measured + ' This machine\'s GPU is one the HIP build '
                'supports, so it takes over from Vulkan once installed.')
    return (measured + ' Whether this machine\'s GPU is one the HIP build '
            'supports could not be determined here, so it may or may not take '
            'over from Vulkan once installed.')


def _cuda_speed_sentence(outranks):
    """What to say about CUDA's effect on which engine serves.

    Read off the installer's record rather than asserted here, so this cannot
    contradict the table that actually decides. On the ranking as ratified,
    Vulkan is preferred over CUDA — installing CUDA installs it and leaves
    Vulkan serving — and saying so is the difference between an honest offer
    and selling several gigabytes as a speed-up the project measured as a
    slow-down.
    """
    if outranks:
        return ('On this hardware the CUDA engine is preferred over Vulkan '
                'and takes over once installed.')
    return ('Measured on this project\'s NVIDIA hardware, the Vulkan engine '
            'that already ships was FASTER than the CUDA one on every '
            'measurement taken, so Vulkan stays the engine that serves even '
            'after CUDA is installed. Choose this to have the CUDA engine '
            'available, not to make InterGen faster.')


def _gpu_install_command(selected, offers):
    """The one shell command that installs everything selected, in order.

    Returns None when nothing is selected. The parts are joined with `&&` so a
    refused licence or a failed step stops the chain instead of the next
    install running as though the previous one had succeeded. The driver comes
    first because the CUDA engine links a library only it provides.
    """
    parts = [o['command'] for o in offers if o['key'] in selected]
    if not parts:
        return None
    return ' && '.join(parts)


def _gpu_required_dependencies(selected, offers):
    """Add what a selection cannot work without.

    The CUDA engine links the CUDA driver API, which comes from the
    proprietary driver and nowhere else. Stated on the offer and enforced
    here, rather than left for the package manager to discover.
    """
    chosen = set(selected)
    if 'compute_engine' in chosen and any(
            o['key'] == 'nvidia_driver' for o in offers):
        chosen.add('nvidia_driver')
    return [o['key'] for o in offers if o['key'] in chosen]



def _build_gpu_install_offer(record):
    """The offer section: what was detected, and a switch per available item.

    Built only when the installer's record names something this machine can
    add. The switches choose; ONE button performs the whole selection in a
    terminal, where the vendor's own licence gate runs and the user answers it
    with the vendor's text in front of them. Nothing here accepts a licence on
    anyone's behalf, and the toggles cannot: the licence text ships inside the
    download, so the only honest place to accept it is the terminal.

    Returns None when there is nothing to offer, so the caller appends
    nothing rather than an empty box.
    """
    offers = _gpu_offers(record)
    if not offers:
        return None

    section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    section.add_css_class('intergen-advisory')
    section.set_halign(Gtk.Align.CENTER)

    heading = Gtk.Label(label=_gpu_offer_heading(record))
    heading.add_css_class('intergen-advisory-title')
    heading.set_justify(Gtk.Justification.CENTER)
    heading.set_wrap(True)
    heading.set_max_width_chars(64)
    section.append(heading)
    section.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    rows = {}
    group = Adw.PreferencesGroup()
    for offer in offers:
        row = Adw.SwitchRow()
        # libadwaita parses row titles and subtitles as Pango markup, so a bare
        # ampersand in ordinary prose aborts the parse and the WHOLE row
        # renders nothing but a GTK warning. The driver command contains one
        # (`sudo pkm update && sudo pkm install nvidia`), so the offer that
        # matters most is exactly the one that would silently vanish (measured
        # 2026-08-05). None of this copy wants markup.
        row.set_use_markup(False)
        row.set_title(offer['title'])
        detail = offer['detail']
        if offer['proprietary']:
            detail = detail + '\n\n' + _VENDOR_LICENCE_NOTICE
        detail = detail + '\n\nRuns: ' + offer['command']
        row.set_subtitle(detail)
        row.set_active(False)
        group.add(row)
        rows[offer['key']] = row
    section.append(group)

    install_btn = Gtk.Button(label=_GPU_INSTALL_BUTTON_LABEL)
    install_btn.add_css_class('suggested-action')
    install_btn.set_halign(Gtk.Align.CENTER)
    install_btn.set_sensitive(False)
    section.append(install_btn)

    status = Gtk.Label(label='')
    status.add_css_class('intergen-advisory-title')
    status.set_justify(Gtk.Justification.CENTER)
    status.set_wrap(True)
    status.set_max_width_chars(64)
    status.set_visible(False)
    status.set_margin_top(4)
    section.append(status)

    # Re-entrance guard: switching the driver on from inside the handler must
    # not re-enter it.
    coupling = {'busy': False}

    def _selected():
        return [k for k, r in rows.items() if r.get_active()]

    def _on_toggled(_row, _pspec):
        if not coupling['busy']:
            coupling['busy'] = True
            try:
                for key in _gpu_required_dependencies(_selected(), offers):
                    row = rows.get(key)
                    if row is not None and not row.get_active():
                        row.set_active(True)
            finally:
                coupling['busy'] = False
        install_btn.set_sensitive(bool(_selected()))

    for row in rows.values():
        row.connect('notify::active', _on_toggled)

    def _on_install_clicked(btn):
        command = _gpu_install_command(
            _gpu_required_dependencies(_selected(), offers), offers)
        if command is None:
            return
        # One transaction at a time — a second press would open a second
        # terminal running a second privileged package transaction.
        btn.set_sensitive(False)
        status.set_visible(True)
        status.set_text(_GPU_TERMINAL_NOTICE)
        # A driver install needs a reboot, and the notice promises the
        # Welcomer will be back afterwards. Requested only when the driver is
        # actually part of what was selected.
        if 'nvidia_driver' in _gpu_required_dependencies(_selected(), offers):
            _request_welcomer_rearm()

        def _open_after_delay():
            proc = _open_terminal_running(command)
            if proc is None:
                _clear_welcomer_rearm()
                status.set_markup(
                    GLib.markup_escape_text(
                        'A terminal could not be opened on this machine. Open '
                        'one yourself and run: ')
                    + _code_span(command))
                btn.set_sensitive(True)
            else:
                _watch(btn, proc)
            return GLib.SOURCE_REMOVE

        # The delay is the point: the notice has to be read BEFORE the terminal
        # window opens over it and takes the foreground.
        GLib.timeout_add_seconds(_TERMINAL_OPEN_DELAY_SECONDS,
                                 _open_after_delay)

    def _watch(btn, proc):
        """Hand the button back when the terminal window closes.

        Whether the packages actually installed is not knowable from here —
        the window wraps the command in a pause, so its exit status belongs to
        the pause, not to the package manager. This claims nothing about the
        outcome; it reports the one fact it has and makes a retry possible.
        """
        def _poll():
            if btn.get_root() is None:
                return GLib.SOURCE_REMOVE
            if proc.poll() is None:
                return GLib.SOURCE_CONTINUE
            btn.set_sensitive(True)
            btn.set_label(_GPU_INSTALL_RETRY_LABEL)
            status.set_text(_GPU_TERMINAL_CLOSED_NOTICE)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(2, _poll)

    install_btn.connect('clicked', _on_install_clicked)
    return section


def _gpu_offer_heading(record):
    """The section's headline: what the installer found on this machine."""
    vendor = (record or {}).get('vendor')
    if vendor == 'nvidia':
        return 'NVIDIA GRAPHICS DETECTED - OPTIONAL SOFTWARE IS AVAILABLE'
    if vendor == 'amd':
        return 'AMD GRAPHICS DETECTED - OPTIONAL SOFTWARE IS AVAILABLE'
    return 'OPTIONAL GRAPHICS SOFTWARE IS AVAILABLE FOR THIS MACHINE'


_GPU_INSTALL_BUTTON_LABEL = 'Open a terminal and install what I selected'
_GPU_INSTALL_RETRY_LABEL = 'Open the terminal again'

_GPU_TERMINAL_NOTICE = (
    'A TERMINAL WINDOW WILL OPEN MOMENTARILY - Enter your sudo password when '
    'prompted, and ACCEPT the vendor licence when the package manager shows '
    'it. Reboot once the driver is installed; you\'ll be shown the Welcomer '
    'again afterwards so you can continue InterGen\'s setup.'
)

_GPU_TERMINAL_CLOSED_NOTICE = (
    'The terminal window has closed. If the installation did not finish — for '
    'example if the password was mistyped or the licence was declined — open '
    'it again with the button above.'
)


def _launch_intergen_setup(on_line, on_done, tier=None):
    """Run the InterGen model setup as a one-click action (Issue #2).

    Spawns `pkexec intergen setup --yes` in a background thread and streams its
    stdout (hardware tier, the chosen model + its license URL, download
    progress) line-by-line to on_line(text); calls on_done(success: bool) when
    it exits. On success it also best-effort enables the user service so
    InterGen is live without a reboot. `intergen setup` itself does the real
    work — hardware detect, fit the model to the tier (the 2B on this class of
    box), record license acceptance, download into the root-owned store, mint
    the auth/dispatch tokens. Factored to module scope so the dev preview
    harness can stub it (no real pkexec / no multi-GB download in a render).
    """
    import threading

    def worker():
        try:
            # Connectivity pre-check (GBC004.1 Issue-1): the model download
            # needs the network, so a setup that cannot succeed does not get to
            # fire a polkit prompt first. Runs in the worker thread so the
            # short probe never freezes the UI. `intergen setup` guards this
            # too (defense in depth); this just avoids a doomed auth prompt.
            #
            # The message now depends on WHY. Until this changed, every reason
            # produced "connect to WiFi", which for the user whose network is
            # fine and whose name server is not was an instruction they could
            # follow perfectly and still get nowhere. When name lookups are the
            # failure, the Finding Websites page is put in front of them, since
            # that page is where the failure can actually be fixed.
            probe = _probe_download_sources()
            if not probe.reachable:
                message = (netdiag.cause_headline(probe.cause) + ' '
                           + netdiag.cause_detail(probe.cause))
                if netdiag.cause_is_name_resolution(probe.cause):
                    GLib.idle_add(_request_dns_page, probe.cause)
                    message += (' The Finding Websites page has been opened '
                                'for you.')
                else:
                    message += (' Then click "Set up InterGen now" again.')
                GLib.idle_add(on_done, False, message)
                return
            argv = ['pkexec', 'intergen', 'setup', '--yes']
            if tier is not None:
                # The user picked a model on the card above; pass it through so
                # setup installs THAT one rather than re-deciding.
                argv.append(f'--tier={int(tier)}')
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode == 0
            if ok:
                # Opt the user in for real — enable+start the user daemon so
                # InterGen is usable immediately. Best-effort, non-fatal.
                try:
                    subprocess.run(
                        ['systemctl', '--user', 'enable', '--now', 'intergen'],
                        check=False, timeout=30,
                    )
                except Exception:
                    pass
            GLib.idle_add(on_done, ok)
        except Exception as e:  # defensive — surface, don't crash the greeter
            GLib.idle_add(on_line, f'Setup error: {e}')
            GLib.idle_add(on_done, False)

    threading.Thread(target=worker, daemon=True).start()


def _intergen_is_set_up():
    """True if an InterGen chat model is installed in the system model store.

    Standalone, read-only filesystem check — no network, no `intergen` import
    (same rationale as _probe_download_sources: the Welcomer is a GTK app, not part
    of the intergen package). Mirrors the exact signal `intergen setup` itself
    uses to short-circuit to "InterGen is ready": its `model.downloaded` test
    is just "the model's .gguf exists in the store" (intergen.model_manager
    MODEL_DIR = /var/lib/intergen/models/llm; setup.py `if model.downloaded:
    ... 'InterGen is ready'`). The embedding model (nomic-embed*) and the
    multimodal projector (mmproj-*) live in that same dir but are companions,
    not a chat model, so they're excluded — the presence of a primary LLM
    .gguf is the "he's set up" signal (verified against a real install: the
    dir holds the InternVL LLM, its mmproj, and the nomic embedding). The
    store is root-owned but world-readable (0755 dirs / 0644 files), so the
    check works from the unprivileged Welcomer.

    Fails toward "not set up" on any error (missing/unreadable dir): a false
    "not set up" merely re-offers a one-click setup that no-ops if the model
    is already present, whereas a false "ready" would hide the only onboarding
    path — so the safe default is to show setup.
    """
    store = '/var/lib/intergen/models/llm'
    try:
        for entry in os.listdir(store):
            if not entry.endswith('.gguf'):
                continue
            low = entry.lower()
            if 'embed' in low or 'nomic' in low or low.startswith('mmproj'):
                continue
            if os.path.getsize(os.path.join(store, entry)) > 0:
                return True
    except OSError:
        return False
    return False


# ── Engine readiness probe (mirror of intergen/panel/extension/extension.js) ──
# _intergen_is_set_up() answers "is a model in the store" — the download. It
# CANNOT see whether the engine (llama-server) is actually serving. The panel
# extension gates its icon on exactly that engine signal
# (components.llama_server === true), so a "ready — look for his icon" card that
# trusts only the store read claims ready while the panel keeps the icon hidden
# (a post-install evaluation finding). These helpers add the same engine probe.
_INTERGEN_BUS = 'com.intergenos.InterGen'
_INTERGEN_PATH = '/com/intergenos/InterGen'


def _engine_state_from_json(payload):
    """Map a daemon Status (s) JSON payload to 'ready' or 'down'.

    'ready' iff components.llama_server is truthy — the exact signal the panel
    extension gates its icon on (extension.js _checkReady:
    status.components.llama_server). Any other well-formed status (model present
    but llama-server not serving) is 'down'. Raises on malformed JSON; the
    caller treats a decode failure as inconclusive.
    """
    status = json.loads(payload)
    components = (status or {}).get('components') or {}
    return 'ready' if components.get('llama_server') else 'down'


def _intergen_engine_state_blocking():
    """Probe the InterGen daemon's engine readiness over D-Bus. BLOCKING — run
    off the UI thread (see _intergen_engine_probe).

    Returns one of:
      'ready'        — daemon answered, components.llama_server === true.
      'down'         — daemon answered but the engine is not up (model present,
                       llama-server not serving) OR the daemon is not running at
                       all (SERVICE_UNKNOWN / NAME_HAS_NO_OWNER — the definitive
                       "gone" the extension handles via its name-vanished cb).
      'inconclusive' — the call timed out / no-reply, almost always because the
                       single-threaded daemon is BUSY doing inference and cannot
                       answer Status (the G3-6 false-negative class). Must NOT
                       downgrade an already-shown ready card.

    Mirrors extension.js _checkReady: same bus / path / iface / method, same 5s
    timeout, same components.llama_server gate, same inconclusive discipline.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            _INTERGEN_BUS, _INTERGEN_PATH, _INTERGEN_BUS, 'Status', None,
            GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 5000, None)
        (payload,) = reply.unpack()
        return _engine_state_from_json(payload)
    except GLib.Error as e:
        # No name owner / no such service => the daemon is not running => a
        # definitive not-ready (down), mirroring the extension's name-vanished
        # path. Anything else (timeout / no-reply while the daemon is busy) is
        # inconclusive and must not downgrade a shown ready card.
        if (e.matches(Gio.dbus_error_quark(), Gio.DBusError.SERVICE_UNKNOWN) or
                e.matches(Gio.dbus_error_quark(), Gio.DBusError.NAME_HAS_NO_OWNER)):
            return 'down'
        return 'inconclusive'
    except Exception:
        # Malformed Status payload / unexpected error — treat as inconclusive so
        # a transient glitch never downgrades a shown ready card.
        return 'inconclusive'


def _next_shown_state(current, probe):
    """The state a state-driven view should show, given what is currently shown
    and a fresh probe result. Pure — the testable core of the extension's
    inconclusive discipline.

      - 'inconclusive' NEVER flips a shown card: keep ``current``; with nothing
        shown yet, fall to the honest not-ready default ('down').
      - 'ready' / 'down' are definitive and replace the shown state.
    """
    if probe == 'inconclusive':
        return current if current is not None else 'down'
    return probe


def _intergen_engine_probe(on_state):
    """Probe engine readiness on a worker thread, then deliver the result to
    ``on_state(state)`` back on the GTK main loop. Returns immediately to the
    caller (same threading shape as _launch_intergen_setup)."""
    def worker():
        state = _intergen_engine_state_blocking()
        GLib.idle_add(on_state, state)
    threading.Thread(target=worker, daemon=True).start()


def _build_intergen_ready_card():
    """The 'InterGen is ready' card — shown ONLY when the engine is definitively
    up. Points the user at the panel icon, which the panel extension shows on the
    same components.llama_server === true gate, so the two never disagree."""
    ready = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    ready.add_css_class('intergen-summon')
    ready.set_halign(Gtk.Align.CENTER)
    ready.set_margin_top(8)

    ready_title = Gtk.Label()
    # GREEN status: engine up.
    ready_title.set_markup(
        '<span foreground="#3ddc84">●</span>  InterGen is ready')
    ready_title.add_css_class('intergen-summon-key')
    ready.append(ready_title)

    ready_desc = Gtk.Label(
        label='InterGen is already set up on this machine — there\'s nothing '
              'more to do here. He\'s live and waiting in your top panel.'
    )
    ready_desc.add_css_class('intergen-summon-text')
    ready_desc.set_justify(Gtk.Justification.CENTER)
    ready_desc.set_wrap(True)
    ready_desc.set_max_width_chars(88)
    ready.append(ready_desc)

    ready_hint = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    ready_hint.set_halign(Gtk.Align.CENTER)
    ready_hint.set_margin_top(6)
    ready_icon = Gtk.Image.new_from_icon_name('intergenos-symbolic')
    ready_icon.set_pixel_size(30)
    ready_icon.add_css_class('intergen-icon-preview')
    ready_hint_label = Gtk.Label(
        label='Look for this icon in the top-right of your panel — '
              'click it anytime to start a conversation.')
    ready_hint_label.add_css_class('intergen-summon-text')
    ready_hint_label.set_wrap(True)
    ready_hint_label.set_max_width_chars(48)
    ready_hint.append(ready_icon)
    ready_hint.append(ready_hint_label)
    ready.append(ready_hint)
    return ready


def _build_intergen_starting_card():
    """The honest intermediate card — InterGen is set up (model present) but his
    engine is not serving yet (first-boot warm-up, or the daemon restarting after
    setup). Does NOT claim ready and does NOT point at the panel icon (the panel
    gate won't show it until the engine is up), so the Welcomer and the panel
    never disagree. Flips to the ready card automatically when the engine comes
    up (see the poll in build_intergen_page)."""
    starting = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    starting.add_css_class('intergen-summon')
    starting.set_halign(Gtk.Align.CENTER)
    starting.set_margin_top(8)

    starting_title = Gtk.Label()
    # AMBER status: set up, engine warming up / not yet serving.
    starting_title.set_markup(
        '<span foreground="#e5a13a">●</span>  InterGen is starting…')
    starting_title.add_css_class('intergen-summon-key')
    starting.append(starting_title)

    starting_desc = Gtk.Label(
        label='InterGen is set up on this machine, but his engine isn\'t '
              'responding yet — he may still be warming up. There\'s nothing '
              'you need to do; once he\'s running he\'ll appear in your top '
              'panel automatically.'
    )
    starting_desc.add_css_class('intergen-summon-text')
    starting_desc.set_justify(Gtk.Justification.CENTER)
    starting_desc.set_wrap(True)
    starting_desc.set_max_width_chars(88)
    starting.append(starting_desc)
    return starting


def build_intergen_page():
    """Page 5: Meet InterGen — the AI assistant.

    This is the tallest page (intro copy + a 6-item example grid + an
    opt-in disclosure + a summon hint). valign=FILL + scroll=True below
    lets it scroll from the top rather than centering past both window
    edges (which clipped the title under the header and the last
    paragraph off the bottom — GBC001 welcomer overflow).
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
    box.set_valign(Gtk.Align.FILL)
    box.set_halign(Gtk.Align.CENTER)
    box.set_margin_start(20)
    box.set_margin_end(20)
    box.set_margin_top(24)
    box.set_margin_bottom(24)

    title = Gtk.Label(label='Meet InterGen')
    title.add_css_class('welcome-title')
    box.append(title)

    # Let the copy WRAP to the (now wider) content box rather than hard-breaking
    # at fixed points — fewer lines, so the one-click setup card below lifts
    # into the initial view instead of sitting under the fold.
    subtitle = Gtk.Label(
        label='InterGen is your personal AI, built right into InterGenOS. '
              'He can help you find files, explain system settings, troubleshoot '
              'problems, and he\'ll remember the preferences you ask him to.\n\n'
              'Talk to him like you\'d talk to a colleague who knows his way '
              'around your system.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    subtitle.set_wrap(True)
    subtitle.set_max_width_chars(82)
    box.append(subtitle)

    # What this machine can run — asked ONCE, here, because the driver advisory
    # it may carry belongs at the top of the page rather than beside the button
    # at the bottom. A user who needs to install graphics drivers should learn
    # that before reading anything else on the page, not after scrolling past
    # the examples (decided 2026-07-31).
    offer = _model_offer()
    if offer and offer.get('advisory'):
        box.append(_build_driver_advisory())

    # The vendor driver / compute-engine offer, from the record the installer
    # wrote about this machine's display controller. It sits directly under the
    # advisory because it is the same subject: the advisory says the machine
    # would do better with the vendor's driver, and this is where that is
    # actually installed. Absent record, or hardware with nothing to add,
    # appends nothing.
    gpu_section = _build_gpu_install_offer(_gpu_detection_record())
    if gpu_section is not None:
        box.append(gpu_section)

    # Example prompts — two columns. No "Things you can ask:" label: the boxed
    # questions are self-evidently prompts, so the heading is inferred visually
    # and just costs vertical space the setup card needs.
    examples_left = [
        '"What\'s using my disk space?"',
        '"Set up a firewall"',
        '"Why is my WiFi dropping?"',
    ]

    # Example prompts are drawn from request forms the shipped models pass in
    # measured testing — swap only for other measured-passing forms.
    examples_right = [
        '"What\'s my IP address?"',
        '"Explain what this process does"',
        '"Is sshd running?"',
    ]

    columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    columns.set_homogeneous(True)

    for column_examples in [examples_left, examples_right]:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for example in column_examples:
            frame = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            frame.add_css_class('intergen-example')

            label = Gtk.Label(label=example)
            label.add_css_class('intergen-example-text')
            label.set_halign(Gtk.Align.START)
            frame.append(label)
            col.append(frame)
        columns.append(col)

    box.append(columns)

    # State detection (observed 2026-07-05; engine probe added 2026-07-11
    # after a post-install evaluation). On a Welcomer re-run where
    # InterGen is ALREADY set up, don't offer the setup button — but "set up"
    # (a model is in the store) is NOT "ready" (the engine is serving). The
    # store read is truthful about the download yet blind to the daemon, so the
    # old ready card claimed "look for his icon" while the panel extension —
    # which gates that icon on components.llama_server === true — kept it hidden
    # (the eval finding). So gate the ready card on the SAME engine signal, and
    # show an honest "starting" card until the engine is definitively up. First-
    # run onboarding is UNCHANGED: the opt-in disclosure + one-click setup card
    # below only build when NOT set up (this branch returns early).
    if _intergen_is_set_up():
        state_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        state_box.set_halign(Gtk.Align.CENTER)
        # `shown['state']` carries the currently-displayed state across probe
        # callbacks so an INCONCLUSIVE probe never flips an already-shown ready
        # card (extension.js _checkReady discipline; logic in _next_shown_state).
        shown = {'state': None}

        def _render(probe):
            new_state = _next_shown_state(shown['state'], probe)
            if new_state == shown['state']:
                return
            shown['state'] = new_state
            child = state_box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                state_box.remove(child)
                child = nxt
            state_box.append(_build_intergen_ready_card() if new_state == 'ready'
                             else _build_intergen_starting_card())

        # Honest default until a definitive ready arrives — mirrors the extension
        # starting with its icon HIDDEN and showing it only on a definitive ready.
        _render('down')
        box.append(state_box)

        def _poll():
            # GLib timers outlive widgets: stop once this page leaves the window
            # tree (Welcomer closed / page destroyed — GTK4 has no ::destroy).
            if state_box.get_root() is None:
                return GLib.SOURCE_REMOVE
            _intergen_engine_probe(_render)
            return GLib.SOURCE_CONTINUE

        # Probe now, then re-probe on a timer: the engine may still be warming up
        # while the user reads this page (first boot / just-finished setup), so
        # the card flips starting -> ready the moment llama_server comes up.
        _intergen_engine_probe(_render)
        # PyGObject override signature is (interval, function) — NOT the GJS
        # (priority, interval, function) the extension.js mirror uses.
        GLib.timeout_add_seconds(15, _poll)

        return wrap_with_background(box, 'intergen-bg', scroll=True)

    # Opt-in disclosure (D-010 posture: the AI does not run without
    # explicit user consent)
    opt_in = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    opt_in.add_css_class('intergen-summon')
    opt_in.set_halign(Gtk.Align.CENTER)
    opt_in.set_margin_top(8)

    opt_in_title = Gtk.Label(label='InterGen is opt-in — we don\'t run AI without your consent')
    opt_in_title.add_css_class('intergen-summon-key')
    opt_in.append(opt_in_title)

    opt_in_desc = Gtk.Label(
        label='Forge prompted you during installation: "Enable the InterGen AI assistant?" '
              '(default: NO). If you said YES, your service is already enabled. '
              'If you said NO, you can opt in any time — either by opening the '
              'InterGen AI app from your Applications menu, or with the '
              'one-click button below.'
    )
    opt_in_desc.add_css_class('intergen-summon-text')
    opt_in_desc.set_justify(Gtk.Justification.CENTER)
    opt_in_desc.set_wrap(True)
    opt_in_desc.set_max_width_chars(88)
    opt_in.append(opt_in_desc)

    box.append(opt_in)

    # One-click setup (Issue #2: no-terminal model-install onboarding).
    # Replaces the old "run intergen setup in a terminal" dead-end. Clicking
    # this IS the explicit opt-in gesture (D-010 consent posture); the streamed
    # output shows the chosen model + its license before the download proceeds.
    setup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    setup_box.add_css_class('intergen-summon')
    setup_box.set_halign(Gtk.Align.CENTER)
    setup_box.set_margin_top(8)

    setup_title = Gtk.Label(label='Ready to meet him? Set InterGen up now')
    setup_title.add_css_class('intergen-summon-key')
    setup_box.append(setup_title)

    setup_desc = Gtk.Label(
        label='One click downloads the local AI model that fits your hardware '
              '(about 4–5 GB; 5–30 minutes) and gets InterGen ready — no '
              'terminal needed. The model runs entirely on your machine; no '
              'conversation data ever leaves your computer. You\'ll be asked to '
              'authenticate once, and the model\'s license is shown as it installs.'
    )
    setup_desc.add_css_class('intergen-summon-text')
    setup_desc.set_justify(Gtk.Justification.CENTER)
    setup_desc.set_wrap(True)
    setup_desc.set_max_width_chars(88)
    setup_box.append(setup_desc)

    # Which model to install. The box reports what it can run; the person
    # decides (decided 2026-07-31). One runnable model = nothing to ask, so the
    # card stays exactly as it was. More than one = a real choice. An NVIDIA
    # card on the open-source driver reports no video memory, so capability
    # cannot be read at all — say so plainly and offer the 2B now or drivers
    # first, rather than silently installing the smallest model.
    # `offer` was read at the top of the page (the advisory banner is up there).
    chosen_tier = {'value': None}
    if offer:
        tiers = [int(t) for t in offer.get('tiers', []) if int(t) in _TIER_CHOICE_LABELS]
        if len(tiers) > 1:
            chosen_tier['value'] = tiers[0]
            choice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            choice_box.set_halign(Gtk.Align.CENTER)
            choice_box.set_margin_top(6)
            first_radio = None
            for t in tiers:
                title, note = _TIER_CHOICE_LABELS[t]
                radio = Gtk.CheckButton(label=f'{title} — {note}')
                if first_radio is None:
                    first_radio = radio
                    radio.set_active(True)
                else:
                    radio.set_group(first_radio)

                def _on_pick(button, tier_value=t):
                    if button.get_active():
                        chosen_tier['value'] = tier_value

                radio.connect('toggled', _on_pick)
                choice_box.append(radio)
            setup_box.append(choice_box)
        elif tiers:
            chosen_tier['value'] = tiers[0]

    setup_btn = Gtk.Button(label='Set up InterGen now')
    setup_btn.add_css_class('suggested-action')
    setup_btn.set_halign(Gtk.Align.CENTER)
    setup_btn.set_margin_top(4)
    setup_box.append(setup_btn)

    setup_status = Gtk.Label(label='')
    setup_status.add_css_class('intergen-summon-text')
    setup_status.set_justify(Gtk.Justification.CENTER)
    setup_status.set_wrap(True)
    setup_status.set_max_width_chars(70)
    setup_status.set_visible(False)
    setup_box.append(setup_status)

    # On completion, show EXACTLY what to look for: the brand-blue panel icon
    # the user will see in the top bar (top-right, next to the system icons).
    setup_done_hint = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    setup_done_hint.set_halign(Gtk.Align.CENTER)
    setup_done_hint.set_margin_top(6)
    setup_done_hint.set_visible(False)
    done_icon = Gtk.Image.new_from_icon_name('intergenos-symbolic')
    done_icon.set_pixel_size(28)
    done_icon.add_css_class('intergen-icon-preview')
    done_hint_label = Gtk.Label(
        label='Look for this icon in the top-right of your panel — click it anytime.')
    done_hint_label.add_css_class('intergen-summon-text')
    done_hint_label.set_wrap(True)
    done_hint_label.set_max_width_chars(48)
    setup_done_hint.append(done_icon)
    setup_done_hint.append(done_hint_label)
    setup_box.append(setup_done_hint)

    def _start_setup(btn):
        btn.set_sensitive(False)
        btn.set_label('Setting InterGen up…')
        setup_status.set_visible(True)
        setup_status.set_text('Starting setup…')

        def on_line(line):
            setup_status.set_text(line)

        def on_done(success, message=None):
            if success:
                btn.set_label('InterGen is ready ✓')
                setup_status.set_text(
                    'InterGen is ready — find him in your top panel.')
                setup_done_hint.set_visible(True)
            else:
                btn.set_sensitive(True)
                btn.set_label('Set up InterGen now')
                setup_status.set_text(
                    message or
                    'Setup didn\'t finish. You can try again, or run '
                    '"intergen setup" in a terminal.')

        _launch_intergen_setup(on_line, on_done, tier=chosen_tier['value'])

    def _on_setup_clicked(btn):
        """Gate the install behind an explicit choice when the advisory is on.

        Without this the button silently meant "install the 2B" on a machine
        whose capability could not be read — the user was told nothing at the
        moment they committed, and the choice they were supposed to be offered
        (drivers first, or the 2B now) existed only in the terminal flow. The
        dialog puts the same two options in front of them here, and installs
        nothing until one is picked (decided 2026-07-31).
        """
        if not (offer and offer.get('advisory')):
            _start_setup(btn)
            return
        dialog = Gtk.AlertDialog()
        dialog.set_modal(True)
        dialog.set_message(_ADVISORY_HEADING)
        dialog.set_detail(_ADVISORY_BODY_PLAIN)
        dialog.set_buttons(['Install NVIDIA drivers first',
                            'Continue with the 2B model'])
        dialog.set_default_button(0)
        dialog.set_cancel_button(0)

        def _answered(dlg, result):
            try:
                choice = dlg.choose_finish(result)
            except Exception:
                # Dismissed (Escape / closed) — the safe reading is "I did not
                # agree to anything", so nothing installs.
                choice = 0
            if choice == 1:
                _start_setup(btn)
                return
            setup_status.set_visible(True)
            setup_status.set_text(
                'Nothing was installed. Install NVIDIA\'s drivers, reboot, '
                'then open this page again to be offered the larger models.')

        dialog.choose(btn.get_root(), None, _answered)

    setup_btn.connect('clicked', _on_setup_clicked)
    box.append(setup_box)

    # AFTER THE DRIVER LEG, SETTING INTERGEN UP IS THE NEXT ACTION — so it is
    # put where the next action belongs, at the top of the page.
    #
    # The driver install ends in a reboot, and this page is deliberately shown
    # again afterwards so the user can carry on. But the setup card sits below
    # the opt-in disclosure and the model-choice block, which on a laptop
    # screen is below the fold: a user who has just rebooted arrives, sees
    # prose about a page they have already read, and has to scroll or press
    # Next to find the one thing they came back to do. Reordering it — rather
    # than adding a second button — keeps one control for one action, so there
    # is no way for two copies to disagree about state.
    #
    # Only in that state. On a first visit, with no driver installed, the
    # existing order is correct: the disclosure is meant to be read before the
    # button that acts on it.
    if _driver_leg_is_done():
        done_note = Gtk.Label(
            label='Your graphics driver is installed. The next step is to set '
                  'InterGen up.')
        done_note.add_css_class('intergen-summon-key')
        done_note.set_justify(Gtk.Justification.CENTER)
        done_note.set_wrap(True)
        done_note.set_max_width_chars(88)
        box.append(done_note)
        # reorder_child_after(child, None) moves the child to first position.
        box.reorder_child_after(setup_box, None)
        box.reorder_child_after(done_note, None)

    # Once enabled — where to find InterGen
    summon = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    summon.add_css_class('intergen-summon')
    summon.set_halign(Gtk.Align.CENTER)
    summon.set_margin_top(8)

    summon_icon = Gtk.Image.new_from_icon_name('intergenos-symbolic')
    summon_icon.set_pixel_size(30)
    summon_icon.add_css_class('intergen-icon-preview')
    summon_icon.set_halign(Gtk.Align.CENTER)
    summon.append(summon_icon)

    summon_title = Gtk.Label(label='This is InterGen\'s icon — once enabled, it lives in your top panel')
    summon_title.add_css_class('intergen-summon-key')
    summon.append(summon_title)

    summon_desc = Gtk.Label(
        label='Click it anytime to start a conversation. He\'s there, ready when you are.'
    )
    summon_desc.add_css_class('intergen-summon-text')
    summon_desc.set_justify(Gtk.Justification.CENTER)
    summon.append(summon_desc)

    box.append(summon)

    return wrap_with_background(box, 'intergen-bg', scroll=True)


def build_community_page():
    """Page 7: Documentation and community links — branded link cards (G3-14)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)

    title = Gtk.Label(label='You\'re Part of Something')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='InterGenOS is open source and built in the open.\n'
              'Read it, build it, make it better.'
    )
    subtitle.add_css_class('page-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    box.append(subtitle)

    cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    cards_box.set_halign(Gtk.Align.CENTER)
    cards_box.set_margin_top(8)

    # (icon, heading, description, url). Every name here must resolve in the
    # shipped InterGenOS -> Adwaita -> hicolor lookup chain; emblem-* symbolics
    # are pruned from modern Adwaita and render as image-missing (the wiki row
    # shipped that way on ge9b-11). The wiki row carries the first-party mark.
    links = [
        ('applications-engineering-symbolic', 'Source & Releases',
         'Every line is on GitHub — clone it, build it from scratch, follow development.',
         'https://github.com/InterGenJLU/intergenos'),
        ('dialog-warning-symbolic', 'Report an Issue',
         'Found something off? Every report makes the system more secure.',
         'https://github.com/InterGenJLU/intergenos/issues'),
        ('org.intergenos.Wiki', 'Documentation & Wiki',
         'Install guides, the build pipeline, and how every piece fits together.',
         'https://github.com/InterGenJLU/intergenos/wiki'),
    ]

    def _open_uri(url):
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except Exception:
            subprocess.Popen(['xdg-open', url])

    for icon_name, heading_text, desc_text, url in links:
        card = Gtk.Button()
        card.add_css_class('community-card')
        card.add_css_class('flat')
        card.set_can_focus(True)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.set_hexpand(True)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(26)
        icon.add_css_class('community-card-icon')
        icon.set_valign(Gtk.Align.CENTER)
        row.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        text_box.set_valign(Gtk.Align.CENTER)
        name_label = Gtk.Label(label=heading_text, xalign=0)
        name_label.add_css_class('community-card-heading')
        text_box.append(name_label)
        desc_label = Gtk.Label(label=desc_text, xalign=0)
        desc_label.add_css_class('community-card-desc')
        desc_label.set_wrap(True)
        desc_label.set_max_width_chars(50)
        text_box.append(desc_label)
        row.append(text_box)

        arrow = Gtk.Label(label='↗')  # ↗ — opens in your browser
        arrow.add_css_class('community-card-arrow')
        arrow.set_valign(Gtk.Align.START)
        row.append(arrow)

        card.set_child(row)
        card.connect('clicked', lambda b, u=url: _open_uri(u))
        cards_box.append(card)

    box.append(cards_box)

    creed = Gtk.Label(label='Security is not first — it is only.')
    creed.add_css_class('community-creed')
    creed.set_justify(Gtk.Justification.CENTER)
    creed.set_margin_top(6)
    box.append(creed)

    return wrap_with_background(box, 'community-bg', scroll=True)


def build_done_page():
    """Page 8: All set."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)

    title = Gtk.Label(label='You\'re All Set')
    title.add_css_class('welcome-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='Your InterGenOS desktop is configured and ready.\n\n'
              'Anything you chose here can be changed anytime — re-run '
              'this welcomer, or use Settings, the Extensions app, '
              'or the InterGen AI app from your Applications menu.\n\n'
              'Enjoy your machine.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    subtitle.set_wrap(True)
    subtitle.set_max_width_chars(70)
    box.append(subtitle)

    return wrap_with_background(box, 'done-bg', scroll=True)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class WelcomeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.intergenos.welcome')

    def do_activate(self):
        load_css()

        win = Adw.ApplicationWindow(application=self)
        win.set_title('InterGenOS')
        # 760x720 gives every page vertical headroom (font-scale variance,
        # the Meet InterGen page) while still fitting a 1366x768 laptop
        # under the GNOME top bar. The Meet InterGen page additionally
        # scrolls (wrap_with_background scroll=True) for any residual
        # overflow at large text scale.
        win.set_default_size(760, 720)
        win.add_css_class('welcome-window')

        # Main layout: navigation view
        nav = Adw.NavigationView()
        win.set_content(nav)

        # Build all pages
        pages = []

        # Page 1: Welcome
        welcome = build_welcome_page()
        pages.append(('Welcome', welcome))

        # Page 2: Appearance
        appearance = build_appearance_page()
        pages.append(('Appearance', appearance))

        # Page 3: Layout (Windows-style taskbar vs GNOME top bar) — directly
        # after the Theme page per the G3-20 greenlit design; Windows default.
        layout = build_layout_page()
        pages.append(('Layout', layout))

        # Page 4: Extensions
        extensions = build_extensions_page()
        pages.append(('Extensions', extensions))

        # Page 4: Shortcuts
        shortcuts = build_shortcuts_page()
        pages.append(('Shortcuts', shortcuts))

        # Enable Services (opt-in print / discovery / SSH toggles — privileged
        # via pkexec). After Shortcuts, before the Prompt/Meet-InterGen pages.
        services = build_services_page()
        pages.append(('Enable Services', services))

        # Finding Websites — which name server this machine uses. After the
        # service toggles because it is the same kind of thing (a system
        # setting the user may want to change), and before the pages about
        # InterGen because a machine that cannot look names up cannot download
        # his model, which is the failure that brings people here.
        dns = build_dns_page()
        pages.append(('Finding Websites', dns))

        # Page 5: Prompt (Stock vs Starship — D-014 ratified 2026-05-20)
        prompt = build_prompt_page()
        pages.append(('Prompt', prompt))

        # Page 6: Meet InterGen
        # Short header title — 'Introducing your system AI' was truncated in
        # the header bar (wordmark + Next button squeeze the centered title
        # slot), so this stays well under the length that fits.
        #
        # Changed 2026-07-31 from 'Meet InterGen', which repeated the page's
        # own H1 verbatim and spent the header on a word the reader has just
        # read. 'AI Assistant' says what the page is about to someone who does
        # not yet know the product name, and it stays accurate once InterGen is
        # set up and the page shows its ready state instead of a setup card —
        # which a 'Setup' wording would not.
        intergen = build_intergen_page()
        pages.append(('AI Assistant', intergen))

        # Page 6: Community
        community = build_community_page()
        pages.append(('Community', community))

        # Page 7: Done
        done = build_done_page()
        pages.append(('Done', done))

        # Build navigation pages with Next/Back buttons
        # Header-bar wordmark crop (170x45). The full
        # /usr/share/intergenos/intergenos_wordmark_transparent.png is
        # too tall and trips a Gtk.Picture+ContentFit.CONTAIN measure
        # assertion in libadwaita when its aspect is shorter than the
        # widget slot. intergen-mark ships the header crop at this path.
        wordmark_path = '/usr/share/intergenos/intergenos_wordmark_header.png'

        for i, (title, content) in enumerate(pages):
            page = Adw.NavigationPage()
            page.set_title(title)

            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            header.add_css_class('welcome-header')
            header.add_css_class('flat')

            # Wordmark in the header bar's left-aligned slot via
            # pack_start (decided 2026-05-22: left-aligned).
            # pack_start leaves the centered title-widget slot free, so
            # each page's title text still renders in the center per
            # Adw.HeaderBar default behavior. Gtk.Picture handles HiDPI.
            if os.path.exists(wordmark_path):
                wordmark = Gtk.Picture.new_for_filename(wordmark_path)
                wordmark.set_content_fit(Gtk.ContentFit.CONTAIN)
                # Native aspect 170:45 = 3.778; sits just above the
                # widget's 170:46 = 3.696 so CONTAIN is width-bound.
                wordmark.set_size_request(170, 46)
                # can_shrink(False) pins the minimum at the requested size
                # (PI-11). All 8 page headers are built upfront, before the
                # window is sized; without this the header Picture gets a
                # transient 0x0 allocation and ContentFit.CONTAIN snapshots a
                # degenerate rect — a flood of "pixman_region32_init_rect:
                # Invalid rectangle passed" (dozens of repeats, one wave per
                # page) and a relayout churn that pegged CPU at first boot on
                # 1366x768. The crop is shown at ~native size, so refusing to
                # shrink is render-neutral.
                wordmark.set_can_shrink(False)
                wordmark.add_css_class('welcome-header-wordmark')
                header.pack_start(wordmark)

            # Navigation buttons
            if i < len(pages) - 1:
                next_btn = Gtk.Button(label='Next')
                next_btn.add_css_class('suggested-action')

                def on_next(btn, idx=i):
                    # Apply the extension picks when leaving the page that
                    # carries them.
                    #
                    # Found by identity, not by position. This used to read
                    # pages[2] and fire on idx == 2, which was the Extensions
                    # page when it was written and stopped being it the day
                    # the Layout page was inserted ahead of it. Nothing failed
                    # loudly: the Layout widget has no _collect_extensions, so
                    # the hasattr guard turned the whole thing into a silent
                    # no-op and every extension toggle on that page went
                    # nowhere. Asking the widget what it is removes the class
                    # rather than re-fixing the number (corrected 2026-08-04).
                    leaving_widget = pages[idx][1]
                    if hasattr(leaving_widget, '_collect_extensions'):
                        set_enabled_extensions(
                            leaving_widget._collect_extensions())

                    next_page_title = pages[idx + 1][0]
                    for child_page in nav_pages:
                        if child_page.get_title() == next_page_title:
                            nav.push(child_page)
                            break

                next_btn.connect('clicked', on_next)
                header.pack_end(next_btn)
            else:
                # Last page: Close button
                close_btn = Gtk.Button(label='Get Started')
                close_btn.add_css_class('suggested-action')
                close_btn.connect('clicked', lambda b: self.quit())
                header.pack_end(close_btn)

            if i == 0:
                skip_btn = Gtk.Button(label='Skip Setup')
                skip_btn.connect('clicked', lambda b: self.quit())
                header.pack_start(skip_btn)

            toolbar.add_top_bar(header)
            toolbar.set_content(content)
            page.set_child(toolbar)
            pages[i] = (title, content, page)

        nav_pages = [p[2] for p in pages]
        nav.push(nav_pages[0])

        # ---- Auto-surface the Finding Websites page ----
        # The ruled behaviour: when name resolution is what failed, this page
        # is shown by itself, with the cause named. It is what makes this a
        # fix rather than a settings panel — the user who needs it is exactly
        # the user who has no way of knowing it exists, because from where
        # they are sitting the machine simply does not work.
        #
        # The probe runs on a worker thread: it does name lookups and TCP
        # connects with timeouts, and the interface has to stay responsive
        # while it does. The result arrives back on the main loop.
        dns_page_title = 'Finding Websites'
        dns_nav_page = next(p for p in nav_pages
                            if p.get_title() == dns_page_title)

        def _surface(cause):
            if hasattr(dns, '_show_resolution_cause'):
                dns._show_resolution_cause(cause)
            if nav.get_visible_page() is not dns_nav_page:
                nav.push(dns_nav_page)
            return False

        global _surface_dns_page
        _surface_dns_page = _surface

        def _startup_probe():
            probe = _probe_download_sources(hosts=_STARTUP_PROBE_HOSTS)
            if netdiag.cause_is_name_resolution(probe.cause):
                GLib.idle_add(_surface, probe.cause)

        threading.Thread(target=_startup_probe, daemon=True).start()

        win.present()


def main():
    app = WelcomeApp()
    app.run()


if __name__ == '__main__':
    main()
