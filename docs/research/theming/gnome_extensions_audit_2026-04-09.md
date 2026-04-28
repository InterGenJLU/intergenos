# GNOME Extensions Audit — April 9, 2026

## Source
Research conducted on InterGenOS laptop (hp-laptop) via extensions.gnome.org API.
All extensions verified for GNOME Shell 49 compatibility.

## Default-Enabled Extensions (6)

| UUID | Name | Version | Downloads | Purpose |
|------|------|---------|-----------|---------|
| user-theme@gnome-shell-extensions.gcampax.github.com | User Themes | v69 | 7,156,284 | Required for custom shell theme loading |
| appindicatorsupport@rgcjonas.gmail.com | AppIndicator Support | v64 | 2,859,367 | System tray icons (Discord, Steam, etc.) |
| CoverflowAltTab@palatis.blogspot.com | Coverflow Alt-Tab | v83 | 1,483,764 | 3D window switcher |
| blur-my-shell@aunetx | Blur my Shell | v71 | 4,330,896 | Blur effects on panel/overview/lockscreen |
| bluetooth-quick-connect@bjarosze.gmail.com | Bluetooth Quick Connect | v54 | 688,417 | Connect/disconnect BT from panel |
| burn-my-windows@schneegans.github.com | Burn My Windows | v48 | 1,193,333 | TV power-off effect on window close |

## Pre-Installed Extensions (18)

| UUID | Name | Version | Downloads | Category |
|------|------|---------|-----------|----------|
| dash-to-dock@micxgx.gmail.com | Dash to Dock | v104 | 10,943,018 | Layout |
| dash-to-panel@jderose9.github.com | Dash to Panel | v73 | 4,920,654 | Layout |
| arcmenu@arcmenu.com | ArcMenu | v71 | 2,606,827 | Layout |
| just-perfection-desktop@just-perfection | Just Perfection | v36 | 1,802,405 | Customization |
| caffeine@patapon.info | Caffeine | v59 | 2,817,974 | Utility |
| gsconnect@andyholmes.github.io | GSConnect | v71 | 2,486,616 | Connectivity |
| Vitals@CoreCoding.com | Vitals | v74 | 2,192,182 | System Monitor |
| clipboard-indicator@tudmotu.com | Clipboard Indicator | v69 | 2,165,875 | Utility |
| ding@rastersoft.com | Desktop Icons NG | v83 | 1,484,141 | Desktop |
| tilingshell@ferrarodomenico.com | Tiling Shell | v71 | 716,084 | Window Mgmt |
| forge@jmmaranan.com | Forge | v89 | 332,022 | Window Mgmt |
| mediacontrols@cliffniff.github.com | Media Controls | v47 | 448,909 | Media |
| desktop-cube@schneegans.github.com | Desktop Cube | v31 | 521,177 | Visual |
| AlphabeticalAppGrid@stuarthayhurst | Alphabetical App Grid | v44 | 472,624 | Organization |
| ddterm@amezin.github.com | ddterm | v64 | 367,044 | Terminal |
| nightthemeswitcher@romainvigier.fr | Night Theme Switcher | v81 | 320,224 | Theming |
| show-desktop-button@amivaleo | Show Desktop Button | v49 | 204,426 | Utility |
| rounded-window-corners@fxgn | Rounded Window Corners | v15 | 192,405 | Visual |

## Installation Gotchas

1. **chmod -R a+rX** is MANDATORY after unzip — zips preserve 600 perms
2. **glib-compile-schemas** must run in each extension's `schemas/` dir AND globally
3. gsettings override sets DEFAULT for new users — existing users need manual enable
4. Extensions are JS in gnome-shell's process — only install versions declaring our shell version

## Rejected Extensions

- CoverflowAltTab GitHub release (v77) only declared shell-version 46-47
- EGO version (v83) declares 44-49 — used the EGO version instead
- Sentinel policy: don't patch metadata.json to override version checks, don't disable version validation globally
