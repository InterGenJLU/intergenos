#!/usr/bin/env python3
"""
InterGenOS Welcome — First-boot greeter
GTK4 / libadwaita native application

Flows naturally from the boot animation:
  ECG pulse → "Hello." → "Shall we get started?" → fade → this greeter
"""

import gi
import subprocess
import json
import os

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango


# ---------------------------------------------------------------------------
# CSS — background images, gradients, and visual polish
# ---------------------------------------------------------------------------

# Image paths — replace with FLUX-generated art when ready.
# For now, uses pure gradient backgrounds as elegant placeholders.
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backgrounds')

CUSTOM_CSS = """
/* ---- Base window ---- */
window.welcome-window {
    background: #0a0a0a;
}

/* ---- Page background layers ---- */
/* Each page gets a gradient placeholder. When FLUX art arrives,
   add: background-image: url('backgrounds/welcome.png');
   The gradient sits on TOP of the image as a readability scrim. */

.welcome-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.3) 0%,
            rgba(10, 10, 10, 0.85) 60%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(30, 60, 120, 0.4) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

.appearance-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.3) 0%,
            rgba(10, 10, 10, 0.85) 60%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(80, 40, 120, 0.3) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

.extensions-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.3) 0%,
            rgba(10, 10, 10, 0.85) 60%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(20, 80, 60, 0.3) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

.shortcuts-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.3) 0%,
            rgba(10, 10, 10, 0.85) 60%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(100, 60, 20, 0.3) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

.community-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.3) 0%,
            rgba(10, 10, 10, 0.85) 60%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(40, 80, 120, 0.3) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

.intergen-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.2) 0%,
            rgba(10, 10, 10, 0.8) 55%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(20, 50, 90, 0.5) 0%,
            rgba(10, 30, 50, 0.3) 50%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

/* ---- InterGen conversation examples ---- */
.intergen-example {
    background: rgba(40, 50, 70, 0.4);
    border-radius: 8px;
    border-left: 2px solid rgba(100, 160, 255, 0.6);
    padding: 8px 12px;
    margin: 3px 0;
}

.intergen-example-text {
    color: rgba(255, 255, 255, 0.85);
    font-style: italic;
    font-size: 12px;
}

/* ---- InterGen summon hint ---- */
.intergen-summon {
    background: rgba(50, 70, 100, 0.35);
    border-radius: 10px;
    padding: 14px 20px;
    margin-top: 12px;
}

.intergen-summon-key {
    font-size: 13px;
    font-weight: 600;
    color: rgba(100, 170, 255, 0.95);
}

.intergen-summon-text {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.7);
}

.intergen-name {
    font-size: 20px;
    font-weight: 600;
    color: rgba(100, 170, 255, 0.95);
    text-shadow: 0 1px 6px rgba(50, 100, 200, 0.4);
}

.done-bg {
    background:
        linear-gradient(180deg,
            rgba(10, 10, 10, 0.2) 0%,
            rgba(10, 10, 10, 0.7) 50%,
            rgba(10, 10, 10, 1.0) 100%),
        linear-gradient(135deg,
            rgba(30, 90, 50, 0.4) 0%,
            rgba(10, 10, 10, 0.9) 100%);
    background-size: cover;
    background-position: center;
}

/* ---- Title styling ---- */
.welcome-title {
    font-size: 28px;
    font-weight: 700;
    color: white;
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
    line-height: 1.4;
}

.welcome-subtitle {
    font-size: 15px;
    color: rgba(255, 255, 255, 0.8);
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.5);
    line-height: 1.6;
    margin-top: 8px;
}

/* ---- Page titles over backgrounds ---- */
.page-title {
    font-size: 24px;
    font-weight: 700;
    color: white;
    text-shadow: 0 2px 6px rgba(0, 0, 0, 0.5);
    line-height: 1.4;
    margin-bottom: 4px;
}

.page-subtitle {
    color: rgba(255, 255, 255, 0.7);
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
    line-height: 1.6;
    margin-bottom: 8px;
}

/* ---- Semi-transparent preference groups over backgrounds ---- */
.transparent-group {
    background: rgba(30, 30, 30, 0.6);
    border-radius: 12px;
    padding: 4px;
}

/* ---- Theme preview thumbnail ---- */
.theme-preview {
    border-radius: 8px;
    border: 2px solid rgba(255, 255, 255, 0.15);
    min-width: 160px;
    min-height: 100px;
    background: rgba(40, 40, 40, 0.5);
}

.theme-preview-active {
    border-color: @accent_color;
    border-width: 2px;
}

/* ---- Theme row with preview ---- */
.theme-row-box {
    padding: 8px;
}

/* ---- Navigation buttons ---- */
.nav-next {
    padding: 8px 24px;
}

/* ---- Header bar transparency ---- */
.welcome-header {
    background: transparent;
    box-shadow: none;
    border: none;
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

def wrap_with_background(content, css_class):
    """Wrap a content widget in a background overlay container."""
    overlay = Gtk.Overlay()
    overlay.set_vexpand(True)
    overlay.set_hexpand(True)

    # Background layer (receives the gradient/image CSS)
    bg = Gtk.Box()
    bg.set_vexpand(True)
    bg.set_hexpand(True)
    bg.add_css_class(css_class)
    overlay.set_child(bg)

    # Content on top
    overlay.add_overlay(content)

    return overlay


def build_welcome_page():
    """Page 1: Welcome — brand moment."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)
    box.set_margin_top(48)
    box.set_margin_bottom(48)

    title = Gtk.Label(label='Welcome to InterGenOS')
    title.add_css_class('welcome-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='Your system is ready. Let\'s make it yours.\n\n'
              'The next few steps will help you choose your look,\n'
              'pick your tools, and learn the shortcuts.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    box.append(subtitle)

    return wrap_with_background(box, 'welcome-bg')


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

    def collect_extensions():
        result = {'user-theme@gnome-shell-extensions.gcampax.github.com'}
        for uuid, row in switches.items():
            if row.get_active():
                result.add(uuid)
        return result

    box._collect_extensions = collect_extensions
    wrapped = wrap_with_background(box, 'extensions-bg')
    wrapped._collect_extensions = collect_extensions
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

    # Spacer pushes columns to center of remaining space
    spacer_top = Gtk.Box()
    spacer_top.set_vexpand(True)
    box.append(spacer_top)

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

    box.append(columns)

    # Bottom spacer to balance vertical centering of columns
    spacer_bottom = Gtk.Box()
    spacer_bottom.set_vexpand(True)
    box.append(spacer_bottom)

    return wrap_with_background(box, 'shortcuts-bg')


def build_intergen_page():
    """Page 5: Meet InterGen — the AI assistant."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)
    box.set_margin_start(48)
    box.set_margin_end(48)
    box.set_margin_top(32)
    box.set_margin_bottom(32)

    title = Gtk.Label(label='Meet InterGen')
    title.add_css_class('welcome-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='InterGen is your personal AI, built right into InterGenOS.\n'
              'He can help you find files, explain system settings,\n'
              'troubleshoot problems, and learn your preferences over time.\n\n'
              'Talk to him like you\'d talk to a colleague who happens\n'
              'to know everything about your entire system.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    box.append(subtitle)

    # Example prompts — two columns
    examples_label = Gtk.Label(label='Things you can ask:')
    examples_label.add_css_class('page-subtitle')
    examples_label.set_margin_top(12)
    examples_label.set_halign(Gtk.Align.START)
    box.append(examples_label)

    examples_left = [
        '"What\'s using my disk space?"',
        '"Set up a firewall"',
        '"Why is my WiFi dropping?"',
    ]

    examples_right = [
        '"Help me install dev tools"',
        '"Explain what this process does"',
        '"Update my system packages"',
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

    # How to summon InterGen
    summon = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    summon.add_css_class('intergen-summon')
    summon.set_halign(Gtk.Align.CENTER)

    summon_title = Gtk.Label(label='Look for the InterGen icon in your top panel')
    summon_title.add_css_class('intergen-summon-key')
    summon.append(summon_title)

    summon_desc = Gtk.Label(
        label='Click it anytime to start a conversation. He\'s always there, ready when you are.'
    )
    summon_desc.add_css_class('intergen-summon-text')
    summon_desc.set_justify(Gtk.Justification.CENTER)
    summon.append(summon_desc)

    box.append(summon)

    return wrap_with_background(box, 'intergen-bg')


def build_community_page():
    """Page 7: Documentation and community links."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)

    title = Gtk.Label(label='You\'re Part of Something')
    title.add_css_class('page-title')
    box.append(title)

    subtitle = Gtk.Label(
        label='InterGenOS is open source and community-driven.\n'
              'Here\'s where to find us.'
    )
    subtitle.add_css_class('page-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    box.append(subtitle)

    link_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    link_box.set_halign(Gtk.Align.CENTER)
    link_box.set_margin_top(16)

    links = [
        ('GitHub', 'https://github.com/InterGenJLU/intergenos'),
        ('Report Issues', 'https://github.com/InterGenJLU/intergenos/issues'),
        ('Documentation', 'https://github.com/InterGenJLU/intergenos/wiki'),
    ]

    for label_text, url in links:
        button = Gtk.LinkButton.new_with_label(url, label_text)
        link_box.append(button)

    box.append(link_box)

    return wrap_with_background(box, 'community-bg')


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
              'Everything you chose can be changed anytime\n'
              'in Settings or the Extensions app.\n\n'
              'Enjoy your machine.'
    )
    subtitle.add_css_class('welcome-subtitle')
    subtitle.set_justify(Gtk.Justification.CENTER)
    box.append(subtitle)

    return wrap_with_background(box, 'done-bg')


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
        win.set_default_size(720, 640)
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

        # Page 3: Extensions
        extensions = build_extensions_page()
        pages.append(('Extensions', extensions))

        # Page 4: Shortcuts
        shortcuts = build_shortcuts_page()
        pages.append(('Shortcuts', shortcuts))

        # Page 5: Meet InterGen
        intergen = build_intergen_page()
        pages.append(('Introducing your system AI', intergen))

        # Page 6: Community
        community = build_community_page()
        pages.append(('Community', community))

        # Page 7: Done
        done = build_done_page()
        pages.append(('Done', done))

        # Build navigation pages with Next/Back buttons
        for i, (title, content) in enumerate(pages):
            page = Adw.NavigationPage()
            page.set_title(title)

            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            header.add_css_class('welcome-header')
            header.add_css_class('flat')

            # Navigation buttons
            if i < len(pages) - 1:
                next_btn = Gtk.Button(label='Next')
                next_btn.add_css_class('suggested-action')

                def on_next(btn, idx=i):
                    # Apply extensions when leaving extensions page
                    ext_widget = pages[2][1]
                    if idx == 2 and hasattr(ext_widget, '_collect_extensions'):
                        exts = ext_widget._collect_extensions()
                        set_enabled_extensions(exts)

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

        win.present()


def main():
    app = WelcomeApp()
    app.run()


if __name__ == '__main__':
    main()
