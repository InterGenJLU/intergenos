# Theme, Icon, and Cursor Audit — April 9, 2026

## Source
Research conducted on InterGenOS laptop via GitHub repos and gnome-look.org data.
All themes verified for GTK4/libadwaita and GNOME Shell 48+ compatibility.

## GTK/Shell Themes (10 families, ~60 variants)

| Theme | GitHub Stars | GTK4 | Shell 48+ | License | Source |
|-------|-------------|------|-----------|---------|--------|
| WhiteSur | 8,752 | YES | YES | MIT | vinceliuice/WhiteSur-gtk-theme |
| Orchis | 3,846 | YES | YES | GPL-3 | vinceliuice/Orchis-theme |
| Nordic | 2,672 | YES | YES | GPL-3 | EliverLara/Nordic |
| adw-gtk3 | 1,946 | Bridge* | N/A | LGPL-2.1 | lassekongo83/adw-gtk3 |
| Sweet | 1,666 | YES | YES | GPL-3 | EliverLara/Sweet |
| Graphite | 1,438 | YES | YES | GPL-3 | vinceliuice/Graphite-gtk-theme |
| Colloid | 1,394 | YES | YES | GPL-3 | vinceliuice/Colloid-gtk-theme |
| Fluent | 1,381 | YES | YES | GPL-3 | vinceliuice/Fluent-gtk-theme |
| Dracula | 1,046 | YES | YES | GPL-3 | dracula/gtk |
| Catppuccin | 960 | YES | YES | GPL-3 | catppuccin/gtk |

*adw-gtk3 bridges GTK3 apps to match libadwaita — essential for visual consistency.

## Icon Themes (7 families) — Coverage Audit

| Theme | Unique App Icons | Total Files | Rating |
|-------|-----------------|-------------|--------|
| Papirus / Papirus-Dark | 8,735 | 305,384 | EXCELLENT — best coverage |
| Kora | 8,061 | 23,453 | EXCELLENT |
| Qogir / Qogir-Dark | 6,953 | 56,974 | VERY GOOD |
| Fluent / Fluent-dark | 6,965 | varies | VERY GOOD |
| Tela / Tela-dark | 6,690 | 52,072 | VERY GOOD |
| WhiteSur / WhiteSur-dark | 1,170 | 53,348 | MODERATE — curated, relies on fallback |
| Colloid / Colloid-Dark | 1,162 | 47,788 | MODERATE — curated, relies on fallback |

All themes inherit from hicolor as fallback — no completely missing icons.

## Cursor Themes (4 families)

| Theme | GitHub Stars | License | Variants |
|-------|-------------|---------|----------|
| Bibata | 3,482 | GPL-3 | Modern-Classic, Modern-Ice, Modern-Amber, Original-Classic |
| Apple Cursor (macOS) | 1,885 | GPL-3 | macOS, macOS-White |
| Phinger | 763 | CC-BY-SA-4.0 | light, dark, light-left, dark-left |
| WhiteSur Cursors | 364 | GPL-3 | WhiteSur-cursors |

## Default Theme Combination

- GTK: adw-gtk3-dark (matches libadwaita for GTK3 apps)
- Shell: Orchis-Dark
- Icons: Papirus-Dark
- Cursor: Bibata-Modern-Classic
- Color scheme: prefer-dark

## Suggested Combos for First-Boot Greeter

1. Orchis-Dark + Papirus-Dark + Bibata-Modern-Classic (DEFAULT)
2. WhiteSur + WhiteSur icons + macOS cursors
3. Catppuccin-Mocha + Papirus-Dark + Bibata-Modern-Ice
4. Nordic + Papirus-Dark + phinger-cursors-dark
5. Graphite-Dark + Tela-dark + Bibata-Modern-Classic
6. Dracula + Papirus-Dark + Bibata-Modern-Amber

## License Note

Some install.sh scripts don't copy LICENSE/COPYING to installed dirs.
Build system must explicitly copy license files after install.
