# IGIC prototype — RUN

Exact commands (repo-relative paths, run from the repo root) to run the InterGenOS Icon Compiler prototype and the
outputs to expect. Everything lives under `assets/theming/igic/`. The
compiler reads the repo trees NOWHERE — it is self-contained in `igic/` and writes only
to `igic/../out/`.

## 0. Prerequisites

Verified present on this host (2026-07-09): Python 3.12.3, cairosvg 2.9.0, PyYAML 6.0.3,
lxml 6.0.2, Pillow 10.2.0. If a fresh box lacks them:

```
pip install --user cairosvg PyYAML lxml Pillow
```

`validate_icons.py` needs only python3 + PyYAML + lxml. `build_icons.py` needs cairosvg
(+ Pillow unless `--no-sheet`).

## 1. Validate (both gates, all recipes + families)

```
python3 assets/theming/igic/validate_icons.py
```
Expected — 54 PASS lines, exit 0 (7 single recipes + the 9-folder, 9-mimetype and 7-device families + the 5-state wifi, 12-state battery and 5-state audio-volume ladders):
```
  PASS  org.intergenos.app-grid      (org.intergenos.app-grid.yaml)
  ...
  PASS  folder                       (folders.yaml)
  ...
  PASS  drive-harddisk               (devices.yaml)
  ...
  PASS  network-wireless-signal-excellent (network-wireless-signal.yaml)

54/54 icons clean; 0 failed.
```

## 2. Compile the theme + samples

```
python3 assets/theming/igic/build_icons.py --clean
```
Expected tail — `54/54 icons compiled clean; 0 failed.`, exit 0. Produces:
- `assets/theming/out/theme/InterGenOS/` — the freedesktop theme
  (54 `scalable/<context>/*.svg` + 309 `<size>x<size>/<context>/*.png`; the 31 color icons
  span 16/24/32/48/64/128/256, the 23 symbolic icons — `preferences-system` + the 5 wifi,
  12 battery and 5 audio-volume states — 16/24/32/48) + `index.theme` (`Inherits=Adwaita,hicolor`).
- `assets/theming/out/build-report.json` — deterministic per-icon
  provenance + validation report.
- `assets/theming/out/contact-sheet.png` — the taste-iteration
  preview (all 37 icons at 128px on the void canvas).

`--no-sheet` skips the contact sheet (drops the Pillow requirement). `--out DIR` redirects
the output root.

## 3. Look at the result

```
xdg-open assets/theming/out/contact-sheet.png
xdg-open assets/theming/out/theme/InterGenOS/256x256/apps/terminal.png
xdg-open assets/theming/out/theme/InterGenOS/48x48/status/network-wireless-signal-excellent-symbolic.png
```
Expected: flat-color, line-forward icons on the near-black void — the app/status anchors
(terminal, settings, app-grid, web-browser, system-monitor, trash), the **nine-folder**
color-coded family (teal Documents, orange Downloads, magenta Pictures, green Music, violet
Videos + Development, red Important, yellow Public, blue plain — each with its emblem), the
**nine mimetypes** (a document page + a type emblem, accent by type), the **seven devices**
(drive / usb / phone / camera / printer / display / speakers, accent by class), the off-white
**symbolic** gear, the **wifi signal ladder** (five states, dimmed→lit arcs), the **battery**
and **audio-volume** ladders (charge / wave levels with the charging bolt + mute / boost
overlays), and the ECG-blue
**pulse** signature (system-monitor + the line under the terminal prompt). Color icons carry
the baked ECG-blue edge glow; the symbolic icons (gear, wifi) do not.

## 4. Verify the security gate rejects disallowed constructs (fail-closed, gate 2)

Two reject-case SVGs — one carrying the disallowed active/external constructs, one
carrying a DOCTYPE/XXE surface — both must be rejected:

```
python3 - <<'PY'
from pathlib import Path
Path("/tmp/igic_reject1.svg").write_text(
 '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
 '<script>x=1</script><rect x="40" y="40" width="176" height="176" onload="run()"/>'
 '<image href="https://example.com/a.png" x="0" y="0" width="10" height="10"/>'
 '<use href="https://example.com/lib.svg#g"/>'
 '<path d="M40 40 L216 216" fill="url(https://example.com/p)"/></svg>')
Path("/tmp/igic_reject2.svg").write_text(
 '<?xml version="1.0"?>\n<!DOCTYPE svg [ <!ENTITY e SYSTEM "file:///etc/passwd"> ]>\n'
 '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
 '<rect x="40" y="40" width="10" height="10"/></svg>')
PY
python3 assets/theming/igic/validate_icons.py /tmp/igic_reject1.svg /tmp/igic_reject2.svg
```
Expected: both files **FAIL**, exit 1 — `<script>`, `onload`, `<image>`, external
`href`, external `<use>`, external `url()`, and DOCTYPE/XXE each named as rejected.

## 5. Verify determinism (the load-bearing property)

```
cp -r assets/theming/out /tmp/igic_ref
python3 assets/theming/igic/build_icons.py --clean
diff -r /tmp/igic_ref assets/theming/out && echo "DETERMINISTIC: byte-identical"
```
Expected: no diff — `DETERMINISTIC: byte-identical`. (Content-addressed provenance +
no wall-clock is what guarantees this; it is the basis for the Phase-2 CI
regenerate-and-diff gate.)

## 6. Regenerate-vs-delivered diff

The delivered `out/` tree is a committed sample. To confirm the delivered assets match a
fresh compile:
```
cp -r assets/theming/out /tmp/igic_delivered
python3 assets/theming/igic/build_icons.py --clean
diff -r /tmp/igic_delivered assets/theming/out
```
Expected: no output (identical).

## Files

- `igic/palette.yaml` — controlled palette (VL §4 tokens + accent set).
- `igic/igic_core.py` — library (palette, safe parse, both gates, glyph/template/pulse/
  glow geometry, provenance, the compositor + the family/state generators + `gather_units`).
- `igic/validate_icons.py` — standalone two-gate validator (single recipes + families).
- `igic/build_icons.py` — the compiler CLI.
- `igic/glyphs/*.svg` — colorless glyphs (base/anchor + the `wifi-arcs` / `battery-body` /
  `volume-speaker` states bases, 8 folder emblems, 9 `mime-*` emblems, 7 `device-*` glyphs).
- `igic/recipes/*.yaml` — 7 single icon recipes.
- `igic/families/*.yaml` — 6 family manifests (`folders` 9 rows, `mimetypes` 9 rows, `devices`
  7 rows, `network-wireless-signal` 5 states, `battery` 12 states, `audio-volume` 5 states).
- `ICON_CATEGORIES.md` — the per-category rulebook (the accent enumeration, the family
  schema, the symbolic/system class).
- `intergen-icon-compiler-design.md` — the executable-grade spec (full replacement for
  the in-tree `docs/architecture/intergen-icon-compiler-design.md`).
- `out/` — the generated sample theme + report + contact sheet.
