# DistroWatch leaderboards

Regenerate both PNGs from the repository root with `python3 assets/intergen-mark/banners/render.py`; requires Pillow, librsvg's `rsvg-convert`, and Inter Variable at `/usr/share/fonts/inter/InterVariable.ttf` (or pass `--font PATH`).

The script renders the committed SVG pulse and typesets Inter directly at each
target size, preserving the wide tracking of the canonical wordmark. Only
“only.” is bold in the tagline. It logs the actual font bytes, variation axes,
renderer versions, and output hashes. The opaque, 8-bit sRGB outputs are
970×90 and 728×90; `--output-dir PATH` writes a separate regeneration for comparison.
