#!/usr/bin/env python3
"""IGIC core — the InterGenOS Icon Compiler shared library (prototype 0.2.0).

One place for: the controlled palette, the safe SVG parser, the two validation
gates, glyph load+sanitize+extract, the template / pulse / glow geometry, the
provenance block, and the compositor. Imported by build_icons.py (the compiler)
and validate_icons.py (the standalone gates).

Renderer is cairosvg (the house pipeline — VISUAL_LANGUAGE.md §15; matches
assets/intergen-mark/generate.py). This module does no rasterizing itself, so it
imports no cairosvg — it only produces SVG strings and validates them.

DETERMINISM is load-bearing (design §1/§4/§10.5): no wall-clock, no RNG anywhere.
Same recipe + glyph + palette bytes -> byte-identical SVG. Provenance is
content-addressed (sha256), never time-stamped, precisely so regenerate-and-diff
stays clean.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml
from lxml import etree

# ---------------------------------------------------------------------------
# Constants (VISUAL_LANGUAGE.md §11 icon anatomy)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
GLYPH_DIR = ROOT / "glyphs"
RECIPE_DIR = ROOT / "recipes"
FAMILY_DIR = ROOT / "families"     # table-generation manifests (§5.1 rows / §6.3 states)
PALETTE_PATH = ROOT / "palette.yaml"
PASSTHROUGH_DIR = ROOT / "passthrough"   # official vendor marks staged as swap-ready fallbacks (IC-029)
GRADIENT_DIR = ROOT / "gradient"         # full-color KNOWN-ICON sources, emitted luminance-keyed to gradient blue (IC-034)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
IGIC_NS = "https://intergenos.org/ns/igic/1"

COMPILER_VERSION = "igic 0.2.0-prototype"
CANVAS = 256                 # 256x256 base grid (VL §7/§11)
# Optical band (VL §11, Decided 2026-07-22): rendered content occupies [14, 242]/256 = 0.891 —
# the measured neighbor-theme band (reference set read 0.88-0.89 uniform; the prior 40px margin
# rendered the corpus ~22% undersized on hardware). Authored sources stay drawn on the historic
# [40, 216] AUTHOR grid; the compositor maps author grid -> optical band (one uniform transform,
# stroke-compensated so the §6 stroke law holds in RENDERED pixels). A per-mark `fit` recipe field
# multiplies the mapping for marks whose authored extent sits off the grid (the tuned re-fit lane).
SAFE_MARGIN = 14             # optical-band margin: rendered content lives in [14, 242] (VL §11)
AUTHOR_MARGIN = 40           # the authoring grid all glyph/template/badge/pulse geometry is drawn on
BAND_SCALE = (CANVAS - 2 * SAFE_MARGIN) / (CANVAS - 2 * AUTHOR_MARGIN)   # 228/176
FIT_MIN, FIT_MAX = 0.5, 2.0  # sanity bounds for the per-mark `fit` factor (gate-1 validated)
# The VL §11 optical-size law T(q) applies to EVERY mark class (Decided 2026-07-23): perceived
# size follows ink coverage, so low-coverage line art sits at the band top while solid-bodied
# marks sit low (0.86) — measured against the shipped neighbor references (neighbor-theme
# solids 0.84-0.86). Color-glyph marks are tuned per-mark via `fit` (measured).
#
# SYMBOLIC CLASS CEILING (Decided 2026-07-23, comparison-calibrated): the reference toolkit's
# symbolic set measures median 1.000 dominant-axis coverage of the nominal box (p25 1.000,
# min 0.875, n=101 paired names rendered at 256, alpha>64) — symbolic content is drawn to the
# box, not to an inset band, and at the 16px shell contexts the prior 0.94 band read ~25%
# undersized against it on hardware. The symbolic/state class ceiling is therefore the nominal
# box itself; the engine default lands marks at 0.984 (252/256) to hold a 2px anti-alias guard
# at the authoring size. Per-mark `fit` follows the reference's own measured coverage where the
# same freedesktop name ships deliberately smaller (0.875-0.969 tail). App-mark classes are
# unchanged by this ruling.
SYMBOLIC_TARGET = 0.984            # class-default dominant-axis coverage (252/256)
SYMBOLIC_BAND_FIT = SYMBOLIC_TARGET / 0.891   # class-default fit for symbolic kind + state families


def band_transform(fit: float = 1.0, center: tuple[float, float] | None = None) -> tuple[str, float]:
    """The author-grid -> optical-band mapping for one mark: scale k = BAND_SCALE * fit,
    centring the authored CONTENT box on the canvas centre (Decided 2026-07-23: several
    bases are authored off the canvas centre; a canvas-centred scale drifts them toward an
    edge as k grows — the content box, not the canvas, is what the band law sizes). For a
    state family the centre comes from the BASE only, so every state of a family shares one
    centre and overlay marks never shift the body. Returns (the `<g transform>` open tag, k).
    Fixed 6-decimal formatting -> deterministic (same recipe bytes, same string)."""
    k = BAND_SCALE * float(fit)
    cx, cy = center if center else (CANVAS / 2, CANVAS / 2)
    tx, ty = CANVAS / 2 - k * cx, CANVAS / 2 - k * cy
    return f'<g transform="translate({tx:.6f},{ty:.6f}) scale({k:.6f})">', k


# The band centring uses a RENDER-PROBE (build_icons._probe_center): the mark is composed
# at k=1 (author coordinates) and its alpha-bbox centre measured from pixels — shape-true
# for arcs/curves where any coordinate-parse approximation misses the bulge. This module
# stays renderer-free; callers pass the probed centre in. The probe fit below makes
# band_transform the identity (k = BAND_SCALE * 1/BAND_SCALE = 1, no translate).
AUTHOR_PROBE_FIT = 1.0 / BAND_SCALE


def band_stroke(base_sw: int, k: float) -> float:
    """Pre-transform stroke width that RENDERS at base_sw after the band scale — the §6
    percentage law is stated in rendered pixels, so the mapping must not inflate weight."""
    return round(base_sw / k, 4)
STROKE_DETAIL = 5            # detailed variant — ~2% of the 256 canvas (VL §6 line 248 +
                             #   the mark's 10/512). Supersedes the §11 anatomy-table 12px,
                             #   which conflicts with §6 and reads too heavy — flagged to reconcile.
STROKE_SIMPLE = 16           # simplified variant — ~6% of canvas (VL §6 line 250 + the mark 32/512)
SIMPLIFY_AT = 48             # sizes <= this render simplified (the mark's routing: <=48 simplified)
SMALL_GEOM_AT = 24           # sizes <= this compose a base's `<g class="small">` alternative
                             #   geometry when the base carries one (size-conditional detail:
                             #   sub-pixel feature runs at 16-24px simplify; 32+ keep full detail)
NODE_LIMIT = 500             # gate-1 node-count ceiling (guards runaway authored SVG)

FREEDESKTOP_CONTEXTS = {"apps", "places", "mimetypes", "devices", "status", "actions"}
RASTER_SIZES_ALLOWED = {16, 24, 32, 48, 64, 128, 256}

# Drawable SVG elements a glyph is permitted to contain (VL §7 primitives +
# the container <g>). Anything else in a glyph is a gate-2 structural reject.
GLYPH_DRAWABLE = {"path", "rect", "circle", "line", "polyline", "polygon", "ellipse", "g"}

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
def load_palette(path: Path = PALETTE_PATH) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "tokens" not in data:
        raise ValueError(f"palette {path} missing 'tokens'")
    merged = {}
    for group in ("tokens", "accents"):
        for name, hexval in (data.get(group) or {}).items():
            merged[name] = _norm_hex(hexval)
    data["_by_name"] = merged
    data["_hex_set"] = {v for v in merged.values()}
    return data


def _norm_hex(h: str) -> str:
    h = str(h).strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", h):
        raise ValueError(f"palette value {h!r} is not a #rrggbb hex")
    return h


def resolve_color(token: str, palette: dict) -> str:
    """Palette token -> #rrggbb. Raises on an unknown token (fail-closed)."""
    by_name = palette["_by_name"]
    if token not in by_name:
        raise KeyError(f"color token {token!r} is not in the controlled palette")
    return by_name[token]


# ---------------------------------------------------------------------------
# Hashing (provenance + build report)
# ---------------------------------------------------------------------------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Safe SVG parsing (XXE-hardened) + gate 2 (security sanitization)
# ---------------------------------------------------------------------------
def safe_parser() -> etree.XMLParser:
    # No network, no DTD load, no entity resolution -> no XXE, no billion-laughs.
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def parse_svg_bytes(data: bytes):
    return etree.fromstring(data, parser=safe_parser())


def _localname(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


_URL_REF = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.I)


