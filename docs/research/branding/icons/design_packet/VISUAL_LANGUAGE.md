# InterGenOS Visual Language

**Status:** v1 — April 11, 2026
**Scope:** All user-facing visual assets: logo, shell theme, icons, cursors,
wallpapers, boot chain, installer, wordmark, promotional materials.
**Authoritative source of palette & interaction rules:** the committed shell,
GTK3, and GTK4 themes at `assets/intergen-shell-theme/` as of commit `79e32c9`.

---

## 1. Purpose

This document is the single source of truth for how InterGenOS looks. Every
visual asset — from the 16-pixel favicon to the full-screen boot animation —
must descend from the rules in this document. Nothing is arbitrary. Nothing
is borrowed. Every pixel should be able to answer the question "why is it
this way?" with a reference to something here.

If a design decision cannot be justified by a rule in this document, the
rule is missing and this document must be updated before the decision is
finalized. If a new decision contradicts an existing rule, one of the two
must change — never both.

This document is a living spec. It evolves as the system matures. Every
update must be committed to the repository with a clear rationale.

---

## 2. The Prime Directive (visual interpretation)

> *InterGenOS exists to put the user in control of their own machine.*

Applied to visual design, this means:

- **Clarity over decoration.** Every visual element must communicate
  something functional or emotional. Pure ornament is not welcome.
- **Deliberate over default.** No stock assets, no "good enough" choices,
  no hiding behind convention.
- **Nothing is hidden.** What the system is doing is visible. What the
  system is should be legible from how it looks.
- **Restraint is a feature.** Empty space is not waste. Bold simplicity
  beats busy complexity.

---

## 3. Brand Story — Light Emerging from Dark

The core philosophy, transcribed directly from the canonical shell CSS:

> **The darkness isn't empty — it's the canvas.**
> **The blue isn't decoration — it's energy.**
> **Every interactive element responds with light.**
> **Touching the UI activates it — glow responds to input.**

InterGenOS is a system that is **alive** — not animated, not cute, but
genuinely aware. It has a heartbeat. It watches over the user without
surveilling them. It is dark because it respects attention. It is blue
because blue is the color of the signal that proves something is alive.

The visual system is not decorative. It is diagnostic. Every element
reports on the system's state: healthy, working, waiting, ready. The
user should be able to read the interface the way a doctor reads an ECG —
at a glance, with confidence.

Every interactive element begins inert and near-invisible. When you
touch it — hover, focus, press — it ignites. Borders that were barely
visible now glow. Backgrounds that were black now carry a subtle blue
charge. The UI is dormant until engaged, alive under the user's hand.

---

## 4. Palette

### The three-tier dark system

The background is not one color. It is a hierarchy of near-blacks with
blue DNA in the pigment. Tokens are named for their role, not their value.

| Token            | Hex       | Role                                     |
|------------------|-----------|------------------------------------------|
| `--bg-void`      | `#050810` | Deepest dark — the canvas                |
| `--bg-surface`   | `#0a0e1a` | Elevated surface — panels, popovers      |
| `--bg-card`      | `#0f1525` | Cards, toggles, entry backgrounds        |
| `--bg-view`      | `#080c18` | Content views (file manager, text areas) |
| `--bg-sidebar`   | `#030609` | Deepest container — sidebars             |

The shift from void to card is ~4% lightness — just enough to register
as a layer, never enough to break the dark unity.

**Pure black (`#000000`) is reserved for the logo mark.** It is the only
place pure black appears in the system, and only because the ECG pulse
needs maximum contrast against the purest possible field to read as a
signal. Everywhere else, the void (`#050810`) is canonical — black with
a whisper of blue in its soul.

### The three-tier blue

| Token              | Value                        | Role                              |
|--------------------|------------------------------|-----------------------------------|
| `--accent`         | `#0099FF`                    | ECG blue — the primary pulse color|
| `--accent-bright`  | `#33b1ff`                    | Hover / active intensification    |
| `--accent-glow`    | `rgba(0, 153, 255, 0.4)`     | The light behind elements         |

### The three-tier text

