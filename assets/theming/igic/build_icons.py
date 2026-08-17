#!/usr/bin/env python3
"""IGIC compiler — recipes -> validated, composed, rasterized freedesktop icon theme.

The core path, end to end (design §1/§4/§5/§6/§7):
  recipe.yaml + glyph.svg + palette.yaml
    -> gate 1 (recipe) + gate 2 (glyph security) + gate 1 (glyph structure)
    -> compose detailed + simplified SVG (template + glyph + palette color + baked glow + pulse)
    -> gate 1 + gate 2 on the COMPOSED output   (fail-closed: a bad icon never ships)
    -> write scalable/<context>/<stem>.svg + hybrid-routed PNGs
    -> index.theme + build-report.json + contact-sheet.png

Renderer: cairosvg (VISUAL_LANGUAGE.md §15). Deterministic: no wall-clock, no RNG.

Usage:
  python3 build_icons.py [--out DIR] [--clean] [--no-sheet]
    --out DIR   output root (default: ../out relative to this file)
    --clean     wipe the theme tree first (safe: IGIC wholly owns it)
    --no-sheet  skip the contact-sheet render (skips the only Pillow dependency)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml
import cairosvg
import igic_core as ic

THEME_NAME = "InterGenOS"


def raster_variant_for(size: int) -> str:
    return "simple" if size <= ic.SIMPLIFY_AT else "detailed"


# ---------------------------------------------------------------------------
# Occlusion gate (§11.8) — a green build must WITNESS occlusion in pixels
# ---------------------------------------------------------------------------
# cairosvg 2.9.0 ignores clip-rule="evenodd" and applies NONZERO winding, so a badge `sil`
# occludes the base under it ONLY when its footprint winds opposite the canvas rect.
# igic_core._silhouette_subpath now forces every footprint CCW by construction — but that stays
# an unverified assumption until a render proves it. For every occluding badge, PROBE its real
# clip with a solid base and assert the footprint interior is a hole in rendered pixels. This is
# the prior strip-and-measure proof promoted from a manual step to a build gate and generalized:
# a solid probe is strictly stronger than measuring the shipping base+glow residual (a working
# clip clears ANY base, so a solid probe cleared in the interior proves every real base is too),
# and unlike the shipping base it cannot pass VACUOUSLY where the base happens not to cross the
# footprint. Fail-closed: a mark whose occlusion cannot be witnessed does not ship.
_OCC_RES = 1024          # witness raster (the manual strip-and-measure proof ran at 1024)
_OCC_ERODE = 9           # MinFilter kernel: erode ~4px @1024 of boundary AA off the footprint mask
_OCC_ALPHA = 16          # a probe pixel counts as base-present above this alpha (0..255)
_OCC_MIN_MASK = 256      # the eroded footprint must be >= this many px, else too small to witness


def _render_alpha(svg: str, res: int):
    """Rasterize an SVG string at res x res and return its alpha channel (PIL 'L')."""
    from PIL import Image
    buf = io.BytesIO()
    cairosvg.svg2png(bytestring=svg.encode(), write_to=buf, output_width=res, output_height=res)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGBA").getchannel("A")


def _witness_badge(spec: dict, palette: dict) -> tuple[int, int]:
    """Render-probe ONE occluding badge's clip. Returns (residual_px, eroded_mask_px): residual 0
    == the footprint interior is a hole (occlusion witnessed); residual > 0 == the base reads
    through (the clip winding is wrong / cairosvg did not punch the hole)."""
    from PIL import ImageChops, ImageFilter
    foot = ic._silhouette_subpath(spec)
    if not foot:
        return -1, 0                                   # no footprint -> cannot witness (caller fails it)
    clip = (f'<svg xmlns="{ic.SVG_NS}" viewBox="0 0 {ic.CANVAS} {ic.CANVAS}">'
            f'<defs><clipPath id="occ" clipPathUnits="userSpaceOnUse">'
            f'<path d="M0 0 H{ic.CANVAS} V{ic.CANVAS} H0 Z {foot}" clip-rule="evenodd"/></clipPath>'
            f'</defs><g clip-path="url(#occ)">'
            f'<rect x="0" y="0" width="{ic.CANVAS}" height="{ic.CANVAS}" fill="#ffffff"/></g></svg>')
    mask_svg = (f'<svg xmlns="{ic.SVG_NS}" viewBox="0 0 {ic.CANVAS} {ic.CANVAS}">'
                f'<path d="{foot}" fill="#ffffff"/></svg>')
    clipped = _render_alpha(clip, _OCC_RES).point(lambda p: 255 if p > _OCC_ALPHA else 0)
    mask = _render_alpha(mask_svg, _OCC_RES).filter(ImageFilter.MinFilter(_OCC_ERODE))
    mask = mask.point(lambda p: 255 if p > 200 else 0)
    mask_area = mask.histogram()[255]
    residual = ImageChops.multiply(mask, clipped).histogram()[255]
    return residual, mask_area


def _occlusion_witness(recipe: dict, palette: dict) -> list[str]:
    """Witness occlusion for every occluding badge of a recipe. Empty == all holes proven."""
    problems = []
    for k, spec in enumerate(recipe.get("badges") or []):
        if not spec.get("occlude", True):
            continue
        residual, mask_area = _witness_badge(spec, palette)
        shp = (spec.get("sil") or {}).get("shape", "bbox")
        if residual < 0 or mask_area < _OCC_MIN_MASK:
            problems.append(f"badge #{k} ({spec.get('glyph')}, {shp}): footprint interior too "
                            f"small to witness occlusion ({mask_area}px eroded @ {_OCC_RES})")
        elif residual:
            problems.append(f"badge #{k} ({spec.get('glyph')}, {shp}): base reads through the "
                            f"footprint interior — {residual}/{mask_area}px "
                            f"({100 * residual // mask_area}%) NOT occluded (clip winding wrong)")
    return problems


def _alpha_center(svg: str):
    """Alpha-bbox centre of an SVG string rendered at the author canvas (256) — the
    band-centring probe. Deterministic (fixed renderer, fixed threshold)."""
    a = _render_alpha(svg, ic.CANVAS).point(lambda p: 255 if p > 8 else 0)
    b = a.getbbox()
    if not b:
        return None
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _probe_center_single(recipe, palette, glyph, recipe_sha, palette_sha):
    """Compose the mark at k=1 (author coordinates: fit = AUTHOR_PROBE_FIT, no centring)
    and measure its true content centre from pixels — shape-true where any coordinate
    parse would miss an arc bulge (VL LOOK-first, applied inside the engine)."""
    probe = dict(recipe); probe["fit"] = ic.AUTHOR_PROBE_FIT
    svg = ic.compose_icon(probe, palette, glyph, "detailed", recipe_sha, palette_sha)
    return _alpha_center(svg)


def _compile_single(u, palette, palette_sha, theme_root, out_root, sheet,
                    report, contexts_seen, sheet_tiles) -> bool:
    """Compile one single-recipe unit (a recipes/ file or an expanded family row)
    through the proven validate -> compose -> raster path. Returns True on PASS."""
    recipe, recipe_sha, rid = u["recipe"], u["recipe_sha"], u["id"]

    # ---- gate 1 (recipe) + load/sanitize glyph (gate 2 + structure) ----
    problems = ic.validate_recipe(recipe, palette)
    glyph = None
    gname = recipe.get("glyph")
    if gname and not problems:
        glyph = ic.load_glyph(gname)
        problems += glyph.sanitize()
        problems += glyph.structural_violations()
        if not problems:
            glyph.extract()

    # ---- compose both variants + gate the OUTPUT ----
    svgs: dict[str, str] = {}
    if not problems:
        center = _probe_center_single(recipe, palette, glyph, recipe_sha, palette_sha)
        for variant in ("detailed", "simple"):
            svg = ic.compose_icon(recipe, palette, glyph, variant, recipe_sha, palette_sha,
                                  center=center)
            out_problems = ic.validate_composed_svg(svg, palette)
            if out_problems:
                problems += [f"[{variant}] {m}" for m in out_problems]
            svgs[variant] = svg

    # ---- Occlusion witness: prove every occluding badge punches a real hole (§11.8) ----
    # Recipe-level (the clip footprint is variant-independent), so witness once per mark.
    if not problems and recipe.get("badges"):
        problems += [f"[occlusion] {m}" for m in _occlusion_witness(recipe, palette)]

    if problems:                       # fail-closed — skip emitting this icon
        print(f"  FAIL  {rid}")
        for m in problems:
            print(f"          - {m}")
        report["icons"].append({"id": rid, "status": "FAIL", "source": u["source"],
                                "problems": problems})
        return False

    context = recipe["category"]
    stem = ic.out_stem(recipe)
    scal_dir = theme_root / "scalable" / context
    scal_dir.mkdir(parents=True, exist_ok=True)
    (scal_dir / f"{stem}.svg").write_text(svgs["detailed"])

    sizes = sorted(set(recipe.get("export", {}).get("png", [])))
    png_paths = []
    for size in sizes:
        variant = raster_variant_for(size)
        size_dir = theme_root / f"{size}x{size}" / context
        size_dir.mkdir(parents=True, exist_ok=True)
        dest = size_dir / f"{stem}.png"
        cairosvg.svg2png(bytestring=svgs[variant].encode(),
                         write_to=str(dest), output_width=size, output_height=size)
        png_paths.append(str(dest.relative_to(out_root)))
        contexts_seen.setdefault(f"{size}x{size}/{context}", set()).add(size)

    if sheet:
        buf = io.BytesIO()
        cairosvg.svg2png(bytestring=svgs["detailed"].encode(), write_to=buf,
                         output_width=128, output_height=128)
        sheet_tiles.append((rid, buf.getvalue()))

    contexts_seen.setdefault(f"scalable/{context}", set()).add(0)
    report["icons"].append({
        "id": rid, "status": "PASS", "category": context, "kind": recipe.get("kind", "color"),
        "stem": stem, "recipe_sha256": recipe_sha, "source": u["source"],
        "glyph": gname, "glyph_sha256": glyph.sha if glyph else None,
        "sizes": sizes, "svg": str((scal_dir / f"{stem}.svg").relative_to(out_root)),
        "png": png_paths})
    print(f"  PASS  {rid:32s} -> scalable/{context}/{stem}.svg + {len(sizes)} png")
    return True


def _compile_state(u, palette, palette_sha, theme_root, out_root, sheet,
                   report, contexts_seen, sheet_tiles) -> bool:
    """Compile one states-family unit -> a single -symbolic.svg (all raster sizes
    render from that one SVG; symbolic icons are single-form). Returns True on PASS."""
    sid, context = u["state_id"], u["category"]
    # R6-1: state icons now compose BOTH variants (detailed for scalable/large, simple for small),
    # exactly like the rows path, so the sheet reads at the uniform detailed weight and small sizes
    # stay bold. Route size -> variant with the shared raster_variant_for().
    svgs: dict[str, str] = {}
    problems: list[str] = []
    # probe the BASE centre once per unit (same base -> same centre family-wide): the plain
    # body at full level, overlays excluded, composed at k=1 in author coordinates.
    probe = ic.compose_state_icon(sid, palette, u["base_sha"], palette_sha,
                                  u["always_on"], u["levels"],
                                  len(u["levels"]) if u.get("gauge") is None else 100,
                                  overlays=u.get("overlays"), state_overlays=(),
                                  variant="detailed", gauge=u.get("gauge"), dim=False,
                                  fit=ic.AUTHOR_PROBE_FIT)
    center = _alpha_center(probe)
    for variant in ("detailed", "simple"):
        svg = ic.compose_state_icon(sid, palette, u["base_sha"], palette_sha,
                                    u["always_on"], u["levels"], u["level"],
                                    overlays=u.get("overlays"),
                                    state_overlays=u.get("state_overlays", []),
                                    variant=variant,
                                    gauge=u.get("gauge"), dim=u.get("dim", False),
                                    fit=u.get("fit", ic.SYMBOLIC_BAND_FIT),
                                    center=center)
        problems += [f"[{variant}] {m}" for m in ic.validate_composed_svg(svg, palette)]
        svgs[variant] = svg
    if u.get("small"):
        # size-conditional detail: the base's `<g class="small">` alternative geometry
        # composes for raster sizes <= SMALL_GEOM_AT (simple stroke; overlays the small
        # subtree does not redefine fall back to full detail). Same probed centre — the
        # small body is authored to the full content box, so states stay family-aligned.
        sm = u["small"]
        svg = ic.compose_state_icon(sid, palette, u["base_sha"], palette_sha,
                                    sm["always_on"], u["levels"], u["level"],
                                    overlays={**(u.get("overlays") or {}), **sm["overlays"]},
                                    state_overlays=u.get("state_overlays", []),
                                    variant="small",
                                    gauge=u.get("gauge"), dim=u.get("dim", False),
                                    fit=u.get("fit", ic.SYMBOLIC_BAND_FIT),
                                    center=center)
        problems += [f"[small] {m}" for m in ic.validate_composed_svg(svg, palette)]
        svgs["small"] = svg
    if problems:                       # fail-closed
        print(f"  FAIL  {sid}")
        for m in problems:
            print(f"          - {m}")
        report["icons"].append({"id": sid, "status": "FAIL", "source": u["source"],
                                "problems": [f"[symbolic] {m}" for m in problems]})
        return False

    stem = f"{sid}-symbolic"
    scal_dir = theme_root / "scalable" / context
    scal_dir.mkdir(parents=True, exist_ok=True)
    (scal_dir / f"{stem}.svg").write_text(svgs["detailed"])

    sizes = sorted(set(u.get("export", {}).get("png", [])))
    png_paths = []
    for size in sizes:
        variant = raster_variant_for(size)
        if size <= ic.SMALL_GEOM_AT and "small" in svgs:
            variant = "small"          # size-conditional base geometry (<= SMALL_GEOM_AT)
        size_dir = theme_root / f"{size}x{size}" / context
        size_dir.mkdir(parents=True, exist_ok=True)
        dest = size_dir / f"{stem}.png"
        cairosvg.svg2png(bytestring=svgs[variant].encode(),
                         write_to=str(dest), output_width=size, output_height=size)
        png_paths.append(str(dest.relative_to(out_root)))
        contexts_seen.setdefault(f"{size}x{size}/{context}", set()).add(size)

    if sheet:
        buf = io.BytesIO()
        cairosvg.svg2png(bytestring=svgs["detailed"].encode(), write_to=buf,
                         output_width=128, output_height=128)
        sheet_tiles.append((sid, buf.getvalue()))

    contexts_seen.setdefault(f"scalable/{context}", set()).add(0)
    report["icons"].append({
        "id": sid, "status": "PASS", "category": context, "kind": "symbolic",
        "stem": stem, "base": u["base_name"], "base_sha256": u["base_sha"],
        "level": u["level"], "overlays": u.get("state_overlays", []),
        "source": u["source"], "sizes": sizes,
        "svg": str((scal_dir / f"{stem}.svg").relative_to(out_root)), "png": png_paths})
    print(f"  PASS  {sid:32s} -> scalable/{context}/{stem}.svg + {len(sizes)} png (level {u['level']})")
    return True


# ---------------------------------------------------------------------------
# Gradient recolor (§IC-034) — the two-register rule's third-party treatment
# ---------------------------------------------------------------------------
# The approved look (IC-032/033): a third-party app wears its OWN full-color known
# icon recolored to a luminance-keyed gradient blue; first-party + system marks stay flat
# line-art. The recolor is keyed on ITU-R 709 luminance (the exact weights the approved
# proposal used) through a fixed DEEP -> BRAND -> LIGHT blue ramp. Pure LUT + ImageMath: no
# RNG, no wall-clock -> deterministic (regenerate-and-diff stays clean, design §1/§10.5).
_GRAD_DEEP = (8, 40, 77)         # luminance 0.0  (shadow blue)
_GRAD_BRAND = (0, 153, 255)      # luminance 0.5  (intergen brand blue)
_GRAD_LIGHT = (214, 232, 255)    # luminance 1.0  (highlight blue)


def _blue_ramp(lf: float) -> tuple[int, int, int]:
    """Map a luminance in [0,1] onto the two-segment DEEP->BRAND->LIGHT blue ramp."""
    if lf < 0.5:
        a, b, t = _GRAD_DEEP, _GRAD_BRAND, lf / 0.5
    else:
        a, b, t = _GRAD_BRAND, _GRAD_LIGHT, (lf - 0.5) / 0.5
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _paint_gradient_blue(im):
    """Recolor a full-color RGBA icon to gradient blue, keyed on ITU-R 709 luminance. Alpha is
    preserved (transparent stays transparent). Deterministic (fixed LUTs + ImageMath)."""
    from PIL import Image
    im = im.convert("RGBA")
    a = im.getchannel("A")
    # exact ITU-R 709 luminance via a single convert matrix (deterministic; the approved ramp weights)
    lum = im.convert("RGB").convert("L", matrix=(0.2126, 0.7152, 0.0722, 0.0))
    lut_r = [_blue_ramp(i / 255.0)[0] for i in range(256)]
    lut_g = [_blue_ramp(i / 255.0)[1] for i in range(256)]
    lut_b = [_blue_ramp(i / 255.0)[2] for i in range(256)]
    return Image.merge("RGBA", (lum.point(lut_r), lum.point(lut_g), lum.point(lut_b), a))


def _compile_gradient(u, palette, theme_root, out_root, sheet,
                      report, contexts_seen, sheet_tiles) -> bool:
    """Compile one gradient-mode unit (IC-034): emit the app's OWN full-color KNOWN icon
    recolored to gradient blue under the app's stem. The full-color source is staged in
    GRADIENT_DIR; a staged SVG passes the gate-2 security scan FIRST (fail-closed:
    script/foreignObject/image/external-ref/on* -> no ship), then is rasterized and
    luminance-keyed — the source SVG is NEVER emitted verbatim, so no active content survives
    to the theme. Sizes are painted PER-SIZE (raster the source at the size, then recolor) so
    there is no resize-after-paint alpha halo; the scalable slot is an engine-authored data-URI
    wrapper over the 256 painted raster (parity with the passthrough PNG path). glow: none by
    construction (a solid raster fill carries no outline for the signature to blur — IC-027).
    Extends the IC-029 passthrough branch; flipping `mode` to `passthrough` swaps to the
    verbatim vendor mark, `styled` to the composed glyph, with nothing else re-rendering."""
    import base64
    from PIL import Image
    recipe, rid = u["recipe"], u["id"]
    stem = ic.out_stem(recipe)
    context = recipe["category"]
    asset = recipe.get("gradient")
    src = ic.GRADIENT_DIR / (asset or "")
    sizes = sorted(set(recipe.get("export", {}).get("png", [])))
    scal_dir = theme_root / "scalable" / context
    problems: list[str] = []
    png_paths: list[str] = []
    raw = None
    if not asset or not src.exists():
        problems.append(f"gradient asset missing: {src}")
    elif src.suffix.lower() == ".svg":
        raw = src.read_bytes()
        try:
            root = ic.parse_svg_bytes(raw)
            problems += [f"[security] {m}" for m in ic.security_violations(root, None)]
        except Exception as e:                           # report + fail-closed on a bad asset
            problems.append(f"gradient SVG parse failed: {e}")
    elif src.suffix.lower() != ".png":
        problems.append(f"unsupported gradient asset type {src.suffix!r}")

    def source_raster(size: int):
        if raw is not None:                              # SVG (already security-scanned)
            buf = io.BytesIO()
            cairosvg.svg2png(bytestring=raw, write_to=buf, output_width=size, output_height=size)
            return Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")
        return Image.open(io.BytesIO(src.read_bytes())).convert("RGBA").resize(
            (size, size), Image.LANCZOS)

    if not problems:
        scal_dir.mkdir(parents=True, exist_ok=True)
        for size in sizes:
            sd = theme_root / f"{size}x{size}" / context
            sd.mkdir(parents=True, exist_ok=True)
            _paint_gradient_blue(source_raster(size)).save(sd / f"{stem}.png")
            png_paths.append(str((sd / f"{stem}.png").relative_to(out_root)))
            contexts_seen.setdefault(f"{size}x{size}/{context}", set()).add(size)
        base = io.BytesIO()
        _paint_gradient_blue(source_raster(ic.CANVAS)).save(base, format="PNG")
        b64 = base64.b64encode(base.getvalue()).decode()
        (scal_dir / f"{stem}.svg").write_text(
            f'<svg xmlns="{ic.SVG_NS}" viewBox="0 0 {ic.CANVAS} {ic.CANVAS}">'
            f'<image x="0" y="0" width="{ic.CANVAS}" height="{ic.CANVAS}" '
            f'href="data:image/png;base64,{b64}"/></svg>')
        contexts_seen.setdefault(f"scalable/{context}", set()).add(0)

    if problems:
        print(f"  FAIL  {rid}  (gradient)")
        for m in problems:
            print(f"          - {m}")
        report["icons"].append({"id": rid, "status": "FAIL", "source": u["source"],
                                "problems": problems})
        return False

    if sheet:                                            # tile from the emitted 128 raster
        tile = theme_root / "128x128" / context / f"{stem}.png"
        sheet_tiles.append((rid, tile.read_bytes()))

    report["icons"].append({
        "id": rid, "status": "PASS", "category": context, "kind": "gradient",
        "stem": stem, "mode": "gradient", "gradient_asset": asset,
        "gradient_sha256": ic.sha256_file(src), "treatment": "gradient-blue",
        "source": u["source"], "sizes": sizes,
        "svg": str((scal_dir / f"{stem}.svg").relative_to(out_root)), "png": png_paths})
    print(f"  PASS  {rid:32s} -> gradient ({asset})")
    return True


def _compile_passthrough(u, palette, theme_root, out_root, sheet,
                         report, contexts_seen, sheet_tiles) -> bool:
    """Compile one SWAP-READY unit in passthrough mode (IC-029): emit the staged OFFICIAL
    vendor mark VERBATIM under the app's stem, instead of the IGIC restyle — the fallback a
    vendor complaint swaps to. Deliberately bypasses the IGIC style/palette gate (the asset
    is the vendor's own art, not an IGIC composite), but a staged SVG must pass the gate-2
    security scan first (fail-closed: script/foreignObject/image/external-ref/on* → no ship).
    A PNG asset carries no active-content surface; its sizes are resampled and the scalable
    slot is an engine-authored data-URI wrapper. Flipping the one row field `mode` back to
    `styled` restores the composed mark; nothing else re-renders."""
    import base64
    from PIL import Image
    recipe, rid = u["recipe"], u["id"]
    stem = ic.out_stem(recipe)
    context = recipe["category"]
    asset = recipe.get("passthrough")
    src = ic.PASSTHROUGH_DIR / (asset or "")
    sizes = sorted(set(recipe.get("export", {}).get("png", [])))
    scal_dir = theme_root / "scalable" / context
    problems: list[str] = []
    png_paths: list[str] = []
    if not asset or not src.exists():
        problems.append(f"passthrough asset missing: {src}")
    else:
        ext = src.suffix.lower()
        if ext == ".svg":
            raw = src.read_bytes()
            try:
                root = ic.parse_svg_bytes(raw)
                problems += [f"[security] {m}" for m in ic.security_violations(root, None)]
            except Exception as e:                       # report + fail-closed on a bad asset
                problems.append(f"passthrough SVG parse failed: {e}")
            if not problems:
                scal_dir.mkdir(parents=True, exist_ok=True)
                (scal_dir / f"{stem}.svg").write_bytes(raw)
                for size in sizes:
                    sd = theme_root / f"{size}x{size}" / context
                    sd.mkdir(parents=True, exist_ok=True)
                    cairosvg.svg2png(bytestring=raw, write_to=str(sd / f"{stem}.png"),
                                     output_width=size, output_height=size)
                    png_paths.append(str((sd / f"{stem}.png").relative_to(out_root)))
                    contexts_seen.setdefault(f"{size}x{size}/{context}", set()).add(size)
                contexts_seen.setdefault(f"scalable/{context}", set()).add(0)
        elif ext == ".png":
            im = Image.open(io.BytesIO(src.read_bytes())).convert("RGBA")
            for size in sizes:
                sd = theme_root / f"{size}x{size}" / context
                sd.mkdir(parents=True, exist_ok=True)
                im.resize((size, size), Image.LANCZOS).save(sd / f"{stem}.png")
                png_paths.append(str((sd / f"{stem}.png").relative_to(out_root)))
                contexts_seen.setdefault(f"{size}x{size}/{context}", set()).add(size)
            b64 = base64.b64encode(src.read_bytes()).decode()
            scal_dir.mkdir(parents=True, exist_ok=True)
            (scal_dir / f"{stem}.svg").write_text(
                f'<svg xmlns="{ic.SVG_NS}" viewBox="0 0 {ic.CANVAS} {ic.CANVAS}">'
                f'<image x="0" y="0" width="{ic.CANVAS}" height="{ic.CANVAS}" '
                f'href="data:image/png;base64,{b64}"/></svg>')
            contexts_seen.setdefault(f"scalable/{context}", set()).add(0)
        else:
            problems.append(f"unsupported passthrough asset type {ext!r}")

    if problems:
        print(f"  FAIL  {rid}  (passthrough)")
        for m in problems:
            print(f"          - {m}")
        report["icons"].append({"id": rid, "status": "FAIL", "source": u["source"],
                                "problems": problems})
        return False

    if sheet:                                            # tile from an emitted raster (128 always present)
        tile = theme_root / "128x128" / context / f"{stem}.png"
        sheet_tiles.append((rid, tile.read_bytes()))

    report["icons"].append({
        "id": rid, "status": "PASS", "category": context, "kind": "passthrough",
        "stem": stem, "mode": "passthrough", "passthrough_asset": asset,
        "passthrough_sha256": ic.sha256_file(src), "source": u["source"],
        "sizes": sizes, "svg": str((scal_dir / f"{stem}.svg").relative_to(out_root)),
        "png": png_paths})
    print(f"  PASS  {rid:32s} -> passthrough ({asset})")
    return True


def build(out_root: Path, clean: bool, sheet: bool) -> int:
    palette = ic.load_palette()
    palette_sha = ic.sha256_file(ic.PALETTE_PATH)

    theme_root = out_root / "theme" / THEME_NAME
    if clean and theme_root.exists():
        import shutil
        shutil.rmtree(theme_root)
    theme_root.mkdir(parents=True, exist_ok=True)

    units, family_failures = ic.gather_units(palette)
    if not units and not family_failures:
        print("no recipes or families found", file=sys.stderr)
        return 1

    # ---- duplicate-id fail-closed ACROSS everything (singles + expanded rows +
    #      states): a collision means we cannot tell which asset is authoritative,
    #      so NEITHER ships (§5.1 / §7). Removing the 3 single folder recipes is what
    #      keeps the folders family from colliding with them.
    dup_ids = {rid for rid, c in Counter(u["id"] for u in units).items() if c > 1}

    report: dict = {"compiler": ic.COMPILER_VERSION, "canvas": ic.CANVAS,
                    "palette_sha256": palette_sha, "theme": THEME_NAME, "icons": []}
    contexts_seen: dict[str, set[int]] = {}
    sheet_tiles: list[tuple[str, bytes]] = []
    failures = 0

    print(f"IGIC compile -> {theme_root}")

    for ff in family_failures:             # families whose header/base failed the gate
        failures += 1
        print(f"  FAIL  {ff['source']} (family)")
        for m in ff["problems"]:
            print(f"          - {m}")
        report["icons"].append({"id": ff["source"], "status": "FAIL",
                                "source": ff["source"], "problems": ff["problems"]})

    for u in units:
        if u["id"] in dup_ids:
            failures += 1
            print(f"  FAIL  {u['id']}  (duplicate id — source {u['source']})")
            report["icons"].append({"id": u["id"], "status": "FAIL", "source": u["source"],
                                    "problems": [f"duplicate id across the set (source {u['source']})"]})
            continue
        if u["kind"] == "state":
            ok = _compile_state(u, palette, palette_sha, theme_root, out_root, sheet,
                                report, contexts_seen, sheet_tiles)
        elif u["recipe"].get("mode") == "passthrough":       # IC-029 swap-ready fallback path
            ok = _compile_passthrough(u, palette, theme_root, out_root, sheet,
                                      report, contexts_seen, sheet_tiles)
        elif u["recipe"].get("mode") == "gradient":          # IC-034 gradient-blue known-icon path
            ok = _compile_gradient(u, palette, theme_root, out_root, sheet,
                                   report, contexts_seen, sheet_tiles)
        else:
            ok = _compile_single(u, palette, palette_sha, theme_root, out_root, sheet,
                                 report, contexts_seen, sheet_tiles)
        if not ok:
            failures += 1

    # ---- index.theme ----
    _write_index_theme(theme_root, sorted(contexts_seen.keys()))

    # ---- build report (deterministic; no timestamp) ----
    (out_root / "build-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    # ---- contact sheet ----
    if sheet and sheet_tiles:
        _write_contact_sheet(out_root / "contact-sheet.png", sheet_tiles, palette)

    total = len(units) + len(family_failures)
    print(f"\n{total-failures}/{total} icons compiled clean; {failures} failed.")
    print(f"theme:  {theme_root}")
    print(f"report: {out_root/'build-report.json'}")
    if sheet and sheet_tiles:
        print(f"sheet:  {out_root/'contact-sheet.png'}")
    return 1 if failures else 0


def _write_index_theme(theme_root: Path, dirs: list[str]) -> None:
    ctx_map = {"apps": "Applications", "places": "Places", "mimetypes": "MimeTypes",
               "devices": "Devices", "status": "Status", "actions": "Actions"}
    lines = ["[Icon Theme]", f"Name={THEME_NAME}",
             "Comment=InterGenOS icon theme (IGIC-compiled)",
             "Inherits=Adwaita,hicolor", f"Directories={','.join(dirs)}", ""]
    for d in dirs:
        sizeseg, ctx = d.split("/", 1)
        lines.append(f"[{d}]")
        if sizeseg == "scalable":
            lines += ["Size=256", "MinSize=8", "MaxSize=512", "Type=Scalable"]
        else:
            s = int(sizeseg.split("x")[0])
            lines += [f"Size={s}", "Type=Fixed"]
        lines.append(f"Context={ctx_map.get(ctx, ctx.title())}")
        lines.append("")
    (theme_root / "index.theme").write_text("\n".join(lines))


def _write_contact_sheet(dest: Path, tiles: list[tuple[str, bytes]], palette: dict) -> None:
    from PIL import Image, ImageDraw
    tile, pad, label_h, cols = 128, 24, 22, 4
    void = palette["_by_name"]["bg-void"]
    rows = (len(tiles) + cols - 1) // cols
    cw = tile + pad
    ch = tile + pad + label_h
    W, H = cols * cw + pad, rows * ch + pad
    sheet = Image.new("RGBA", (W, H), void)
    draw = ImageDraw.Draw(sheet)
    for i, (rid, png) in enumerate(tiles):
        r, c = divmod(i, cols)
        x, y = pad + c * cw, pad + r * ch
        icon = Image.open(io.BytesIO(png)).convert("RGBA")
        sheet.alpha_composite(icon, (x, y))
        draw.text((x, y + tile + 4), rid.split(".")[-1][:20], fill=palette["_by_name"]["text-dim"])
    sheet.save(dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="IGIC compiler (prototype)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "out"))
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--no-sheet", action="store_true")
    a = ap.parse_args()
    sys.exit(build(Path(a.out).resolve(), clean=a.clean, sheet=not a.no_sheet))