def security_violations(root, doctype: str | None) -> list[str]:
    """Gate 2 — the security-only sanitization gate (design §5.2). Fail-closed:
    any hit means the asset never ships. Applies to a glyph input AND to the
    composed output.
    """
    v: list[str] = []
    if doctype:
        v.append("DOCTYPE/DTD present (XXE surface) — rejected")

    for el in root.iter():
        if not isinstance(el.tag, str):        # comments / PIs
            if "cursor" in str(el):            # (defensive; PIs are dropped anyway)
                pass
            continue
        name = _localname(el.tag)
        if name in ("script", "foreignObject", "image"):
            v.append(f"<{name}> element — rejected")
        for attr, val in el.attrib.items():
            aname = _localname(attr).lower()
            if aname.startswith("on"):
                v.append(f"event-handler attribute {aname!r} on <{name}> — rejected")
            if aname in ("href",) or attr == f"{{{XLINK_NS}}}href":
                if not str(val).strip().startswith("#"):
                    v.append(f"external reference {aname}={val!r} on <{name}> — rejected")
            if "javascript:" in str(val).lower():
                v.append(f"javascript: URI in {aname} on <{name}> — rejected")
            for _q, target in _URL_REF.findall(str(val)):
                if not target.strip().startswith("#"):
                    v.append(f"external url() {target!r} in {aname} on <{name}> — rejected")
        if name == "use":
            href = el.get("href") or el.get(f"{{{XLINK_NS}}}href") or ""
            if not href.strip().startswith("#"):
                v.append("<use> with external/absent href — rejected")
    return v


# ---------------------------------------------------------------------------
# Glyph load + sanitize + extract
# ---------------------------------------------------------------------------
_NUM = re.compile(r"-?\d+\.?\d*")


def _coords_in(el) -> list[float]:
    nums: list[float] = []
    for attr in ("x", "y", "cx", "cy", "r", "rx", "ry", "width", "height",
                 "x1", "y1", "x2", "y2"):
        val = el.get(attr)
        if val is not None:
            nums += [float(n) for n in _NUM.findall(val)]
    for attr in ("d", "points"):
        val = el.get(attr)
        if val is not None:
            nums += [float(n) for n in _NUM.findall(val)]
    return nums


class Glyph:
    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.bytes = path.read_bytes()
        self.sha = sha256_bytes(self.bytes)
        self.root = parse_svg_bytes(self.bytes)
        self.doctype = self.root.getroottree().docinfo.doctype or None
        self.elements: list[str] = []      # serialized drawable children (stroke-less)
        self.filled: list[bool] = []       # parallel: True if the primitive sits in <g class="fill">
        self.silhouette: list = []         # (localname, attrs) of a <g class="silhouette"> footprint (§11 R5 occlusion)
        self.min_xy = 256.0
        self.max_xy = 0.0

    def sanitize(self) -> list[str]:
        return security_violations(self.root, self.doctype)

    def structural_violations(self) -> list[str]:
        v = []
        vb = (self.root.get("viewBox") or "").strip()
        if vb != f"0 0 {CANVAS} {CANVAS}":
            v.append(f"glyph {self.name}: viewBox {vb!r} != '0 0 {CANVAS} {CANVAS}'")
        n = sum(1 for _ in self.root.iter())
        if n > NODE_LIMIT:
            v.append(f"glyph {self.name}: node count {n} > {NODE_LIMIT}")
        for el in self.root.iter():
            if not isinstance(el.tag, str):
                continue
            ln = _localname(el.tag)
            if ln in ("svg", "defs", "metadata", "title", "desc"):
                continue
            if ln not in GLYPH_DRAWABLE:
                v.append(f"glyph {self.name}: non-primitive <{ln}> (VL §7 permits {sorted(GLYPH_DRAWABLE)})")
        return v

    def extract(self) -> None:
        """Pull the drawable geometry out of the glyph, stripped of any stroke/
        fill color (the compiler owns color). Records an approximate bbox."""
        for el in self.root:
            ln = _localname(el.tag)
            if ln in ("defs", "metadata", "title", "desc"):
                continue
            self._collect(el)

    def _collect(self, el, filled: bool = False) -> None:
        ln = _localname(el.tag)
        if ln == "g":
            # A <g class="fill"> region marks its descendants SOLID (filled in the owning
            # color NAME) while everything else strokes — parity with the states-base
            # collect() (§6.3.1), for a rows/single glyph that mixes a fill with an outline
            # (the SOLID record dot / stop square inside a stroked button RING, R4-8).
            cls = (el.get("class") or "").split()
            # <g class="overlay ..."> groups are STATE-ONLY decorations (§6.3.1: the disabled
            # X, acquiring dots, wired pins/divider). When a status/state base is reused as a
            # plain emblem (a Settings panel identity icon = its status sibling's full form),
            # those decorations are not part of the base identity — skip them. The `levels`
            # ladder (the signal bars) IS the identity and stays, so reuse reads as full-signal.
            if "overlay" in cls:
                return
            if "silhouette" in cls:      # occlusion footprint (§11 R5) — captured, NOT drawn
                skip = ("stroke", "fill", "stroke-width", "style", "class", "opacity")
                for child in el:
                    cn = _localname(child.tag)
                    if cn in GLYPH_DRAWABLE and cn != "g":
                        self.silhouette.append(
                            (cn, {_localname(a): v for a, v in child.attrib.items()
                                  if _localname(a) not in skip}))
                return
            g_filled = filled or ("fill" in cls)
            for child in el:
                self._collect(child, g_filled)
            return
        if ln not in GLYPH_DRAWABLE:
            return
        for c in _coords_in(el):
            self.min_xy = min(self.min_xy, c)
            self.max_xy = max(self.max_xy, c)
        # Re-emit the primitive with color stripped; keep geometry + fill=none.
        attrs = {}
        for a, val in el.attrib.items():
            an = _localname(a)
            if an in ("stroke", "fill", "stroke-width", "style", "class",
                      "stroke-linecap", "stroke-linejoin", "opacity"):
                continue
            attrs[an] = val
        attr_str = "".join(f' {a}="{v}"' for a, v in attrs.items())
        self.elements.append(f"<{ln}{attr_str} fill=\"none\"/>")
        self.filled.append(filled)


def load_glyph(name: str, glyph_dir: Path = GLYPH_DIR) -> Glyph:
    path = glyph_dir / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"glyph {name!r} not found at {path}")
    return Glyph(name, path)


# ---------------------------------------------------------------------------
# Template geometry (built-in flat silhouettes; design §3/§4)
# ---------------------------------------------------------------------------
def template_elements(name: str) -> list[str]:
    """Return the outline element(s) for a named template, color-stripped
    (the compositor applies stroke color). All geometry inside [40,216]."""
    if name == "none":
        return []
    if name == "app-tile":
        # rounded container; radius scaled from the VL §7 "14px app icon container"
        return [f'<rect x="44" y="44" width="168" height="168" rx="34" ry="34" fill="none"/>']
    if name == "folder":
        # a front-facing folder: single clean silhouette — a STRAIGHT left edge (no
        # top-left cut-in notch), a raised tab on the left sloping to the body top.
        # Dark body; the accent lives in the stroke (VL §11 folder = accent color + glyph;
        # the dark-body-vs-color-fill choice is an operator call — ICON_CATEGORIES.md).
        # Body proportions raised per the recorded contact-sheet nit ("folders read
        # short/smushed"): tab-top 78 / body-top 98 / bottom 208 -> body ~110px tall (was
        # 96), vertical centre ~153. Stays inside the VL §1 safe area [40,216] on the
        # detailed variant; the simple-variant stroke reaches the 216 edge, not past it.
        d = ("M 56 78 L 100 78 L 116 98 L 200 98 "
             "Q 212 98 212 110 L 212 196 Q 212 208 200 208 "
             "L 56 208 Q 44 208 44 196 L 44 90 Q 44 78 56 78 Z")
        return [f'<path d="{d}" fill="none"/>']
    if name == "document":
        # a page with a folded top-right corner — the mimetypes base (§2/§5): the
        # per-type emblem sits on the page, the accent color-codes the type. The page is
        # CANVAS-CENTRED (x-centre 128, matching the folder body) and widened to 116px so
        # each type emblem has room to carry more detail — per the recorded contact-sheet
        # directive. This supersedes the earlier text-x-generic byte-identity (the page had
        # sat at x-centre 134 to keep the retired document.svg pixel-identical); that
        # constraint is intentionally released, so the text/document row now re-renders.
        # Fold = a 28x28 dog-ear at the top-right. NOT in the tile-body list — a mimetype
        # has no dark tile.
        return ['<path d="M 70 56 L 158 56 L 186 84 L 186 200 L 70 200 Z" fill="none"/>',
                '<path d="M 158 56 L 158 84 L 186 84" fill="none"/>']
    raise KeyError(f"unknown template {name!r}")


TEMPLATE_NAMES = {"none", "app-tile", "folder", "document"}


