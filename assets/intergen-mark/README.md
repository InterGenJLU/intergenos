# InterGenOS Logo Mark

The InterGenOS logo mark is a single ECG heartbeat pulse — the brand's signature
symbol of a system that is alive, aware, and under its user's control.

## Design

The pulse has full 180° symmetry around a rotation point on the baseline:
- Q dip (small, below baseline) mirrors T peak (small, above baseline)
- R peak (big, above baseline) mirrors S trough (big, below baseline)
- Every diagonal segment shares the same slope

The overall layout is asymmetric — a short lead-in on the left and an
extended baseline trail on the right. The trail is where the "InterGenOS"
wordmark will sit in the full brand lockup.

## Assets

### `svg/`

**Icon (square, 512×512, left-aligned pulse):**
- `intergenos_icon_full.svg` — full Q/R/S/T detail, stroke 10, black bg
- `intergenos_icon_full_transparent.svg` — same, transparent bg
- `intergenos_icon_full_white.svg` — white stroke, transparent bg (for printing)
- `intergenos_icon_simple.svg` — simplified (no Q/T), stroke 32, black bg
- `intergenos_icon_simple_transparent.svg` — simplified, transparent
- `intergenos_icon_simple_white.svg` — simplified, white stroke

**Logo (wide, 998×512, with long right trail for wordmark):**
- `intergenos_logo.svg` — full detail, stroke 10, black bg
- `intergenos_logo_transparent.svg` — transparent
- `intergenos_logo_white.svg` — white stroke, transparent

### `png/`

Hybrid size routing — each icon PNG is rendered from the SVG best suited
to its output size:

| Size range | Source SVG | Reason |
|------------|------------|--------|
| 16, 24, 32, 48 | `intergenos_icon_simple.svg` | Thick stroke + simple path stays readable at tiny sizes |
| 64, 128, 256, 512, 1024 | `intergenos_icon_full.svg` | Preserves Q/R/S/T detail at sizes where it renders clearly |

Both versions read as "heartbeat pulse" so the transition is seamless — this
is the standard approach for logos that need to scale from favicons to
high-res displays.

Logo PNGs are rendered at 512, 1024, 1536, 2048 px wide.

## Palette

- **Background** — pure black `#000000`
- **Pulse** — ECG blue `#0099FF`
- **White variant** — off-white `#e2e8f0` for printing and reversed contexts

## Regenerating

Run `python3 generate.py` from this directory to regenerate all SVG and PNG
assets. Requires `cairosvg` (install with `pip install --user cairosvg`).
