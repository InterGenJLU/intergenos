

# GNOME Shell Theme Architecture — Exhaustive Research for InterGenOS

## 1. GNOME Shell Theme Architecture

### Files That Make Up a GNOME Shell Theme

A GNOME Shell theme is fundamentally simple in structure. The minimum viable theme consists of:

- **`gnome-shell.css`** — The single most important file. This is the stylesheet that controls the entire shell UI: panel, overview, notifications, dialogs, lock screen, OSD, and every other shell element.
- **`gnome-shell-high-contrast.css`** — Optional high-contrast variant for accessibility.
- **SVG/PNG assets** — Icons, toggle switches, checkbox graphics, radio buttons, close buttons, process-working spinners, and other graphical elements referenced by the CSS.
- **`pad-osd.css`** — Styles for drawing tablet OSD (if applicable).
- **`gnome-shell-start.svg`** — The startup animation/splash graphic.

### Directory Structure

```
~/.themes/InterGenOS/gnome-shell/
├── gnome-shell.css              # Main stylesheet
├── gnome-shell-high-contrast.css # Accessibility variant (optional)
├── toggle-on.svg                # Toggle switch ON state
├── toggle-off.svg               # Toggle switch OFF state  
├── checkbox.svg                 # Checkbox states
├── checkbox-off.svg
├── checkbox-focused.svg
├── radio.svg                    # Radio button states
├── radio-off.svg
├── process-working.svg          # Loading spinner
├── no-events.svg                # "No notifications" icon
├── no-notifications.svg
├── calendar-today.svg           # Calendar marker
├── close.svg                    # Close button
├── close-window.svg             # Alt+F4 close symbol
├── eye-open-negative.svg        # Password reveal
├── eye-not-looking-symbolic.svg
├── gnome-shell-start.svg        # Startup splash
├── pad-osd.css
└── icons/
    └── scalable/
        └── status/
            └── (status icons as SVGs)
```

System-wide installation path: `/usr/share/themes/InterGenOS/gnome-shell/`

For GNOME Shell to recognize a theme, it needs a top-level `index.theme` file at the theme root:

```
~/.themes/InterGenOS/index.theme
```

Contents:
```ini
[Desktop Entry]
Type=X-GNOME-Metatheme
Name=InterGenOS
Comment=InterGenOS Shell Theme
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=InterGenOS
MetacityTheme=InterGenOS
IconTheme=InterGenOS
CursorTheme=InterGenOS
ButtonLayout=:minimize,maximize,close
```

### How gnome-shell.css Works

GNOME Shell uses **St (Shell Toolkit)**, which is a Clutter-based UI toolkit. St implements a subset of CSS — it is NOT a browser engine. The CSS is parsed by St's `StThemeContext` and applied to `StWidget` actors.