# ---------------------------------------------------------------------------
# The pulse motif (VL §8) — the signature, scaled into a 256 icon
# ---------------------------------------------------------------------------
def pulse_path(kind: str) -> str | None:
    """The InterGen ECG pulse on a baseline (VL §8). The layout is ASYMMETRIC by
    construction — a SHORT lead-in on the left, the QRST spike LEFT-of-center, and a
    LONG baseline tail to the right (assets/intergen-mark/README.md:13; VL §8). This is a
    hard conformance property, not a style preference: a centered spike is wrong, so the
    asymmetry is asserted here (the single source of the motif) and cannot silently drift
    back to centered. `bottom` is a restrained accent line low under a glyph (e.g. the
    terminal); `primary` is the full signal (system-monitor). All geometry stays inside
    the safe area [40,216]."""
    if kind == "none":
        return None
    if kind == "bottom":
        # a taller, NARROWER spike with a near-vertical R-wave — the system-monitor pulse
        # in miniature (the operator's live in-set reference), sitting low under the glyph.
        # Its OWN x layout (narrow) so `primary` (system-monitor) stays byte-identical.
        baseline, peak, trough, qd, td = 168, 42, 32, 8, 8
        x0, lead, q, r, s, t, se, x1 = 44, 80, 94, 102, 114, 122, 136, 212
    else:  # primary — the system-monitor signal (ruled correct; unchanged)
        baseline, peak, trough, qd, td = 132, 60, 60, 16, 16
        x0, lead, q, r, s, t, se, x1 = 44, 72, 86, 100, 118, 132, 146, 212
    lead_len, tail_len, spike_center = lead - x0, x1 - se, (q + t) / 2
    assert tail_len > lead_len, "pulse: long tail vs short lead (VL §8 / mark) violated"
    assert spike_center < CANVAS / 2, "pulse: spike must sit left-of-center (VL §8 / mark)"
    return (f"M {x0} {baseline} L {lead} {baseline} "
            f"L {q} {baseline+qd} L {r} {baseline-peak} "
            f"L {s} {baseline+trough} L {t} {baseline-td} "
            f"L {se} {baseline} L {x1} {baseline}")


# ---------------------------------------------------------------------------
# Glow (the baked ECG-blue signature — VL §11.4, the scoped icon exception to
# "glow don't tint"). Implemented as a blurred ecg-blue copy of the outline,
# behind the mark. All colors stay on-palette; softness is a filter, not a color.
# ---------------------------------------------------------------------------
def glow_defs(std: float = 4.0) -> str:
    return (f'<filter id="igic-glow" x="-30%" y="-30%" width="160%" height="160%">'
            f'<feGaussianBlur stdDeviation="{std}"/></filter>')


# ---------------------------------------------------------------------------
# Provenance (design §7) — content-addressed, timestamp-free (determinism)
# ---------------------------------------------------------------------------
def provenance_block(recipe_id: str, recipe_sha: str, glyph_name: str | None,
                     glyph_sha: str | None, palette_sha: str, variant: str) -> str:
    g = f' glyph="{glyph_name}" glyph-sha256="{glyph_sha}"' if glyph_name else ""
    return (f'<metadata><igic:provenance xmlns:igic="{IGIC_NS}" '
            f'compiler="{COMPILER_VERSION}" recipe-id="{recipe_id}" '
            f'recipe-sha256="{recipe_sha}"{g} palette-sha256="{palette_sha}" '
            f'canvas="{CANVAS}" variant="{variant}"/></metadata>')


# ---------------------------------------------------------------------------
# Compositor — recipe + glyph -> one SVG string (design §4)
# ---------------------------------------------------------------------------
def _group(elements: list[str], stroke: str, sw: int, extra: str = "") -> str:
    if not elements:
        return ""
    inner = "".join(elements)
    return (f'<g fill="none" stroke="{stroke}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"{extra}>{inner}</g>')


# Badge marks (§11 R2): a secondary glyph composited at reduced scale, centred on a named
# corner anchor. The Settings-panel family/overlay marks layer these over (or as the backdrop
# under) the main glyph — the accessibility family badge, the security shield on a panel, the
# cascade marks. Anchors are points in the 256 canvas inside the safe area.
BADGE_ANCHORS = {
    "center": (128, 128), "top-left": (84, 84), "upper-left": (84, 84),
    "top-right": (172, 84), "lower-left": (84, 172), "lower-right": (172, 172),
    "corner-tl": (70, 70),   # a tighter top-left corner for a free-floating family cue (§11 R3-1)
}


def _badge_anchor(spec: dict) -> tuple[float, float]:
    """The badge centre: an explicit x/y overrides the named `at` anchor (fine placement, e.g.
    a caret nudged onto a screen). §11 R2/R4."""
    if "x" in spec and "y" in spec:
        return float(spec["x"]), float(spec["y"])
    return BADGE_ANCHORS[spec.get("at", "top-left")]


def _badge_group(spec: dict, sw: float, fg_paint: str, accent_paint: str, sym: bool) -> str:
    """One badge -> a `<g transform>` wrapping the extracted glyph at `scale`, centred on the
    `at` anchor (or explicit x/y). The pre-scale stroke is sw/scale so the rendered badge stroke
    matches the main mark weight. Strokes/fills in the foreground NAME (or accent when role=accent)."""
    g = load_glyph(spec["glyph"]); g.extract()
    ax, ay = _badge_anchor(spec)
    s = float(spec.get("scale", 0.4))
    is_accent = spec.get("role") == "accent"
    paint = accent_paint if is_accent else fg_paint
    scls = (' class="accent-stroke"' if is_accent else ' class="foreground-stroke"') if sym else ""
    fcls = (' class="accent-fill"' if is_accent else ' class="foreground-fill"') if sym else ""
    stroked = [e for e, f in zip(g.elements, g.filled) if not f]
    filled = [e for e, f in zip(g.elements, g.filled) if f]
    inner = []
    if stroked:
        inner.append(_group(stroked, paint, sw / s, extra=scls))
    for _el in filled:
        inner.append(_el.replace(' fill="none"/>', f' fill="{paint}"{fcls}/>'))
    tx, ty = ax - 128 * s, ay - 128 * s
    return f'<g transform="translate({tx:.3f},{ty:.3f}) scale({s})">{"".join(inner)}</g>'


def _signed_area2(ring: list[tuple[float, float]]) -> float:
    """Twice the shoelace signed area of a footprint ring in canvas (y-down) coords. > 0 is
    CLOCKWISE — the winding of the occlusion canvas rect `M0 0 H256 V256 H0 Z`; < 0 is CCW."""
    a = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        a += x0 * y1 - x1 * y0
    return a


