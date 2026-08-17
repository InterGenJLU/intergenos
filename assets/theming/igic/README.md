# igic/ — InterGenOS Icon Compiler (prototype 0.2.0)

A deterministic, recipe-driven compiler that turns colorless glyphs + per-icon recipes +
the controlled palette into a validated, provenance-stamped freedesktop icon theme.
Renderer is cairosvg (the house pattern — `assets/intergen-mark/generate.py`,
VISUAL_LANGUAGE.md §15).

- **Run it:** see `../RUN.md` (exact commands + expected outputs).
- **The spec:** see `../intergen-icon-compiler-design.md` (executable-grade design;
  every field, gate, and failure mode pinned; the BUILT-vs-SPEC-ONLY ledger in §19).

Layout:
- `palette.yaml` — controlled palette (VISUAL_LANGUAGE.md §4 tokens + a folder/MIME
  accent starter set; the authoritative accent enumeration is the operator's
  `ICON_CATEGORIES.md`).
- `igic_core.py` — library: palette, XXE-safe parser, both validation gates, glyph
  load/sanitize/color-strip, template + pulse + glow geometry, provenance, compositor.
- `validate_icons.py` — standalone two-gate validator (also gates arbitrary SVG files).
- `build_icons.py` — the compiler CLI (recipes → theme + report + contact sheet).
- `glyphs/` — 8 colorless monochrome glyphs (the AI-draftable unit; sanitized by gate 2).
- `recipes/` — 11 icon recipes (the 7 VL anchors + folder color-coding + a symbolic icon
  + the pulse signature).

Determinism is load-bearing: no wall-clock, no RNG — same inputs produce byte-identical
output, which is what makes regenerate-and-diff meaningful.
