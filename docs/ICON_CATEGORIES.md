# InterGenOS Icon Categories (ICON_CATEGORIES.md)

**Status:** DRAFT (Leg A of the full-set campaign) — the per-category rulebook extracted
from the 11 declared anchor icons, plus the table-generation and symbolic/system
specifications the campaign builds on.
**Owner:** InterGenJLU · **Author:** the icon-design workstream. **Companion to:**
`docs/VISUAL_LANGUAGE.md` §11 (the Phase-2 companion it names) and
`docs/architecture/intergen-icon-compiler-design.md` (IGIC v0.3). On any conflict the
Visual Language and the IGIC design doc win.
**Grounding:** the 11 declared anchors (the IGIC prototype `recipes/` + `glyphs/`), VL v2,
the IGIC design doc, and — for the symbolic-recoloring mechanism — the GTK4 symbolic-icon
format (cited in §6 and §9). This rulebook **extracts** the conventions the anchors already
embody; it does not invent a new grammar.

**Rule 21:** every rule here is either embodied by a shipped anchor (marked **from-anchor**)
or a specification for the campaign's build leg (marked **SPEC** — the generator that
consumes it is Leg B, named honestly, never a silent stub).

---

## 1. The inherited foundation (every category obeys)

These are fixed by VL + the IGIC design doc and are NOT re-litigated per category:

- **Canvas 256×256**, 16% safe margin (content in `[40,216]`). [VL §7/§11; from-anchor]
- **Stroke = ~2% of canvas detailed / ~6% simplified** → **5px / 16px** on 256, hybrid
  routing sizes ≤48 simplified / ≥64 detailed. [VL §6 lines 248/250 + the logo mark;
  from-anchor. Supersedes the VL §11 anatomy-table 12px/24px, which was reconciled to §6
  on the public tree.]
- **Round caps + round joins, stroke over fill** (line-forward). [VL §6; from-anchor]
- **Controlled palette only** — VL §4 tokens + the accent set (§3 below). No off-palette
  color; the gate rejects it. [from-anchor]
- **Content-addressed provenance** on every emitted SVG (recipe/glyph/palette sha; no
  timestamp → deterministic). [from-anchor]
- **The two gates** (spec compliance + security sanitization) run on every glyph input and
  composed output, fail-closed. [from-anchor]
- **The pulse asymmetry invariant** (§4). [from-anchor, asserted in `pulse_path()`]

## 2. The seven categories — conventions extracted from the anchors

| Category | Container | Color model | Baked glow | Pulse | Extracted from anchor(s) |
|---|---|---|---|---|---|
| **apps** | `app-tile` (rounded container) OR bare glyph | brand/ECG-blue color; an app may carry its own brand color | yes (signature) | only where the app is "live" (terminal) | terminal, settings, web-browser, app-grid |
| **places — folders** | `folder` template | **accent color-coding** + a small emblem glyph | yes | no | folder, folder-documents, folder-downloads |
| **places — other** | bare glyph | ECG-blue color | yes | no | user-trash |
| **mimetypes** | bare glyph (document base) | **accent by type** | yes | no | text-x-generic |
| **devices** | bare glyph (device base) | accent by device class | yes | no | *(SPEC — no anchor; §2 rules apply)* |
| **status** | bare glyph | ECG-blue color; **the pulse mark for live/activity** | yes; **primary pulse** for activity | yes (primary) | system-monitor |
| **actions** | bare glyph, **symbolic** (§6) | **symbolic color names** (no baked color/glow) | **no** (recolored away) | pulse-**silhouette** only | preferences-system |

**Reading the table.** Color categories (apps/places/mimetypes/devices/status) carry the
baked ECG-blue glow signature and a palette color. The **actions/symbolic** class is
different in kind — toolkit-recolored — and is fully specified in §6. App icons are the one
place a non-palette brand color is allowed (a third-party app ships branded; forcing
monochrome hurts recognition — VL §11 color policy).

## 3. Palette routing — the accent enumeration (the design doc §10.2 reservation, now proposed)

The IGIC design doc (§10.2) reserved the authoritative accent enumeration for this file.
Proposed here, pending maintainer ratification, grounded in the `palette.yaml` accent set. Every value is
an existing palette token — no new color is introduced.

**Folder color-coding** (the `folder` family, §5):

| Folder class | accent token | hex |
|---|---|---|
| default / generic | `intergen-blue` | `#0099FF` |
| Documents | `accent-teal` | `#14b8a6` |
| Downloads | `accent-orange` | `#fb8c3b` |
| Pictures | `accent-magenta` | `#e05299` |
| Music / Media | `accent-green` | `#10b981` |
| Videos | `accent-violet` | `#8b5cf6` |
| Development / Code | `accent-violet` | `#8b5cf6` |
| Important | `accent-red` | `#ef4444` |
| Public / Templates | `accent-yellow` | `#ffd23f` |

**MIME-type color-coding** (the `mimetypes` family, §5):