def _ensure_ccw(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Force an occlusion footprint COUNTER-CLOCKWISE so its winding OPPOSES the clockwise canvas
    rect and the clip-path punches a real hole. cairosvg 2.9.0 ignores clip-rule="evenodd" and
    applies NONZERO winding, so a footprint wound the SAME way as the rect ADDS winding (no hole,
    no occlusion — a silent failure no green build catches) instead of subtracting. A ring already
    CCW is returned UNCHANGED (byte-identical output); only a CW ring is reversed. Circle/ellipse
    footprints are emitted below as sweep=0 arcs — CCW by construction — so they never need this."""
    return ring[::-1] if _signed_area2(ring) > 0 else ring


def _silhouette_subpath(spec: dict) -> str:
    """The occlusion FOOTPRINT of a badge, as an SVG subpath in canvas coordinates (§11 R5): the
    badge glyph's declared <g class="silhouette"> outer shape (or its bbox as a fallback), baked
    through the badge transform, and forced COUNTER-CLOCKWISE. Appended to the clockwise canvas
    rect so the clip-path clears the ENTIRE badge footprint from the base. cairosvg 2.9.0 ignores
    clip-rule="evenodd" and applies NONZERO winding, so the hole punches ONLY when the footprint
    winds OPPOSITE the rect — hence _ensure_ccw on every vertex footprint (circle/ellipse arcs are
    CCW by construction). cairosvg honors fill inside clipPath (not inside <mask>), so a shaped
    clip is what actually occludes the interior, not just the badge's own strokes."""
    ax, ay = _badge_anchor(spec)
    s = float(spec.get("scale", 0.4))
    tx, ty = ax - 128 * s, ay - 128 * s
    def X(v): return tx + float(v) * s
    def Y(v): return ty + float(v) * s
    # the footprint: a badge `sil` shape dict (glyph coords) wins; else the glyph's own
    # <g class="silhouette">; else its bbox. Carrying `sil` on the badge keeps a shared glyph
    # file unchanged (no provenance ripple to icons that use it as a main glyph).
    sil = spec.get("sil")
    if sil:
        shapes = [(sil.get("shape"), sil)]
    else:
        g = load_glyph(spec["glyph"]); g.extract()
        shapes = g.silhouette or [("rect", {"x": g.min_xy, "y": g.min_xy,
                                            "width": g.max_xy - g.min_xy, "height": g.max_xy - g.min_xy})]
    out = []
    for ln, a in shapes:
        if ln == "circle":
            cx, cy, r = X(a["cx"]), Y(a["cy"]), float(a["r"]) * s
            out.append(f"M {cx-r:.2f} {cy:.2f} a {r:.2f} {r:.2f} 0 1 0 {2*r:.2f} 0 "
                       f"a {r:.2f} {r:.2f} 0 1 0 {-2*r:.2f} 0 Z")
        elif ln == "ellipse":
            cx, cy, rx, ry = X(a["cx"]), Y(a["cy"]), float(a["rx"]) * s, float(a["ry"]) * s
            out.append(f"M {cx-rx:.2f} {cy:.2f} a {rx:.2f} {ry:.2f} 0 1 0 {2*rx:.2f} 0 "
                       f"a {rx:.2f} {ry:.2f} 0 1 0 {-2*rx:.2f} 0 Z")
        elif ln == "rect":
            x0, y0, w, h = X(a["x"]), Y(a["y"]), float(a["width"]) * s, float(a["height"]) * s
            ring = _ensure_ccw([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])
            out.append("M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in ring) + " Z")
        elif ln == "polygon":
            pts = a["points"].replace(",", " ").split()
            xy = _ensure_ccw([(X(pts[i]), Y(pts[i + 1])) for i in range(0, len(pts) - 1, 2)])
            out.append("M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in xy) + " Z")
    return " ".join(out)


def compose_icon(recipe: dict, palette: dict, glyph: Glyph | None,
                 variant: str, recipe_sha: str, palette_sha: str,
                 center: tuple[float, float] | None = None) -> str:
    """Build the composed SVG for one recipe + variant ('detailed'|'simple')."""
    kind = recipe.get("kind", "color")
    ecg = resolve_color("ecg-blue", palette)

    if kind == "symbolic":
        stroke = resolve_color("text", palette)              # off-white
        color = resolve_color(recipe.get("accent", "ecg-blue"), palette)
        want_glow = False
        template = "none"
    else:
        color = resolve_color(recipe.get("color", "ecg-blue"), palette)
        stroke = color
        want_glow = (recipe.get("glow", "none") == "signature") and variant == "detailed"
        template = recipe.get("template", "none")

    tmpl = template_elements(template)
    glyph_elems = glyph.elements if glyph else []
    glyph_filled = glyph.filled if glyph else []
    outline = tmpl + glyph_elems

    # author grid -> optical band: the caller passes the render-probed content centre
    # (build_icons._probe_center); None (the validator, the probe itself) = canvas centre.
    default_fit = SYMBOLIC_BAND_FIT if kind == "symbolic" else 1.0
    band_open, band_k = band_transform(recipe.get("fit", default_fit), center)
    sw = band_stroke(STROKE_DETAIL if variant == "detailed" else STROKE_SIMPLE, band_k)

    parts = [f'<svg xmlns="{SVG_NS}" viewBox="0 0 {CANVAS} {CANVAS}">']
    defs = glow_defs() if want_glow else ""
    if defs:
        parts.append(f"<defs>{defs}</defs>")
    parts.append(provenance_block(
        recipe["id"], recipe_sha, glyph.name if glyph else None,
        glyph.sha if glyph else None, palette_sha, variant))
    # author grid -> optical band (VL §11): ONE uniform transform wraps the whole drawable
    # body, so badges/occlusion clips/pulse stay in author coordinates and scale together.
    parts.append(band_open)
    body_start = len(parts)          # occludable body begins here (§11 R3 opaque overlays)

    # dark tile body for templates that carry one (fill on-palette bg-card)
    if template in ("app-tile", "folder"):
        body = resolve_color("bg-card", palette)
        for el in tmpl:
            parts.append(el.replace('fill="none"', f'fill="{body}"'))

    # baked glow: an ecg-blue, blurred copy of the outline behind the mark
    if want_glow and outline:
        parts.append(_group(outline, ecg, sw + 2,
                            extra=f' filter="url(#igic-glow)" opacity="0.55"'))

    # the mark: template outline + glyph, in the recipe color / off-white.
    # SYMBOLIC (§6.2): the concrete off-white `text` paint carries the standalone/
    # cairosvg render, and the legacy GTK symbolic class `foreground-stroke` rides
    # ALONGSIDE it so GTK recolors the region to the theme foreground by NAME (not by
    # honoring the baked hex). Leg B symbolic icons (wifi/gear) are foreground-only —
    # every extracted glyph primitive is stroked (fill="none"), so the stroke class is
    # the only carrier needed; the `accent`-NAME region (ECG-blue via §6.2 carrier 2)
    # and the `.gpa` attribute form (§6.4) are the modern option, not exercised here.
    sym_cls = ' class="foreground-stroke"' if kind == "symbolic" else ""
    parts.append(_group(tmpl, stroke, sw, extra=sym_cls))
    if glyph_elems:
        if kind == "symbolic" and recipe.get("fill"):
            # solid-fill symbolic (platform convention for the record dot / stop square, R3-7):
            # each primitive filled in the foreground color NAME, no stroke — the opt-in exception
            # to VL line-forward that these privacy indicators need.
            for _el in glyph_elems:
                parts.append(_el.replace(' fill="none"/>', f' fill="{stroke}" class="foreground-fill"/>'))
        else:
            # a glyph may mix a stroked outline with a <g class="fill"> SOLID region (R4-8: a
            # button RING stroked at the family weight around a solid record dot / stop square).
            # With no fill region `filled` is empty and this is byte-identical to the prior single
            # stroked group.
            stroked = [e for e, f in zip(glyph_elems, glyph_filled) if not f]
            filled = [e for e, f in zip(glyph_elems, glyph_filled) if f]
            parts.append(_group(stroked, stroke, sw, extra=sym_cls))
            fill_cls = ' class="foreground-fill"' if kind == "symbolic" else ""
            for _el in filled:
                parts.append(_el.replace(' fill="none"/>', f' fill="{stroke}"{fill_cls}/>'))

    # accent sub-glyph (the power-bolt rule): an optional second glyph rendered in the accent
    # color NAME (ecg-blue) alongside the foreground body — a small "indicating" mark (the power
    # bolt on power-tied icons). Symbolic only; loaded here so build + validate share one path,
    # and gated by validate_composed_svg on the output like every other region.
    acc_name = recipe.get("accent_glyph")
    if kind == "symbolic" and acc_name:
        acc_glyph = load_glyph(acc_name)
        acc_glyph.extract()
        if acc_glyph.elements:
            # A <g class="fill"> region in the accent glyph renders SOLID in the accent color
            # NAME (parity with the main-glyph fill split above). The R5-2 power symbol reads as a
            # filled silhouette where it is a small embedded indicator whose outline interior would
            # close at status sizes (the power-saver plug, the gnome-power-manager gear); the large
            # top-left accent bolt (no fill region) stays a stroked outline — byte-identical to the
            # prior single group, since `filled` is then empty.
            stroked = [e for e, f in zip(acc_glyph.elements, acc_glyph.filled) if not f]
            filled = [e for e, f in zip(acc_glyph.elements, acc_glyph.filled) if f]
            if stroked:
                parts.append(_group(stroked, color, sw, extra=' class="accent-stroke"'))
            for _el in filled:
                parts.append(_el.replace(' fill="none"/>', f' fill="{color}" class="accent-fill"/>'))

    # badge marks (§11 R2/R3): secondary glyphs at reduced scale on a corner anchor, layered in
    # order over the main mark. An `occlude: true` badge (R3 opaque overlays) clears everything
    # drawn before it within its dilated silhouette via a luminance <mask> (white shows, a black
    # badge footprint hides), so nothing of the base reads THROUGH the badge.
    occ_defs = []
    for k, spec in enumerate(recipe.get("badges") or []):
        if spec.get("occlude", True):        # R4-1: occlusion is the badge DEFAULT (opt out with occlude:false)
            cid = f"igic-occ-{k}"
            # canvas rect + the badge FOOTPRINT, clip-rule evenodd -> the base is clipped to
            # EXCLUDE the footprint (a real hole under the whole badge, interior included). R5-1.
            occ_defs.append(
                f'<clipPath id="{cid}" clipPathUnits="userSpaceOnUse">'
                f'<path d="M0 0 H{CANVAS} V{CANVAS} H0 Z {_silhouette_subpath(spec)}" '
                f'clip-rule="evenodd"/></clipPath>')
            below = "".join(parts[body_start:]); del parts[body_start:]
            parts.append(f'<g clip-path="url(#{cid})">{below}</g>')
        parts.append(_badge_group(spec, sw, stroke, color, kind == "symbolic"))
    if occ_defs:
        parts.insert(body_start, f'<defs>{"".join(occ_defs)}</defs>')

    # pulse accent / primary (the signature motif)
    pd = pulse_path(recipe.get("pulse", "none"))
    if pd:
        pstroke = ecg
        parts.append(f'<path d="{pd}" fill="none" stroke="{pstroke}" '
                     f'stroke-width="{sw}" stroke-linecap="round" '
                     f'stroke-linejoin="round"/>')

    parts.append("</g></svg>")       # closes the band-transform group
    return "".join(parts)


# ---------------------------------------------------------------------------
# Family generation (§5.1 rows) — one manifest -> N single-recipes
# ---------------------------------------------------------------------------
# A family manifest is a thin front end over the proven single-recipe pipeline:
# every row expands to a full recipe (family fields inherited, row overrides a
# small set) and the existing validate -> compose -> raster path runs UNCHANGED.
# No change to compositing, the gates, provenance, or determinism.

FAMILY_INHERITED = ("category", "kind", "template", "glow", "pulse", "export")


def expand_row(family: dict, row: dict) -> dict:
    """Expand one `rows` family row into a full single-recipe dict (§5.1). Family
    fields are inherited; the row overrides id / glyph(=emblem) / color(=accent).
    The result is a normal recipe — validate_recipe + compose_icon consume it as-is."""
    kind = family.get("kind", "color")
    r: dict = {
        "schema_version": 1,
        "id": row.get("id"),
        "category": family.get("category"),
        "kind": kind,
        "template": family.get("template", "none"),
        "glow": family.get("glow", "none"),
        "pulse": family.get("pulse", "none"),
        "export": family.get("export", {}),
    }
    accent = row.get("accent")
    if kind == "symbolic":
        r["accent"] = accent          # symbolic recipes name the accent token
    else:
        r["color"] = accent           # color recipes name it `color`
    emblem = row.get("emblem", "none")
    if emblem and emblem != "none":
        r["glyph"] = emblem           # `none` -> template-only (e.g. the plain folder)
    if row.get("accent_glyph"):
        r["accent_glyph"] = row["accent_glyph"]   # a second glyph in the accent color (power bolt)
    if "fill" in row:
        r["fill"] = row["fill"]                    # solid-fill symbolic (record dot / stop square)
    if "glow" in row:
        r["glow"] = row["glow"]                    # row opts out of the family glow: a large solid-fill
                                                   # vendor ingest (U1) halos under the blurred signature,
                                                   # while a line-forward sibling in the same family keeps it
    if "mode" in row:
        r["mode"] = row["mode"]                    # IC-029 swap switch: styled (default) | passthrough
    if "passthrough" in row:
        r["passthrough"] = row["passthrough"]      # official vendor mark staged in PASSTHROUGH_DIR (fallback)
    if "gradient" in row:
        r["gradient"] = row["gradient"]            # IC-034: full-color KNOWN-icon source in GRADIENT_DIR, emitted luminance-keyed to gradient blue
    if row.get("name"):
        r["name"] = row["name"]
    if row.get("stem"):
        r["stem"] = row["stem"]        # explicit theme filename (reverse-DNS panel names)
    if row.get("badges"):
        r["badges"] = row["badges"]    # secondary corner-anchored marks (§11 R2)
    if "fit" in row:
        r["fit"] = row["fit"]          # per-mark optical-band fit factor (VL §11 band mapping)
    return r


# ---------------------------------------------------------------------------
# Stateful families (§6.3) — one base glyph + a level ladder -> -symbolic.svg set
# ---------------------------------------------------------------------------
# The base glyph carries an always-on part and an ordered <g class="levels"> ladder
# (inner -> outer). A state at `level` renders levels[i] at full opacity when i<level,
# dimmed otherwise. Opacity survives GTK recoloring (it is not a color), so the ladder
# reads correctly on any theme; the concrete off-white `text` paint + the symbolic
# `foreground` class NAME are emitted together (§6.2), no glow, no tile.
STATE_DIM = 0.3               # inactive-level opacity (§6.3)
_STATE_COLOR_ATTRS = ("stroke", "fill", "stroke-width", "style", "class",
                      "stroke-linecap", "stroke-linejoin", "opacity")

# Symbolic color ROLES a state overlay may carry (§6.1/§6.2): recolorable NAMES GTK maps to
# the theme palette. `foreground` = theme text (off-white); `accent` = the ECG-blue theme
# accent (e.g. the charging bolt / boost mark); the semantic roles are used only where the
# icon's meaning genuinely IS that state. Each role -> the concrete on-palette paint for the
# standalone/cairosvg render (GTK recolors by the class NAME regardless of the baked paint).
_SYMBOLIC_ROLES = {"foreground", "accent", "success", "warning", "error"}
_ROLE_PAINT = {"foreground": "text", "accent": "ecg-blue",
               "success": "success", "warning": "warning", "error": "error"}


def _strip_primitive(el) -> tuple[str, str]:
    """(localname, geometry-only attr string) — color/style/opacity/class stripped,
    so the state compositor owns paint. Attribute order is the glyph's document order
    (deterministic: same base bytes -> same string)."""
    ln = _localname(el.tag)
    attrs = {}
    for a, val in el.attrib.items():
        an = _localname(a)
        if an in _STATE_COLOR_ATTRS:
            continue
        attrs[an] = val
    return ln, "".join(f' {a}="{v}"' for a, v in attrs.items())


def load_state_base(name: str, glyph_dir: Path = GLYPH_DIR):
    """Parse a states-family base glyph and split it into
    (base_sha, always_on, levels, overlays, gauge). always_on/levels are ordered
    [(localname, attr_str), ...] with color stripped; `overlays` maps an overlay NAME ->
    (role, [primitives]). An overlay group is `<g class="overlay NAME [role]">` and renders
    ONLY for the states that request it (§6.3 charging bolt / muted slash / boost mark).
    A base carries AT MOST ONE of a discrete `<g class="levels">` ladder OR a proportional
    `<g class="gauge">` (a single reference rect whose width the compositor scales by the
    state's level 0..100 -> the decile fill, §6.3.2); `gauge` is that rect's geometry or None.
    A base may also carry NEITHER — an overlay-only base whose states differ by mark alone
    (wired / vpn / bluetooth: one silhouette + a per-state overlay, no signal ladder).
    Everything outside levels/gauge and any overlay group is always-on. Fail-closed: gate 2
    (security) + structural (viewBox / node-count / primitive-only / a levels/gauge/overlay
    differentiator must exist / overlay roles known) raise ValueError so the family fails, never partial."""
    path = glyph_dir / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"state base glyph {name!r} not found at {path}")
    data = path.read_bytes()
    base_sha = sha256_bytes(data)
    root = parse_svg_bytes(data)
    doctype = root.getroottree().docinfo.doctype or None

    problems = security_violations(root, doctype)          # gate 2
    vb = (root.get("viewBox") or "").strip()
    if vb != f"0 0 {CANVAS} {CANVAS}":
        problems.append(f"state base {name}: viewBox {vb!r} != '0 0 {CANVAS} {CANVAS}'")
    if sum(1 for _ in root.iter()) > NODE_LIMIT:
        problems.append(f"state base {name}: node count exceeds {NODE_LIMIT}")

    always_on: list[tuple[str, str]] = []
    levels: list[tuple[str, str]] = []
    overlays: dict[str, tuple[str, list]] = {}
    gauge: dict | None = None
    small: dict | None = None    # optional `<g class="small">` alternative subtree:
                                 # {"always_on": [...], "overlays": {name: (role, prims, osw)}}
                                 # composed for raster sizes <= SMALL_GEOM_AT; overlays it
                                 # does NOT redefine fall back to the full-detail versions

    def collect(el, out: list, filled: bool = False) -> None:   # flatten drawables under a group
        for child in el:
            if not isinstance(child.tag, str):
                continue
            ln = _localname(child.tag)
            if ln in ("defs", "metadata", "title", "desc"):
                continue
            if ln == "g":                                       # <g class="fill"> -> solid descendants
                cls = (child.get("class") or "").split()
                collect(child, out, filled or ("fill" in cls))
            elif ln in GLYPH_DRAWABLE:
                lnm, attrs = _strip_primitive(child)
                out.append((lnm, attrs, filled))
            else:
                problems.append(f"state base {name}: non-primitive <{ln}> "
                                f"(VL §7 permits {sorted(GLYPH_DRAWABLE)})")

    def _read_gauge(g) -> None:                            # <g class="gauge"> -> the 100%-fill ref rect
        nonlocal gauge
        rects = [c for c in g if isinstance(c.tag, str) and _localname(c.tag) == "rect"]
        if len(rects) != 1:
            problems.append(f"state base {name}: gauge group needs exactly one <rect> "
                            f"(got {len(rects)})")
            return
        if gauge is not None:
            problems.append(f"state base {name}: more than one gauge group")
            return
        r = rects[0]
        miss = [k for k in ("x", "y", "width", "height") if r.get(k) is None]
        if miss:
            problems.append(f"state base {name}: gauge rect missing {miss}")
            return
        try:
            fw = int(r.get("width"))
        except (TypeError, ValueError):
            problems.append(f"state base {name}: gauge rect width {r.get('width')!r} "
                            f"must be an integer")
            return
        gauge = {"x": r.get("x"), "y": r.get("y"), "width": fw,
                 "height": r.get("height"), "rx": r.get("rx", "0")}

    def _read_small(g) -> None:
        """`<g class="small">` — the size-conditional alternative subtree (fail-closed):
        its direct drawables replace always_on and its overlay groups replace same-named
        full overlays at raster sizes <= SMALL_GEOM_AT. No levels/gauge inside (a ladder
        cannot be size-conditional); subset-only overlay names, checked after the walk."""
        nonlocal small
        if small is not None:
            problems.append(f"state base {name}: more than one small group")
            return
        s_always: list = []
        s_overlays: dict = {}
        for child in g:
            if not isinstance(child.tag, str):
                continue
            ln = _localname(child.tag)
            if ln in ("defs", "metadata", "title", "desc"):
                continue
            if ln == "g":
                cls = (child.get("class") or "").split()
                if "overlay" in cls:
                    toks = [t for t in cls if t != "overlay"]
                    oname = toks[0] if toks else "overlay"
                    orole = toks[1] if len(toks) > 1 else "foreground"
                    osw = child.get("stroke-width")
                    prims: list = []
                    collect(child, prims)
                    s_overlays[oname] = (orole, prims, osw)
                elif "levels" in cls or "gauge" in cls:
                    problems.append(f"state base {name}: small group carries a "
                                    f"levels/gauge group (unsupported)")
                else:
                    problems.append(f"state base {name}: small group contains an "
                                    f"unnamed nested group")
            elif ln in GLYPH_DRAWABLE:
                s_always.append(_strip_primitive(child))
            else:
                problems.append(f"state base {name}: non-primitive <{ln}> in small group "
                                f"(VL §7 permits {sorted(GLYPH_DRAWABLE)})")
        if not s_always and not s_overlays:
            problems.append(f"state base {name}: small group is empty")
            return
        small = {"always_on": s_always, "overlays": s_overlays}

    def walk(el, in_levels: bool) -> None:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            ln = _localname(child.tag)
            if ln in ("defs", "metadata", "title", "desc"):
                continue
            if ln == "g":
                cls = (child.get("class") or "").split()
                if "small" in cls:
                    _read_small(child)
                elif "overlay" in cls:
                    toks = [t for t in cls if t != "overlay"]
                    oname = toks[0] if toks else "overlay"
                    orole = toks[1] if len(toks) > 1 else "foreground"
                    osw = child.get("stroke-width")     # optional per-overlay stroke override
                    prims: list = []
                    collect(child, prims)
                    overlays[oname] = (orole, prims, osw)
                elif "gauge" in cls:
                    _read_gauge(child)
                else:
                    walk(child, in_levels or ("levels" in cls))
            elif ln in GLYPH_DRAWABLE:
                (levels if in_levels else always_on).append(_strip_primitive(child))
            else:
                problems.append(f"state base {name}: non-primitive <{ln}> "
                                f"(VL §7 permits {sorted(GLYPH_DRAWABLE)})")

    walk(root, False)
    if not levels and gauge is None and not overlays:
        problems.append(f"state base {name}: no <g class=\"levels\"> ladder, "
                        f"<g class=\"gauge\"> gauge, or overlay group found")
    if levels and gauge is not None:
        problems.append(f"state base {name}: carries both a levels ladder and a gauge "
                        f"(a base is exactly one)")
    for oname, (orole, _prims, _osw) in overlays.items():
        if orole not in _SYMBOLIC_ROLES:
            problems.append(f"state base {name}: overlay {oname!r} role {orole!r} "
                            f"not in {sorted(_SYMBOLIC_ROLES)}")
    if small is not None:
        for oname, (orole, _prims, _osw) in small["overlays"].items():
            if oname not in overlays:
                problems.append(f"state base {name}: small overlay {oname!r} has no "
                                f"full-detail counterpart (subset-only)")
            elif orole != overlays[oname][0]:
                problems.append(f"state base {name}: small overlay {oname!r} role "
                                f"{orole!r} != full-detail role {overlays[oname][0]!r}")
        if small["overlays"] and not small["always_on"] and not always_on:
            problems.append(f"state base {name}: small group redefines overlays only "
                            f"but the base has no always-on body")
    if problems:
        raise ValueError("; ".join(problems))
    return base_sha, always_on, levels, overlays, gauge, small


def state_provenance_block(state_id: str, base_sha: str, palette_sha: str,
                           level: int, variant: str = "symbolic",
                           overlays: tuple = ()) -> str:
    """Content-addressed provenance for a state icon (§6.3). Records `level`, any active
    overlays, and the base-glyph sha; NO timestamp -> regenerate is byte-identical. The
    overlays attr is omitted when empty, so a plain level ladder (e.g. wifi) is unchanged."""
    ov = f' overlays="{" ".join(overlays)}"' if overlays else ""
    return (f'<metadata><igic:provenance xmlns:igic="{IGIC_NS}" '
            f'compiler="{COMPILER_VERSION}" recipe-id="{state_id}" '
            f'base-sha256="{base_sha}" palette-sha256="{palette_sha}" '
            f'canvas="{CANVAS}" variant="{variant}" level="{level}"{ov}/></metadata>')


def _state_primitive(localname: str, attr_str: str, paint: str, opacity: float,
                     role: str = "foreground") -> str:
    """Emit one state primitive in a symbolic color ROLE (§6.2): a concrete on-palette paint
    plus the legacy symbolic class NAME so GTK recolors the region by name. A circle fills
    (`<role>-fill`), any other primitive strokes (`<role>-stroke`); stroke-width / caps /
    fill=none are inherited from the group. role='foreground' + the fg paint reproduces the
    prior output byte-for-byte (so a plain level ladder like wifi is unchanged)."""
    op = f"{opacity:g}"
    if localname == "circle":
        return f'<{localname}{attr_str} fill="{paint}" class="{role}-fill" opacity="{op}"/>'
    return f'<{localname}{attr_str} stroke="{paint}" class="{role}-stroke" opacity="{op}"/>'


def compose_state_icon(state_id: str, palette: dict, base_sha: str, palette_sha: str,
                       always_on, levels, level: int, overlays: dict | None = None,
                       state_overlays: tuple = (), variant: str = "simple",
                       gauge: dict | None = None, dim: bool = False,
                       fit: float = SYMBOLIC_BAND_FIT,
                       center: tuple[float, float] | None = None) -> str:
    """Build one symbolic state-ladder SVG (§6.3). Always-on primitives render at full
    opacity; level i renders at full opacity if i<level else STATE_DIM; then any overlays
    THIS state requests (§6.3: the charging bolt = accent, a muted slash, a boost mark, ...)
    render in their symbolic color role. Symbolic status icons live at 16-24px (top-bar /
    nav-pane), so the ~6% simplified stroke is used — the exact weight/legibility at 16px is
    the §6.4 on-our-GTK-stack check (PROXY). No glow, no tile. Deterministic (no timestamp).
    With no overlays this is byte-identical to the plain level ladder (e.g. wifi). A `gauge`
    base (§6.3.2) renders a proportional fill (a single rect whose width scales with level
    0..100) in place of the discrete ladder — the runtime battery-level family."""
    fg = resolve_color("text", palette)          # off-white foreground; derived, not hardcoded
    # R6-1 (stroke uniformity, ruling R2): state families now carry the ROWS' two-variant stroke —
    # STROKE_DETAIL (~2%) on the scalable/large render, STROKE_SIMPLE (~6%) at status sizes — so the
    # contact sheet renders every family at the same detailed weight (no grouping reads heavier /
    # lighter) while small sizes stay bold. build routes size -> variant exactly like the rows path.
    # centre = the render-probed BASE content centre (always-on + ladder + gauge, overlays
    # excluded) — one centre per family, so a wide overlay never shifts the shared body.
    band_open, band_k = band_transform(fit, center)
    sw = band_stroke(STROKE_DETAIL if variant == "detailed" else STROKE_SIMPLE, band_k)
    parts = [f'<svg xmlns="{SVG_NS}" viewBox="0 0 {CANVAS} {CANVAS}">',
             state_provenance_block(state_id, base_sha, palette_sha, level,
                                    variant=variant, overlays=tuple(state_overlays)),
             band_open,
             f'<g fill="none" stroke-width="{sw}" stroke-linecap="round" '
             f'stroke-linejoin="round">']
    base_op = STATE_DIM if dim else 1.0   # a disabled state greys the whole glyph (R3-2 disabled-v2)
    for ln, attr_str in always_on:
        parts.append(_state_primitive(ln, attr_str, fg, base_op))
    if gauge is not None:
        # proportional decile gauge (§6.3.2): a single filled foreground rect whose width
        # scales with level 0..100 (recolored by the `foreground-fill` name). Integer
        # arithmetic -> deterministic; level 0 -> no fill (an empty body outline).
        gw = level * gauge["width"] // 100
        if gw > 0:
            parts.append(f'<rect x="{gauge["x"]}" y="{gauge["y"]}" width="{gw}" '
                         f'height="{gauge["height"]}" rx="{gauge["rx"]}" '
                         f'fill="{fg}" class="foreground-fill" opacity="1"/>')
    else:
        for i, (ln, attr_str) in enumerate(levels):
            parts.append(_state_primitive(ln, attr_str, fg, 1.0 if i < level else STATE_DIM))
    for oname in state_overlays:                 # per-state marks in their symbolic color role
        orole, prims, osw = (overlays or {})[oname]
        paint = resolve_color(_ROLE_PAINT[orole], palette)
        if osw is not None:                      # R6-1: per-overlay stroke = a MULTIPLE of the base
            # stroke (e.g. the disabled X at 2.5x), so "thicker than the glyph" (R4-1) holds in BOTH
            # the detailed and simple variants — an absolute width could not (it would invert small).
            parts.append(f'<g stroke-width="{sw * float(osw):g}">')
        for ln, attr_str, filled in prims:
            if filled:                           # a <g class="fill"> region -> a solid shape
                parts.append(f'<{ln}{attr_str} fill="{paint}" class="{orole}-fill" opacity="1"/>')
            else:
                parts.append(_state_primitive(ln, attr_str, paint, 1.0, orole))
        if osw is not None:
            parts.append('</g>')
    parts.append("</g></g></svg>")   # closes the stroke group + the band-transform group
    return "".join(parts)


# ---------------------------------------------------------------------------
# Gate 1 — spec compliance (recipe-level + composed-output-level)
# ---------------------------------------------------------------------------
RECIPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# An explicit output stem may carry the reverse-DNS app-id casing GTK looks up verbatim
# (e.g. org.gnome.Settings-network) — a superset of the lowercase recipe-id grammar.
STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GLOW_VALUES = {"none", "signature"}
PULSE_VALUES = {"none", "bottom", "primary"}
KIND_VALUES = {"color", "symbolic"}
MODE_VALUES = {"styled", "passthrough", "gradient"}   # IC-029: styled = IGIC restyle; passthrough = emit the unmodified vendor mark.
                                          # IC-034: gradient = luminance-key the full-color KNOWN icon to gradient blue (two-register rule: third-party marks only)


def validate_recipe(recipe: dict, palette: dict, glyph_dir: Path = GLYPH_DIR) -> list[str]:
    v: list[str] = []
    def req(k):
        if k not in recipe:
            v.append(f"missing required key {k!r}")
            return False
        return True

    if recipe.get("schema_version") != 1:
        v.append(f"schema_version must be 1 (got {recipe.get('schema_version')!r})")
    if req("id") and not RECIPE_ID_RE.match(str(recipe["id"])):
        v.append(f"id {recipe.get('id')!r} fails {RECIPE_ID_RE.pattern}")
    if req("category") and recipe["category"] not in FREEDESKTOP_CONTEXTS:
        v.append(f"category {recipe.get('category')!r} not in {sorted(FREEDESKTOP_CONTEXTS)}")
    kind = recipe.get("kind", "color")
    if kind not in KIND_VALUES:
        v.append(f"kind {kind!r} not in {sorted(KIND_VALUES)}")

    # color / accent tokens must resolve
    if kind == "color":
        for key in ("color",):
            tok = recipe.get(key, "ecg-blue")
            if tok not in palette["_by_name"]:
                v.append(f"{key} token {tok!r} not in controlled palette")
        template = recipe.get("template", "none")
        if template not in TEMPLATE_NAMES:
            v.append(f"template {template!r} not in {sorted(TEMPLATE_NAMES)}")
        if recipe.get("glow", "none") not in GLOW_VALUES:
            v.append(f"glow {recipe.get('glow')!r} not in {sorted(GLOW_VALUES)}")
        # IC-029 swap-ready: mode + a staged official-vendor passthrough asset
        mode = recipe.get("mode", "styled")
        if mode not in MODE_VALUES:
            v.append(f"mode {mode!r} not in {sorted(MODE_VALUES)}")
        pt = recipe.get("passthrough")
        if pt is not None and not (PASSTHROUGH_DIR / pt).exists():
            v.append(f"passthrough asset {pt!r} has no file at {PASSTHROUGH_DIR}/{pt}")
        if mode == "passthrough" and not pt:
            v.append("mode 'passthrough' requires a passthrough asset")
        # IC-034 gradient: a staged full-color KNOWN-icon source, emitted luminance-keyed to blue
        gr = recipe.get("gradient")
        if gr is not None and not (GRADIENT_DIR / gr).exists():
            v.append(f"gradient asset {gr!r} has no file at {GRADIENT_DIR}/{gr}")
        if mode == "gradient" and not gr:
            v.append("mode 'gradient' requires a gradient asset")
    else:
        acc = recipe.get("accent", "ecg-blue")
        if acc not in palette["_by_name"]:
            v.append(f"accent token {acc!r} not in controlled palette")

    if recipe.get("pulse", "none") not in PULSE_VALUES:
        v.append(f"pulse {recipe.get('pulse')!r} not in {sorted(PULSE_VALUES)}")

    fit = recipe.get("fit", 1.0)
    if not (isinstance(fit, (int, float)) and FIT_MIN <= fit <= FIT_MAX):
        v.append(f"fit {fit!r} must be a number in [{FIT_MIN}, {FIT_MAX}]")

    # glyph must exist (unless a template-only color icon like a plain folder)
    glyph_name = recipe.get("glyph")
    # a passthrough/gradient mark emits a STAGED asset, not a composed glyph — it carries its
    # own content and legitimately has no glyph (IC-029 passthrough, IC-034 gradient).
    emits_asset = recipe.get("mode", "styled") in ("passthrough", "gradient")
    if glyph_name is None:
        if kind == "symbolic":
            v.append("symbolic icon requires a glyph")
        # a template-less color icon needs SOME content: a glyph or the pulse motif
        if kind == "color" and not emits_asset \
                and recipe.get("template", "none") == "none" \
                and recipe.get("pulse", "none") == "none":
            v.append("a template-less, pulse-less icon requires a glyph")
    elif not (glyph_dir / f"{glyph_name}.svg").exists():
        v.append(f"glyph {glyph_name!r} has no file at {glyph_dir}/{glyph_name}.svg")

    # optional accent sub-glyph (power bolt) must exist if named
    acc_name = recipe.get("accent_glyph")
    if acc_name is not None and not (glyph_dir / f"{acc_name}.svg").exists():
        v.append(f"accent_glyph {acc_name!r} has no file at {glyph_dir}/{acc_name}.svg")

    # optional badge marks (§11 R2) — each names an existing glyph, a corner anchor, a scale
    for spec in (recipe.get("badges") or []):
        if not isinstance(spec, dict):
            v.append(f"badge {spec!r} must be a mapping"); continue
        bg = spec.get("glyph")
        if not bg or not (glyph_dir / f"{bg}.svg").exists():
            v.append(f"badge glyph {bg!r} has no file at {glyph_dir}/{bg}.svg")
        has_xy = "x" in spec and "y" in spec
        if not has_xy and spec.get("at", "top-left") not in BADGE_ANCHORS:
            v.append(f"badge at {spec.get('at')!r} not in {sorted(BADGE_ANCHORS)}")
        for k2 in ("x", "y"):
            if k2 in spec and not isinstance(spec[k2], (int, float)):
                v.append(f"badge {k2} {spec[k2]!r} must be a number")
        s = spec.get("scale", 0.4)
        if not (isinstance(s, (int, float)) and 0 < s <= 1):
            v.append(f"badge scale {s!r} must be a number in (0, 1]")

    # export sizes
    exp = recipe.get("export", {})
    sizes = exp.get("png", [])
    bad = [s for s in sizes if s not in RASTER_SIZES_ALLOWED]
    if bad:
        v.append(f"export.png sizes {bad} outside allowed {sorted(RASTER_SIZES_ALLOWED)}")
    return v


def validate_composed_svg(svg_string: str, palette: dict) -> list[str]:
    """Gate 1 (output structure + palette-only) AND gate 2 (security), run on the
    final composed SVG. Fail-closed union of both gates."""
    v: list[str] = []
    data = svg_string.encode()
    try:
        root = parse_svg_bytes(data)
    except etree.XMLSyntaxError as e:
        return [f"composed SVG does not parse: {e}"]
    doctype = root.getroottree().docinfo.doctype or None

    # --- gate 2 security ---
    v += security_violations(root, doctype)

    # --- gate 1 structure ---
    vb = (root.get("viewBox") or "").strip()
    if vb != f"0 0 {CANVAS} {CANVAS}":
        v.append(f"composed viewBox {vb!r} != '0 0 {CANVAS} {CANVAS}'")
    n = sum(1 for _ in root.iter())
    if n > NODE_LIMIT:
        v.append(f"composed node count {n} > {NODE_LIMIT}")

    # provenance present
    if root.find(f".//{{{IGIC_NS}}}provenance") is None:
        v.append("composed SVG missing igic:provenance metadata")

    # <mask>/<clipPath> content is occlusion geometry, not icon paint — exempt its descendants
    # from the palette-only + canvas-bound checks (§11 R3/R5 opaque overlays).
    mask_desc = set()
    for m in root.iter():
        if isinstance(m.tag, str) and _localname(m.tag).lower() in ("mask", "clippath"):
            mask_desc.update(m.iter())

    # palette-only: every explicit color must be a palette hex (or none/url(#)/currentColor)
    allowed = set(palette["_hex_set"]) | {"none", "currentcolor", "transparent"}
    for el in root.iter():
        if not isinstance(el.tag, str) or el in mask_desc:
            continue
        for attr in ("stroke", "fill", "stop-color", "flood-color", "color"):
            val = el.get(attr)
            if val is None:
                continue
            val = val.strip().lower()
            if val.startswith("url(#"):
                continue
            if val in allowed:
                continue
            v.append(f"off-palette color {attr}={val!r} on <{_localname(el.tag)}>")

    # safe-area (approximate): all path/shape coordinates within the canvas (HARD);
    # warn if any drifts outside the 16% safe area (SOFT — precise stroke-aware bbox
    # is a Phase-2 refinement).
    minc, maxc = 999.0, -999.0
    for el in root.iter():
        if not isinstance(el.tag, str) or el in mask_desc:
            continue
        if _localname(el.tag) in ("path", "rect", "circle", "line", "polyline", "polygon", "ellipse"):
            for c in _coords_in(el):
                minc, maxc = min(minc, c), max(maxc, c)
    if maxc > CANVAS or minc < 0:
        v.append(f"geometry outside canvas [0,{CANVAS}] (min {minc}, max {maxc})")
    return v


def validate_family(fam: dict, palette: dict) -> list[str]:
    """Gate the family MANIFEST header once (§5.1 / §6.3). Per-row deep validation
    still runs via validate_recipe after expansion; per-state via load_state_base +
    validate_composed_svg. Fail-closed union."""
    v: list[str] = []
    if fam.get("schema_version") != 1:
        v.append(f"family schema_version must be 1 (got {fam.get('schema_version')!r})")
    if not fam.get("family"):
        v.append("family manifest missing the 'family' name")
    has_rows, has_states = "rows" in fam, "states" in fam
    if has_rows == has_states:
        v.append("family must carry exactly one of 'rows' or 'states'")
    if fam.get("category") not in FREEDESKTOP_CONTEXTS:
        v.append(f"family category {fam.get('category')!r} not in {sorted(FREEDESKTOP_CONTEXTS)}")
    kind = fam.get("kind", "color")
    if kind not in KIND_VALUES:
        v.append(f"family kind {kind!r} not in {sorted(KIND_VALUES)}")
    bad = [s for s in (fam.get("export", {}).get("png", []) or []) if s not in RASTER_SIZES_ALLOWED]
    if bad:
        v.append(f"family export.png sizes {bad} outside allowed {sorted(RASTER_SIZES_ALLOWED)}")
    if has_rows:
        rows = fam.get("rows")
        if not isinstance(rows, list) or not rows:
            v.append("rows-family needs a non-empty 'rows' list")
        else:
            for row in rows:
                if "id" not in row or "accent" not in row:
                    v.append(f"row {row!r} needs both id and accent")
    if has_states:
        if kind != "symbolic":
            v.append("a states-family must be kind: symbolic")
        if not fam.get("base"):
            v.append("states-family missing the 'base' glyph")
        if "stroke" in fam and not (isinstance(fam["stroke"], int) and fam["stroke"] > 0):
            v.append(f"states-family stroke {fam.get('stroke')!r} must be a positive integer")
        ffit = fam.get("fit", SYMBOLIC_BAND_FIT)
        if not (isinstance(ffit, (int, float)) and FIT_MIN <= ffit <= FIT_MAX):
            v.append(f"states-family fit {fam.get('fit')!r} must be a number in [{FIT_MIN}, {FIT_MAX}]")
        for st in fam.get("states", []) or []:
            if "id" not in st or "level" not in st:
                v.append(f"state {st!r} needs both id and level")
            elif not isinstance(st["level"], int):
                v.append(f"state {st.get('id')!r} level must be an integer")
            ov = st.get("overlays", [])
            if not isinstance(ov, list) or any(not isinstance(x, str) for x in ov):
                v.append(f"state {st.get('id')!r} overlays must be a list of names")
            if "fit" in st:                      # per-state override (comparison-calibrated lane)
                sfit = st["fit"]
                if not (isinstance(sfit, (int, float)) and FIT_MIN <= sfit <= FIT_MAX):
                    v.append(f"state {st.get('id')!r} fit {sfit!r} must be a number in "
                             f"[{FIT_MIN}, {FIT_MAX}]")
    return v


def gather_units(palette: dict):
    """The single source of truth for "what this repo compiles": every compile unit,
    from single recipes (recipes/*.yaml) AND family manifests (families/*.yaml).
    Shared by the compiler (build_icons) and the validator (validate_icons) so both
    agree on the exact set — no cairosvg here, only file reads + string composition.

    A `rows` family expands to single-recipe units, each with a CONTENT-ADDRESSED
    recipe_sha over its own expanded dict (adding/reordering rows never churns the
    others' output). A `states` family loads its base ONCE and expands to per-state
    units. Returns (units, family_failures); a family whose header or base fails the
    gate lands in family_failures — fail-closed, no partial family ships."""
    units: list[dict] = []
    family_failures: list[dict] = []

    for rp in sorted(RECIPE_DIR.glob("*.yaml")):
        recipe = yaml.safe_load(rp.read_text())
        units.append({"kind": "single", "id": recipe.get("id", rp.stem),
                      "recipe": recipe, "recipe_sha": sha256_file(rp), "source": rp.name})

    if FAMILY_DIR.exists():
        for fp in sorted(FAMILY_DIR.glob("*.yaml")):
            fam = yaml.safe_load(fp.read_text())
            probs = validate_family(fam, palette)
            if probs:
                family_failures.append({"source": fp.name, "problems": probs})
                continue
            if "rows" in fam:
                for row in fam["rows"]:
                    expanded = expand_row(fam, row)
                    rsha = sha256_bytes(
                        json.dumps(expanded, sort_keys=True, separators=(",", ":")).encode())
                    units.append({"kind": "single", "id": expanded.get("id"),
                                  "recipe": expanded, "recipe_sha": rsha, "source": fp.name})
            else:  # states family
                try:
                    base_sha, always_on, levels, overlays, gauge, small = \
                        load_state_base(fam["base"])
                except (ValueError, FileNotFoundError) as e:
                    family_failures.append({"source": fp.name, "problems": [str(e)]})
                    continue
                bad_ov = [f"state {st.get('id')!r} names overlay {o!r} absent from base "
                          f"{fam['base']!r} (base has {sorted(overlays)})"
                          for st in fam["states"] for o in st.get("overlays", [])
                          if o not in overlays]
                if bad_ov:
                    family_failures.append({"source": fp.name, "problems": bad_ov})
                    continue
                if gauge is not None:                      # a gauge state's level is a 0..100 decile
                    bad_lv = [f"state {st.get('id')!r} level {st.get('level')!r} outside "
                              f"the gauge range 0..100"
                              for st in fam["states"]
                              if not (isinstance(st.get("level"), int) and 0 <= st["level"] <= 100)]
                    if bad_lv:
                        family_failures.append({"source": fp.name, "problems": bad_lv})
                        continue
                for st in fam["states"]:
                    units.append({"kind": "state", "id": st["id"], "state_id": st["id"],
                                  "level": st["level"], "base_name": fam["base"],
                                  "base_sha": base_sha, "always_on": always_on, "levels": levels,
                                  "overlays": overlays, "state_overlays": st.get("overlays", []),
                                  "gauge": gauge, "dim": st.get("dim", False),
                                  "small": small,
                                  # per-state fit first (the reference toolkit sizes states
                                  # individually — one family fit cannot match every state's
                                  # measured read), family fit next, class default last
                                  "fit": st.get("fit", fam.get("fit", SYMBOLIC_BAND_FIT)),
                                  "category": fam["category"], "export": fam.get("export", {}),
                                  "source": fp.name})
    return units, family_failures


def is_symbolic(recipe: dict) -> bool:
    return recipe.get("kind", "color") == "symbolic"


def out_stem(recipe: dict) -> str:
    """Theme filename stem. An explicit `stem` overrides the id-derived name: reverse-DNS
    theme names (org.gnome.Settings-<panel>) are looked up VERBATIM by GTK, so the id-tail
    derivation below would truncate the org.gnome.Settings- prefix and the theme override
    would silently miss. `stem` absent -> the historic id-tail behavior (byte-identical).
    Symbolic icons take the freedesktop -symbolic suffix."""
    stem = recipe.get("stem")
    if stem is None:
        stem = str(recipe["id"]).split(".")[-1] if "." in str(recipe["id"]) else str(recipe["id"])
    return f"{stem}-symbolic" if is_symbolic(recipe) else stem
