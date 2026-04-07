# Complete GNOME Theme Package & Icon Pack Research

**Date:** 2026-04-02
**Purpose:** Understand every component of a complete GNOME desktop theme, from bootloader to desktop, with emphasis on zero-fallback icon coverage.
**Method:** Examination of installed themes (Adwaita, Yaru, hicolor) on Ubuntu 25.04 host, BLFS 13.0 documentation, and freedesktop specifications.

---

## Table of Contents

1. [GTK Theme (Widget Theme)](#1-gtk-theme-widget-theme)
2. [Icon Theme](#2-icon-theme)
3. [Cursor Theme](#3-cursor-theme)
4. [Sound Theme](#4-sound-theme)
5. [GNOME Shell Theme](#5-gnome-shell-theme)
6. [Plymouth Boot Theme](#6-plymouth-boot-theme)
7. [GDM (Login Screen) Theme](#7-gdm-login-screen-theme)
8. [GRUB Theme](#8-grub-theme)
9. [rEFInd Theme](#9-refind-theme)
10. [AI Art (FLUX) for Theming](#10-ai-art-flux-for-theming)
11. [Zero-Fallback Icon Strategy](#11-zero-fallback-icon-strategy)

---

## 1. GTK Theme (Widget Theme)

### Directory Structure

GTK themes live in one of these locations:
- `/usr/share/themes/ThemeName/` (system-wide)
- `~/.themes/ThemeName/` (per-user, legacy)
- `~/.local/share/themes/ThemeName/` (per-user, XDG)

A complete GTK theme directory:

```
ThemeName/
├── index.theme              # Metatheme descriptor (links GTK, icon, cursor, shell)
├── gtk-2.0/                 # GTK2 theme (legacy apps)
│   ├── gtkrc                # Main GTK2 resource file
│   ├── main.rc              # Widget styling
│   ├── apps.rc              # Per-app overrides
│   ├── hacks.rc             # Compatibility hacks
│   └── assets/              # PNG images for buttons, scrollbars, checkboxes, etc.
│       ├── checkbox-checked.png
│       ├── radio-checked.png
│       ├── scrollbar-*.png
│       └── ... (50-100+ PNGs for GTK2)
├── gtk-3.0/                 # GTK3 theme
│   ├── gtk.css              # Main stylesheet
│   ├── gtk-dark.css         # Dark variant
│   ├── thumbnail.png        # Theme preview (optional)
│   └── assets/              # SVG/PNG assets referenced by CSS
├── gtk-4.0/                 # GTK4 theme
│   ├── gtk.css              # Main stylesheet (or resource import)
│   ├── gtk-dark.css         # Dark variant
│   └── assets/              # SVG/PNG assets
├── gnome-shell/             # GNOME Shell theme (sometimes bundled)
│   ├── gnome-shell.css
│   ├── pad-osd.css
│   └── *.svg                # Shell-specific assets
├── metacity-1/              # Window decoration theme (Mutter/Metacity)
│   ├── metacity-theme-3.xml # Window frame definition
│   ├── close_focused_normal.svg
│   ├── maximize_focused_normal.svg
│   ├── minimize_focused_normal.svg
│   └── ... (SVGs for each button state: normal, prelight, pressed x focused/unfocused)
└── xfwm4/                   # Xfce window manager (if supporting multi-DE)
```

### GTK3 Theming

GTK3 themes are pure CSS. The main file is `gtk-3.0/gtk.css`. It styles all standard widgets:
- Buttons, entries, labels, spinbuttons
- Notebooks (tabs), treeviews, listboxes
- Scrollbars, sliders, progressbars
- Menus, popovers, tooltips
- Dialogs, headerbars, sidebars
- Checkboxes, radio buttons, switches

**Configuration:** `~/.config/gtk-3.0/settings.ini` or `/etc/gtk-3.0/settings.ini`:
```ini
[Settings]
gtk-theme-name = Adwaita
gtk-icon-theme-name = Adwaita
gtk-font-name = DejaVu Sans 12
gtk-cursor-theme-name = Adwaita
gtk-cursor-theme-size = 24
gtk-xft-antialias = 1
gtk-xft-hinting = 1
gtk-xft-hintstyle = hintslight
gtk-xft-rgba = rgb
```

**Note on Adwaita:** Since GTK 3.14, Adwaita is built directly into libgtk-3. The file at `/usr/share/themes/Adwaita/gtk-3.0/gtk.css` literally contains `/* Adwaita is now part of GTK+ 3, this file is no longer used */`. The theme engine is compiled into GTK itself. Third-party themes still work by overriding via CSS.

### GTK4 Theming and the libadwaita Problem

**GTK4 themes work the same way structurally** -- `gtk-4.0/gtk.css` -- but there is a critical distinction:

**libadwaita (GNOME 42+):** Applications that use libadwaita (most modern GNOME apps) have the Adwaita stylesheet *compiled into the library*. libadwaita explicitly ignores GTK4 themes by design. The GNOME project's position is that widget themes break accessibility, cause visual bugs, and increase maintenance burden on app developers.

**What this means for theming GTK4 apps:**
- **Apps using plain GTK4** (not libadwaita): Fully themeable via `gtk-4.0/gtk.css`
- **Apps using libadwaita:** The Adwaita stylesheet is hardcoded. CSS overrides require one of:
  - Setting `GTK_THEME=YourTheme` environment variable (officially unsupported, increasingly broken)
  - Patching the `gtk.css` embedded in libadwaita's GResource at build time
  - Using libadwaita's built-in accent color/dark mode API (limited, intentionally constrained)
  - Flatpak override: `flatpak override --filesystem=~/.themes` (fragile)

**Practical reality as of GNOME 46/47:** You can still do color scheme changes (light/dark) and accent colors through libadwaita's API. Full widget restyling (border radius, padding, shadows, completely different aesthetics) is actively resisted by GNOME upstream. The `GTK_THEME` variable still works for many apps but produces visual glitches with increasing frequency as libadwaita evolves.

**For InterGenOS:** Since we build from source, we have the nuclear option: patch libadwaita at build time to load an external stylesheet instead of (or in addition to) its compiled default. This is what distros like Fedora Silverblue theme projects effectively do. It is maintenance burden but totally achievable.

### GTK Theme Build Tools

- **sassc** -- Compiles SCSS to CSS. Most serious GTK themes are authored in SCSS (Sass) for variables, mixins, and modularity, then compiled to the final `gtk.css`. The Adwaita source uses SCSS extensively.
- **gtk-css** -- Not a separate tool; GTK's CSS engine is a subset of web CSS with GTK-specific extensions (custom properties, `-gtk-` prefixed properties like `-gtk-icon-size`, `-gtk-icon-style`, etc.)
- **Inkscape** -- For creating SVG assets (checkboxes, radio buttons, switches, etc.)
- **meson** -- Modern GNOME themes (Yaru, Adwaita) use meson build systems to compile SCSS, bundle GResources, generate variants

### Metacity/Mutter Window Decorations

The `metacity-1/` directory contains:
- `metacity-theme-1.xml`, `metacity-theme-2.xml`, `metacity-theme-3.xml` -- XML definition of window frame geometry, colors, gradients
- SVGs for window buttons in every state combination:
  - Actions: close, maximize, unmaximize, minimize, menu
  - States: focused/unfocused x normal/prelight/pressed
  - That's 5 actions x 2 focus states x 3 interaction states = **30 SVG button assets**

In GNOME 45+, Mutter uses its own internal rendering for window decorations and largely ignores metacity themes for CSD (client-side decoration) apps. Server-side decorations still use them for legacy X11 apps.

---

## 2. Icon Theme

### The Freedesktop Icon Theme Specification

The icon theme spec (maintained at freedesktop.org) defines:
- How icon themes are structured on disk
- How icon lookup works (name resolution, size matching, fallback chain)
- What contexts (categories) icons belong to
- How inheritance works between themes

**Key file:** `index.theme` -- The manifest describing every directory in the theme, its size, type, and context.

### Directory Structure

```
/usr/share/icons/ThemeName/
├── index.theme              # Theme manifest (REQUIRED)
├── icon-theme.cache         # Binary cache for fast lookup (generated by gtk-update-icon-cache)
├── cursor.theme             # Cursor inheritance pointer (optional, for bundled cursors)
├── cursors/                 # Cursor files (if bundled)
│
│ # Fixed-size raster directories (PNG)
├── 16x16/
│   ├── actions/
│   ├── apps/
│   ├── categories/
│   ├── devices/
│   ├── emblems/
│   ├── emotes/
│   ├── mimetypes/
│   ├── places/
│   └── status/
├── 22x22/
│   └── ... (same categories)
├── 24x24/
│   └── ...
├── 32x32/
│   └── ...
├── 48x48/
│   └── ...
├── 64x64/
│   └── ...
├── 96x96/
│   └── ...
├── 128x128/
│   └── ...
├── 256x256/
│   └── ...
├── 512x512/
│   └── ...
│
│ # HiDPI variants (2x scale)
├── 16x16@2x/
│   └── ... (32px PNGs displayed at 16 logical pixels)
├── 22x22@2x/
│   └── ...
│ ... (every size can have a @2x variant)
│
│ # Scalable vector directories (SVG)
├── scalable/
│   ├── actions/
│   ├── apps/
│   ├── categories/
│   ├── devices/
│   ├── emblems/
│   ├── emotes/
│   ├── mimetypes/
│   ├── places/
│   └── status/
│
│ # Symbolic icons (monochrome, recolorable)
├── symbolic/
│   ├── actions/
│   ├── apps/
│   ├── categories/
│   ├── devices/
│   ├── emblems/
│   ├── emotes/
│   ├── mimetypes/
│   ├── places/
│   ├── status/
│   ├── ui/
│   └── legacy/
│
│ # Symbolic icons with max rendered size
├── symbolic-up-to-32/       # Adwaita-specific: symbolic icons only used <= 32px
│   └── status/
│
│ # Legacy/compatibility icons
└── legacy/
```

### index.theme Format

```ini
[Icon Theme]
Name=ThemeName
Comment=Description
Example=folder
Inherits=hicolor           # Fallback chain -- CRITICAL

# KDE metadata (optional but good practice)
DisplayDepth=32
DesktopDefault=48
ToolbarDefault=22
MainToolbarDefault=22
SmallDefault=16
PanelDefault=32

# Every directory must be listed
Directories=16x16/actions,16x16/apps,...,scalable/actions,...,symbolic/actions,...

[16x16/actions]
Context=Actions
Size=16
Type=Fixed

[scalable/actions]
Context=Actions
Size=16
MinSize=8
MaxSize=512
Type=Scalable

[symbolic/actions]
Context=Actions
Size=16
MinSize=8
MaxSize=512
Type=Scalable
```

**Directory types:**
- `Fixed` -- Icons are exactly this size (raster)
- `Scalable` -- SVG icons, with MinSize/MaxSize range
- `Threshold` -- Fixed size but acceptable within a threshold (default: 2px)

### Icon Contexts (Categories)

The freedesktop spec defines these standard contexts:

| Context | Purpose | Examples |
|---------|---------|----------|
| **Actions** | Toolbar/menu action icons | edit-copy, document-save, go-next, zoom-in |
| **Applications** | Application launcher icons | firefox, org.gnome.Nautilus, utilities-terminal |
| **Categories** | Application category icons | preferences-system, applications-multimedia |
| **Devices** | Hardware device icons | drive-harddisk, input-keyboard, printer |
| **Emblems** | Overlay badge icons | emblem-important, emblem-readonly, emblem-symbolic-link |
| **Emotes** | Emoticon/emoji icons | face-smile, face-sad, face-cool |
| **MimeTypes** | File type icons | text-plain, image-jpeg, application-pdf |
| **Places** | Filesystem location icons | folder, user-home, network-server, user-trash |
| **Status** | State/status indicator icons | battery-full, network-wireless, audio-volume-high |
| **UI** | UI element icons (GNOME-specific) | pan-down, window-close, checkbox-checked |
| **Legacy** | Deprecated/compatibility icons | Older icon names kept for backward compat |
| **Animations** | Animated icons (hicolor spec) | process-working (spinner frames) |
| **International** | Locale-specific icons | Input method indicators |

### Required Icon Sizes

For a complete theme with zero visual gaps:

**Mandatory sizes (what the desktop actually requests):**
- **16x16** -- Menus, small list views, panel indicators
- **22x22** -- Toolbar icons (GNOME's standard toolbar size)
- **24x24** -- Panel icons, some toolbars
- **32x32** -- Large toolbar, some dialogs
- **48x48** -- File manager grid view, app chooser
- **64x64** -- Larger grid views
- **128x128** -- Large previews
- **256x256** -- App info pages, about dialogs

**Nice to have:**
- **8x8** -- Tiny emblems (Yaru has this)
- **96x96** -- Some KDE apps request this
- **512x512** -- Very large previews, software center

**HiDPI:** Every fixed size needs a `@2x` variant for HiDPI displays (so 16x16@2x contains 32px PNGs rendered at 16 logical pixels).

**Scalable (SVG):** Covers all sizes with vector scaling. Having a good scalable/ directory means you technically only need fixed sizes for pixel-hinting at small sizes (16, 22, 24).

**Symbolic:** A complete set of monochrome symbolic icons. These are SVGs where the fill color is `#2e3436` which GTK replaces at runtime with the current foreground color. This is how icons adapt to light/dark themes automatically.

### Icon Counts -- What "Complete" Looks Like

Measured from installed themes on this system:

**Adwaita (GNOME's default):**
- Total unique icon files: ~771
- Scalable: 70 (devices:22, mimetypes:27, places:16, status:5)
- Symbolic: 649 (actions:182, apps:1, categories:20, devices:65, emblems:11, emotes:26, legacy:50, mimetypes:20, places:18, status:231, ui:25)
- 16x16: 51 (devices:2, emblems:5, mimetypes:27, places:17)
- Adwaita is **NOT a complete theme** -- it focuses on symbolic icons and defers app icons entirely to hicolor fallback

**Yaru (Ubuntu's theme):**
- Total unique icon files: **5,542**
- Scalable categories: actions:193, apps:100, camera:8, categories:49, devices:78, emblems:24, emotes:27, mimetypes:19, places:17, status:239, ui:26, plus app-specific directories
- Sizes: 8x8, 16x16, 22x22, 24x24, 32x32, 48x48, 256x256 + all @2x variants + scalable
- Yaru inherits from Humanity, which inherits from hicolor

**Papirus (popular third-party, not installed but well-documented):**
- 5,000-7,000+ unique icon names
- One of the most complete themes available
- Covers most popular Linux applications individually

**hicolor (the universal fallback):**
- Sizes: 16-512 + scalable + @2x for all
- 134 app icons in scalable/apps on this system (installed by individual apps)
- hicolor is where apps install their own icons; it is NOT a visual theme

### How Icon Inheritance/Fallback Works

The lookup chain when an app requests an icon:

1. Look in the current theme (e.g., "InterGenOS") at the requested size
2. Look in the current theme at other sizes (scaled up/down per directory type rules)
3. Look in the inherited theme(s) -- `Inherits=hicolor` in index.theme
4. Look in hicolor (the mandatory universal fallback)
5. If still not found, look for a file directly matching the icon name in the theme's top-level directories
6. If STILL not found, the app gets a broken/missing icon placeholder

**Yaru's chain:** Yaru -> Humanity -> hicolor
**Adwaita's chain:** Adwaita -> hicolor

### Regular Icons vs Symbolic Icons

**Regular icons:**
- Full-color, detailed artwork
- Used in file managers, app grids, about dialogs, large UI contexts
- SVG or PNG format
- Sizes: 16px up to 512px
- Each icon is hand-crafted art at its intended display size

**Symbolic icons (suffix: `-symbolic`):**
- Monochrome, simple, geometric
- Drawn in a single color (conventionally `#2e3436`)
- GTK automatically recolors them to match the current text color
- Used in toolbars, headerbars, panel indicators, small UI contexts
- GNOME 40+ uses symbolic icons almost exclusively in the shell and core apps
- SVGs only (no raster symbolic icons)
- The symbolic icon has the same name as its regular counterpart plus `-symbolic` suffix (e.g., `document-save-symbolic.svg`)
- Apps request `-symbolic` explicitly when they want the monochrome version

**Critical insight:** Modern GNOME apps overwhelmingly use symbolic icons in their UI. The regular (full-color) icons are primarily used for:
- Application icons (in the app grid, alt-tab, etc.)
- File type icons in Nautilus
- Device icons in Settings

### Image Formats

- **SVG** -- Used for scalable/ and symbolic/ directories. Best practice for all new icons.
- **PNG** -- Used for fixed-size directories (16x16, 22x22, etc.). Necessary for pixel-perfect hinting at small sizes. Should be pre-rendered from SVG sources.
- **XPM** -- Legacy format, not used in modern themes.
- **icon-theme.cache** -- Binary cache generated by `gtk-update-icon-cache`. Must be regenerated after any icon changes. Dramatically speeds up icon lookup.

---

## 3. Cursor Theme

### What Makes a Cursor Theme

Cursors live in the icon theme's `cursors/` subdirectory or in a standalone cursor theme package.

**Location:**
- `/usr/share/icons/ThemeName/cursors/` (system-wide, bundled with icon theme)
- `~/.icons/ThemeName/cursors/` (per-user)
- `/usr/share/icons/ThemeName/cursor.theme` (inheritance pointer)

**cursor.theme file:**
```ini
[Icon Theme]
Inherits=Adwaita
```

### Cursor File Format

Each cursor is a binary file in the Xcursor format (no extension). Created by `xcursorgen` from source PNG frames and a config file.

**xcursorgen config format:**
```
# size  xhot yhot  filename  [delay_ms]
24      12   12    cursor24.png
32      16   16    cursor32.png
48      24   24    cursor48.png
```

Multiple sizes are embedded in a single cursor file. The system picks the closest match to the requested cursor size.

### Required Cursor Names

Adwaita ships **59 cursors**. Yaru ships **296 files** (but many are hash-named aliases using X11 cursor name hashing).

**Essential cursors (the core set every theme needs):**

| Cursor | Purpose |
|--------|---------|
| `default` / `left_ptr` | Normal arrow pointer |
| `text` / `xterm` | Text selection I-beam |
| `pointer` / `hand2` | Clickable link/button |
| `grab` / `grabbing` | Drag handle / actively dragging |
| `move` / `fleur` | Move/reposition |
| `crosshair` / `cross` | Precision select |
| `not-allowed` / `no-drop` | Forbidden action |
| `help` / `question_arrow` | Help/context help |
| `wait` / `watch` | Busy spinner |
| `progress` | Busy + pointer (background activity) |
| `context-menu` | Right-click menu available |
| `cell` | Spreadsheet cell select |
| `vertical-text` | Vertical text selection |
| `alias` | Create alias/shortcut |
| `copy` | Copy cursor (drag-copy) |
| `col-resize` / `row-resize` | Column/row resize handles |
| `n-resize`, `s-resize`, `e-resize`, `w-resize` | Edge resize |
| `ne-resize`, `nw-resize`, `se-resize`, `sw-resize` | Corner resize |
| `ns-resize`, `ew-resize`, `nesw-resize`, `nwse-resize` | Bidirectional resize |
| `all-scroll` | Omnidirectional scroll |
| `zoom-in` / `zoom-out` | Magnifier cursors |
| `top_left_corner`, `top_right_corner`, `bottom_left_corner`, `bottom_right_corner` | Window corner resize |
| `top_side`, `bottom_side`, `left_side`, `right_side` | Window edge resize |

**Many "cursors" are actually symlinks/aliases.** X11 uses hash-based naming (`00008160000006810000408080010102`) alongside human-readable names. A cursor theme typically has 20-30 unique cursors and 100+ symlinks mapping all the legacy names.

### Required Cursor Sizes

Standard sizes embedded in each cursor file:
- **24px** -- Standard (1x) displays
- **32px** -- 1.25x-1.5x scaling
- **48px** -- 2x HiDPI displays
- **64px** -- 2.5x-3x scaling
- **96px** -- 4x scaling (optional, nice for accessibility)

All sizes are embedded in a single Xcursor file; the system selects the best match.

### X11 vs Wayland Cursors

**X11:** Uses Xcursor format, looked up from `~/.icons/*/cursors/` or `XCURSOR_PATH`. The cursor name lookup uses a two-step process: try the string name, then try the X11 cursor font hash.

**Wayland:** Uses the exact same Xcursor file format and directory structure. The `libwayland-cursor` and `xcursor` libraries read the same theme directories. There is **no difference in the theme files**. The protocol-level handling is different (Wayland compositors handle cursor rendering, X11 uses the X server), but theme authors produce identical files for both.

**Animated cursors:** Supported on both. Xcursorgen supports frame delays. Common for `wait`/`watch`/`progress` cursors.

---

## 4. Sound Theme

### The XDG Sound Theme Specification

Mirrors the icon theme spec but for sounds. Defined at freedesktop.org.

**Location:**
- `/usr/share/sounds/ThemeName/` (system-wide)
- `~/.local/share/sounds/ThemeName/` (per-user)

### Structure

```
ThemeName/
├── index.theme              # Theme manifest
└── stereo/                  # Output profile directory
    ├── alert.oga
    ├── bell.oga
    ├── complete.oga
    └── ...
```

**index.theme:**
```ini
[Sound Theme]
Name=ThemeName
Directories=stereo

[stereo]
OutputProfile=stereo
```

### Required Sounds for a Complete Theme

The freedesktop sound naming spec defines these categories:

**Alert sounds:**
- `bell` -- Terminal bell / general alert
- `dialog-error`, `dialog-warning`, `dialog-information`, `dialog-question`

**Notification sounds:**
- `message-new-instant`, `message-new-email`
- `complete` -- Operation finished
- `alarm-clock-elapsed`

**Action feedback:**
- `trash-empty`
- `camera-shutter`
- `screen-capture`

**Device events:**
- `device-added`, `device-removed`
- `battery-low`, `power-plug`, `power-unplug`

**System events:**
- `service-login`, `service-logout`
- `suspend-error`

**Audio channel identification:**
- `audio-channel-front-left`, `audio-channel-front-right`, etc.
- `audio-volume-change`
- `audio-test-signal`

**Phone (optional):**
- `phone-incoming-call`, `phone-outgoing-busy`, `phone-outgoing-calling`

The freedesktop sound theme ships **27 sound files** covering these. A custom theme would want at minimum the alert, notification, action, device, and system categories (~15-20 sounds).

### Format

- **OGA** (Ogg Vorbis Audio) is the standard format
- Can also use WAV, FLAC
- Short sounds, typically 0.5-3 seconds
- 44.1kHz or 48kHz sample rate

---

## 5. GNOME Shell Theme

### What It Is

The GNOME Shell theme controls the entire desktop chrome *outside* of application windows:
- Top panel (activities button, clock, system indicators)
- Overview (workspace thumbnails, app grid, search)
- Dash/dock
- Notifications and message tray
- OSD (on-screen display: volume, brightness)
- Lock screen
- Login dialog
- Screen shield
- System modals (polkit, Wi-Fi password, etc.)
- Alt-tab / window switcher
- Screenshot UI
- Pad OSD (drawing tablet)

### Files and Structure

**Standalone theme (in a GTK theme):**
```
ThemeName/gnome-shell/
├── gnome-shell.css          # Main stylesheet (~3,000-5,000+ lines for a complete theme)
├── pad-osd.css              # Styling for drawing tablet OSD (optional, sometimes inlined)
├── toggle-on.svg            # Switch/toggle assets
├── toggle-off.svg
├── checkbox.svg             # Shell-specific checkbox assets
├── checkbox-focused.svg
├── checkbox-off.svg
├── calendar-today.svg       # Calendar date highlight
├── dash-placeholder.svg     # Empty dash slot
├── process-working.svg      # Activity spinner
├── running-indicator.svg    # Running app indicator dot
├── gnome-shell-start.svg    # Shell startup graphic
├── workspace-placeholder.svg
├── gnome-shell-icons.gresource  # Compiled GResource bundle of shell icons
└── gnome-shell-theme.gresource  # Compiled GResource bundle of entire theme
```

**System default:** `/usr/share/gnome-shell/gnome-shell-theme.gresource` is a compiled GResource (binary) containing the default GNOME Shell CSS and assets. Custom themes override this.

### How It Relates to GTK Theme

They are **independent**. The GNOME Shell theme uses a CSS dialect based on St (Shell Toolkit), which is similar to but NOT identical to GTK CSS. Key differences:
- St CSS uses `StWidget`, `StButton`, `StLabel`, etc. as selectors
- Shell CSS uses element IDs (`#panel`, `#dash`, `#overview`, `#lockDialogGroup`)
- Shell CSS has its own set of custom properties
- Transition/animation syntax differs slightly

A GTK theme and GNOME Shell theme are typically designed together for visual consistency but are separate files loaded by separate rendering engines.

### What Can Be Customized

**Everything visible in the shell:**
- Panel: height, background color/gradient/transparency, text styling, indicator spacing
- Clock: font, position, format
- Activities button: shape, color, hover effects
- System indicators: icon style, spacing, popup menus
- Overview: background, search bar, workspace thumbnail styling
- App grid: icon size, spacing, folder appearance
- Dash: background, icon spacing, running indicator style
- Notifications: card style, colors, spacing, blur effects
- OSD: volume/brightness popup appearance
- Lock screen: clock font, unlock dialog, background
- Login dialog: user list, authentication prompts
- Alt-tab: switcher window appearance
- Workspace switcher: animation style, thumbnail appearance

### Building a GNOME Shell Theme

Shell themes are either:
1. **Raw CSS** placed in the theme directory (loaded by User Themes extension or Tweaks)
2. **Compiled GResource** -- the CSS and all SVG assets are bundled into a binary GResource file using `glib-compile-resources`. This is what GNOME ships as default.

For a custom distro theme, the GResource approach is better for performance and distribution.

---

## 6. Plymouth Boot Theme

### What It Is

Plymouth shows the boot splash screen (between GRUB/bootloader and the login screen). It runs in kernel DRM mode, providing graphical boot feedback.

### Theme Structure

```
/usr/share/plymouth/themes/ThemeName/
├── ThemeName.plymouth        # Theme configuration file
├── watermark.png             # Distro logo / branding watermark
├── bgrt-fallback.png         # Background if UEFI BGRT is unavailable
│
│ # For "two-step" module (most common)
├── animation-0001.png        # Boot animation frames
├── animation-0002.png
├── ...                       # Typically 30-40 frames
├── animation-0036.png
├── throbber-0001.png         # Password prompt spinner frames
├── throbber-0002.png
├── ...
├── throbber-0030.png
│
│ # Password dialog assets
├── bullet.png                # Password character bullet
├── entry.png                 # Text entry field background
├── lock.png                  # Lock icon
├── capslock.png              # Caps lock indicator
├── keyboard.png              # On-screen keyboard icon
├── keymap-render.png         # Keyboard layout indicator
│
│ # For "script" module (alternative)
└── ThemeName.script          # Plymouth scripting language for animations
```

### Plymouth Configuration File (.plymouth)

```ini
[Plymouth Theme]
Name=InterGenOS
Description=InterGenOS boot splash
ModuleName=two-step          # or "script", "fade-in", "details", "text"

[two-step]
Font=Cantarell 12
TitleFont=Cantarell Light 30
ImageDir=/usr/share/plymouth/themes/intergenos
DialogHorizontalAlignment=.5
DialogVerticalAlignment=.382
TitleHorizontalAlignment=.5
TitleVerticalAlignment=.382
HorizontalAlignment=.5
VerticalAlignment=.7
WatermarkHorizontalAlignment=.5
WatermarkVerticalAlignment=.96
Transition=none
TransitionDuration=0.0
BackgroundStartColor=0x000000
BackgroundEndColor=0x000000
ProgressBarBackgroundColor=0x606060
ProgressBarForegroundColor=0xffffff
MessageBelowAnimation=true

[boot-up]
UseEndAnimation=false

[shutdown]
UseEndAnimation=false

[reboot]
UseEndAnimation=false

[updates]
SuppressMessages=true
ProgressBarShowPercentComplete=true
UseProgressBar=true
Title=Installing Updates...
SubTitle=Do not turn off your computer
```

### Plymouth Module Types

- **two-step** -- The standard module. Shows animation frames, password dialog, progress bar. Most distros use this. (Fedora, Ubuntu, Arch default)
- **script** -- A scripting language for custom animations. More flexible but harder to author. Can do sprite animations, particle effects, etc.
- **fade-in** -- Simple fade-in of a logo with a star/dot spinner
- **details** -- Text-only boot messages (like verbose boot)
- **text** -- Minimal text spinner

### Image Requirements

- **Animation frames:** PNG, any resolution. The spinner in Adwaita uses 36 frames at 64x64px. Ubuntu uses 66 frames. More frames = smoother animation.
- **Watermark:** PNG, typically the distro logo. Placed at the bottom center by default. Transparent background.
- **Background:** Supports solid color, gradient (start/end color), or an image. The BGRT module uses the UEFI firmware logo.
- **Password dialog assets:** Fixed-size PNGs for entry field, bullet, lock icon, etc.

### Activation

```bash
plymouth-set-default-theme intergenos
dracut -f   # or update-initramfs -u (rebuild initramfs)
```

The theme must be included in the initramfs to be available at boot.

---

## 7. GDM (Login Screen) Theme

### Can GDM Be Themed Separately?

**Technically yes, practically it is tied to GNOME Shell.**

GDM runs a special GNOME Shell session (gnome-shell --mode=gdm). It loads the same GNOME Shell theme system. The login screen IS GNOME Shell with a restricted set of enabled features.

### How GDM Theming Works

**The GResource approach (how distros do it):**
GDM loads `/usr/share/gnome-shell/gnome-shell-theme.gresource`. To theme GDM, you replace or extend this GResource with your CSS and assets. This affects both GDM and the logged-in Shell session unless you use separate GResources.

**Configuration:**
- `/etc/gdm3/custom.conf` -- GDM configuration (not theming, but things like auto-login, Wayland enable)
- `/etc/gdm3/greeter.dconf-defaults` -- dconf database overrides for the greeter session
- GDM's dconf profile controls the icon theme, GTK theme, background, etc. for the login screen

**What you can customize:**
- Login screen background (image or color)
- Logo/branding on the login screen
- Font used for user list and authentication
- Color scheme of login dialog elements
- Clock position and format
- Disable user list (show username entry instead)
- Login dialog button styling

**Practical approach for a distro:**
1. Build a custom `gnome-shell-theme.gresource` that includes your GDM CSS
2. Set GDM's dconf defaults to use your icon theme, GTK theme, cursor theme
3. Set the background via dconf: `org.gnome.desktop.background picture-uri`
4. The GDM session uses a separate user account (`gdm`) with its own dconf database

**Important limitation:** GDM in GNOME 43+ runs on Wayland by default and uses the Shell's lock screen CSS classes for the login dialog. You cannot run arbitrary GTK apps on the GDM greeter -- it is a constrained environment.

---

## 8. GRUB Theme

### What Makes a GRUB Theme

GRUB themes provide a graphical boot menu. Without a theme, GRUB shows a basic text menu on a black background.

### Theme Structure

```
/boot/grub/themes/ThemeName/
├── theme.txt                # Theme layout and styling definition
├── background.png           # Background image (or .jpg, .tga)
├── font_name.pf2            # GRUB font file(s) (converted from TTF/OTF)
│
│ # Menu styling assets
├── menu_*.png               # 9-slice images for menu box (optional)
│   ├── menu_n.png           # North (top border)
│   ├── menu_s.png           # South (bottom border)
│   ├── menu_e.png           # East (right border)
│   ├── menu_w.png           # West (left border)
│   ├── menu_ne.png          # Corners
│   ├── menu_nw.png
│   ├── menu_se.png
│   ├── menu_sw.png
│   └── menu_c.png           # Center (background tile)
│
│ # Selection highlight
├── select_*.png             # 9-slice for selected item highlight
│
│ # Scrollbar (optional)
├── scrollbar_*.png          # Scrollbar thumb and track
│
│ # Boot icons (optional)
├── icons/
│   ├── linux.png            # Linux penguin
│   ├── windows.png          # Windows logo
│   ├── memtest.png          # Memtest icon
│   └── ...
│
│ # Progress bar (optional)
├── progress_bar_*.png
│
│ # Terminal styling
└── terminal_*.png
```

### theme.txt Format

```
# Global settings
title-text: ""
desktop-image: "background.png"
desktop-color: "#000000"
terminal-font: "DejaVu Sans Regular 14"
terminal-left: "0"
terminal-top: "0"
terminal-width: "100%"
terminal-height: "100%"

# Boot menu
+ boot_menu {
    left = 25%
    top = 30%
    width = 50%
    height = 50%
    item_font = "DejaVu Sans Regular 16"
    item_color = "#cccccc"
    selected_item_font = "DejaVu Sans Bold 16"
    selected_item_color = "#ffffff"
    item_height = 30
    item_padding = 5
    item_spacing = 10
    icon_width = 32
    icon_height = 32
    scrollbar = true
    scrollbar_width = 20
    scrollbar_thumb = "scrollbar_thumb_*.png"
    menu_pixmap_style = "menu_*.png"
    selected_item_pixmap_style = "select_*.png"
}

# Clock (optional)
+ hbox {
    left = 80%
    top = 5%
    + label {
        text = "@KEYMAP@"
        font = "DejaVu Sans Regular 12"
        color = "#ffffff"
    }
}

# Label for countdown
+ label {
    left = 25%
    top = 85%
    text = "Booting in %d seconds"
    font = "DejaVu Sans Regular 12"
    color = "#999999"
}
```

### Font Conversion

GRUB cannot use TTF/OTF directly. Fonts must be converted:
```bash
grub-mkfont -s 16 -o font16.pf2 /path/to/DejaVuSans.ttf
grub-mkfont -s 24 -o font24.pf2 /path/to/DejaVuSans.ttf
```

### Image Constraints

- **Background:** PNG, JPEG, or TGA. Resolution should match the target display. GRUB's graphics mode is typically set via `GRUB_GFXMODE` in `/etc/default/grub` (e.g., `1920x1080x32`). Common practice: provide 1920x1080 and let GRUB scale.
- **9-slice images:** Small PNGs used to create scalable bordered boxes. The center tile repeats.
- **Icons:** Typically 32x32 or 64x64 PNG.

### Activation

```bash
# In /etc/default/grub:
GRUB_THEME="/boot/grub/themes/ThemeName/theme.txt"
GRUB_GFXMODE="1920x1080x32"

# Then:
grub-mkconfig -o /boot/grub/grub.cfg
```

---

## 9. rEFInd Theme

### What Makes a rEFInd Theme

rEFInd is a UEFI boot manager (runs before GRUB). Themes control the OS selection screen shown at the firmware level.

### Theme Structure

```
/boot/efi/EFI/refind/themes/ThemeName/
├── theme.conf               # Theme configuration
├── background.png           # Background image
├── icons/                   # OS selection icons
│   ├── os_linux.png         # Linux icon
│   ├── os_win.png           # Windows icon
│   ├── os_mac.png           # macOS icon
│   ├── os_unknown.png       # Unknown OS fallback
│   ├── tool_shell.png       # EFI shell
│   ├── tool_memtest.png     # Memtest
│   ├── func_about.png       # About/info
│   ├── func_reset.png       # Reboot
│   ├── func_shutdown.png    # Shutdown
│   ├── func_firmware.png    # UEFI firmware setup
│   └── arrow_left.png       # Navigation arrows
│       arrow_right.png
├── icons_dark/              # Dark mode variants (optional)
│   └── ...
├── selection-big.png        # Highlight for selected OS icon
├── selection-small.png      # Highlight for selected tool icon
└── font.png                 # Bitmap font sprite sheet (or use built-in)
```

### theme.conf Format

```
# Colors
selection_big   selection-big.png
selection_small selection-small.png
banner          background.png
banner_scale    fillscreen    # or noscale, fillscreen

# Icon sizes
big_icon_size   128           # OS selector icons
small_icon_size 48            # Tool icons

# Fonts
font            font.png      # or omit for built-in

# Screen layout
showtools       shell, memtest, firmware
timeout         5
use_graphics_for linux,windows

# Icon directory
icons_dir       themes/ThemeName/icons
```

### Image Requirements

- **Background:** PNG. Resolution should match common UEFI framebuffer resolutions. rEFInd supports `banner_scale fillscreen` to auto-scale. Provide at least 1920x1080.
- **OS icons:** PNG with transparency. 128x128 for big icons, 48x48 for tools. rEFInd supports 2x variants by appending `_2x` suffix.
- **Selection highlight:** PNG with transparency, sized to frame the icon (e.g., 144x144 for a 128px icon).
- **Fonts:** Either a PNG bitmap font sprite sheet or use rEFInd's built-in font.

### Integration with InterGenOS

rEFInd runs at UEFI firmware level, before any OS loads. The theme must be installed to the EFI System Partition (`/boot/efi/`). It is the very first visual branding the user sees.

---

## 10. AI Art (FLUX) for Theming

### Which Elements Benefit from AI-Generated Art

| Element | AI Art Suitability | Notes |
|---------|-------------------|-------|
| **Wallpapers** | Excellent | Highest-impact, most visible, infinite variety |
| **GRUB background** | Excellent | Single image, dramatic branding opportunity |
| **rEFInd background** | Excellent | First visual impression of the distro |
| **Plymouth watermark/logo** | Good | Logo should be crisp; FLUX can generate the artistic base |
| **Plymouth background** | Good | Typically solid/gradient, but image backgrounds possible |
| **GDM login background** | Excellent | Major branding surface |
| **Lock screen background** | Excellent | High-visibility, user-facing |
| **Icon base art** | Moderate | Can generate concepts; needs manual cleanup for pixel-perfect icons |
| **Application-specific splash screens** | Good | Installer backgrounds, about dialogs, etc. |
| **Cursor art** | Poor | Too small, too precise; cursors need pixel-exact work |
| **Sound design** | N/A | FLUX is image-only; sound needs different tools |

### Resolution and Format Requirements by Element

| Element | Minimum Resolution | Recommended | Format |
|---------|--------------------|-------------|--------|
| Wallpapers | 1920x1080 | 3840x2160 (4K), provide multiple ratios | PNG or WebP (lossless) |
| GRUB background | 1920x1080 | 1920x1080 (GRUB doesn't do 4K well) | PNG or JPEG |
| rEFInd background | 1920x1080 | 3840x2160 for HiDPI | PNG |
| Plymouth watermark | 200x200+ | 512x512 (centered on screen) | PNG with transparency |
| Plymouth background | 1920x1080 | 1920x1080 (initramfs size concern) | PNG |
| GDM login background | 1920x1080 | 3840x2160 | PNG, JPEG, or WebP |
| Lock screen | 1920x1080 | 3840x2160 | PNG or JPEG |
| Icon base art | 512x512+ | 1024x1024 (downscale for quality) | PNG with transparency |

### SVG Generation from FLUX Output

**Can FLUX output be vectorized for scalable icons?**

Not directly. FLUX outputs raster images (PNG). To get SVGs:

1. **Auto-trace tools:** `potrace`, `autotrace`, `Inkscape's Trace Bitmap` can convert raster to vector. Works well for:
   - Simple, high-contrast shapes (logos, symbolic icons)
   - Silhouettes and geometric designs
   Works poorly for:
   - Photorealistic images (produces massive, ugly SVGs)
   - Fine gradients and details

2. **Practical workflow for icons:**
   - Use FLUX to generate icon concepts at high resolution (1024x1024+)
   - Use the AI output as reference art, not final product
   - Manually redraw in Inkscape as clean SVG with proper structure
   - For symbolic icons: trace to get the basic shape, then manually clean up
   - For full-color app icons: the FLUX output can be used directly as PNG in fixed-size directories, but scalable/ needs hand-drawn SVG

3. **Best approach for a distro icon theme:**
   - Use FLUX to establish the visual language (generate 50-100 concept icons)
   - Define a consistent style guide from the generated concepts
   - Hand-draw the actual icons following the style guide
   - For symbolic icons: These MUST be hand-drawn SVGs (monochrome, geometrically precise)

---

## 11. Zero-Fallback Icon Strategy

### The Goal

Every icon visible on the desktop has a themed version from the InterGenOS icon theme. No hicolor fallback icons visible. No missing icon placeholders.

### Why This Is Hard

**The problem is application icons.** There are two categories:

1. **Desktop infrastructure icons** (actions, status, devices, places, mimetypes, categories, emblems) -- These are defined by the freedesktop naming spec. There is a finite, documented list. A theme can cover 100% of these.

2. **Application icons** -- Each application defines its own icon name (e.g., `org.gnome.Nautilus`, `firefox`, `gimp`). Applications install their icon into hicolor. There are **thousands** of possible application icons, and new ones appear with every new app installed.

### Which Apps Ship Their Own Icons vs Rely on the Theme

**Apps that ship their own icons (install to hicolor):**
Most modern apps install at least one icon into `/usr/share/icons/hicolor/`. On this system, 134 apps have scalable icons in hicolor. These include:
- GNOME core apps (Nautilus, Terminal, TextEditor, Calculator, Settings, etc.)
- Firefox, LibreOffice, GIMP, VLC, etc.
- System utilities (NetworkManager, IBus, etc.)

**Apps that rely on the theme:**
- Very few modern apps do this. The convention since ~2015 is to ship your own icon.
- Some legacy apps reference generic icon names like `utilities-terminal` or `text-editor`
- Some apps reference icons from the `actions/` or `status/` context (e.g., `edit-preferences`, `help-browser`)

**The icon name an app uses is declared in its `.desktop` file:**
```
Icon=org.gnome.Nautilus
```

### Strategy for Zero Fallback

**Phase 1: Infrastructure icons (achievable, finite)**

Cover every icon name in these contexts:
- **actions** (~200 standard names): document-new, edit-copy, edit-paste, edit-undo, edit-redo, view-refresh, go-previous, go-next, zoom-in, zoom-out, process-stop, list-add, list-remove, system-log-out, system-shutdown, etc.
- **status** (~250 standard names): battery-*, network-*, audio-volume-*, weather-*, mail-*, user-*, etc.
- **devices** (~80 standard names): drive-harddisk, input-keyboard, input-mouse, printer, camera-*, etc.
- **places** (~20 standard names): folder, folder-documents, folder-downloads, user-home, user-trash, network-server, etc.
- **mimetypes** (~30 standard names): text-plain, text-html, image-*, audio-*, video-*, application-pdf, application-x-executable, etc.
- **categories** (~50 standard names): applications-multimedia, preferences-desktop, preferences-system, etc.
- **emblems** (~15 standard names): emblem-important, emblem-readonly, emblem-symbolic-link, etc.
- **emotes** (~30 standard names): face-smile, face-sad, face-laugh, etc.

**Total for Phase 1: ~675 unique icon names, each needing:**
- 1 scalable SVG (covers all sizes)
- 1 symbolic SVG (for -symbolic variant)
- Optionally pixel-hinted PNGs at 16, 22, 24, 32, 48

This is the ~700-icon core that makes the desktop feel "themed."

**Phase 2: Application icons (the long tail)**

For every application included in the InterGenOS default install:
1. List all installed .desktop files
2. Extract every `Icon=` value
3. Check if that icon name exists in the InterGenOS icon theme
4. For any missing: create a themed version

For a typical GNOME desktop + common apps, this is approximately:
- GNOME core apps: ~40-50 icons
- System utilities and settings panels: ~20-30 icons
- Default applications (browser, email, office, etc.): ~10-20 icons
- **Total for default install: ~80-100 application icons**

**Phase 3: Ecosystem coverage (Papirus-level ambition)**

To cover popular Linux applications beyond the default install:
- Popular CLI tools with .desktop files: ~50
- Popular GUI applications: ~200-500
- Flatpak/Snap common apps: ~200-500
- **This is where themes like Papirus get to 5,000+ icons**

For InterGenOS, Phase 3 is a community/ongoing effort, not a launch blocker.

### Auditing for Missing Icons

**Method 1: Runtime audit script**
```bash
# Find all icon names referenced by installed .desktop files
grep -rh "^Icon=" /usr/share/applications/ ~/.local/share/applications/ | \
    sort -u | sed 's/^Icon=//' > /tmp/needed_icons.txt

# Check which ones exist in our theme
while read icon; do
    if ! find /usr/share/icons/InterGenOS -name "${icon}.*" | grep -q .; then
        echo "MISSING: $icon"
    fi
done < /tmp/needed_icons.txt
```

**Method 2: GTK icon audit tool**
```bash
# gtk4-icon-browser (ships with GTK4) shows all named icons
# and can highlight which come from fallback themes
gtk4-icon-browser
```

**Method 3: Icon diff against hicolor**
```bash
# List all icons installed in hicolor by packages (these are app-provided)
find /usr/share/icons/hicolor -type f -name "*.svg" -o -name "*.png" | \
    xargs -I{} basename {} | sed 's/\..*//' | sort -u > /tmp/hicolor_icons.txt

# Compare against our theme
find /usr/share/icons/InterGenOS -type f -name "*.svg" -o -name "*.png" | \
    xargs -I{} basename {} | sed 's/\..*//' | sort -u > /tmp/our_icons.txt

comm -23 /tmp/hicolor_icons.txt /tmp/our_icons.txt > /tmp/missing_from_theme.txt
```

**Method 4: Live visual audit**
Run GNOME with `GTK_DEBUG=icontheme` environment variable. GTK will log icon lookup failures to stderr, showing every icon that fell back to hicolor or was missing entirely.

### The Realistic Scope

For the InterGenOS default desktop (GNOME + our chosen default apps):

| Category | Icon Count | Effort |
|----------|-----------|--------|
| Standard freedesktop icons (all contexts) | ~675 | Major effort, but finite |
| Symbolic variants of above | ~675 | Derive from regular icons |
| GNOME core app icons | ~50 | Moderate |
| InterGenOS custom app icons | ~10-20 | AI-assisted for InterGen apps |
| System panel / status icons | ~50 | Part of the standard set |
| **Total for zero-fallback on default install** | **~1,450-1,500** | |

This is less than Adwaita (~770) + Papirus (~5,000-7,000), because we are only targeting zero-fallback for the default install, not for every Linux app in existence.

### Recommended Icon Theme Design

Based on all of the above:

```
/usr/share/icons/InterGenOS/
├── index.theme
├── scalable/            # Full-color SVGs -- the master source
│   ├── actions/         # ~200 icons
│   ├── apps/            # ~70-100 icons (default install apps)
│   ├── categories/      # ~50 icons
│   ├── devices/         # ~80 icons
│   ├── emblems/         # ~15 icons
│   ├── emotes/          # ~30 icons
│   ├── mimetypes/       # ~30 icons
│   ├── places/          # ~20 icons
│   └── status/          # ~250 icons
├── symbolic/            # Monochrome recolorable SVGs
│   ├── actions/         # Mirror of scalable with -symbolic suffix
│   ├── apps/
│   ├── categories/
│   ├── devices/
│   ├── emblems/
│   ├── mimetypes/
│   ├── places/
│   ├── status/
│   ├── ui/              # GTK UI elements (pan arrows, checkboxes, etc.)
│   └── legacy/          # Deprecated names for backward compat
├── 16x16/               # Pixel-hinted PNGs for small sizes
│   └── ... (key contexts: actions, apps, mimetypes, places, status)
├── 22x22/               # Toolbar size
│   └── ...
├── 24x24/               # Panel size
│   └── ...
├── 32x32/               # Dialog size
│   └── ...
├── 48x48/               # File manager grid
│   └── ...
├── 256x256/             # Large previews / software center
│   └── ...
├── cursors/             # Cursor theme (see Section 3)
└── cursor.theme
```

With `Inherits=hicolor` as safety net, but the goal is to never need it for anything visible on the default desktop.

---

## Summary: The Complete InterGenOS Theme Stack

| Layer | What | Files/Assets | Effort Level |
|-------|------|-------------|-------------|
| rEFInd | UEFI boot selector | 1 background, ~10 OS icons, 2 selection PNGs, theme.conf | Low |
| GRUB | Bootloader menu | 1 background, ~20 9-slice PNGs, 1+ fonts (.pf2), theme.txt | Low |
| Plymouth | Boot splash | 30-40 animation PNGs, logo, password dialog assets, .plymouth | Medium |
| GDM | Login screen | GNOME Shell theme CSS + dconf overrides + background image | Medium (shared with Shell) |
| GNOME Shell | Desktop chrome | 1 large CSS file (~5,000 lines), ~15 SVG assets, GResource | High |
| GTK3 | Application widgets | 1 large CSS file (SCSS source), ~100 PNG assets (GTK2), index.theme | High |
| GTK4/libadwaita | Modern app widgets | 1 CSS file (may need libadwaita patching at build time) | High (political) |
| Metacity | Window decorations | 30 SVG button assets, 3 XML theme files | Medium |
| Icons | All icons | ~1,500 SVGs + derived PNGs, index.theme, cache | Very High |
| Cursors | Mouse pointers | ~25 unique cursors + ~35 symlinks, multiple sizes per cursor | Medium |
| Sounds | System sounds | ~20 OGA audio files, index.theme | Low |

**Total unique art assets for a complete theme: ~1,700-2,000 files**
**Total including derived sizes/formats: ~5,000-8,000 files**

The icon theme is by far the largest single effort. The GNOME Shell CSS and GTK CSS are the most technically complex. The bootloader/boot splash themes are the most immediately visible to a new user.

---

## Open Questions for Planning

1. **libadwaita strategy:** Patch at build time to respect external themes? Or accept Adwaita for GTK4/libadwaita apps and only theme GTK3, Shell, and non-libadwaita GTK4 apps?
2. **Icon theme scope:** Zero-fallback for default install only (Phase 1+2, ~1,500 icons) or aim broader?
3. **SCSS framework:** Build our own from scratch or fork an existing theme (Yaru, Adwaita, Colloid, etc.) as starting point?
4. **Cursor source format:** Create from scratch in Inkscape or adapt an existing cursor theme (most are GPL/CC)?
5. **Plymouth module:** Use two-step (simpler, PNG frames) or script (more flexible, custom animations)?
6. **Sound design:** Create original sounds, adapt from freedesktop defaults, or commission?