| Token          | Hex       | Role                                   |
|----------------|-----------|----------------------------------------|
| `--text`       | `#e2e8f0` | Primary text — cool white              |
| `--text-dim`   | `#7a8ba8` | Secondary text — slate                 |
| `--text-ghost` | `#3d4f6a` | Ghost text — disabled, placeholders    |

For selected states and active hover, text becomes pure white `#ffffff`.
This is not a fourth text tier — it is a signal that the element is now
"live." Pure white text means "the system acknowledges you are here."

### The border system

Borders are invisible by default and glow on interaction. This is the
**most important visual rule** in the system and is worth repeating
exactly as written in the shell CSS:

| Token             | Value                         | Role                         |
|-------------------|-------------------------------|------------------------------|
| `--border`        | `rgba(0, 153, 255, 0.08)`     | Structural — almost invisible|
| `--border-glow`   | `rgba(0, 153, 255, 0.25)`     | Interactive — hover / focus  |
| `--border-active` | `rgba(0, 153, 255, 0.6)`      | Active / pressed / selected  |

Borders are **never** neutral gray. The system's structure is traced
in blue — you just can't see it until you need to.

### State colors

| Name    | Hex       | Usage                            |
|---------|-----------|----------------------------------|
| Success | `#10b981` | Green — terminal, system health  |
| Warning | `#f59e0b` | Amber — warnings only            |
| Error   | `#ef4444` | Red — errors, destructive, close |

These are reserved for semantic state. They never appear for decoration.
Red is the color of the window close button and the destructive action.
Amber is the color of end-session warnings. Green is the color of a
running terminal and an OK status.

---

## 5. Typography

### Primary typeface

**Inter** (variable font, open source, SIL OFL 1.1)

Inter is the default interface font across the entire system: shell, apps,
installer, welcome greeter, wordmark. The shell CSS declares
`font-family: 'Inter', 'Cantarell', sans-serif` — Cantarell is the fallback
for systems where Inter is not installed.

### Secondary typeface (monospace)

**JetBrains Mono** (open source, SIL OFL 1.1)

For all fixed-width contexts: terminal, code blocks, any place where
character alignment matters. The existing gsettings override specifies
DejaVu Sans Mono as the monospace font — we will transition this to
JetBrains Mono as part of the visual language rollout.

### Type scale (from the shell CSS)

| Context                 | Weight      | Size    |
|-------------------------|-------------|---------|
| Stage default (shell)   | Regular 500 | 11pt    |
| Panel clock             | Semibold 600| 11pt    |
| Popup menu item         | Medium 500  | 11pt    |
| Quick toggle title      | Semibold 600| 9pt     |
| Quick toggle subtitle   | Regular 400 | 8pt     |
| Header / dialog title   | Bold 700    | varies  |
| Lock screen clock       | Bold 700    | 56pt    |
| OSD monitor label       | Bold 700    | 32pt    |

### Rules

- **Antialiasing required.** No pixel-forced text except in intentional
  retro contexts (clearly demarcated).
- **No italic system text.** Italic is permitted in user content (docs,
  markdown rendering) but not in system UI.
- **Letter-spacing: 0.** Inter is designed for zero tracking. Do not
  adjust.
- **Font weights are semantic.** 400 is body, 500 is a slight emphasis
  (panel buttons, menu items), 600 is a heading or active element, 700
  is a title or dialog header. Do not use weights arbitrarily.

---

## 6. Line and Stroke

The visual system is **line-forward**. Most symbolic assets are built
from strokes, not fills. The logo is a stroke. Icons will be strokes.
Cursors will be strokes.

### Stroke anatomy

- **Caps:** always `round`. Never `butt`, never `square`.
- **Joins:** always `round`. Never `miter`, never `bevel`.
- **Fills:** rare. Stroke over fill by default. If a fill is used, it is
  ECG Blue at 100% opacity (for the active state of a switch or checkbox),
  and the element has no stroke.

### Glow, don't tint — the core rule

The most important rule in the entire visual system:

> **Accents emit light. They do not stain surfaces.**

When you hover a button, the border does not turn blue — the border
*glows* blue. The background does not become saturated — a soft blue
light lands on it, picking out the element from the dark.

Concretely:
- A structural border is `rgba(0, 153, 255, 0.08)` — you can barely see it.
- On hover, it becomes `rgba(0, 153, 255, 0.22)` — visible, still not a
  solid blue.
