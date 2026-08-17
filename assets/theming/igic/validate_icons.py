#!/usr/bin/env python3
"""IGIC validator — runs the two gates standalone (design §5).

  gate 1  spec compliance  (recipe keys, palette-only, viewBox, node count,
                            safe-area, provenance present)
  gate 2  security         (no script / handlers / external refs / foreignObject /
                            <image> / <use>-external / DOCTYPE-XXE)

Usage:
  python3 validate_icons.py               # validate every recipe + family + composed SVG
  python3 validate_icons.py FILE.svg ...  # gate an arbitrary SVG file (both gates)

Exit code 0 = all clean; 1 = one or more violations (fail-closed).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import igic_core as ic


def _validate_single_unit(u: dict, palette: dict, palette_sha: str) -> list[str]:
    """gate 1 (recipe) + gate 2/1 (glyph) + both gates on the composed SVG, for a
    single recipe OR an expanded family row (identical path — a row IS a recipe)."""
    recipe, recipe_sha = u["recipe"], u["recipe_sha"]
    problems = [f"[gate1/recipe] {m}" for m in ic.validate_recipe(recipe, palette)]
    glyph = None
    gname = recipe.get("glyph")
    if gname and (ic.GLYPH_DIR / f"{gname}.svg").exists():
        glyph = ic.load_glyph(gname)
        problems += [f"[gate2/glyph] {m}" for m in glyph.sanitize()]
        problems += [f"[gate1/glyph] {m}" for m in glyph.structural_violations()]
        if not problems:
            glyph.extract()
    if not problems:                       # compose + gate the output only if inputs are clean
        for variant in ("detailed", "simple"):
            svg = ic.compose_icon(recipe, palette, glyph, variant, recipe_sha, palette_sha)
            problems += [f"[{variant}] {m}" for m in ic.validate_composed_svg(svg, palette)]
    return problems


def _validate_state_unit(u: dict, palette: dict, palette_sha: str) -> list[str]:
    """Compose a states-family unit (BOTH variants, R6-1; plus the small-geometry
    composition when the base carries one) -> its symbolic SVGs, gate each."""
    problems: list[str] = []
    for variant in ("detailed", "simple"):
        svg = ic.compose_state_icon(u["state_id"], palette, u["base_sha"], palette_sha,
                                    u["always_on"], u["levels"], u["level"],
                                    overlays=u.get("overlays"),
                                    state_overlays=u.get("state_overlays", []),
                                    variant=variant,
                                    gauge=u.get("gauge"), dim=u.get("dim", False),
                                    fit=u.get("fit", ic.SYMBOLIC_BAND_FIT))
        problems += [f"[{variant}] {m}" for m in ic.validate_composed_svg(svg, palette)]
    if u.get("small"):
        sm = u["small"]
        svg = ic.compose_state_icon(u["state_id"], palette, u["base_sha"], palette_sha,
                                    sm["always_on"], u["levels"], u["level"],
                                    overlays={**(u.get("overlays") or {}), **sm["overlays"]},
                                    state_overlays=u.get("state_overlays", []),
                                    variant="small",
                                    gauge=u.get("gauge"), dim=u.get("dim", False),
                                    fit=u.get("fit", ic.SYMBOLIC_BAND_FIT))
        problems += [f"[small] {m}" for m in ic.validate_composed_svg(svg, palette)]
    return problems


def validate_recipes() -> int:
    """Validate the FULL set the compiler would build — single recipes AND family
    manifests (rows + states) via the shared ic.gather_units, plus the cross-set
    duplicate-id check. No cairosvg: validation only composes SVG strings."""
    palette = ic.load_palette()
    palette_sha = ic.sha256_file(ic.PALETTE_PATH)
    units, family_failures = ic.gather_units(palette)
    if not units and not family_failures:
        print("no recipes or families found", file=sys.stderr)
        return 1

    dup_ids = {rid for rid, c in Counter(u["id"] for u in units).items() if c > 1}
    failures = 0

    for ff in family_failures:             # family header/base failed the gate
        print(f"  FAIL  {ff['source']:28s} (family manifest)")
        for m in ff["problems"]:
            print(f"         - {m}")
        failures += 1

    for u in units:
        if u["id"] in dup_ids:
            print(f"  FAIL  {u['id']:28s} (duplicate id — {u['source']})")
            failures += 1
            continue
        problems = (_validate_state_unit if u["kind"] == "state" else _validate_single_unit)(
            u, palette, palette_sha)
        status = "PASS" if not problems else "FAIL"
        print(f"  {status}  {(u['id'] or '?'):28s} ({u['source']})")
        for m in problems:
            print(f"         - {m}")
        failures += 1 if problems else 0

    total = len(units) + len(family_failures)
    print(f"\n{total-failures}/{total} icons clean; {failures} failed.")
    return 1 if failures else 0


def validate_files(paths: list[str]) -> int:
    palette = ic.load_palette()
    failures = 0
    for p in paths:
        data = Path(p).read_bytes()
        try:
            root = ic.parse_svg_bytes(data)
        except Exception as e:
            print(f"  FAIL  {p}: does not parse ({e})")
            failures += 1
            continue
        doctype = root.getroottree().docinfo.doctype or None
        problems = [f"[gate2] {m}" for m in ic.security_violations(root, doctype)]
        problems += [f"[gate1] {m}" for m in ic.validate_composed_svg(data.decode(), palette)]
        status = "PASS" if not problems else "FAIL"
        print(f"  {status}  {p}")
        for m in problems:
            print(f"         - {m}")
        failures += 1 if problems else 0
    return 1 if failures else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    rc = validate_files(args) if args else validate_recipes()
    sys.exit(rc)
