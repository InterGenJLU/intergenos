# Icon Design Research — April 12, 2026

Research across 7 major OS folder icons, top 5 GNOME themes (all time),
and top 5 KDE themes (all time). Informs InterGenOS icon theme design.

---

## Universal Folder Rules (every OS agrees)

1. **Tab is non-negotiable** — the single universal signifier
2. **Two-plane construction** — back panel (with tab) + front panel (flap)
3. **Front-facing orientation** — no isometric, no top-down
4. **Rounded corners** — varies but always present
5. **No outlines at full size** — form defined by fill/gradient/shadow
6. **Wider than tall** — landscape orientation
7. **Matte surfaces** — never glossy

## OS Folder Icon Summary

| OS | Color | Style | Signature move |
|----|-------|-------|----------------|
| Windows 11 | Gold body, blue interior | Semi-flat + subtle depth | Blue interior accent |
| macOS | Blue | Flat-adjacent, precise | Blue = digital-native |
| Android | Dynamic (wallpaper) | Pure flat | Adaptive color theming |
| iOS | Frosted glass | Translucent + blur | Content preview grid |
| Ubuntu (Yaru) | Orange-tan | Flat + subtle depth | Brand warmth via color |
| Fedora (Adwaita) | Blue | Pure flat | Neutral baseline |
| Mint (Mint-Y) | Green | Semi-flat | 17 color variants |

## Top 5 GNOME Icon Themes (All Time)

| Rank | Theme | Stars | Style | Folder signature |
|------|-------|-------|-------|------------------|
| 1 | Papirus | 7.8k | Semi-flat material | Pixel-perfect, 11 colors |
| 2 | Tela | 1.8k | Bold flat geometric | Clean, 14 colors |
| 3 | Flat Remix | 1.8k | Material + depth | Shadow float, 12 colors |
| 4 | Numix Circle | 923 | Uniform circles | Muted, minimal |
| 5 | Candy | 1.3k | Gradient neon | Purple-pink gradient |

## Top 5 KDE Icon Themes (All Time, by KDE Store LaPlace)

| Rank | Theme | Score | Likes | Style |
|------|-------|-------|-------|-------|
| 1 | Candy | 955 | 159 | Gradient neon |
| 2 | Kora | 938 | 191 | Vivid hand-crafted |
| 3 | Tela | 933 | 197 | Bold flat geometric |
| 4 | Newaita-reborn | 932 | 55 | — |
| 5 | Flatery | 929 | 85 | — |
| *9 | *Papirus | *925 | *325 | *#1 in raw engagement* |

## What Makes Top Themes Win

1. **Consistent visual grammar** — one style, every icon, no exceptions
2. **SVG-first, pixel-tuned** — vector source, hand-adjusted at key sizes
3. **Complete coverage** — 500+ app icons minimum
4. **Multiple variants** — dark/light/color options are table stakes
5. **The folder IS the brand** — most-seen icon, most carefully designed
6. **Metaphor clarity** — gear = settings, wrench = tools, instantly clear
7. **Size-appropriate detail reduction** — different detail at 48 vs 16

## What Separates Professional from Amateur

| Quality | Professional | Amateur |
|---------|-------------|---------|
| Stroke weight | Uniform | Varies randomly |
| Corner radius | Consistent rule | Mixed |
| Color palette | Deliberate, limited | Random, clashing |
| Grid alignment | Pixel grid at key sizes | Fuzzy edges |
| Coverage | 500+ apps | 50-100 with gaps |
| Folder icon | Signature piece, multiple colors | Afterthought |
| Symbolic icons | Full set | Missing or borrowed |
| Sizes | 16, 22, 24, 32, 48, 64 | One or two |

## Key Insight: Coverage + Personality + Ecosystem = Winner

- **Papirus** wins by coverage (if an app exists, it has an icon)
- **Candy/Kora** win by personality (instantly recognizable)
- **Flat Remix** wins by ecosystem (icons + GTK + shell + cursors)
- InterGenOS has the ecosystem (shell/GTK already built). Now needs
  personality (the "glow on dark" visual language) and coverage (systematic
  generation pipeline)

## Folder Color Variants Are Required

| Theme | Folder colors |
|-------|--------------|
| Flat Remix | 33 |
| Papirus | 17 |
| Tela | 14 |
| Mint-Y | 12 |
| Minimum viable | 8-10 |

Plan folder template for parameterized color from day one.

## Design Direction for InterGenOS Folder

Owner's instinct (confirmed before research):
> "I think I like the thought of our deep navy, with our electric blue
> being an accent."

This aligns with:
- The visual language: darkness is canvas, blue is energy
- The shell theme: `--bg-card` surfaces with `--accent` borders
- **No other major OS or top theme uses a dark-bodied folder with
  glowing accent edges.** This would be genuinely unique.

The closest analog is Candy Icons (gradient glow on dark) — but Candy
uses saturated gradient fills, not our restrained "glow, don't tint"
approach. Our folder would be dark surfaces with blue energy at the
structural edges (tab seam, panel crease, bottom edge) — the folder
GLOWS rather than being colored. Same philosophy as the shell buttons.

---

*Research complete. Ready to design.*