- The background behind it gains `box-shadow: 0 0 12px rgba(0, 153, 255, 0.1)` —
  a soft cyan corona surrounds the element without touching its interior.

This is why the system feels alive. Light emerges from dark surfaces in
response to touch. Static color assignment would feel dead.

The shell CSS header phrases it: *glow on interaction, not tint*.

### The adaptive stroke rule

A logo mark or icon that needs to scale across a wide size range cannot
use a single stroke weight. Below a certain pixel threshold, thin strokes
become sub-pixel and blur. Above that threshold, thick strokes become
clumsy.

The rule: **every scalable mark ships in at least two variants.**

- **Detailed variant** — stroke ~2% of canvas width, full geometry.
  Used at the size where a 2% stroke renders as ≥ 2 physical pixels.
- **Simplified variant** — stroke ~6% of canvas width, reduced geometry
  (fine details removed). Used below the detailed variant's minimum.

The rendering pipeline routes each target size to the correct source
variant automatically.

**Reference — the logo mark:**
- Full detail: 512×512 canvas, stroke 10 (≈2%), used for 64 px and above.
- Simplified: 512×512 canvas, stroke 32 (≈6%), Q and T dips removed,
  used for 48 px and below.

The same rule applies to icons, cursors, and any other scalable mark.

---

## 7. Geometric Primitives and Corner Radius

The visual system is built from a small vocabulary of shapes. Adding new
primitive shapes requires updating this document.

### Permitted primitives

- **Straight line segments** (with round caps and joins).
- **Circles** (stroked or filled).
- **Rectangles** (stroked or filled; rounded radii only from the scale below).
- **Polygons** (triangles, hexagons, octagons). No more than 8 sides.
- **Bezier curves** — only for the logo's pulse path and for specific
  named icons where a curve is essential.

### Corner radius scale

Corner radius is not a single value — it scales with element hierarchy.
This scale is extracted directly from the canonical theme CSS.

| Radius | Used for                                                   |
|--------|------------------------------------------------------------|
| 6 px   | Menu item rows, menubar items                              |
| 8 px   | Base buttons, tooltips, pathbar buttons, workspace thumbs  |
| 10 px  | Text entries, list rows, quick toggle menu items, modal buttons |
| 12 px  | Quick toggles, window CSD, toolbar buttons, sidebar rows   |
| 14 px  | Cards, toast notifications, OSD windows, app icon containers|
| 16 px  | Popovers, Looking Glass, dialogs, search section content   |
| 18 px  | Quick settings panel, popup menu content, notifications    |
| 20 px  | Date menu / calendar popover                               |
| 22 px  | Modal dialogs, end-session dialogs                         |
| 24 px  | Lock screen, login dialog                                  |
| 26 px  | Dash pill                                                  |
| 50%    | Avatar buttons, close buttons, check/radio indicators      |
| 9999px | Pills and stadia (switches, progress bars, scrollbar trough)|

Use the smallest radius that fits the element's hierarchy. Do not invent
new radii without updating this scale.

### Grids

- **Icons** use a 256×256 base grid. Design at 256, render at target.
- **Cursors** use a 32×32 base grid. Design at 32, render at 32 (no scaling).
- **Logo mark** uses a 512×512 base grid.
- **All paths** snap to the grid unless a specific design choice
  requires sub-grid positioning (document the reason).

---

## 8. The Pulse Motif

The ECG heartbeat pulse is the **signature motif** of the entire visual
system. It appears in the logo mark, in the boot animation, and as an
accent in certain icons. It is **the reason the visual system exists**.

### The canonical pulse shape

The logo mark's pulse is built from this geometry:

```
QR_SPAN     = 32    Q dip → R peak horizontal span
Q_DIP       = 45    Q dip depth below baseline
PEAK_HEIGHT = 176   R peak height above baseline
```

Derived:
```
Q_SLOPE    = (Q_DIP + PEAK_HEIGHT) / QR_SPAN  = 6.906
ENTRY_SPAN = Q_DIP / Q_SLOPE                  = 6.516
S_DEPTH    = PEAK_HEIGHT                      (full mirror)
DELTA      = (2 × PEAK_HEIGHT) / Q_SLOPE      = 50.967
```