| MIME class | accent token |
|---|---|
| text / document | `accent-teal` |
| spreadsheet | `accent-green` *(shares audio's green, differentiated by emblem)* |
| presentation | `accent-orange` *(shares archive's orange, differentiated by emblem)* |
| image | `accent-magenta` |
| audio | `accent-green` |
| video | `accent-violet` |
| archive / package | `accent-orange` |
| code / script | `accent-violet` |
| pdf | `accent-red` |
| generic / unknown | `intergen-blue` |

**Device color-coding** (the `devices` family, §5): default `intergen-blue`; fixed drive
`accent-teal`; removable/USB `accent-green`; optical `accent-violet`; phone `accent-magenta`;
camera `accent-violet`; printer `accent-orange`; display/monitor `intergen-blue`; speakers
`accent-yellow`. (phone/camera/printer/display/speakers were ratified as extensions to the
original design-doc reservation, which enumerated through optical; the accents draw from the
same set.)

**Guard:** the accent set deliberately keeps `accent-yellow` (`#ffd23f`) distinct from the
warning amber (`#f59e0b`) so a yellow folder never reads as a warning (VL §11 / design §10.2).
Semantic state colors (success/warning/error) are **not** folder/MIME accents — they are
reserved for the symbolic state class (§6).

## 4. The pulse-mark usage rules

The ECG pulse is the brand signature; it means *the system is alive* and is not decoration
(VL §8). Rules, extracted from the anchors (terminal, system-monitor):

- **Where it appears:** only where "aliveness" is genuine — `status`/activity icons
  (system-monitor: the pulse IS the icon, `pulse: primary`), and a "live" app surface
  (terminal: a restrained `pulse: bottom` line under the prompt). It is **not** stamped on
  inert icons (folders, mimetypes, plain apps). [VL §8 "do not force the pulse".]
- **`primary` vs `bottom`:** `primary` is the full centered-band signal (the icon itself);
  `bottom` is a restrained low accent under a glyph, a tall-narrow miniature of the same
  character.
- **The asymmetry invariant (HARD):** every pulse is a **short lead-in, spike LEFT-of-center,
  long tail** (VL §8; `assets/intergen-mark/README.md:13`). This is asserted in the single
  `pulse_path()` source (`tail>lead` and spike-left-of-center) so a centered spike cannot
  ship. Any new pulse usage inherits it.
- **Character:** an abrupt near-vertical R-wave (a heartbeat, not a squiggle) — the
  system-monitor pulse is the ratified reference.
- **Color:** ECG-blue `#0099FF` on color icons; on symbolic icons the pulse is carried by
  **silhouette** (the shape), recolored by the toolkit (§6) — never a baked hue.

## 5. The table-generation pattern (mimetypes / folders / devices)

The scale mechanism for Papirus-level completeness: a category whose members share ONE base
shape and differ only by **emblem + accent** is authored as **one template + a table**, not
N hand-written recipes. This is how a category grows from a table row, not a code change
(the charter's data-driven rule).

### 5.1 The `family` manifest (SPEC — the recipe-schema extension IGIC needs)

One YAML file generates the whole category. Family-level fields are inherited by every row;
each row overrides a small set:

```yaml
schema_version: 1
family: folders                 # marks this a family manifest (vs a single recipe)
category: places                # inherited by every row
kind: color                     # inherited
template: folder                # the ONE shared base shape
glow: signature                 # inherited
export: { svg: true, png: [16, 24, 32, 48, 64, 128, 256] }   # inherited
rows:                           # one row -> one generated icon (the ratified nine)
  - { id: folder,             emblem: none,            accent: intergen-blue }
  - { id: folder-documents,   emblem: emblem-doc,      accent: accent-teal }
  - { id: folder-downloads,   emblem: emblem-download, accent: accent-orange }
  - { id: folder-pictures,    emblem: emblem-image,    accent: accent-magenta }
  - { id: folder-music,       emblem: emblem-note,     accent: accent-green }
  - { id: folder-videos,      emblem: emblem-film,     accent: accent-violet }
  - { id: folder-development, emblem: emblem-code,     accent: accent-violet }
  - { id: folder-important,   emblem: emblem-star,     accent: accent-red }
  - { id: folder-public,      emblem: emblem-public,   accent: accent-yellow }
```

(`folder-videos` and `folder-development` share `accent-violet` from §3, differentiated by
emblem; a taste refinement may split them. The shipped `families/folders.yaml` also carries
an optional human `name` per row.)

**Expansion (SPEC).** IGIC expands each row to a full single-recipe (family fields +
row fields), then the existing compiler path (validate → compose → raster → theme) runs
per generated recipe **unchanged**. The generator is a thin front end over today's pipeline;
no change to compositing, the gates, or provenance. **The generator itself is Leg B** —
this section is the schema it consumes.

**Per-row field set:** `id` (required, unique, slug), `emblem` (a glyph name or
`none`), `accent` (a palette token), an optional human `name`, and an optional `note`/
`quirk`. Everything else is inherited from the family header. A **mimetypes** family uses
`template: document` (the page + folded-corner base built into igic_core) + a per-row type
emblem + accent — exactly the folder pattern, a different base. A **devices** family uses
`template: none` with each row naming its own device glyph as `emblem` + accent (devices are
heterogeneous shapes — there is no shared base to template).

**Validation (SPEC):** the family header validates once (category/kind/template/glow/export);
each row validates as a recipe after expansion (the existing gate-1 rules), and `emblem` must
name a sanitized glyph (gate 2). Duplicate `id` across the family fails closed.

### 5.2 Emblem art is authored (honest bound)

The table generates the *composition*; each distinct `emblem` glyph is **authored art**
(the design review loop, at emblem level). Shipped: the eight folder-class emblems
(`emblem-doc`/`-download`/`-image`/`-note`/`-film`/`-code`/`-star`/`-public`; the plain
`folder` needs none); the nine mimetype emblems (`mime-{text,image,audio,video,code,pdf,
archive,spreadsheet,presentation}`, on the `document` template); and the seven device glyphs
(`device-{drive,usb,phone,camera,printer,display,audio}`, bare on `template: none`). Further
categories' emblems are authored as the campaign fills the set. A row whose emblem glyph is
absent fails the gate (Rule 21 — no silent blank).

## 6. The symbolic / system class (RESOLVED, not hand-waved)

Confirmed in scope: top-bar status, the Settings nav-pane, and actions. Symbolic
icons are a **different kind** from the color icons — they are **toolkit-recolored**, so the
baked-glow signature (a color-icon feature) cannot apply. This section decides how the
identity survives recoloring and encodes it.

### 6.1 How GTK recolors symbolic icons (grounded)

GTK renders a `*-symbolic.svg` by **recoloring named regions**, not by honoring baked hex.
The recolorable symbolic colors are a small fixed set — **`foreground` (default), `success`,
`warning`, `error`, and `accent`** — selected by CSS style classes (`foreground-fill`/
`-stroke`, `success-fill`, `warning-fill`, `error-fill`) or, in the newer path-animation
(`.gpa`) format, by the `gpa:fill`/`gpa:stroke` attribute taking a symbolic color **name**.
GTK maps those names to the running theme's palette via the `-gtk-icon-palette` CSS property.
A baked arbitrary hex is treated as `foreground`. [GTK4 symbolic-icon format — §9 citation.]

### 6.2 The resolution (encoded)

**Identity survives by two carriers, not by baked color:**

1. **The stroke grammar + silhouette** — round caps/joins, the 2% stroke, the geometric
   primitive vocabulary, and (for live/activity) the **pulse silhouette**. This is the
   toolkit-agnostic identity: it reads the same after any recolor. This is the primary
   identity carrier for symbolic icons.
2. **The ECG-blue accent, via the symbolic `accent` NAME (not a baked hex).** Because the
   InterGenOS theme accent IS ECG-blue (`#0099FF` = VL §4 `--accent`), marking a region
   `accent` (class `foreground`→`accent`, or `gpa:stroke="accent"`) makes GTK recolor it to
   ECG-blue in our theme. So the ECG-blue accent **does** survive recoloring — as a name, not
   a hue — and follows the theme if the accent ever changes. The **baked glow does NOT**
   survive and is **not emitted** on symbolic icons.

**Emission rules (SPEC — the IGIC symbolic path):**
- Filename `<id>-symbolic.svg`, in `scalable/<context>/`.
- The main body strokes as **`foreground`** (theme text — off-white `#e2e8f0` in the dark
  theme); an accent region, where warranted, strokes as **`accent`** (→ ECG-blue). No baked
  hex, no glow filter, no `bg-card` tile.
- Emit BOTH color carriers by symbolic NAME so recoloring is correct on any theme; the
  legacy class form (`foreground-fill`/`accent`…) is the compatibility baseline, with the
  `.gpa` attribute form as the modern option (§6.4).
- **Semantic state colors** (`success`/`warning`/`error`) are used ONLY where the icon's
  meaning is genuinely that state (a connected indicator = `success`, an error indicator =
  `error`) — never decoratively.

**VL §11.3 reconciliation (not a conflict — a refinement).** VL §11.3 says symbolic icons are
"off-white stroke + ECG-blue accent." That is exactly achievable **and correct** once
expressed as symbolic color **names**: off-white = the `foreground` symbolic color, ECG-blue
accent = the `accent` symbolic color. The one refinement to record in VL §11.3: state it as
symbolic color *names* (so it survives recoloring), not baked hex. [Flagged for maintainer review,
like the §6/§11-stroke reconciliation — a wording refinement, not a redesign.]

### 6.3 Stateful families — wifi / battery / volume (the strongest table case)

A stateful family is one base shape + a **state parameter** → a ladder of variants. This is
the table-generation pattern's strongest case and how the status area reaches completeness.

**Parameterization (SPEC):**
```yaml
schema_version: 1
family: network-wireless-signal
category: status
kind: symbolic
base: wifi-arcs               # the base glyph: 4 nested signal arcs + the dot
states:                       # each -> one -symbolic.svg
  - { id: network-wireless-signal-none,      level: 0 }
  - { id: network-wireless-signal-weak,      level: 1 }
  - { id: network-wireless-signal-ok,        level: 2 }
  - { id: network-wireless-signal-good,      level: 3 }
  - { id: network-wireless-signal-excellent, level: 4 }
```
- **Filled vs unfilled** arcs encode the level: an *active* arc strokes `foreground` at full
  opacity; an *inactive* arc strokes `foreground` at reduced opacity (e.g. 0.3). **Opacity
  survives recoloring** (it is not a color), so the ladder reads correctly on any theme.
- **battery** (BUILT) = a battery outline + a 4-segment `level` fill ladder; the **charging**
  states add a `bolt` overlay (`accent`), **full-charged** a `check` (`accent`), **missing** a
  `slash`. **volume** (BUILT) = a speaker cone + a 3-wave `level` ladder; **muted** = level 0
  + a `cross` overlay (an X over the emission), **overamplified** = level 3 + a `plus` (`accent`). Both are one
  base + a state param + optional per-state **overlays** (§6.3.1).
- **Two emission options:** (SPEC-preferred) generate the **discrete per-state
  `-symbolic.svg`** set (widest compatibility — the traditional freedesktop ladder); OR
  (SPEC-alternative) a single `.gpa` file using the format's native `gpa:state-names`
  (modern, single-file). The campaign's build leg picks per compatibility need; the schema
  above generates the discrete set by default.

**§6.3.1 — per-state overlays (the charging bolt / mute slash / boost mark).** A pure level
ladder cannot express a mark that appears on *some* states only. So a states base may carry
named **overlay groups** — `<g class="overlay NAME ROLE">` — rendered ONLY for the states
that request them (`overlays: [NAME]` on the state). Each overlay carries a symbolic color
ROLE (`foreground` by default, or `accent` = ECG-blue for the charging bolt / boost / charged
mark; the semantic roles used only where the meaning genuinely IS that state). It is a thin,
backward-compatible addition: a base with no overlay group and a state with no `overlays`
composes byte-identically to the plain ladder (the wifi ladder is unchanged). This realizes
§6.3's own battery-`charging` boolean and volume-`muted` mark. A primitive placed under a
nested `<g class="fill">` inside an overlay renders SOLID (a filled shape in the role color)
rather than stroked — e.g. the plugged-in plug's square head; every other overlay primitive strokes.
An overlay group may also carry a `stroke-width` to thin its stroked marks below the family stroke
(the plug's cord + prongs read finer than the body). A states base may also be **overlay-only** —
carrying overlay groups and NEITHER a levels ladder nor a gauge — for a family whose members share
one silhouette and differ only by mark (the wired plug, the vpn padlock, the bluetooth rune: a bare
base for the connected state + acquiring / disabled / no-route overlays). Each such state sets
`level: 0` (with no ladder the level is inert); a base that carries a ladder is unchanged.

A states family may also set an optional **`stroke: N`** — the simplified stroke width for its
symbolic states (default = the §1 `STROKE_SIMPLE`, 16px ≈ 6%). A DENSER base (the battery gauge,
the speaker + waves) thins its lines to read clean at small size; a sparser base (wifi) omits the
field, keeps the default, and stays byte-identical. The weight is a per-family taste call, walked
at batch level.

**§6.3.2 — the proportional gauge (the runtime `battery-level` family).** GNOME Shell's
top bar does NOT request the freedesktop battery ladder (§6.3 / `families/battery.yaml`) as its
primary icon. Grounded in the pinned gnome-shell-49.4 (`js/ui/status/system.js` lines 77-92): the
shell constructs `battery-level-<10*floor(pct/10)><suffix>-symbolic` with `use_default_fallbacks:
false`, and sets UPower's `IconName` — our freedesktop ladder — as the `fallback-icon-name`.
So the ladder is the designed FALLBACK surface; first-party top-bar parity needs a `battery-level`
family as the PRIMARY.

It is **33 load-bearing names** (exact strings, char-for-char against system.js):
`battery-level-{0,10,..,100}-symbolic` (11) · `battery-level-{0,10,..,90}-charging-symbolic`
(10 — a full charge resolves to charged, so there is no `-100-charging`) ·
`battery-level-{0,10,..,100}-plugged-in-symbolic` (11 — the `PENDING_CHARGE` /
charge-threshold-held state) · `battery-level-100-charged-symbolic` (1).

MECHANISM — a **proportional gauge**, opt-in and backward-compatible. A states base carries
AT MOST ONE of a discrete `<g class="levels">` ladder (wifi / battery / volume) OR a
`<g class="gauge">` — one reference rect defining the 100% fill (or NEITHER, for an overlay-only
base, §6.3.1). For a gauge base the compositor
emits a single filled foreground rect whose width is `level * gauge_width // 100` (the state's
`level` is the decile 0..100), so the eleven deciles read as a filling bar — the encoding an
11-step family needs, where the 4-segment ladder does not divide. Integer arithmetic keeps it
deterministic; level 0 emits no fill (an empty body). Overlays are the shared §6.3.1 mechanism:
`bolt` (charging) and `check` (charged) reused, plus a NEW `plug` (plugged-in), all `accent`.
Stroke 11 per the ratified per-family override. The invariant holds: a base with a levels ladder
and no gauge composes byte-identically to before — the 54 prior icons are unchanged.

### 6.4 What the symbolic path adds to IGIC (SPEC summary)

- A `kind: symbolic` emission that writes symbolic color **names** (not baked hex), no glow,
  no tile — the prototype's `preferences-system` symbolic proves the base path; the
  name-based recoloring markup is the Leg-B addition.
- The `states` family generator (§6.3).
- A **build-time symbolic check** (verify-on-our-GTK-stack): render each `-symbolic.svg`
  under the InterGenOS GTK theme and confirm `foreground`→off-white and `accent`→ECG-blue
  recolor as intended, and that the icon reads at 16px (top-bar/nav-pane scale). [PROXY until
  run on our stack — the charter's measured-on-our-stack rule.]

## 7. Recipe-schema extension — the concrete IGIC changes (SPEC, Rule 21)

Everything the campaign's generator (Leg B) implements, specified here so it is not a stub:

1. **The `family` document** (§5.1): `family` + `rows[]`; family fields inherited, per-row
   `{id, emblem, accent, note?}`; expands to single-recipes the existing pipeline compiles
   unchanged.
2. **The `states` family** (§6.3): `family` + `base` + an optional `stroke` + `states[]` with a
   per-state `level` and optional `overlays: [name]`; the base carries the level ladder + any
   named overlay groups (§6.3.1); generates the discrete `-symbolic.svg` ladder.
3. **Symbolic emission by color NAME** (§6.2): `kind: symbolic` writes `foreground`/`accent`/
   semantic names, no glow/tile.
4. **Validation:** family header once + each expanded row/state as a recipe (existing gates);
   emblem/base must name a sanitized glyph; duplicate `id` fails closed.

None of this changes the compositing core, the gates, provenance, or determinism — it is a
thin table/family front end over today's proven pipeline. **The generator is Leg B; this is
its specification.**

## 8. Honest bounds

- The design review loop stays at **template + batch level**: author a base shape / emblem,
  then walk a batch contact sheet — not per-icon fiddling. The tables scale the proven look.
- **Emblem and base glyphs are authored art** (§5.2); the table generates compositions, not
  the art. Coverage grows as emblems/bases are drawn.
- **The symbolic recoloring is verified on our GTK stack** (§6.4) before the class is
  declared done — the exact class/attribute form (legacy `-fill` classes vs `.gpa`) is a
  per-stack build check, HARD in mechanism, PROXY on our exact toolkit version.
- This rulebook is **Leg A** (the extraction + specification). The generator, the emblem/base
  art, and the batch fills are the campaign's build legs — named, not stubbed.
- App-brand color (a third-party app's own hue) remains the one sanctioned off-accent-set
  color, per VL §11.

## 9. Sources (symbolic-recoloring grounding)

- GTK4 symbolic icon format (recolorable names `foreground`/`success`/`warning`/`error`/
  `accent`; `-symbolic.svg`; legacy `-fill`/`-stroke` classes vs the `.gpa` `gpa:fill`/
  `gpa:stroke` attributes; `-gtk-icon-palette`): `https://docs.gtk.org/gtk4/icon-format.html`
- GTK CSS `-gtk-recolor` / `-gtk-icon-palette` (palette mapping of the symbolic names):
  `https://docs.gtk.org/gtk4/css-properties.html`
- freedesktop Icon Theme Specification (theme layout, contexts, the scalable/`<size>` tree) —
  the base the IGIC exporter already follows.

## 10. Change log

| Date | Author | Change |
|---|---|---|
| 2026-07-10 | icon-design | Initial DRAFT — Leg A of the full-set campaign. Extracted the per-category conventions from the 11 declared anchors (§2), proposed the accent enumeration the design doc §10.2 reserved (§3), the pulse-mark usage rules with the asymmetry invariant (§4), the table-generation `family` schema for mimetypes/folders/devices (§5), and the symbolic/system class RESOLVED against the GTK4 symbolic-recoloring mechanism — identity via stroke-grammar + the symbolic `accent` NAME (ECG-blue survives as a name, baked glow does not) + stateful-family parameterization for wifi/battery/volume (§6). Recipe-schema extension summary (§7) + honest bounds (§8). The generator + emblem/base art + batch fills are the campaign's build legs, named not stubbed. |
| 2026-07-10 | icon-design | Leg B built the §5–§7 generator (IGIC): the `family` rows generator (§5.1), the `states` ladder generator (§6.3), and symbolic emission by color NAME (§6.2). §5.1 example synced to the ratified nine-folder set (added `folder-development`, `folder-public`) and §5.2 emblem-inventory updated (all nine folder-class emblems ship). The `network-wireless-signal` five-state ladder is the first stateful family. §6.4 render-on-our-GTK-stack legibility at 16px remains PROXY. |
| 2026-07-10 | icon-design | mimetypes + devices families built. mimetypes = a new `document` template (page + folded corner, in igic_core) + nine per-type emblems + §3 MIME accents; the `text-x-generic` single folds in pixel-identical (PNG byte-identical; SVG differs by template-split + provenance). devices = `template: none` + seven per-row device glyphs + §3 device accents. §3 extended with proposed accents (spreadsheet/presentation; phone/camera/printer/display/speakers); §5.1 corrected (mimetypes use `template: document`, devices per-row glyph); §5.2 inventory updated. Existing 22 icons unchanged at the raster level. pdf emblem (bookmark ribbon) flagged for taste — PDF has no text-free convention. States ladders (battery/volume) are the next leg. |
| 2026-07-10 | icon-design | mimetypes + devices nit round (design review verdict: accepted). The `document` template page is canvas-centred (x-centre 128, matching the folder body) and widened to 116px; all nine mime emblems bbox-centred to (128,128) (they had sat ~16px below the page centre). mime-code refined to a "</>" mark, spreadsheet to a 4-column grid, presentation to a 4-bar chart, the framed emblems widened. Three device glyphs re-authored: drive-harddisk = a traditional top-down platter (chassis + platter + spindle + actuator arm), printer wider + shorter, audio-speakers = three sound waves. The §3 accent extensions (spreadsheet/presentation; phone/camera/printer/display/speakers) and the pdf bookmark-ribbon are ratified — the proposed/flagged hedges removed. Widening the page releases the text-x-generic PNG byte-identity (deliberate); measured raster change is the nine mimetypes + three device rows only — folders, wifi, and the other singles stay byte-identical. Battery re-passed 37/37 (validate + build), determinism byte-identical, security gate exit 1. |
| 2026-07-10 | icon-design | States ladders built — the battery + audio-volume symbolic families (the pre-named leg after the mimetypes+devices arc landed). A §6.3.1 overlay mechanism was added to the states generator: a base carries named `<g class="overlay NAME ROLE">` groups rendered only for the states that request them (`overlays: [name]`), each in a symbolic color role (`accent` = ECG-blue for the charging bolt / boost / charged mark). battery = a 4-segment level ladder + bolt/check/slash overlays -> 12 -symbolic icons (empty/caution/low/good/full x {plain, charging} + full-charged + missing); audio-volume = a speaker + 3-wave level ladder + slash/plus overlays -> 5 -symbolic icons (muted/low/medium/high/overamplified). Basenames verified against the coverage inventory by exact match. The overlay addition is backward-compatible — the wifi ladder is byte-identical. Battery re-passed: validate/build 54/54, determinism byte-identical, security exit 1; pixel-identity vs the landed tree = additions only (17 SVG + 68 PNG). §6.3 / §6.3.1 / §7 updated. |
| 2026-07-10 | icon-design | States-ladders walk round (design review verdict: accepted with three nits; the +3 scope and the overlay mechanism stand). (1) Stroke weight: the 17 states read muddy at the walk scale, so the states family gained an optional `stroke` override (default STROKE_SIMPLE); battery + audio-volume set `stroke: 11` (thinner), while the wifi ladder omits it and stays byte-identical. (2) audio-volume-muted: the mute mark became a `cross` overlay (an X over the emission) instead of a diagonal slash (battery-missing's slash stands). (3) audio-volume-overamplified: the `plus` overlay enlarged. Battery re-passed: validate/build 54/54, determinism byte-identical, security exit 1; pixel-identity vs the prior tree = only the 17 state rows + contact-sheet + build-report (wifi/folders/mimetypes/devices/singles byte-identical). §6.3 / §6.3.1 / §7 updated. |
| 2026-07-10 | icon-design | `battery-level` runtime family — 33 -symbolic icons GNOME Shell's top bar builds as its PRIMARY (grounded in the pinned gnome-shell-49.4 `js/ui/status/system.js` 77-92: `battery-level-<10*floor(pct/10)><suffix>` with `use_default_fallbacks:false`, our freedesktop ladder set as the fallback-icon-name). A §6.3.2 proportional GAUGE was added to the states generator: a base carries EXACTLY ONE of a `<g class="levels">` ladder OR a `<g class="gauge">` reference rect whose width the compositor scales by the state's decile 0..100 — the 11-step fill a 4-segment ladder cannot express. Overlays reused: bolt (charging) / check (charged) + a NEW plug (plugged-in / PENDING_CHARGE), accent. New base `battery-gauge.svg` + `families/battery-level.yaml` (33 states); igic_core / build_icons / validate_icons thread the gauge; stroke 11. Battery re-passed: validate/build 87/87, determinism byte-identical, security exit 1; pixel-identity vs the landed tree = additions only (33 SVG + 132 PNG; the 54 prior icons byte-identical). §6.3.2 added. |
| 2026-07-11 | icon-design | GNOME-runtime parity continuation — the cellular ladder + the statics wave (34 -symbolic status icons, every name grounded char-for-char in the pinned gnome-shell-49.4 js/ui/status sources). network-cellular (8: a 4-bar signal ladder none/weak/ok/good/excellent per signalToIcon @46-57 + acquiring/connected/disabled), network-wired (4), network-vpn (3) and bluetooth (3) as OVERLAY-ONLY bases — a §6.3.1 relaxation: a states base may carry overlays and no ladder/gauge (one silhouette + a per-state mark, level 0). microphone-sensitivity (4: a mic + a 3-arc input-level ladder mirroring audio-volume) + audio-input-microphone + audio-headphones singles. power-profile (4: rocket / gauge / leaf + the gnome-power-manager gear) and capture (3: media-record / screencast-stop / screen-shared) as symbolic rows-families. network-workgroup / airplane-mode / night-light singles. A consistent overlay vocabulary (accent ellipsis = acquiring, foreground slash = disabled/disconnected, warning "!" = no-route, cross = muted). Re-passed: validate/build 121/121, determinism byte-identical, security exit 1; additions only (34 SVG + 136 PNG; the 87 prior byte-identical). §6.3.1 / §6.3.2 updated. |
| 2026-07-11 | icon-design | Corpus-wide standards sweep + re-imagines (design review verdict). THREE retroactive standards applied across the full 121: **S1** the disabled indicator is now an accent (ECG-blue) X, never a white slash — converted cellular / wired-disconnected / vpn / bluetooth-disabled + the landed battery-missing (the ONLY landed slash carrier; there is no wifi-disabled state in the landed set). The muted mark stays a foreground cross (reconciliation: disabled = accent X, muted = foreground cross — distinguished by color + context). **S2** thin strokes — the network-cellular / -wired / -vpn / bluetooth / network-wireless-signal state families dropped to stroke 11 (matching battery / volume / mic). **S3** no protrusions — audio-headphones re-drawn, the acquiring ellipsis sits clear above each glyph, the gauge needle contained. NEW **power-bolt rule**: an `accent_glyph` field (a second glyph rendered in the accent color NAME; added to compose_icon / expand_row / validate_recipe — backward-compatible, inert when unused, measured byte-identical) puts an ECG-blue bolt on all four power icons. Re-imagined originals to VL: network-wired (a line-forward RJ45), network-vpn (a shield + keyhole), network-workgroup (connected device-nodes), gnome-power-manager (our own tuning dial — no GNOME foot), media-record (a record target), screencast-stop (a stop button). Re-passed: validate / build 121/121, determinism byte-identical, security exit 1. The additions-only invariant is REPLACED this leg by an explicit changed-file manifest (43 SVGs + 128 PNGs touched, 0 added/removed) — the landed bytes change by design per the standards. §6.3.1 / §6.3.2 / §10 + a new `accent_glyph` mechanism; §6.2 / §7 formalization flagged for review at land. |
| 2026-07-11 | icon-design | Round 3 — second design review verdict (eight items). **S1-v2 disabled = grey glyph + accent X:** a new per-state `dim` flag renders the always-on layer at STATE_DIM (signal-ladder grey) beneath the accent (ECG-blue) X — applied to network-wired-disconnected, network-vpn-disabled, bluetooth-disabled (network-cellular-disabled already rendered grey; battery-missing untouched per the declared-perfect ruling). **R3-1 optical stroke:** dense glyphs read heavier than sparse ones at equal stroke, so the dense families were thinned to equal measured ink against the approved wifi ceiling — network-wired 11->9, network-vpn 11->9, bluetooth 11->8; wifi / cellular / mic / volume stay 11. Battery HELD at 11: thinning its shared base re-renders the declared-perfect battery-missing, so the battery-family stroke is a decision to be taken (surfaced, not assumed). **Re-imagines to the supplied designs:** network-wired-no-route gains a warning "?" on the left beside the liked "!" on the right; the power bolt moved to the top-left corner on all four power icons; power-saver re-drawn as a veined leaf with a stem (prior leaf withdrawn); gnome-power-manager re-drawn as three adjustment sliders (tuning dial withdrawn); network-vpn gains a network node bar across the bottom on all vpn states (reads network, not antivirus). **R3-7 media-record + screencast-stop, researched:** Adwaita-49.0 ships media-record-symbolic as a solid filled circle and media-playback-stop-symbolic as a solid rounded square, and gnome-shell-49.4 screencast-stop-symbolic as a solid rounded square; ours match the convention via a new per-recipe `fill` flag (glyph primitives rendered solid in the foreground NAME) — media-record = a solid dot, screencast-stop = a solid rounded square, our proportions and color. Both new flags are backward-compatible and inert when unused (measured byte-identical rebuild). Changed-file manifest = 16 SVG + 64 PNG + 0 added/removed (bluetooth x3, network-vpn x3, network-wired x4, power-profile x3 + gnome-power-manager, media-record, screencast-stop); corpus stays 121. Re-passed: validate / build 121/121, determinism byte-identical, security exit 1. §6.3.1 / §6.3.2 + the `dim` and `fill` mechanisms; formalization flagged for review at land. |
| 2026-07-11 | icon-design | Round 4 — design review verdict on round 3 (eight items). **R4-1 NEW CORPUS STANDARD:** the disabled X is the ONLY element rendered thicker than the icon's own stroke, so it stands out — a per-overlay stroke-width (15) on the network-wired-disconnected / network-vpn-disabled / bluetooth-disabled / network-cellular-disabled X's (via the existing per-overlay override; the glyph bodies stay 8-11). battery-missing untouched — the whole battery family is HELD pending a separate decision. **R4-2** network-vpn's bottom mark re-worked from a bare underline to a network NODE: a line drops from the shield to a circle node at the junction, then branches to a horizontal bus bar (all vpn states). **R4-3** network-wired-no-route's "?" is matched to the "!" height and moved clear of the plug. **R4-4** network-wired-disconnected reads as ONE solid grey silhouette — the four contact pins became a "pins" overlay carried by the connected states only (network-wired / -acquiring / -no-route), so the disconnected state drops them. **R4-5** power-profile-power-saver re-imagined as a power plug (prongs + body + a bolt) paired with a leaf tucked beside it (eco-power; the lone leaf read botanical). **R4-6** gnome-power-manager re-imagined as a gear with an angular lightning bolt centred inside (the sliders withdrawn). **R4-7** the shared power bolt redrawn as a true angular lightning OUTLINE (wide head tapering to a sharp point); it carries the two profile icons with no integral bolt (performance / balanced), while power-saver and gnome-power-manager carry their own bolt, so their redundant top-left accent bolt was removed. **R4-8** media-record / screencast-stop keep the canonical solid shapes and gain a thin concentric OUTER RING at the family stroke weight (physical-button read), preserving the foreground NAME class so the shell's record-red recolor still applies. NEW mechanism: a `<g class="fill">` SOLID region in the rows/single glyph extractor (parity with the states-base collect()), letting one glyph mix a stroked outline with a filled region — backward-compatible, measured byte-identical (the 105 untouched icons unchanged). Changed-file manifest = 24 SVG + 52 PNG + 0 added/removed; corpus stays 121. Re-passed: validate / build 121/121, determinism byte-identical, security exit 1. §6.3.1 / §6.3.2 + the `<g class="fill">` region; formalization flagged for review at land. |
| 2026-07-11 | icon-design | Round 4 battery fold-in (review amendment, R4-9 + R4-10) — the R4-1 battery exemption is VOID. **R4-9** the battery family (families/battery.yaml 12-state ladder + families/battery-level.yaml 33-state runtime) thinned from stroke 11 to 9 by the measured-ink method: at 9 the ladder reads 0.85-0.92x the wifi ceiling, inside the sibling band (network-wired 0.99x, network-vpn 0.81x); the battery-level near-full gauge stays denser by its proportional solid fill (the level indicator, not stroke). **R4-10** battery-missing = grey glyph (a `dim` state) + the accent X thickened to the R4-1 corpus weight (15). Full round-4 changed-file manifest (R4-1..R4-10 vs the round-3 delivered tree) = 69 SVG + 232 PNG + 0 added/removed (24 network/power/capture + 45 battery); corpus stays 121. Re-passed: validate / build 121/121, determinism byte-identical, security exit 1; backward-compat MEASURED (the 52 untouched icons byte-identical). |
| 2026-07-11 | icon-design | Round 5 — design review verdict on round 4 (R5-0..R5-6). **R5-2 the power symbol:** a new canonical elongated angular lightning silhouette (flat wide head, mid-notch, long sharp tail; from the supplied reference art) replaces the R4-7 compact bolt at every site — one shape instantiated from a normalized unit polygon. It renders as a stroked OUTLINE where scale lets it read as one (the performance / balanced top-left accents) and FILLED — the same silhouette, ecg-blue — where it is a small embedded indicator whose outline interior would close at status sizes (the power-saver plug, the gnome-power-manager gear, the battery charging overlay); the outline/fill-by-scale rule is surfaced with render evidence for a recorded decision. NEW mechanism: the `accent_glyph` compose path now honors a `<g class="fill">` region (parity with the main-glyph fill split — backward-compatible: the outline bolt has no fill region, so it is byte-identical). **R5-6** every bolt appearance adopts R5-2 corpus-wide, INCLUDING the battery charging overlay (the squiggle retired); the battery re-renders for the bolt ONLY — the 30 non-charging battery PNGs are byte-identical and the stroke-9 work stands (the 45 battery SVGs differ only in the shared base-sha provenance). **R5-3** power-profile-power-saver = a plug carrying the R5-2 symbol (filled) + a broad ovate leaf with a stem and midrib (the round-4 leaf read as a football; breadth + a stem fix it). **R5-4** gnome-power-manager = the established gear + the R5-2 symbol overlaid (the row repointed to the shared gear glyph; the orphaned pp-custom.svg removed). **R5-5** media-record / screencast-stop return to the pure-solid grammar (recipe `fill: true`) — the R4-8 button ring is retired (unlabeled, it diluted recognition); the foreground NAME preserves the shell's record-red recolor. **R5-1** network-wired-disconnected is now ONE closed connector silhouette + the X — the divider + twin cord moved to a connected-only `leads` overlay, so the dim state carries no open-line linecap dots. **R5-0** bluetooth / network-vpn / network-wired thinned to stroke 6, into the rows band (STROKE_DETAIL 5): root cause = state families render at ONE fixed stroke at every size while rows thin to 5 on the scalable, so 8-9 read ~1.7x heavier than the power / capture rows on the sheet (the earlier ink-parity-vs-wifi test used the wrong ceiling — wifi is itself a stroke-11 state family). Surfaced for a corpus-wide decision: the remaining state families (wifi / cellular / mic / volume at 11, battery at 9) read heavier still; the durable fix is giving state families the rows' two-variant detailed/simple stroke, which re-renders the held battery. Changed-file manifest = 61 SVG + 124 PNG + 0 added/removed (16 network/power/capture geometry + 45 battery; of the 45, the 15 charging change visually, 30 are provenance-only); corpus stays 121. Re-passed: validate / build 121/121, determinism byte-identical, security exit 1; backward-compat MEASURED. |
| 2026-07-11 | icon-design | Round 6 — design review verdict on round 5 (accepted with R6-1..R6-6 outstanding). Both held items decided: **R1** the power-symbol mixed outline/fill treatment is blessed as delivered; **R2** GO on the rows' two-variant stroke for state families as a class (executed as R6-1). **R6-1 STROKE UNIFORMITY:** every state family now carries the two-variant detailed/simple stroke the rows use (STROKE_DETAIL 5 on the scalable / STROKE_SIMPLE 16 at status sizes) — the per-family fixed `stroke` override is retired; compose_state_icon takes a `variant`, build + validate compose both and route size->variant. The per-overlay stroke-width is now a MULTIPLE of the base (the disabled X = 2.5x) so "thicker than the glyph" (R4-1) holds in both variants (an absolute width inverted at small sizes). The held battery re-renders under the same rule — geometry unchanged, weight re-derived (ruling R2). The full contact-sheet now reads at one weight; no family group is heavier or lighter. **R6-3 power symbol:** the lower limb re-derived vs the example art — parallel right edges + a long fine taper to a bottom-left point (the round-5 tail read as pointing the wrong way) — re-instantiated at every bolt site. **R6-5 devices:** drive-removable-media / phone / camera-photo / printer / video-display / audio-speakers rendered at ~half the corpus optical size (ink bbox max ~82-92px, fill 0.47-0.52, vs the sibling drive-harddisk 150 / mimetypes 152) — a normalization miss; scaled to the grid (fill 0.78-0.88), recentred. **R6-6 wired-disconnected:** the cord returns — it is now carried in the always-on for every state (matching connected) while the closed, no-joint-dot silhouette holds; the divider stays connected-only (its side ends would dot the dim state). **R6-2 audio-volume-muted:** the muted mark is now the corpus blue disabled X (accent, 2.5x). **R6-4 media-record + screencast-stop:** escalated to research — the field (Adwaita + Yaru) renders both as a bare solid circle / square, identical to ours, so the convention is met but reads without meaning unlabeled; a reference-comparison sheet (field + current + proposals) + a size test are delivered as the basis for that decision. Recommendation: the screen+mark pair (a monitor + the record dot / stop square) — domain-accurate for screencasting; the on-disk shapes hold as the round-5 solids until that is decided. Changed-file manifest vs the round-5 tree = 87 SVG + 366 PNG + 0 added/removed (77 state icons + 4 power-profile + 6 devices; record/stop / mimetypes / folders / apps unchanged). Re-passed: validate / build 121/121, determinism byte-identical, security exit 1; backward-compat MEASURED. New: state-family two-variant stroke + per-overlay stroke multiplier. |
| 2026-07-12 | icon-design | Round 7 — design review verdict on round 6 (accepted; the final polish items R7-1..R7-4 + the R6-4 ruling). **R6-4 ruling adopted:** the screen+mark record/stop win — media-record is now a stroked monitor + a FILLED record dot (screen-record.svg), screencast-stop a monitor + a FILLED stop square (screen-stop.svg); the bare-solid rec-circle / stop-square glyphs are retired (files removed, `fill: true` dropped from both rows). The foreground NAME preserves the shell's live-recording RED recolor (the composed record/stop carry foreground-stroke on the monitor + foreground-fill on the mark). The camcorder+dot singled out in review is authored (camcorder.svg) and surfaced in the changed sheet as the media-record option (a one-line yaml repoint to swap). **R7-1 power bolt:** the canonical silhouette re-derived from the supplied example art — a 7-vertex lightning with notches on BOTH sides, adding the mid-left concave cut-in the round-6 shape lacked (its lower-left limb read as a smooth "tooth"); measured from the example's per-row edge profile and overlay-verified against it, re-instantiated at every bolt site (bolt / battery-body / battery-gauge / bolt-gear / bolt-plug). **R7-3 phone:** the body narrowed a touch (width 90.4 -> 80, recentred on 128; speaker slot + rx scaled to match), the R6-5 grid height kept. **R7-4 microphone-sensitivity-muted:** the white foreground cross replaced by the corpus blue disabled X (accent, 2.5x — the R4-1/R6-2 class), its centre placed midway between the emission arcs' right edge and the mic frame's left edge per the recorded spec. Changed-file manifest vs the round-6 tree = 56 SVG + 95 PNG + 0 added/removed (23 icons change visually: 15 charging batteries + 4 power-profile [R7-1], media-record + screencast-stop [R7-2], phone [R7-3], microphone-sensitivity-muted [R7-4]; the other 33 SVGs are base-sha-only provenance from the shared battery / mic bases). Glyph set: +3 (screen-record, screen-stop, camcorder), -2 (rec-circle, stop-square). A pure glyph/yaml round — the engine (igic_core / build_icons / validate_icons) is byte-identical to round 6. Re-passed: validate / build 121/121, determinism byte-identical, both reject fixtures exit 1; backward-compat MEASURED. |
| 2026-07-12 | icon-design | Round 8 — design review verdict on round 7 (accepted); the campaign closer, ONE scoped item. Two review decisions: media-record STAYS wired as screen+dot (the camcorder glyph remains authored in-tree, no repoint); the icon workstream's round-7 harmonization flag is ruled GO. **R8-1 screen-shared monitor harmonization:** screen-shared's monitor is brought to the capture-family geometry (rect x40-216 / y52-172 rx14 + the shared stand + base — matching screen-record / screen-stop), so all three capture icons share one monitor frame; the cast dot + broadcast arcs are repositioned into the larger screen (affine-mapped from the old 52-204 / 60-168 screen rect). Nothing else changes. Changed-file manifest vs the round-7 tree = 1 SVG + 4 PNG + 0 added/removed (screen-shared only; screen-cast.svg is referenced by that row alone, so no base-sha propagation). Engine byte-identical (a pure glyph round). Re-passed: validate / build 121/121, determinism byte-identical, both reject fixtures exit 1; backward-compat MEASURED. The closure item: on this delivery plus the battery work, the cumulative rounds-3-through-8 tree in one landing and the campaign goes for a RESOLVED/CLOSED decision. |