Key points:
- St supports a **subset of CSS3** — not the full spec
- It supports: classes, IDs, pseudo-classes (`:hover`, `:active`, `:focus`, `:checked`, `:insensitive`, `:first-child`, `:last-child`)
- It supports CSS custom properties (`--variable-name`) starting from GNOME 44+
- It supports `@import` for including other CSS files
- It does NOT support: `@media` queries, `@keyframes` (uses St's transition properties instead), `::before`/`::after` pseudo-elements (limited support), advanced selectors like `:nth-child()`, CSS grid, flexbox
- Transitions use St-specific syntax: `transition-duration`, `transition-property` work, but the animation system is Clutter's, not CSS

### Where is GNOME's Upstream SCSS Source?

The default GNOME Shell theme (now part of libadwaita/gnome-shell) lives at:

**`https://gitlab.gnome.org/GNOME/gnome-shell/-/tree/main/data/theme`**

The relevant files are:
- `gnome-shell-sass/` directory containing `_common.scss`, `_colors.scss`, `_drawing.scss`, `_variables.scss`
- `gnome-shell.scss` — the main entry point
- `gnome-shell-high-contrast.scss`
- The `meson.build` file that compiles SCSS to CSS using `sassc`

In GNOME 46+, the theme source was reorganized and some aspects were tightened. The SCSS source is the authoritative reference for every selector.

### CSS Support Level

GNOME Shell's St toolkit supports:
- **Basic CSS3 properties**: `color`, `background-color`, `background-gradient-direction` (St-specific), `background-gradient-start`, `background-gradient-end`, `border`, `border-radius`, `padding`, `margin`, `font-family`, `font-size`, `font-weight`, `font-style`, `text-align`, `text-shadow`, `box-shadow`, `icon-shadow` (St-specific), `opacity`, `width`, `height`, `min-width`, `min-height`, `max-width`, `max-height`, `background-image` (url to SVG/PNG)
- **CSS Custom Properties** (GNOME 44+): `--my-variable: value;` and `var(--my-variable)`
- **St-specific properties**: `-st-shadow` (deprecated), `background-gradient-direction: vertical|horizontal`, `-natural-hpadding`, `-minimum-hpadding`, `-minimum-vpadding`
- **Transition properties**: `transition-duration`, `transition-delay`, `transition-property` (but NOT `transition-timing-function` in the CSS sense — Clutter uses its own easing)

### How the Theme Gets Loaded at Runtime

1. GNOME Shell reads the `org.gnome.shell.extensions` gsettings key `name` (or more precisely `org.gnome.desktop.interface` `gtk-theme` for GTK apps, and the shell theme is set via `org.gnome.shell.extensions.user-theme` if the User Themes extension is active)
2. Without the User Themes extension, GNOME Shell loads its built-in default theme from `/usr/share/gnome-shell/gnome-shell.css`
3. With the User Themes extension (or via GDM config), it loads from `~/.themes/<name>/gnome-shell/gnome-shell.css` or `/usr/share/themes/<name>/gnome-shell/gnome-shell.css`
4. The CSS is parsed at shell startup and cached. Changes require a shell restart (Alt+F2, `r`, Enter on X11; full logout on Wayland) unless using Looking Glass for live injection
5. For GDM (login screen), the theme is compiled into `/usr/share/gnome-shell/gnome-shell-theme.gresource` — a GResource bundle

---

## 2. GNOME Shell CSS Selectors — Complete Reference

### Top Panel

```css
#panel {
  /* The entire top panel bar */
  background-color: #0A0A0A;
  height: 2em;  /* Panel height */
  font-weight: bold;
  color: #ffffff;
}

#panel .panel-corner {
  /* Rounded corners at panel edges (removed in GNOME 42+) */
}

#panel .panel-button {
  /* Every clickable element in the panel */
  color: #ffffff;
  -natural-hpadding: 12px;
  -minimum-hpadding: 8px;
}

#panel .panel-button:hover {
  background-color: rgba(255, 255, 255, 0.1);
}

#panel .panel-button:active,
#panel .panel-button:checked {
  background-color: rgba(255, 255, 255, 0.15);
}

#panel #panelActivities {
  /* Activities button (left) */
}

#panel .clock-display {
  /* Center clock */
}

#panel .clock-display .clock {
  /* Clock text specifically */
}

#panel #appMenu {
  /* App menu button (if displayed) */
}

/* Panel areas */
#panel .panel-left { }
#panel .panel-center { }
#panel .panel-right { }
```

### System Menu / Quick Settings (GNOME 43+)

GNOME 43 replaced the old aggregate menu with Quick Settings:

```css
.quick-settings {
  /* Quick settings container */
}

.quick-settings-grid {
  /* The grid layout of quick setting tiles */
}

.quick-toggle {
  /* Individual toggle tiles */
  border-radius: 12px;
  padding: 0 12px;
}

.quick-toggle:checked {
  background-color: #0099FF;  /* Active/checked state */
}

.quick-toggle .quick-toggle-title { }
.quick-toggle .quick-toggle-subtitle { }

.quick-menu-toggle {
  /* Toggle with expandable menu (WiFi, Bluetooth, etc.) */
}

.quick-menu-toggle .quick-toggle-arrow {
  /* The dropdown arrow */
}

.quick-settings .icon-button {
  /* Settings gear, power button, etc. */
}

.quick-settings .background-apps-quick-toggle {
  /* Background apps indicator (GNOME 44+) */
}

/* Quick Settings submenus */
.quick-settings-system-item {
  /* Bottom row: settings gear + lock + power */
}

.nm-network-item { }        /* Network entries */
.bt-device-item { }          /* Bluetooth devices */
```

### Notifications

```css
.notification-banner {
  /* Individual notification popup */
  border-radius: 12px;
  margin: 8px;
  padding: 12px;
  background-color: #1a1a1a;
  color: #ffffff;
}

.notification {
  /* Notification in the message list */
}

.notification .notification-title { }
.notification .notification-body { }
.notification .notification-icon { }

.notification .notification-button {
  /* Action buttons within notification */
}

.notification-banner.urgent {
  /* Critical/urgent notifications */
}

/* Message List (dropdown from clock) */
.message-list {
  /* Container */
}

.message-list-section { }
.message-list-section-title { }

.message {
  /* Individual message in the list */
}

.message .message-title { }
.message .message-body { }
.message .message-close-button { }
```

### Calendar (Clock dropdown)

```css
.events-button { }  /* "Events" header area */

.calendar {
  /* Calendar widget */
}

.calendar .calendar-month-label {
  /* "April 2026" */
}

.calendar .pager-button {
  /* Month navigation arrows */
}

.calendar .calendar-day-heading {
  /* Mon, Tue, etc. */
}

.calendar .calendar-day {
  /* Individual day cells */
}

.calendar .calendar-day:selected {
  background-color: #0099FF;
}

.calendar .calendar-today {
  /* Today's date - special styling */
  font-weight: bold;
}

.calendar .calendar-other-month-day {
  /* Days from adjacent months */
  color: rgba(255, 255, 255, 0.3);
}

.calendar .calendar-week-number {
  /* Week numbers column (if enabled) */
}

/* World clocks */
.world-clocks-button { }
.world-clocks-grid { }

/* Weather */
.weather-button { }
.weather-header { }
```

### Overview

```css
.overview-controls {
  /* Main overview container */
}

#overview {
  /* Overview background - GNOME dims the wallpaper */
}

/* Search */
#searchEntry {
  /* The search bar in overview */
  border-radius: 24px;
  padding: 8px 16px;
  background-color: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

#searchEntry:focus {
  background-color: rgba(255, 255, 255, 0.15);
  border: 2px solid #0099FF;
}

.search-provider-icon { }
.list-search-result { }
.list-search-result-title { }
.list-search-result-description { }
.search-section { }
.search-section-separator { }

/* App Grid */
.app-folder {
  /* Folder icon in app grid */
}

.app-well-app {
  /* Individual app icon */
}

.app-well-app .overview-icon {
  /* The icon + label container */
}

.app-well-app .app-well-app-running-dot {
  /* Running indicator dot under icon */
  background-color: #0099FF;
}

.icon-grid {
  /* App grid layout */
  -shell-grid-horizontal-item-size: 136px;
  -shell-grid-vertical-item-size: 136px;
  row-spacing: 16px;
  column-spacing: 16px;
}

.page-indicator .page-indicator-icon {
  /* Dot pagination indicators */
}

.app-folder-dialog {
  /* Open folder popup */
  border-radius: 18px;
  background-color: rgba(10, 10, 10, 0.8);
}

.app-folder-dialog .folder-name-entry { }
```

### Workspace Thumbnails

```css
.workspace-thumbnails {
  /* Thumbnail strip (right side of overview) */
  padding: 8px;
}

.workspace-thumbnail {
  /* Individual workspace preview */
  border-radius: 12px;
}

.workspace-thumbnail-indicator {
  /* Active workspace highlight */
  border: 3px solid #0099FF;
  border-radius: 12px;
}

/* Workspace switcher popup (Super+scroll or gesture) */
.workspace-switcher-container { }
.workspace-switcher { }
.ws-switcher-box { }
.ws-switcher-active-up, .ws-switcher-active-down { }
```

### Window Switcher (Alt+Tab)

```css
.switcher-popup {
  /* Container for Alt+Tab */
}

.switcher-list {
  /* The horizontal list of windows */
  background-color: rgba(10, 10, 10, 0.9);
  border-radius: 16px;
  padding: 12px;
}

.switcher-list .item-box {
  /* Individual window entry */
  padding: 8px;
}

.switcher-list .item-box:selected {
  background-color: rgba(0, 153, 255, 0.3);
  border-radius: 8px;
}

.switcher-list .thumbnail {
  /* Window thumbnail preview */
}

.switcher-list .thumbnail-box { }
.switcher-list .separator { }

.switcher-arrow {
  /* Overflow arrows */
}

/* App switcher vs Window switcher */
.switcher-list .app-icon-box { }
.switcher-list .window-list { }  /* Expanded per-app windows */
```

### OSD Popups (Volume, Brightness, etc.)

```css
.osd-window {
  /* Volume/Brightness popup */
  background-color: rgba(10, 10, 10, 0.85);
  border-radius: 16px;
  padding: 16px;
}

.osd-window .level {
  /* The slider bar */
  height: 4px;
  border-radius: 2px;
  background-color: rgba(255, 255, 255, 0.2);
  -barlevel-active-background-color: #0099FF;
  -barlevel-overdrive-color: #ff3333;
  -barlevel-active-border-color: transparent;
  -barlevel-border-color: transparent;
  -barlevel-overdrive-border-color: transparent;
  -barlevel-border-width: 0;
}
```

### Lock Screen / Login Screen

```css
.unlock-dialog {
  /* Lock screen dialog */
  background-color: rgba(10, 10, 10, 0.7);
}

.unlock-dialog .unlock-dialog-clock-time {
  font-size: 72pt;
  font-weight: bold;
}

.unlock-dialog .unlock-dialog-clock-date {
  font-size: 18pt;
}

.unlock-dialog .unlock-dialog-clock-hint { }

.unlock-dialog .unlock-dialog-prompt-layout { }

.login-dialog {
  /* GDM login screen */
  background-color: transparent;
}

.login-dialog .user-widget { }
.login-dialog .user-widget .user-icon { }
.login-dialog .user-widget .user-widget-label { }
.login-dialog .login-dialog-prompt-layout { }

/* Screen Shield (the blurred screen before unlock dialog) */
.screen-shield-background { }

.screen-shield-clock-time {
  font-size: 96pt;
}
.screen-shield-clock-date { }
.screen-shield-notifications-container { }
```

### Modal Dialogs

```css
.modal-dialog {
  /* Any shell modal (e.g., auth prompt, extension errors) */
  border-radius: 16px;
  background-color: #1a1a1a;
  color: #ffffff;
  padding: 24px;
}

.modal-dialog .modal-dialog-content-box { }

.modal-dialog .modal-dialog-button-box {
  /* Button row at bottom */
}

.modal-dialog .modal-dialog-button-box .modal-dialog-button {
  border-radius: 8px;
  padding: 8px 24px;
}

/* "Default" action button (e.g., "OK", "Authenticate") */
.modal-dialog .modal-dialog-button-box .modal-dialog-button:default {
  background-color: #0099FF;
  color: #ffffff;
}

/* Auth dialog specifically */
.prompt-dialog { }
.prompt-dialog-headline { }
.prompt-dialog-description { }
.prompt-dialog-password-entry { }

/* End session dialog */
.end-session-dialog { }
.end-session-dialog-battery-warning { }
.end-session-dialog-app-list { }
```

### Tooltips and Popup Menus

```css
.popup-menu {
  /* All popup/dropdown menus */
}

.popup-menu .popup-menu-content {
  background-color: #1a1a1a;
  border-radius: 12px;
  padding: 8px 0;
}

.popup-menu .popup-menu-item {
  padding: 8px 24px;
  color: #ffffff;
}

.popup-menu .popup-menu-item:hover {
  background-color: rgba(0, 153, 255, 0.2);
}

.popup-menu .popup-menu-item:active {
  background-color: rgba(0, 153, 255, 0.3);
}

.popup-separator-menu-item {
  /* Divider line in menus */
  height: 1px;
  background-color: rgba(255, 255, 255, 0.1);
}

.popup-sub-menu { }
.popup-menu-ornament { }  /* Checkmarks in menus */

/* Tooltip */
.dash-label {
  /* Tooltip for dash icons */
  border-radius: 8px;
  padding: 4px 12px;
  background-color: rgba(10, 10, 10, 0.9);
  color: #ffffff;
}
```

### Dash (Dock in Overview)

```css
#dash {
  /* The favorites dock in overview */
  background-color: rgba(10, 10, 10, 0.7);
  border-radius: 18px;
  padding: 4px;
}

#dash .dash-background { }

#dash .app-well-app-running-dot { }

#dash .show-apps {
  /* "Show Applications" grid button */
}

#dash .dash-separator {
  /* Separator between pinned and running */
  width: 1px;
  background-color: rgba(255, 255, 255, 0.15);
  margin: 8px 0;
}

.dash-item-container { }
```

### Slider / Toggle / Entry Widgets

```css
/* Sliders */
.slider {
  height: 20px;
  -slider-handle-border-width: 0;
  -slider-handle-border-color: transparent;
  -barlevel-height: 4px;
  -barlevel-background-color: rgba(255, 255, 255, 0.2);
  -barlevel-active-background-color: #0099FF;
  -barlevel-border-width: 0;
}

/* Toggle switches (GNOME 42+) */
.toggle-switch {
  /* References toggle-on.svg / toggle-off.svg */
  width: 40px;
  height: 20px;
}

.toggle-switch:checked {
  background-image: url("toggle-on.svg");
}

.toggle-switch:not(:checked) {
  background-image: url("toggle-off.svg");
}

/* Text entry fields */
.entry, StEntry {
  border-radius: 8px;
  padding: 8px 12px;
  background-color: rgba(255, 255, 255, 0.1);
  color: #ffffff;
  border: 2px solid transparent;
  selection-background-color: rgba(0, 153, 255, 0.5);
  selected-color: #ffffff;
}

StEntry:focus {
  border-color: #0099FF;
  background-color: rgba(255, 255, 255, 0.15);
}

/* Buttons */
.button, StButton {
  border-radius: 8px;
  padding: 8px 16px;
  background-color: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.button:hover { background-color: rgba(255, 255, 255, 0.15); }
.button:active { background-color: rgba(255, 255, 255, 0.2); }
.button:insensitive { opacity: 0.5; }

/* Check boxes */
.check-box StBin {
  /* The checkbox indicator */
  width: 20px;
  height: 20px;
}

.check-box:checked StBin {
  background-image: url("checkbox.svg");
}

.check-box StBin {
  background-image: url("checkbox-off.svg");
}
```

### Changes Between GNOME 42-49

**GNOME 42:**
- Panel corners removed (`.panel-corner` deprecated)
- Introduction of dark style preference API

**GNOME 43:**
- Quick Settings replaced aggregate menu — all the `.quick-toggle`, `.quick-settings` selectors are new
- Old `#aggregateMenu` selectors removed entirely

**GNOME 44:**
- CSS custom properties (`--variable`) support added to St
- Background apps quick toggle added
- Accessibility quick settings redesigned

**GNOME 45:**
- Extensions system overhauled — User Themes extension must be updated
- `imports.gi` replaced with ES modules — affects extension code, not CSS directly
- Some class names changed for calendar/world-clocks

**GNOME 46:**
- Accent colors introduced — `accent_bg_color`, `accent_fg_color`, `accent_color`
- New CSS custom properties: `--accent-bg-color`, `--accent-color`, `--accent-fg-color`
- File chooser and remote desktop indicators new selectors
- Search results layout changes

**GNOME 47:**
- Accent color picker in Settings
- Small widget adjustments, variable names stabilized
- Improved custom property support

**GNOME 48:**
- Continued refinement of the accent color system
- HDR and color management work (affects rendering, not CSS)
- New notification grouping selectors

**GNOME 49 (current target):**
- Further modularization of theme components
- The theme CSS is increasingly variable-driven
- Custom properties are the recommended way to customize colors
- The upstream theme uses variables extensively — overriding just the variables can retheme most of the shell

---

## 3. The GNOME Shell SCSS Build System

### Upstream SCSS Source Location

```
https://gitlab.gnome.org/GNOME/gnome-shell/-/tree/main/data/theme
```

Directory structure:
```
data/theme/
├── gnome-shell.scss                    # Main entry point
├── gnome-shell-high-contrast.scss
├── pad-osd.css
├── gnome-shell-sass/
│   ├── _colors.scss          # Color variable definitions
│   ├── _common.scss          # All widget/selector styles
│   ├── _drawing.scss         # Mixins for button states, entries, etc.
│   ├── _variables.scss       # Size/spacing/radius variables
│   └── _high-contrast-colors.scss
├── toggle-on.svg
├── toggle-off.svg
├── checkbox.svg
├── checkbox-off.svg
├── process-working.svg
├── ... (other SVG assets)
└── meson.build               # Build configuration
```

### How CSS is Compiled

The `meson.build` uses `sassc` (the C implementation of Sass) to compile:

```meson
gnome_shell_css = custom_target('gnome-shell.css',
  input: 'gnome-shell.scss',
  output: 'gnome-shell.css',
  command: [sassc, '-a', '@INPUT@', '@OUTPUT@'],
)
```

To compile manually:
```bash
sassc gnome-shell.scss gnome-shell.css
# or with dart-sass (modern):
sass gnome-shell.scss gnome-shell.css
```

### Key SCSS Variables

From `_colors.scss` (approximate, varies by version):

```scss
// These are the foundational color variables
$bg_color: #242424;
$fg_color: #ffffff;
$base_color: #303030;
$selected_bg_color: #3584e4;   // This is what you'd change to #0099FF
$selected_fg_color: #ffffff;
$borders_color: rgba(255, 255, 255, 0.1);
$system_bg_color: #1a1a1a;

// Derived colors
$insensitive_fg_color: rgba($fg_color, 0.5);
$osd_bg_color: rgba(#1a1a1a, 0.9);
$osd_fg_color: #ffffff;
$warning_color: #e5a50a;
$error_color: #e01b24;
$destructive_color: #e01b24;
$success_color: #26a269;

// Panel
$panel_bg_color: #000000;
$panel_fg_color: #ffffff;
$panel_transparent_bg_color: rgba(0, 0, 0, 0);
```

From `_variables.scss`:
```scss
$base_padding: 6px;
$base_margin: 4px;
$base_border_radius: 12px;
$modal_radius: 14px;
$base_icon_size: 24px;
$panel_height: 28px;
$dash_padding: 6px;
$base_font_size: 11pt;
$text_shadow: 0 1px rgba(0, 0, 0, 0.8);
```

### Recommended Way to Create a Derivative Theme

**Option A: SCSS Override (Best Approach)**

Fork the upstream SCSS and create an override file:

```scss
// intergen-shell.scss
// Override color variables BEFORE importing common
$bg_color: #0A0A0A;
$fg_color: #ffffff;
$base_color: #141414;
$selected_bg_color: #0099FF;    // InterGenOS ECG blue
$selected_fg_color: #ffffff;
$system_bg_color: #0A0A0A;
$panel_bg_color: transparent;
$osd_bg_color: rgba(#0A0A0A, 0.85);

@import 'gnome-shell-sass/common';
// Then add your custom overrides below
```

**Option B: CSS Custom Properties Override (GNOME 46+ approach)**

Create a CSS file that redefines the custom properties:

```css
stage {
  --bg-color: #0A0A0A;
  --fg-color: #ffffff;
  --accent-bg-color: #0099FF;
  --accent-fg-color: #ffffff;
  --accent-color: #0099FF;
  --system-bg-color: #0A0A0A;
  --osd-bg-color: rgba(10, 10, 10, 0.85);
  /* ... etc ... */
}
```

This is lighter-weight but gives less control.

**Option C: Full Custom CSS (Most Control)**

Write gnome-shell.css entirely from scratch. This gives total control but requires maintaining every selector yourself. This is what the top third-party themes do.

### How Third-Party Themes Structure Their SCSS

**Orchis (vinceliuice):**
```
src/
├── gnome-shell/
│   ├── _common.scss
│   ├── _colors.scss
│   ├── _drawing.scss
│   ├── _extensions.scss     # Dash-to-Dock, etc.
│   ├── gnome-shell.scss
│   └── assets/
├── _colors.scss              # Shared colors across GTK + Shell
├── _variables.scss
└── install.sh                # Build + install script
```

**WhiteSur:**
```
src/
├── gnome-shell/
│   ├── gnome-shell.scss
│   └── _common.scss
├── _color-palette.scss       # macOS-inspired palette
├── _variables.scss
└── tweaks.sh                 # For variant selection
```

**Nordic:**
```
src/
├── gnome-shell/
│   ├── _common.scss
│   ├── _colors.scss
│   └── gnome-shell.scss
├── colors.scss               # Nord color palette
└── parse-sass.sh             # Build script
```

All three use the same general pattern: define colors in a variables file, import a `_common.scss` that is largely adapted from upstream GNOME Shell's `_common.scss`, and compile with `sassc` or `dart-sass`.

---

## 4. GTK4 / libadwaita Integration

### Shell Theme vs GTK Theme Relationship

The GNOME Shell theme and the GTK/libadwaita theme are **completely separate** CSS files processed by different rendering engines:

- **Shell theme** → parsed by St (Shell Toolkit), controls panel, overview, notifications, etc.
- **GTK4/libadwaita theme** → parsed by GTK's CSS engine, controls application windows

They share NO CSS at runtime. You must create two separate themes to achieve visual consistency:
1. `gnome-shell/gnome-shell.css` for the shell
2. `gtk-4.0/gtk.css` (and optionally `gtk-3.0/gtk.css`) for applications

### libadwaita's Theming Model

libadwaita (GNOME's app styling library) is intentionally resistant to theming. It:
- Ships its own built-in stylesheet
- Does NOT respect `~/.themes/` GTK themes by default
- Since GNOME 46+, supports **accent colors** as the official customization mechanism
- Experimental: supports `~/.config/gtk-4.0/gtk.css` for user overrides

To override libadwaita apps' colors, users place CSS in `~/.config/gtk-4.0/gtk.css`:

```css
@define-color accent_bg_color #0099FF;
@define-color accent_fg_color #ffffff;
@define-color accent_color #0099FF;
@define-color window_bg_color #0A0A0A;
@define-color window_fg_color #ffffff;
@define-color headerbar_bg_color #141414;
@define-color headerbar_fg_color #ffffff;
@define-color card_bg_color #1a1a1a;
@define-color view_bg_color #0A0A0A;
@define-color sidebar_bg_color #141414;
```

### @define-color Properties

`@define-color` is a GTK-specific CSS directive (not standard CSS). It defines named colors that can be referenced with `@name`:

```css
@define-color accent_bg_color #0099FF;
@define-color accent_fg_color #ffffff;

/* Usage */
button.suggested-action {
  background-color: @accent_bg_color;
  color: @accent_fg_color;
}
```

**Important**: `@define-color` is a GTK CSS feature, NOT available in GNOME Shell's St CSS. For the shell theme, you use either SCSS variables (at build time) or CSS custom properties `--variable` (at runtime, GNOME 44+).

### Maintaining Visual Consistency

For InterGenOS to have a cohesive look, you need:

1. **Shell theme** (`gnome-shell.css`) using `#0099FF` as accent, `#0A0A0A` as background
2. **GTK4 override** (`gtk.css` with `@define-color`) using the same palette
3. **GTK3 override** (for legacy apps that haven't ported to GTK4)
4. **Icon theme** with matching accent colors
5. Consistent border-radius values, font choices, and spacing

For a distribution, you can:
- Ship the GTK4 CSS as a system-wide override in `/usr/share/gtk-4.0/`
- Set gsettings defaults to apply everything automatically
- The accent color API (GNOME 46+) means you can set `accent-color` in org.gnome.desktop.interface and libadwaita apps will pick it up natively

---

## 5. Assets and Resources

### Required SVG/PNG Assets

Minimum set for a complete shell theme:

| Asset | Purpose |
|-------|---------|
| `toggle-on.svg` | Toggle switch in ON state (Quick Settings, etc.) |
| `toggle-off.svg` | Toggle switch in OFF state |
| `checkbox.svg` | Checked checkbox |
| `checkbox-off.svg` | Unchecked checkbox |
| `checkbox-focused.svg` | Focused checkbox |
| `radio.svg` | Selected radio button |
| `radio-off.svg` | Unselected radio button |
| `process-working.svg` | Loading/spinner animation |
| `no-events.svg` | Calendar "no events" placeholder |
| `no-notifications.svg` | "No notifications" placeholder |
| `calendar-today.svg` | Marker for today in calendar |
| `close.svg` | Generic close button |
| `close-window.svg` | Window close (not commonly used in shell) |
| `eye-open-negative.svg` | Password visibility toggle |
| `eye-not-looking-symbolic.svg` | Password hidden toggle |
| `gnome-shell-start.svg` | Startup splash screen |

### How Assets Are Referenced

```css
.toggle-switch:checked {
  background-image: url("resource:///org/gnome/shell/theme/toggle-on.svg");
  /* or for file-based themes: */
  background-image: url("toggle-on.svg");
}
```

For file-based themes (installed in `~/.themes/` or `/usr/share/themes/`), URLs are relative to the theme directory. For GResource-based themes (compiled into `.gresource`), they use the `resource://` URI scheme.

### Asset Creation Tools

- **Inkscape** — Primary SVG editor for creating assets
- **GIMP** — For PNG rasterization if needed
- **Figma / Penpot** — For designing assets before export to SVG
- **resvg / librsvg** — For rendering/testing SVGs from CLI
- SVGs should be:
  - Optimized (use `svgo` or Inkscape's "Optimized SVG" save)
  - Properly sized (viewBox and dimensions matching CSS width/height)
  - Using `currentColor` where possible for recolorability

For InterGenOS:
- Recolor toggle SVGs to use `#0099FF` for the active state
- Keep SVGs simple — GNOME Shell's SVG rendering (librsvg) has limitations compared to browsers
- No embedded fonts in SVGs — convert text to paths

---

## 6. Color Theming Approach

### How Professional Themes Define Palettes

The standard approach is a layered color system:

```scss
// 1. Brand/Base colors
$blue: #0099FF;         // InterGenOS ECG blue
$dark: #0A0A0A;         // Deep background
$white: #ffffff;

// 2. Semantic colors
$bg_color: $dark;
$fg_color: $white;
$accent_color: $blue;
$accent_fg: $white;

// 3. Surface hierarchy (darker to lighter)
$surface_0: #0A0A0A;   // Deepest background
$surface_1: #111111;   // Panel, sidebars
$surface_2: #1a1a1a;   // Cards, dialogs
$surface_3: #242424;   // Elevated elements
$surface_4: #2e2e2e;   // Hover states

// 4. Text hierarchy
$text_primary: rgba(255, 255, 255, 1.0);
$text_secondary: rgba(255, 255, 255, 0.7);
$text_disabled: rgba(255, 255, 255, 0.38);

// 5. State colors
$hover_overlay: rgba(255, 255, 255, 0.08);
$active_overlay: rgba(255, 255, 255, 0.12);
$focus_outline: rgba($blue, 0.5);
$selected_bg: rgba($blue, 0.3);

// 6. Semantic status colors
$success: #26a269;
$warning: #e5a50a;
$error: #e01b24;
$destructive: #e01b24;
```

### Minimum Color Variables for a Distinct Look

You can get 80% of the visual identity change with just these:

1. `$selected_bg_color` / `--accent-bg-color` → `#0099FF`
2. `$bg_color` → `#0A0A0A`
3. `$base_color` → `#141414`
4. `$fg_color` → `#ffffff`
5. `$panel_bg_color` → `transparent` or `rgba(10, 10, 10, 0.6)`
6. `$osd_bg_color` → `rgba(10, 10, 10, 0.85)`

These six variables will transform the entire shell appearance.

### Dark vs Light Variants

Themes handle this with separate SCSS entry points:

```
gnome-shell.scss          → imports _colors-dark.scss, then _common.scss
gnome-shell-light.scss    → imports _colors-light.scss, then _common.scss
```

Or with a single `_colors.scss` parameterized by a variant flag:

```scss
$variant: 'dark' !default;

@if $variant == 'dark' {
  $bg_color: #0A0A0A;
  $fg_color: #ffffff;
} @else {
  $bg_color: #fafafa;
  $fg_color: #1a1a1a;
}
```

For InterGenOS, given the brand is dark-first, you may only need a dark variant initially.

### Accent Colors in GNOME 46+

GNOME 46 introduced a system-level accent color setting at `org.gnome.desktop.interface accent-color`. This provides a set of predefined accent colors (blue, teal, green, yellow, orange, red, pink, purple, slate).

For the **shell theme**, the accent color is exposed as CSS custom properties:
```css
/* These are set automatically based on user's accent color choice */
--accent-bg-color
--accent-fg-color  
--accent-color
```

For InterGenOS, you have two strategies:
1. **Hard-code your blue** — Override `--accent-bg-color: #0099FF` in your theme CSS, ignoring user preference. This ensures brand consistency.
2. **Respect user choice** — Use `var(--accent-bg-color)` throughout and set the default via gsettings. This is more GNOME-idiomatic.

Recommendation: Hard-code `#0099FF` as the default but use it through a CSS variable so it's easy to adjust.

---

## 7. Panel Transparency and Blur

### Implementing Transparent Panels

```css
/* Fully transparent panel */
#panel {
  background-color: transparent;
}

/* Semi-transparent with tint */
#panel {
  background-color: rgba(10, 10, 10, 0.6);
}

/* Dynamic transparency: transparent in normal state, opaque when a window is near */
/* This requires the "dynamic panel transparency" behavior, which is NOT pure CSS — 
   it needs JavaScript in an extension or a shell patch */
#panel.solid {
  background-color: #0A0A0A;
  transition-duration: 250ms;
}

#panel.transparent {
  background-color: transparent;
  transition-duration: 250ms;
}
```

GNOME Shell itself adds the `.solid` class to the panel when a window is maximized or touching the panel. The default upstream behavior:
- Panel is **semi-transparent** in the overview
- Panel gets a **solid** background when a window is maximized near it

Your CSS can leverage this:
```css
#panel {
  background-color: rgba(10, 10, 10, 0.4);
  transition-duration: 300ms;
}

/* When window maximized/touching panel */
#panel.solid {
  background-color: rgba(10, 10, 10, 0.9);
}
```

### Blur my Shell Interaction

The "Blur my Shell" extension by aunetx adds background blur behind various shell elements. It works by:

1. Adding a `Shell.BlurEffect` to Clutter actors behind the panel, overview, dash, etc.
2. The extension adds its own CSS classes that can be styled
3. Your theme should be designed to work with AND without the extension

Key considerations:
- If you want blur as a core part of InterGenOS, you can either bundle/require the extension or patch gnome-shell directly
- Keep `background-color` semi-transparent (alpha < 1.0) so blur shows through
- Don't rely on blur for readability — ensure text is readable without it via sufficient background opacity

### CSS Properties for Transparency

```css
#panel {
  background-color: rgba(10, 10, 10, 0.5);  /* Semi-transparent */
}

/* For the overview background */
#overview {
  /* The overview automatically dims the wallpaper */
}

/* For the dash in overview */
#dash {
  background-color: rgba(10, 10, 10, 0.6);
}

/* GNOME Shell does NOT support CSS backdrop-filter or filter: blur() */
/* Blur must be done at the compositor level, not CSS */
```

---

## 8. Font Integration

### Setting the Shell Font

**Method 1: CSS (theme-level, shell only)**
```css
stage {
  font-family: 'Inter', sans-serif;
  font-size: 11pt;
}
```

This sets the font for ALL shell elements. Individual elements can override:
```css
.unlock-dialog .unlock-dialog-clock-time {
  font-family: 'Inter';
  font-weight: 700;   /* Bold */
  font-size: 72pt;
}

.notification .notification-title {
  font-family: 'Inter';
  font-weight: 700;
}

.notification .notification-body {
  font-family: 'Inter';
  font-weight: 400;   /* Regular */
}
```

**Method 2: gsettings (system-wide, affects all GNOME apps)**
```bash
gsettings set org.gnome.desktop.interface font-name 'Inter 11'
gsettings set org.gnome.desktop.interface document-font-name 'Inter 11'
gsettings set org.gnome.desktop.interface monospace-font-name 'JetBrains Mono 10'
gsettings set org.gnome.desktop.wm.preferences titlebar-font 'Inter Bold 11'
```

**For InterGenOS**: Use BOTH approaches:
- Set gsettings defaults for the whole system (affects apps)
- Set `font-family: 'Inter'` in the shell theme CSS (for the shell specifically, as a guarantee)

### Can the Shell Use a Custom Font Without Changing the System Font?

**Yes.** The shell CSS `font-family` is independent of gsettings font settings. Setting `font-family: 'Inter'` in `gnome-shell.css` only affects the shell UI. Application fonts are controlled by gsettings or per-app settings.

However, the font MUST be installed system-wide (in `/usr/share/fonts/` or `~/.local/share/fonts/`) for the shell to find it.

### Font-Related CSS Properties Supported by St

```css
stage {
  font-family: 'Inter', 'Cantarell', sans-serif;
  font-size: 11pt;          /* pt, px, or em */
  font-weight: 400;         /* 100-900 or normal/bold */
  font-style: normal;       /* normal, italic, oblique */
  font-variant: normal;     /* normal, small-caps (limited support) */
  text-align: center;       /* left, right, center */
  text-decoration: none;    /* none, underline, line-through */
  text-shadow: 0 1px 0 rgba(0, 0, 0, 0.5);
  color: #ffffff;
  -st-natural-hpadding: 12px;  /* Text padding specific to St */
}
```

St does NOT support: `font-feature-settings`, `font-variation-settings`, `letter-spacing` (limited), `line-height` (use padding instead), `word-spacing`, `text-transform` (no support), `white-space`.

---

## 9. Testing and Development Workflow

### Testing Without Logging Out

**On X11:**
```bash
# Restart GNOME Shell in-place (preserves session)
# Press Alt+F2, type 'r', press Enter
# Or from terminal:
busctl --user call org.gnome.Shell /org/gnome/Shell org.gnome.Shell Eval s 'Meta.restart("Restarting...")'
```

**On Wayland:**
Wayland does NOT support in-place restart. Options:
1. **Nested GNOME Shell session** (best for development):
```bash
dbus-run-session -- gnome-shell --nested --wayland
```
This opens a GNOME Shell instance in a window. You can apply your theme and restart it without affecting your main session.

2. **Looking Glass live CSS injection** (for quick tweaks)

3. **Log out and log in** (slowest, use as last resort)

### Looking Glass Inspector

Looking Glass is GNOME Shell's built-in JavaScript console and inspector:

**Open it:** Alt+F2, type `lg`, Enter

Tabs:
- **Evaluator**: Run JavaScript against the shell
- **Windows**: List all windows
- **Extensions**: List loaded extensions
- **Memory**: Memory usage
- **Logs**: Shell log messages

**Useful commands in Looking Glass:**
```javascript
// Reload theme from disk
Main.loadTheme();

// Get the panel actor
Main.panel

// Inspect a specific widget's CSS classes
Main.panel.get_style_class()

// Apply inline CSS to test
Main.panel.set_style('background-color: rgba(0, 153, 255, 0.5);')

// Remove inline style
Main.panel.set_style('')

// Pick an actor (click to select)
// Use the "pick" button in Looking Glass

// List all CSS classes on the stage
global.stage.get_children()
```

### Live CSS Editing

**Method 1: Looking Glass + Main.loadTheme()**
1. Edit `gnome-shell.css` in your text editor
2. Open Looking Glass (Alt+F2, `lg`)
3. In the Evaluator tab, run: `Main.loadTheme()`
4. The theme reloads instantly

**Method 2: gnome-shell --nested**
```bash
# Terminal 1: Run nested shell
dbus-run-session -- gnome-shell --nested --wayland

# Terminal 2: Edit CSS, then signal the nested shell to reload
# (You'll restart the nested shell to see changes)
```

**Method 3: Using `st-theme-reload` (if available)**
Some development builds have a DBus method to reload theme.

### Fastest Development Iteration Cycle

1. Set up a nested GNOME Shell session for safe testing
2. Use a file watcher to auto-compile SCSS:
```bash
# Watch and auto-compile
sass --watch gnome-shell.scss:gnome-shell.css
```
3. After each CSS change, run `Main.loadTheme()` in Looking Glass
4. Use Looking Glass's picker to identify CSS classes of elements you want to style
5. Keep the upstream `_common.scss` open as reference for all selectors

For InterGenOS development:
```bash
# In your theme dev directory
mkdir -p ~/.themes/InterGenOS/gnome-shell/
ln -sf /path/to/your/dev/gnome-shell.css ~/.themes/InterGenOS/gnome-shell/gnome-shell.css
# Symlink assets too
ln -sf /path/to/your/dev/assets/* ~/.themes/InterGenOS/gnome-shell/
```

---

## 10. Production Packaging

### File System Layout

```
/usr/share/themes/InterGenOS/
├── index.theme
├── gnome-shell/
│   ├── gnome-shell.css
│   ├── gnome-shell-high-contrast.css  (optional)
│   ├── toggle-on.svg
│   ├── toggle-off.svg
│   ├── checkbox.svg
│   ├── checkbox-off.svg
│   ├── process-working.svg
│   ├── no-events.svg
│   ├── no-notifications.svg
│   ├── close.svg
│   ├── calendar-today.svg
│   ├── eye-open-negative.svg
│   └── gnome-shell-start.svg
├── gtk-4.0/
│   └── gtk.css                 # libadwaita/GTK4 overrides
├── gtk-3.0/
│   ├── gtk.css                 # GTK3 theme
│   └── gtk-dark.css
└── metacity-1/                 # Window borders (legacy)
    └── metacity-theme-3.xml
```

### Setting as Default via gsettings Override

Create a GSettings override file:

```bash
# /usr/share/glib-2.0/schemas/10-intergen-theme.gschema.override

[org.gnome.desktop.interface]
gtk-theme='InterGenOS'
icon-theme='InterGenOS'
cursor-theme='InterGenOS'
font-name='Inter 11'
document-font-name='Inter 11'
monospace-font-name='JetBrains Mono 10'
color-scheme='prefer-dark'
accent-color='blue'

[org.gnome.desktop.wm.preferences]
titlebar-font='Inter Bold 11'

[org.gnome.shell.extensions.user-theme]
name='InterGenOS'
```

After placing the file:
```bash
glib-compile-schemas /usr/share/glib-2.0/schemas/
```

### GDM (Login Screen) Theming

GDM uses a GResource file, not the file-based theme. To theme GDM:

```bash
# 1. Extract the existing GDM GResource
mkdir /tmp/gdm-theme
for r in $(gresource list /usr/share/gnome-shell/gnome-shell-theme.gresource); do
  gresource extract /usr/share/gnome-shell/gnome-shell-theme.gresource "$r" > /tmp/gdm-theme/"${r##*/}"
done

# 2. Replace gnome-shell.css with your theme's CSS
cp /usr/share/themes/InterGenOS/gnome-shell/gnome-shell.css /tmp/gdm-theme/

# 3. Copy your assets
cp /usr/share/themes/InterGenOS/gnome-shell/*.svg /tmp/gdm-theme/

# 4. Create a GResource XML manifest
cat > /tmp/gdm-theme/gnome-shell-theme.gresource.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<gresources>
  <gresource prefix="/org/gnome/shell/theme">
    <file>gnome-shell.css</file>
    <file>gnome-shell-high-contrast.css</file>
    <file>toggle-on.svg</file>
    <file>toggle-off.svg</file>
    <file>checkbox.svg</file>
    <file>checkbox-off.svg</file>
    <file>process-working.svg</file>
    <file>no-events.svg</file>
    <file>no-notifications.svg</file>
    <file>calendar-today.svg</file>
    <file>close.svg</file>
    <file>eye-open-negative.svg</file>
    <file>gnome-shell-start.svg</file>
    <file>pad-osd.css</file>
  </gresource>
</gresources>
EOF

# 5. Compile
glib-compile-resources --sourcedir=/tmp/gdm-theme /tmp/gdm-theme/gnome-shell-theme.gresource.xml

# 6. Install
cp /tmp/gdm-theme/gnome-shell-theme.gresource /usr/share/gnome-shell/gnome-shell-theme.gresource
```

For InterGenOS as a from-source distro, you'd bake this into the build process.

### Handling Theme Updates Across GNOME Versions

Key strategies:
1. **Pin your theme to specific GNOME versions** — Have branches like `gnome-48`, `gnome-49`
2. **Track upstream selector changes** — Subscribe to GNOME Shell's git log for `data/theme/` changes
3. **Use SCSS variables extensively** — When upstream adds new selectors, you only need to add the selector and apply existing variables
4. **Test against git main** — Before each GNOME release, test against the development branch
5. **Maintain a selector mapping document** — Track which selectors changed between versions

---

## 11. Real-World Theme Analysis

### Orchis Theme (vinceliuice)

**Structure:**
- Highly organized SCSS with separate files for each component
- Multiple variants: Default, Compact, Light, Dark, Solid
- Color accents: Default (blue), Red, Green, Yellow, Orange, Purple, Pink, Grey
- Single `install.sh` that compiles SCSS and copies files
- Supports GNOME 40-46+ with version-specific SCSS branches

**Key techniques:**
- Uses `$compact: 'false' !default;` flag to generate compact variant from same source
- Extensive use of SCSS mixins for button/entry/toggle drawing
- Panel styling: frosted glass effect using `rgba()` backgrounds
- Custom assets: Renders toggle SVGs programmatically or provides pre-colored variants per accent
- Quick Settings styling is thorough — covers all GNOME 43+ widgets

**Lessons:**
- The install script is critical UX — users expect `./install.sh` to just work
- Supporting multiple GNOME versions from one codebase requires version detection and conditional SCSS

### WhiteSur Theme (vinceliuice)

**Structure:**
- macOS-inspired, most popular macOS-like GNOME theme
- Same author as Orchis, similar build system
- Heavy use of custom assets (window buttons, panel icons)
- Separate `tweaks.sh` for optional modifications (panel size, roundness, etc.)

**Key techniques:**
- Panel blur effect: designed to look good with Blur my Shell
- Panel height is increased for macOS-like feel (custom `$panel_height`)
- Extensive App Grid styling to mimic Launchpad
- Custom close/minimize/maximize buttons (these are for the GTK theme, not shell theme)
- Activities button replaced with Apple logo icon via CSS `background-image`

**Lessons:**
- Users love customization options — multiple variants and tweaks
- Panel look is the #1 visual differentiator for shell themes
- Combining shell theme + GTK theme + icon theme gives the complete experience

### Nordic Theme (EliverLara)

**Structure:**
- Based on the Nord color palette (polar night, snow storm, frost, aurora)
- Cleaner, less opinionated than WhiteSur — closer to upstream GNOME aesthetic
- Simpler SCSS structure, easier to understand
- Fewer variants (dark, light, darker)

**Key techniques:**
- Minimal asset changes — mostly recolors
- Relies heavily on color variable substitution rather than structural CSS changes
- Panel styling is subtle — slightly transparent, matches overall dark aesthetic
- Good high-contrast variant

**Lessons:**
- A cohesive color palette goes further than custom widgets
- Staying close to upstream GNOME's structure makes maintenance easier
- The Nord palette approach (named color layers) is a good model for InterGenOS

### Common Pitfalls (from all three)

1. **Not updating for new GNOME releases** — Themes break when selectors change (especially GNOME 43's Quick Settings rewrite and GNOME 45's extension changes)
2. **Hardcoding pixel values** — GNOME Shell scales for HiDPI; use `em` units where possible
3. **Missing selectors** — New shell features add new CSS classes; if your theme doesn't style them, they fall back to the default (which looks jarring)
4. **GDM theming forgotten** — The login screen uses a separate GResource; many themes only style the session shell
5. **Extension compatibility** — Dash to Dock, Blur my Shell, AppIndicator, etc. add their own CSS classes that need styling
6. **Performance** — Overly complex `box-shadow`, multiple `text-shadow`, and heavy transparency can cause frame drops on lower-end hardware

---

## 12. InterGenOS-Specific Implementation Plan

### Brand Integration

Given your brand elements:
- **ECG Blue**: `#0099FF`
- **Dark Background**: `#0A0A0A`
- **White Text**: `#FFFFFF`
- **Font**: Inter (Bold headings, Regular body)
- **Visual Identity**: ECG pulse line → boot animation → welcome greeter → shell theme

### Recommended Color System

```scss
// _intergen-colors.scss

// Brand
$intergen-blue: #0099FF;
$intergen-blue-dim: #0077CC;
$intergen-blue-bright: #33BBFF;
$intergen-blue-muted: rgba(0, 153, 255, 0.3);

// Surfaces (dark mode hierarchy)
$surface-base: #0A0A0A;       // Desktop / deepest
$surface-1: #0F0F0F;          // Panel background
$surface-2: #151515;          // Dash, cards, dialogs
$surface-3: #1C1C1C;          // Elevated elements, menus
$surface-4: #242424;          // Hover states on surface-2/3
$surface-5: #2C2C2C;          // Active states

// Text
$text-primary: #FFFFFF;
$text-secondary: rgba(255, 255, 255, 0.7);
$text-hint: rgba(255, 255, 255, 0.5);
$text-disabled: rgba(255, 255, 255, 0.3);

// Accent overlays
$accent-hover: rgba(0, 153, 255, 0.15);
$accent-active: rgba(0, 153, 255, 0.25);
$accent-selected: rgba(0, 153, 255, 0.35);

// Status
$success: #26a269;
$warning: #e5a50a;
$error: #e01b24;
$destructive: #c01c28;

// Map to GNOME Shell expected variables
$selected_bg_color: $intergen-blue;
$selected_fg_color: #ffffff;
$bg_color: $surface-base;
$fg_color: $text-primary;
$base_color: $surface-2;
$system_bg_color: $surface-1;
$osd_bg_color: rgba($surface-2, 0.9);
$osd_fg_color: $text-primary;
$panel_bg_color: rgba($surface-1, 0.6);  // Transparent panel
$panel_fg_color: $text-primary;
$borders_color: rgba(255, 255, 255, 0.08);
```

### Transparent Panel Implementation

```scss
// Panel: semi-transparent by default, more opaque when window near
#panel {
  background-color: $panel_bg_color;  // rgba(15, 15, 15, 0.6)
  color: $panel_fg_color;
  font-family: 'Inter', sans-serif;
  font-weight: 500;
  transition-duration: 250ms;
  
  &.solid {
    background-color: rgba($surface-1, 0.95);
  }
  
  .panel-button {
    color: $text-primary;
    -natural-hpadding: 10px;
    
    &:hover {
      background-color: rgba(255, 255, 255, 0.1);
    }
    
    &:active, &:checked {
      background-color: $accent-active;
    }
  }
}
```

### ECG Pulse Visual Continuity

To make the shell feel like a continuation of the boot/greeter experience:
- `gnome-shell-start.svg`: Use an ECG pulse line design with InterGenOS logo
- Panel accent line: Add a subtle 1px blue line at the bottom of the panel (or top of overview)
```scss
#panel {
  // Subtle blue bottom border reminiscent of ECG line
  border-bottom: 1px solid rgba($intergen-blue, 0.3);
}
```
- Active indicators use blue: running app dots, selected workspace highlight, toggle switches
- The overall dark + blue aesthetic should mirror what users saw during boot

### Theme Source Tree for InterGenOS

```
intergen-theme/
├── src/
│   ├── gnome-shell/
│   │   ├── gnome-shell.scss
│   │   ├── _intergen-colors.scss
│   │   ├── _variables.scss
│   │   ├── _drawing.scss
│   │   ├── _common.scss
│   │   └── assets/
│   │       ├── toggle-on.svg
│   │       ├── toggle-off.svg
│   │       ├── checkbox.svg
│   │       ├── checkbox-off.svg
│   │       ├── process-working.svg
│   │       ├── no-events.svg
│   │       ├── no-notifications.svg
│   │       ├── close.svg
│   │       ├── calendar-today.svg
│   │       └── gnome-shell-start.svg
│   ├── gtk-4.0/
│   │   └── gtk.css
│   └── gtk-3.0/
│       └── gtk.css
├── build.sh                  # Compile SCSS + assemble theme
├── install.sh                # Install to system
├── gresource/
│   ├── gnome-shell-theme.gresource.xml
│   └── build-gresource.sh   # Compile GDM GResource
├── index.theme
└── gsettings/
    └── 10-intergen-theme.gschema.override
```

### Build Script Skeleton

```bash
#!/bin/bash
# build.sh

THEME_NAME="InterGenOS"
SASS_CMD="sassc"  # or "sass" for dart-sass
SRC_DIR="src"
BUILD_DIR="build/$THEME_NAME"

mkdir -p "$BUILD_DIR/gnome-shell"
mkdir -p "$BUILD_DIR/gtk-4.0"
mkdir -p "$BUILD_DIR/gtk-3.0"

# Compile GNOME Shell CSS
$SASS_CMD "$SRC_DIR/gnome-shell/gnome-shell.scss" "$BUILD_DIR/gnome-shell/gnome-shell.css"

# Copy assets
cp "$SRC_DIR/gnome-shell/assets/"*.svg "$BUILD_DIR/gnome-shell/"

# Copy GTK CSS
cp "$SRC_DIR/gtk-4.0/gtk.css" "$BUILD_DIR/gtk-4.0/"
cp "$SRC_DIR/gtk-3.0/gtk.css" "$BUILD_DIR/gtk-3.0/"

# Copy index.theme
cp index.theme "$BUILD_DIR/"

echo "Theme built in $BUILD_DIR"
```

### GTK4/libadwaita CSS for InterGenOS

```css
/* src/gtk-4.0/gtk.css */
/* InterGenOS GTK4/libadwaita color overrides */

@define-color accent_bg_color #0099FF;
@define-color accent_fg_color #ffffff;
@define-color accent_color #0099FF;

@define-color window_bg_color #0A0A0A;
@define-color window_fg_color #ffffff;

@define-color headerbar_bg_color #0F0F0F;
@define-color headerbar_fg_color #ffffff;
@define-color headerbar_backdrop_color #0A0A0A;

@define-color card_bg_color #151515;
@define-color card_fg_color #ffffff;

@define-color view_bg_color #0A0A0A;
@define-color view_fg_color #ffffff;

@define-color sidebar_bg_color #0F0F0F;
@define-color sidebar_fg_color #ffffff;
@define-color sidebar_backdrop_color #0A0A0A;

@define-color popover_bg_color #1C1C1C;
@define-color popover_fg_color #ffffff;

@define-color dialog_bg_color #151515;
@define-color dialog_fg_color #ffffff;

@define-color borders rgba(255, 255, 255, 0.08);

@define-color warning_bg_color #e5a50a;
@define-color error_bg_color #e01b24;
@define-color success_bg_color #26a269;
@define-color destructive_bg_color #c01c28;
```

---

## Summary of Key Insights

1. **A GNOME Shell theme is primarily one CSS file** (`gnome-shell.css`) plus SVG assets. The CSS is parsed by the St toolkit, which supports a CSS3 subset plus some custom properties.

2. **The fastest path to a distinctive theme** is forking upstream SCSS, overriding 6-10 color variables, and customizing assets. You do NOT need to write thousands of lines from scratch — the upstream `_common.scss` handles all selectors.

3. **For GNOME 49**, use CSS custom properties (`--accent-bg-color`, etc.) as the backbone. This aligns with GNOME's direction and makes it easier to support accent color preferences.

4. **The shell and GTK themes are completely separate**. You need both for visual consistency. libadwaita apps are themed via `@define-color` overrides or the accent-color API.

5. **For a distribution**, the theme should be compiled into a GResource for GDM, installed to `/usr/share/themes/` for the session, and activated via a gsettings schema override.

6. **Development workflow**: Use `gnome-shell --nested --wayland` for safe testing, `sass --watch` for auto-compilation, and `Main.loadTheme()` in Looking Glass for live reload.

7. **Your InterGenOS palette** (`#0099FF` accent, `#0A0A0A` base, white text, Inter font) maps cleanly to GNOME Shell's color variable system. With the surface hierarchy I outlined (surface-0 through surface-5), you'll have proper depth and visual hierarchy throughout the shell.agentId: aead867a2305ac7be (use SendMessage with to: 'aead867a2305ac7be' to continue this agent)
<usage>total_tokens: 26089
tool_uses: 0
duration_ms: 307284</usage>