The pulse has full 180° symmetry:
- Q dip mirrors T peak (same magnitude, opposite sign).
- R peak mirrors S trough (same magnitude, opposite sign).
- All diagonal segments share the same slope (6.906).

The layout is asymmetric: short lead-in on the left, long baseline trail
on the right. The trail is where the "InterGenOS" wordmark sits in the
full brand lockup.

### Pulse usage rules

- **The mark itself** is the only place the full pulse appears in public.
- **Derived motifs** (a single peak, a simplified waveform, a rhythmic
  repetition) are permitted in secondary contexts but should always be
  identifiable as descendants of the canonical pulse.
- **The pulse is not decoration.** It means the system is alive. Do not
  use it on static or inert contexts.
- **The pulse is always ECG Blue** (`#0099FF`) or off-white (`#e2e8f0`)
  for reverse contexts. Never any other color.

### Pulse as an accent

Certain icons and surfaces may carry a subtle pulse reference to indicate
"live" state:

- **Terminal icon** — may include a pulse line across the bottom.
- **System monitor** — may use a pulse as its primary shape.
- **Activity / network indicators** — may animate a pulse during use.
- **InterGen's AI panel** — shows the pulse as its primary indicator.
- **Boot animation** — already does this (the first thing you see).

Do not force the pulse where it does not belong. The point is not to
stamp it on every asset — it is to make it show up where aliveness
genuinely matters.

---

## 9. Backgrounds and Transparency

### The void is canonical

Window backgrounds are `--bg-void` (`#050810`). Every GTK application
window, the shell wallpaper, the installer, the welcome greeter — they
all start from the void.

Elevated surfaces use `--bg-surface` (`#0a0e1a`). Cards use `--bg-card`
(`#0f1525`). These are layered on top of the void by their CSS, not
by swapping the window background.

### Transparency is the foundation

The theme is built **for Blur My Shell**. Most popover, dialog, and
panel surfaces specify `rgba(10, 14, 26, 0.75-0.95)` — a partial-opacity
version of the surface color — which combines with Blur My Shell's
gaussian blur to produce frosted glass.

Without Blur My Shell, the partial opacity falls back to readable
solid-ish surfaces, but the full visual experience requires the blur
extension. This is documented; it is not a bug.

**Blur My Shell is a hard dependency** for the intended experience.
It must be installed and enabled in the default InterGenOS configuration.

### Negative space is active

Empty areas are not "unused." They provide the room for the eye to
rest, the silence that makes the pulse audible. Do not fill them.

Rules of thumb:
- Icons have at least **16% padding** from the edge of their canvas.
- The logo mark has asymmetric padding — short on the left, long on
  the right. This is the "perspective" that makes the pulse feel like
  it is approaching the viewer.
- The shell panel, application windows, and dialogs all use generous
  internal padding. Cramped is unacceptable.

---

## 10. Interaction Language

Every interactive element in the system follows a three-state progression
from inert to fully active. This is not style — it is a **protocol** that
the entire theme obeys.

### State progression

| State     | Background                      | Border                           | Box shadow                          |
|-----------|---------------------------------|----------------------------------|-------------------------------------|
| **Inert** | Transparent or `--bg-card` 0.5  | `--border` (0.08)                | none                                |
| **Hover** | Touch of blue (0.06–0.12)       | `--border-glow` (0.15–0.25)      | Soft corona (0.08–0.15)             |
| **Active** / pressed / checked | Deeper blue (0.12–0.18) | `--border-active` (0.30–0.40) | Stronger corona (0.15–0.25)       |
| **Focus** | Same as hover                   | Same as active                   | Same as active                      |
| **Default action** (primary)   | Solid blue wash (0.18–0.45)    | Solid blue (0.40–0.65)        | Strong glow (0.15–0.30)             |
| **Disabled**                   | `--bg-card` very faint (0.2–0.3)| Barely visible (0.03–0.05)    | none                                |

This is not arbitrary — every single widget style in the shell, GTK3,
and GTK4 themes obeys this progression. New assets and components must
follow the same protocol.

### Transitions

Interaction transitions use the following durations, extracted from the
shell CSS:

| Duration | Used for                                            |
|----------|-----------------------------------------------------|
| 150ms    | Popup menu items, quick settings rows, list rows    |
| 180ms    | Sidebar rows, navigation rows                       |
| 200ms    | Buttons, toolbar buttons, cards, buttons at rest    |
| 250ms    | Panel buttons, text entries (focus transitions)     |
| 300ms    | Search entry focus                                  |
| 400ms    | Panel background (solid transition on window touch) |

Ease: `ease-out` by default. Longer for panel-level transitions, shorter
for immediate interaction feedback.

### Easing philosophy

The UI should feel like it **responds** to the user, not like it is
animating at them. Durations are short. Nothing bounces. Nothing wobbles.
Everything glides.

---

## 11. Icon System

### Design principles

1. **Outline first.** Icons are strokes. Fills are rare.
2. **Single concept per icon.** A file icon is a file. A folder icon is
   a folder. Do not layer metaphors.
3. **Blue is the accent.** Most of the icon is off-white or blue line
   work. A single blue highlight is used for the one element that makes
   the icon "that specific thing" — the handle of the terminal, the lid
   of the trash, the gear teeth of settings.
4. **No overlays.** No "notification dots," no "online indicators,"
   no badges stamped onto existing icons.
5. **No text in icons.** "T" for terminal, "A" for apps, etc. are
   forbidden. Icons are symbols, not letters.

### Icon anatomy

All icons are designed at **256×256** and rendered at target sizes using
the adaptive stroke rule (§6).

| Element              | Spec                                                  |
|----------------------|-------------------------------------------------------|
| Canvas               | 256 × 256                                             |
| Primary stroke       | 12 pixels (≈ 4.7%) for detailed variant               |
| Simplified stroke    | 24 pixels (≈ 9.4%) for small-size variant             |
| Minimum margin       | 40 pixels from canvas edge (16% padding)              |
| Primary color        | Off-white `#e2e8f0`                                   |
| Accent color         | ECG Blue `#0099FF`                                    |
| Background           | Transparent (icon renders onto whatever surface)       |

### Icon categories

1. **Application icons** — apps installed on the system
2. **File / MIME icons** — document types
3. **Device icons** — drives, mounts, peripherals
4. **Folder icons** — file manager folders
5. **Action icons** — toolbar buttons, menu items
6. **Status icons** — system tray, indicator area
7. **Place icons** — bookmarks, locations, network

Each category has a shared visual treatment (to be documented in a
companion `ICON_CATEGORIES.md` once anchor icons are designed).

### Anchor icons (Phase 2)

The following seven icons set the style for everything else. They will
be designed first, iterated until they feel right, and then the visual
rules will be extracted and applied to the rest:

1. **Folder** (plain, open, important, downloads)
2. **Terminal** / **Console**
3. **Settings** / **Gear**
4. **File** / **Document** (plain)
5. **Application launcher** / **Grid**
6. **Web browser** / **Globe**
7. **Trash** / **Bin**

Once these seven feel right, everything else scales from them.

---

## 12. Cursor System

### Design principles

1. **Cursors are also line art.** Same rules as icons, but tighter
   stroke and simpler geometry.
2. **The arrow is an arrow.** Not a pointing finger, not a character,
   not a mascot. The arrow.
3. **Blue accent on the hotspot.** The user needs to know exactly
   where the cursor "is." The hotspot pixel is marked with ECG Blue.
4. **Busy state is a pulse.** The busy cursor (`watch`, `wait`) is
   an animated pulse — the system is thinking, but alive.

### Cursor anatomy

Cursors are designed at **32×32** and rendered at that exact size. No
scaling. They are drawn for pixels, not vectors.

Xcursor supports multi-size cursors for HiDPI, so we ship the following
sizes:

- `24×24` (legacy, low-density)
- `32×32` (canonical)
- `48×48` (HiDPI)
- `64×64` (4K HiDPI)

### Cursor inventory

The full cursor theme requires ~25 named cursors. The anchors are:

1. `default` / `arrow` — the standard arrow
2. `pointer` / `hand` — for clickable elements
3. `text` / `xterm` — text selection I-beam
4. `wait` / `watch` — busy indicator (animated pulse)
5. `progress` — arrow with small busy indicator
6. `crosshair` — precise selection
7. `resize-*` — eight compass directions
8. `move` — four-way arrow
9. `not-allowed` — denied action
10. `grab` / `grabbing` — dragging

