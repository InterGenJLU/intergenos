# HP Laptop GNOME Configuration Capture

**Date:** April 8, 2026
**Source:** HP Laptop 14-dq1xxx running Arch Linux
**Purpose:** Reference for InterGenOS desktop theming — owner's preferred look and feel

---

## Shell Theme
- **GTK Theme:** Adwaita (system default)
- **Shell Theme:** Flat-Remix-Blue-Darkest
- **Icon Theme:** MB-Blueberry-Suru-GLOW
- **Cursor Theme:** phinger-cursors-dark
- **Color Scheme:** prefer-dark

## Fonts
- **UI Font:** Adwaita Sans 11
- **Document Font:** Adwaita Sans 12
- **Monospace:** Adwaita Mono 11
- **Titlebar:** Adwaita Sans Bold 11

## Window Manager
- **Button Layout:** appmenu:minimize,maximize,close
- **Workspaces:** 4
- **Focus Mode:** click

## GNOME Extensions (Enabled)
1. **burn-my-windows** — Window open/close effects (TV effect enabled)
2. **dash-to-panel** — Taskbar-style panel (32px, bottom dot indicators)
3. **CoverflowAltTab** — 3D alt-tab switcher
4. **user-theme** — Custom shell theme support
5. **transparent-window-moving** — Transparency while dragging windows
6. **bluetooth-quick-connect** — Quick BT device connections
7. **lockkeys** — Caps/Num Lock indicator
8. **quick-settings-audio-panel** — Audio controls in quick settings
9. **apps-menu** — Applications menu in top bar
10. **auto-move-windows** — Auto-assign apps to workspaces
11. **launch-new-instance** — Always launch new window
12. **places-menu** — Places menu in top bar
13. **drive-menu** — Removable drive menu
14. **status-icons** — Legacy tray icons
15. **system-monitor** — System monitor in top bar
16. **workspace-indicator** — Workspace indicator
17. **window-list** — Window list in bottom panel
18. **windowsNavigator** — Keyboard nav in overview

## Burn My Windows Config
- **Active Effect:** TV (classic CRT power-off effect)
- All other effects disabled
- Matrix tip color: white, animation time: 979ms

## Dash to Panel Config
- Panel size: 32px
- Panel length: 100%
- Appicon margin: 8, padding: 4
- Dot position: BOTTOM
- Panel anchors: MIDDLE

## Terminal Profile
- Background: black (rgb(0,0,0))
- Foreground: white (rgb(255,255,255))
- Cursor: green (rgb(43,247,6))
- Font: DejaVu Sans Mono 10
- Scrollback: unlimited
- Standard Linux console color palette

## Themes Installed
### GTK/Shell Themes
- Flat-Remix-Blue-Darkest (active)
- Flat-Remix variants (Blue, Grey, Black — Dark/Darkest/Light)
- Abyss variants (BLOOD, DEEP, ENVY, INK)
- Numix-Pack

### Icon Themes
- MB-Blueberry-Suru-GLOW (active)

### Cursor Themes
- phinger-cursors-dark (active)
- phinger-cursors-light
- Vimix-cursors

---

## What to Bake Into InterGenOS

### Must Have (owner's identity)
- Flat-Remix-Blue-Darkest shell theme
- phinger-cursors-dark cursor theme
- burn-my-windows extension (TV effect)
- dash-to-panel extension
- Dark color scheme
- Green cursor in terminal

### Should Have
- CoverflowAltTab
- user-theme extension
- places-menu, drive-menu, status-icons
- system-monitor

### Nice to Have
- MB-Blueberry-Suru-GLOW icons (or InterGenOS custom icon set)
- transparent-window-moving
- bluetooth-quick-connect
