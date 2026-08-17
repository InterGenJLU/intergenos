#!/usr/bin/env python3
"""Unclipped per-side margin sweep — the edge-law gate the raster canvas cannot see.

The compiled canvas clips at the viewBox, so a raster render can never show content
that was pushed OFF it: max-extent measurements read "in band" while a mark's overlay
is partially or fully outside the frame (the network-acquiring ellipsis regression,
found 2026-07-23). This gate renders every emitted symbolic on a PADDED canvas
(viewBox -128 -128 512 512 at 512px, 1 unit = 1 px) where off-canvas geometry is
visible, measures the alpha>64 content bbox, and asserts >= MARGIN_MIN px of clearance
to every side of the true 256 canvas rect.

Usage:
  python3 sweep_clip.py             # sweep every PASS symbolic in out/build-report.json
                                    #   exit 0 = corpus clean; 1 = any violation
  python3 sweep_clip.py FILE.svg …  # gate arbitrary composed SVGs (the reject-fixture
                                    #   self-test: fixtures/reject-offcanvas-overlay.svg
                                    #   MUST exit 1 here — run it in every gate battery)
"""
from __future__ import annotations

import io, json, re, sys
from pathlib import Path

from PIL import Image
import cairosvg

CANVAS = 256
PAD = 128
MARGIN_MIN = 1.0        # ratified edge law: >=1px canvas clearance per side at 256
ALPHA_GEOM = 64         # geometry threshold (glow/AA halo excluded)

_VIEWBOX_RE = re.compile(r'viewBox="0 0 256 256"')


def side_margins(svg_text: str) -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) clearance of the alpha>64 content bbox to the true
    canvas rect, measured on the padded render. Negative = off-canvas. None = empty."""
    padded, n = _VIEWBOX_RE.subn(f'viewBox="-{PAD} -{PAD} {CANVAS+2*PAD} {CANVAS+2*PAD}"',
                                 svg_text, count=1)
    if n != 1:
        raise ValueError("input is not a 256-canvas composed SVG (viewBox not found)")
    buf = io.BytesIO()
    cairosvg.svg2png(bytestring=padded.encode(), write_to=buf,
                     output_width=CANVAS + 2 * PAD, output_height=CANVAS + 2 * PAD)
    a = Image.open(buf).convert("RGBA").getchannel("A")
    bb = a.point(lambda v: 255 if v > ALPHA_GEOM else 0).getbbox()
    if not bb:
        return None
    x0, y0, x1, y1 = bb          # padded px; canvas rect sits at [PAD, PAD+256)
    return (x0 - PAD, y0 - PAD, PAD + CANVAS - x1, PAD + CANVAS - y1)


def check(name: str, svg_text: str) -> list[str]:
    m = side_margins(svg_text)
    if m is None:
        return [f"{name}: no geometry above alpha {ALPHA_GEOM}"]
    sides = dict(zip(("left", "top", "right", "bottom"), m))
    return [f"{name}: {s} margin {v:.1f}px < {MARGIN_MIN:g}px"
            + (" (OFF-CANVAS)" if v < 0 else "")
            for s, v in sides.items() if v < MARGIN_MIN]


def sweep_corpus() -> int:
    out_root = Path(__file__).resolve().parent.parent / "out"
    report = json.loads((out_root / "build-report.json").read_text())
    checked, violations = 0, []
    for icn in report["icons"]:
        if icn.get("status") != "PASS" or icn.get("kind") != "symbolic":
            continue
        checked += 1
        violations += check(icn["id"], (out_root / icn["svg"]).read_text())
    for v in violations:
        print(f"  FAIL  {v}")
    print(f"{checked} symbolics swept; {len(violations)} margin violations.")
    return 1 if violations else 0


def sweep_files(paths: list[str]) -> int:
    failures = 0
    for p in paths:
        probs = check(p, Path(p).read_text())
        print(f"  {'FAIL' if probs else 'PASS'}  {p}")
        for m in probs:
            print(f"         - {m}")
        failures += 1 if probs else 0
    return 1 if failures else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(sweep_files(args) if args else sweep_corpus())