Everything else is a variation or alias of the anchors.

---

## 13. Taboos

What we never do:

- **No gradients on icons, marks, or cursors.** Subtle vertical gradients
  on shell/GTK headerbars and panel backgrounds are permitted because
  they reinforce the "emerging from dark" feel. Progress bars and sliders
  use a subtle ECG → ECG-bright gradient to suggest flow.
- **No drop shadows on flat assets.** Elevation in the theme uses
  `box-shadow` with black 0.3–0.7 alpha to suggest floating surfaces.
  That is a surface effect, not an asset effect. Icons never ship with
  pre-baked shadows.
- **No 3D.** No beveled buttons, no rendered surfaces, no faux materials.
  This is a flat, honest, line-based system.
- **No photorealism in symbolic assets.** Icons never look like
  photographs. Photographic assets are reserved for wallpapers and boot
  imagery.
- **No stock iconography.** No Font Awesome, no Material Icons, no
  Feather, no Heroicons. Every icon in the system is drawn for InterGenOS.
- **No text in icons.** "T" for terminal, "A" for apps, etc. are forbidden.
- **No mascots.** InterGenOS does not have a mascot character. The pulse
  is the identity. That is enough.
- **No "cute."** The visual system is serious. Not cold, but serious.
  This is a power-user system for people who understand their machines.
- **No copying.** If a proposed design resembles an existing theme, the
  design is rejected and we start over.

---

## 14. Coordination with the Shell Theme

The GNOME Shell, GTK3, and GTK4 themes at `assets/intergen-shell-theme/`
are the **authoritative implementation** of this visual language. Any
conflict between this document and the committed theme is resolved in
favor of the theme — the theme is real, runs on real hardware, and has
been iterated through dozens of passes. This document exists to explain
and extend what the theme has already established.

Updates to this document must be committed alongside updates to the
theme when the two need to change together.

### Key touchpoints where assets must agree

- Background color (`--bg-void` is canonical; layered surfaces use
  `--bg-surface`, `--bg-card`)
- Accent color (ECG Blue `#0099FF` only)
- Focus ring color and stroke
- Corner radius (see §7)
- Typography (Inter everywhere, JetBrains Mono for monospace)
- Border alpha progression (0.08 → 0.22 → 0.40)
- Shadow/glow alpha progression (0.08 → 0.15 → 0.25)

---

## 15. Implementation Notes

### GTK4 CSS parser pitfall (documented the hard way)

GTK4's CSS parser silently rejects `!important` on the following
properties, emitting `Junk at end of value` warnings to the application's
journal:

- `padding`
- `margin`
- `min-width`
- `min-height`
- `-gtk-icon-size`

**Always check `journalctl --user _COMM=<app>`** for parser errors FIRST
when CSS isn't applying as expected. Stale CSS blocks from earlier
attempts can poison the cascade because the parser silently drops the
entire rule rather than the offending declaration. This single gotcha
cost the shell theme work several hours before being diagnosed.

Use shorthand properties (e.g., shorthand `border-radius: TL TR BR BL`)
instead of longhand with `!important` for the same reason.

### SVG generation

All vector assets in `assets/intergen-mark/` and (eventually)
`assets/intergen-icons/`, `assets/intergen-cursors/` are generated by
Python scripts using `cairosvg`. The source SVG files are committed
alongside the scripts so that the assets can be regenerated at any time
with a single command.

### Versioning

The visual language is versioned. This is v1. Major changes to the
palette, typography, or core primitives bump the major version. Minor
additions (new icon categories, expanded cursor inventory) bump the
minor version. Bug fixes to the document (clarifying existing rules)
bump a patch version.

When a version is cut, all assets in the repository must either validate
against the new version or be explicitly flagged for rework.

### When this document is wrong

If working on a concrete asset reveals that a rule in this document is
wrong, **fix the document first, then continue the work**. Do not make
decisions that silently contradict the document — that is how visual
drift begins.

---

*The pulse is the signal. The signal is the system. The system is yours.*
