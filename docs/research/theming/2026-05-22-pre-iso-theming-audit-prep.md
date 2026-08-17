# Pre-ISO Theming Audit — Fine-Tooth Comb Mandate

> **📜 HISTORICAL RESEARCH SNAPSHOT (decided 2026-07-11).** This is a dated
> research record from early development, retained for its historical value. It is not
> maintained and details may no longer match the current tree — the living truth is the
> tree itself and the current `docs/`.

**Authored:** 2026-05-22 during USA-1 Step 2 Bucket B execution
**Mandate source:** decided 2026-05-22 ~12:Z, mid-walk of audit row J-008
**Hard gate:** *This audit must complete before the next ISO build.*

---

## Mandate (verbatim)

> *"We have a significant amount of work to do here. Theming is CRITICAL, to me AND the project. I don't want to further disrupt the audit- but make copious amounts of notes that 'We need to go through every aspect of our theming with a fine tooth comb prior to our next ISO build'."*

**Theming is a Critical-priority concern, gated as a prerequisite for the next ISO build.** This document captures the surface area + every drift / gap / open question observed during the USA-1 Step 2 walk, so the fine-tooth-comb pass has a complete starting state.

---

## How this document came to exist

During USA-1 Step 2 PARTIAL walk item 9 (J-008 — "gschema icon-theme=Papirus-Dark contradicts dconf icon-theme='Cybernetic - Blue'"), the initial framing was a "1-line edit" follow-up. Empirical state was requested instead, and the sweep surfaced that what was framed as a 1-line fix is actually a multi-component drift between the decided intent (Cybernetic Blue per A33 2026-05-03) and shipping reality (Papirus-Dark across the package + dependency + metatheme + gschema chain).

The resolution for J-008 specifically:
> *"We ship Papirus-icon-theme as the installed icon theme — we INCLUDE Cybernetic AND provide attribution for it."*

But the broader concern that surfaced from the J-008 walk — that the theming surface across the project has multi-layer drift between intent and ship state — needed a dedicated audit prep document. That is this document.

J-008 is reclassified as GENUINELY-OPEN-needs-work in the Step 2 aggregate ledger and routed here for resolution under the fine-tooth-comb pass.

---

## Empirical theming surface at HEAD (2026-05-22, master `2811bd28`)

### 1. Default user-facing theme choices (`config/gsettings/90_intergenos.gschema.override`)

| Surface | Default value | Source package | Notes |
|---|---|---|---|
| Color scheme | `prefer-dark` | (built-in GNOME 49) | Decided |
| GTK theme | `InterGenOS` | `packages/desktop/intergenos-theme` | Custom metatheme (ECG blue on deep navy) |
| Icon theme | `Papirus-Dark` | `packages/desktop/papirus-icon-theme` | Decided 2026-05-22 |
| Cursor theme | `Bibata-Modern-Classic` | `packages/desktop/bibata-cursor-theme` | |
| System font | `Inter 11` | `packages/desktop/font-inter` | Decided 2026-05-22 (Walk #10 / ac527a60) — switched from `DejaVu Sans 11` |
| Monospace font | `JetBrains Mono 11` | `packages/desktop/font-jetbrains-mono` | Decided 2026-05-22 (Walk #10 / ac527a60) — switched from `DejaVu Sans Mono 11` |
| Document font | `Inter 11` | `packages/desktop/font-inter` | Decided 2026-05-22 (Walk #10 / ac527a60) — switched from `DejaVu Sans 11` |
| Titlebar font | `Inter Bold 11` | `packages/desktop/font-inter` | Decided 2026-05-22 (Walk #10 / ac527a60) — switched from `DejaVu Sans Bold 11` |
| Sound theme | `freedesktop` (no IGOS-custom) | `packages/desktop/sound-theme-freedesktop` | |
| Welcome dialog | Suppressed (`version='9999'`) | (intergenos-default-settings) | Per v7 arc — IGOS welcomer is canonical |

### 2. Window-management defaults

| Surface | Default value at HEAD | Notes |
|---|---|---|
| Window button layout | **CONFLICT** at HEAD — see drift section | J-009 audit row open |
| Center new windows | `true` | |
| Tap-to-click | `true` | (laptop default) |
| Natural scroll | `true` | |
| Night light | `enabled=true`, `schedule-automatic=true` | |

### 3. Desktop + GDM background

| Surface | Default value | Source |
|---|---|---|
| Desktop background | `primary-color='#1a1a2e'` + `secondary-color='#16213e'` + vertical gradient + `picture-options='none'` (no image) | 90_intergenos.gschema.override |
| GDM background | Same gradient + `banner-message-text='Welcome to InterGenOS'` | 90_intergenos.gschema.override |
| Wallpaper image | **NONE shipped** — gradient only | `images/wallpaper_concept_*.png` exist in repo root but NOT packaged |

### 4. Favorite apps pinned to dash

`['org.gnome.Settings.desktop', 'org.gnome.Nautilus.desktop', 'org.gnome.Terminal.desktop', 'firefox.desktop', 'org.gnome.TextEditor.desktop']`

⚠️ **`firefox.desktop` is pinned but `firefox` is `tier:extra` not installed by default** — audit row J-027 GENUINELY-OPEN; package-presence gating not implemented.

### 5. Terminal palette (`org.gnome.Terminal.Legacy.Profile`)

- `use-theme-colors=false` (override)
- Foreground `rgb(255,255,255)` white
- Background `rgb(0,0,0)` black
- Cursor `rgb(0,255,0)` green
- 16-color xterm-classic palette (full 16-color spec in gschema)
- Scrollback `10000` lines

**Question for the fine-tooth comb:** is the `use-theme-colors=false` intentional? It overrides any GTK-theme-driven terminal palette, meaning the InterGenOS metatheme + the alternate themes cannot influence the terminal colors. Operator may want them theme-driven for consistency.

### 6. Shell extensions enabled by default (`config/gsettings/91_intergenos-extensions.gschema.override`)

- `intergen-no-overview@intergenos.org` (custom; suppresses overview at startup per v7 arc)
- `intergen-firstboot@intergenos.org` (custom; ECG animation overlay per v7 arc cinematic)
- `user-theme@gnome-shell-extensions.gcampax.github.com` (allows per-user shell theming)
- `appindicatorsupport@rgcjonas.gmail.com`
- `CoverflowAltTab@palatis.blogspot.com`
- `blur-my-shell@aunetx`
- `bluetooth-quick-connect@bjarosze.gmail.com`
- `burn-my-windows@schneegans.github.com`
- `pkm-notifier@intergenos.org` (custom; pkm upgrade notification per Q8)

**Questions for the fine-tooth comb:**
- Is the GNOME Shell theme via `user-theme` actually shipping with an InterGenOS-custom shell theme? At HEAD, `intergenos-theme` ships GTK 3/4 + shell stylesheets but the gschema doesn't set a `name=` for the user-theme extension — defaults to vanilla GNOME shell.
- Does `blur-my-shell` interact correctly with the InterGenOS color palette? (May want a shipped profile for it.)
- `burn-my-windows` profile is shipped via intergenos-default-settings — is the profile content reviewed + ratified? (Earlier D-015 precedent removed a stale BMW write block from install-theming.sh.)

### 7. Alternate themes shipped (available for user switching)

**GTK theme alternates:**
- `adw-gtk3-theme` — libadwaita-style port for GTK3 apps
- `catppuccin-gtk-theme` — Catppuccin palette
- `dracula-gtk-theme` — Dracula palette
- `fluent-gtk-theme` — Fluent design
- `graphite-gtk-theme` — Graphite (macOS-inspired)
- `nordic-theme` — Nordic palette
- `orchis-theme` — Orchis
- `whitesur-gtk-theme` — WhiteSur (macOS Big Sur-inspired)

**Icon theme alternates:**
- `papirus-icon-theme` (DEFAULT — decided 2026-05-22)
- `fluent-icon-theme`
- `tela-icon-theme`
- `whitesur-icon-theme`
- `adwaita-icon-theme` (system fallback)
- `hicolor-icon-theme` (system fallback)
- ❌ `cybernetic-icon-theme` — **NOT in tree; decided 2026-05-22: must ship as included alternate + attribution**

**Cursor theme alternates:**
- `bibata-cursor-theme` (DEFAULT)
- `macos-cursor-theme`
- `phinger-cursors`

**Font packages:**
- `font-dejavu` (default; system + mono)
- `font-noto`
- `font-misc-misc`, `font-cursor-misc`, `font-alias`, `font-util`, `mkfontscale`, `xcursorgen`, `xcursor-themes` (system support)

### 8. InterGenOS-branded surfaces in tree

- `assets/intergen-mark/svg/` + `assets/intergen-mark/png/` — InterGenOS brand mark (9 SVGs + 25 PNGs)
- `images/wallpaper_concept_*.png` — concept wallpapers
- `images/grub_background_concept*.png` — concept GRUB background
- `images/intergenos_grub_background_1920x1080.png`
- `packages/desktop/intergenos-grub-theme/` — InterGenOS-branded GRUB theme (shipped)

**Gap:** `assets/intergen-mark/{svg,png}/` is orphaned — no package installs it (audit row D-010 GENUINELY-OPEN). Branding does not propagate to `/usr/share/icons/hicolor/` or `/usr/share/pixmaps/intergenos.png`. `os-release` could ship a `LOGO=` field but doesn't.

---

## Known drift + open audit-rows in the theming surface

| Row | Severity | Concern | Status |
|---|---|---|---|
| **J-001** | Medium | install-theming.sh:371-391 writes divergent intergen-welcome chain (wrong path + stale-Exec + X-GNOME-Autostart-Phase blocker) | Bucket B fast-path — pending removal in this engagement |
| **J-003** | High | 24/25 desktop packages use `file:///` source without authoritative tarball generator | Closed-2026-05-22 (24/24 tarballs sha256-verified; D-017 reproducibility-script follow-up named) |
| **J-008** | Medium | Multi-component drift Papirus-Dark (shipping reality) vs Cybernetic Blue (documented intent) | **Reclassified GENUINELY-OPEN 2026-05-22; routed here for fine-tooth-comb resolution** |
| **J-009** | Medium | Button-layout conflict between 90_ (close-LEFT) + 92_ (close-RIGHT); 92_ wins lexicographically | GENUINELY-OPEN; pending fine-tooth-comb decision |
| **J-010** | Medium | Extension installer at install-theming.sh:62-92 still extracts 23 UUIDs from CACHE_DIR/extensions/*.zip; 4 intergenos-extensions-* packages also ship via file:/// — two writers structurally remain | GENUINELY-OPEN |
| **J-014** | Low | 11 GTK themes installed via install-theming.sh (which is being retired); should be batched in T0-7-E sub-task | INTENTIONALLY-LEFT-OPEN-with-rationale; will be cleaned up under fine-tooth-comb |
| **J-027** | High | firefox.desktop pinned in favorite-apps but firefox is `tier:extra` not installed by default; broken-dock-pin regression | GENUINELY-OPEN; package-presence gating not implemented |
| **D-010** | Medium | `assets/intergen-mark/{svg,png}/` orphaned (no package installs branding) | GENUINELY-OPEN |
| **P-014** | (legal) | Cybernetic icon theme attribution | Will be addressed when Cybernetic package authored by requirement |

---

## Specific items to walk through with fine-tooth comb pre-ISO

This is the work-item surface for the pass. Each item is a discrete decision OR a discrete code change.

### A. Cybernetic icon theme inclusion (decided 2026-05-22)

**CLOSED at commit 563356de (2026-05-22) — cybernetic-icon-theme 2.0 package landed.**

- New `packages/desktop/cybernetic-icon-theme/` package (tier:desktop; build_style:custom; asset-in-package convention mirroring intergenos-grub-theme + intergenos-wallpapers); ships bundled `assets/Cybernetic.tar.gz` (3,418,257 bytes; sha256 `5f9fdc7b4d790f2c5f5c0446e34a9d499a78d34ddf3442322bc5f01af5e097f4`) extracted to `/usr/share/icons/Cybernetic - Blue/`; runtime dep `hicolor-icon-theme`; post_install runs `gtk-update-icon-cache`; defensive FATAL assertion on `index.theme` presence
- License GPL-3.0-or-later upstream (https://github.com/SethStormR/Cybernetic by SethStormR); THIRD-PARTY-NOTICES.md desktop-tier section entry landed at 563356de (between cups-filters + dart-sass alphabetical); CREDITS half closed earlier
- intergen-welcome runtime dep adds `cybernetic-icon-theme` alongside `papirus-icon-theme` + `bibata-cursor-theme` (welcomer's appearance picker can now surface Cybernetic as alternate selection)
- Doc refreshes landed at 563356de:
  - `README.md:22` — caption rewritten: Papirus-Dark default; Cybernetic Blue included alternate, switchable via Settings or first-boot welcomer
  - `docs/VISION.md:271` — theming canonical line refreshed to same shape
  - `docs/users/desktop-experience.md:7` — same shape with "broader application coverage" rationale operator gave for Papirus-default
- `install-theming.sh` Cybernetic extract block retired (lines 272-281; breadcrumb comment retained); `download-theming.sh` Cybernetic download call retired (lines 249-256; breadcrumb comment retained)
- Item J coupling: legacy `assets/theming/icon-themes/Cybernetic.tar.gz` byte-identical to new canonical (both sha256 `5f9fdc7b...`); deduplicated when Item J retires install-theming.sh entirely
- Closes audit-row J-008 (Papirus-Dark vs Cybernetic icon-theme contradiction; resolved via Papirus-default + Cybernetic-alternate canonical posture) + closes P-014 upstream-license-verification arm (THIRD-PARTY-NOTICES.md half; CREDITS half closed earlier)
- Original task list:
  - Author `packages/desktop/cybernetic-icon-theme/`
    - Upstream: https://github.com/SethStormR/Cybernetic
    - License check (currently shows in repo; needs THIRD-PARTY-NOTICES.md entry — closes P-014)
    - Install path: `/usr/share/icons/Cybernetic-Blue/` (or whatever the actual theme dirname is per upstream)
    - Verify_paths in package.yml
  - Add `cybernetic-icon-theme` to intergen-welcome's appearance picker as alternate
  - Update intergen-welcome wizard to surface Cybernetic with preview thumbnail
  - Add THIRD-PARTY-NOTICES.md entry for Cybernetic Blue (attribution)
  - Update `docs/users/desktop-experience.md` to clarify "Papirus-Dark is default; Cybernetic Blue is featured alternate"
  - Update `README.md:22` text to match (currently implies Cybernetic is default)
  - Update `docs/VISION.md:271` to match
- Update the D-006 requirement enumeration if needed
- Update `matrix:1423` annotation if needed

### B. Wallpaper image shipping

**CLOSED at commit 6a5d51e0 (2026-05-22) — intergenos-wallpapers package landed.**

- 4 first-party 3840x2160 wallpapers shipped: ItIsOnly (default) + Helix + Overwatch + Pulse, sourced via Real-ESRGAN x4plus + Lanczos downscale from 1672x941 originals
- New `packages/desktop/intergenos-wallpapers/` package (tier:desktop; build_style:custom; source:[]; asset-in-package convention mirroring intergenos-grub-theme + intergen-firstboot + intergen-welcome) — ships PNGs at `/usr/share/backgrounds/intergenos/` + `gnome-background-properties` manifest at `/usr/share/gnome-background-properties/intergenos.xml` so GNOME Settings → Appearance → Background surfaces them as a curated set
- gschema (`90_intergenos.gschema.override`) updated: `picture-uri` + `picture-uri-dark` + `picture-options=zoom` set under `[org.gnome.desktop.background]` + `[org.gnome.desktop.screensaver]`; `primary-color` + `secondary-color` + `color-shading-type=vertical` retained as gradient fallback for early-boot / broken-file cases; intergenos-default-settings release 2 → 3 since gschema source changed (D-006 SSoT)
- GDM `[org.gnome.login-screen]` left unchanged (OR-branch "gradient stays" selected)
- Original task list:
  - `images/wallpaper_concept_*.png` (15 concepts exist in repo) — none packaged
  - Pick canonical IGOS wallpaper(s)
  - Author `packages/desktop/intergenos-wallpapers/` (or include in intergenos-theme)
  - Update gschema to set `picture-uri-dark='file:///usr/share/backgrounds/intergenos/default.png'` + `picture-options='zoom'`
  - GDM background gradient stays OR set GDM wallpaper too

### C. Brand-mark package (closes D-010)

**CLOSED at commits 5959dde2 + c82a3946 (2026-05-22) — `packages/desktop/intergen-mark/` 1.0 authored per audit prescription + iter-2 cross-check (LOGO=) addition. Closes audit-rows D-010 (Medium) + J-016 (Low) + theming-arc Item C. Walk D-010 Bucket-D, the first item worked.**

- `packages/desktop/intergen-mark/` tier:desktop / source:[] (asset-in-package convention via `IGOS_SOURCE_ROOT` reach into brand-source canonical at `assets/intergen-mark/`; no duplicate-asset-in-package; mirrors intergenos-default-settings in-tree-config pattern)
- Hicolor sized icon stack: 16/24/32/48/64/128/256/512/1024 → `/usr/share/icons/hicolor/<size>/apps/intergenos.png` (transparent variants per hicolor convention)
- Hicolor scalable SVG: 3 variants → `intergenos.svg` (simple flat) + `intergenos-symbolic.svg` (white-on-transparent) + `intergenos-full.svg` (full-detail)
- Pixmaps fallback: `/usr/share/pixmaps/intergenos.png` (256x256 transparent; deprecated XDG path, legacy + os-release LOGO= consumer compat)
- `/usr/share/intergenos/` stack: 2 wordmark PNGs (with/without alpha bg) + 3 logo SVG variants (color/transparent/white) + 4 logo PNG renders (512/1024/1536/2048) + 2 logo-transparent PNGs + 3 full-icon SVG variants. Used by welcomer hero + About dialogs + GDM logo key + intergen-toggle wordmark
- `LOGO=intergenos` added to `/etc/os-release` block in `scripts/chroot-config-ch9.sh` (closes iter-2 cross-check downgrade note at audit:454)
- Bandaid removed: `intergenos-default-settings` release 4 → 5 (delete duplicate `assets/intergenos_wordmark_transparent.png` + remove install block + remove verify_paths entry + add runtime dep on intergen-mark)
- 5th tier:desktop package this arc after intergenos-wallpapers + cybernetic-icon-theme + font-inter + font-jetbrains-mono + intergen-toggle. Dry-run validation: 27 files staged cleanly across 4 install surfaces (1.4MB total); 4 Class A gates ALL PASS; public-content WARN 2 baseline maintained

**Original prescription (kept for provenance):**

- Author `packages/desktop/intergen-mark/`
- Install SVGs to `/usr/share/icons/hicolor/scalable/apps/intergenos.svg`
- Install PNGs to corresponding `/usr/share/icons/hicolor/<size>/apps/intergenos.png`
- Symlink `/usr/share/pixmaps/intergenos.png`
- Add `LOGO=intergenos` to os-release

### D. Window button layout decision (closes J-009)

**CLOSED at commit 12ff01a3 (2026-05-22) — close-RIGHT canonical single-writer SSoT restored. Option A, decided from Walk #9 decision surface.**

- Decided 2026-05-22: close-RIGHT canonical (matches modern GNOME convention + Windows/Linux/Android convention + matches intergenos-theme metatheme + matches what was already shipping at runtime via lexicographic 92 > 90 ordering)
- 12ff01a3 removes the close-LEFT `button-layout='close,minimize,maximize:'` entry from `config/gsettings/90_intergenos.gschema.override:28` and replaces with a one-line breadcrumb pointing at `config/gsettings/92_intergenos-desktop.gschema.override` as canonical writer
- Canonical writer at `config/gsettings/92_intergenos-desktop.gschema.override:14` has `button-layout='appmenu:minimize,maximize,close'` (preserved as-is)
- Metatheme `assets/intergen-shell-theme/index.theme:12` has `ButtonLayout=appmenu:minimize,maximize,close` matching canonical (preserved as-is)
- `titlebar-font='DejaVu Sans Bold 11'` line preserved in 90's `[org.gnome.desktop.wm.preferences]` block per audit-row remediation suggestion (titlebar-font remediation option "can move to 92" not exercised; titlebar-font kept in 90 + only close-LEFT button-layout entry removed)
- Single-source-of-truth restored across all 3 writers; closes audit-row J-009 in the 2026-05-18 comprehensive state audit
- Original decision text:
  - Two contradictory values present at HEAD; 92_intergenos-desktop wins lexicographically. Decision:
    - **Close-LEFT** (`close,minimize,maximize:`) — GNOME 2 / Ubuntu Unity style
    - **Close-RIGHT** (`appmenu:minimize,maximize,close`) — GNOME 3+ canonical / Windows-style
  - Whichever wins, remove the contradictory value from the other override file. Single source of truth.

### E. Terminal palette decision

- `use-theme-colors=false` + explicit white/black + green cursor + 16-color xterm palette
- Question: keep override OR let GTK theme drive terminal colors for consistency?
- If override stays, document the palette choice. If override goes, the InterGenOS metatheme's terminal-color spec needs population.

### F. GNOME Shell theme

**STATE 2026-05-22 (per f0edda75 secondary review pass): Premise bullet 1 below is outdated at audit-prep authoring time — user-session shell-theme `name='InterGenOS'` IS set in `config/gsettings/91_intergenos-extensions.gschema.override:25` since commit `a470d17b` (`feat(packages): intergenos-theme 1.0.0 + welcomer/gschema defaults`; predates 2026-05-22 audit-prep authoring). f0edda75 (2026-05-22) adds the greeter-session equivalent via `/etc/dconf/db/gdm.d/00-intergenos-greeter` (`[org/gnome/shell/extensions/user-theme] name='InterGenOS'`). Item F decision "ship an InterGenOS shell theme via user-theme extension" is substantively implemented across both user-session + greeter-session surfaces; review decides formal closure status (treat below as historical pre-a470d17b state).**

- `user-theme` extension is enabled but no shell-theme `name=` is set in gschema
- `intergenos-theme/build.sh` ships shell stylesheets but they don't get picked up without an explicit name set
- Decision: ship an `InterGenOS` shell theme via user-theme extension, OR remove the user-theme extension from default-enabled list

### G. blur-my-shell + burn-my-windows profiles

- Both extensions enabled by default but no IGOS-custom profile / preset
- May want a shipped profile that matches the InterGenOS palette (deep-navy blur tint; ECG-blue burn animation)
- Or accept defaults

### H. favorite-apps pin cleanup (closes J-027)

**CLOSED at commits D-014 (2026-05-20) + c4649a2b + 92335852 (2026-05-22) — firefox added to default install via iso_include:true + telemetry-OFF lockdown + package.yml alignment.**

- Option 1 selected: "add firefox to default install" — D-014 ratification 2026-05-20 set `iso_include: true` in `packages/extra/firefox/package.yml`; firefox is now in the 15-package ISO set per the extra-tier classification; dock favorites pin `firefox.desktop` at `config/gsettings/90_intergenos.gschema.override:35` now resolves correctly
- c4649a2b lands the companion telemetry-OFF lockdown via canonical Mozilla `/usr/lib/firefox/distribution/policies.json` (11 top-level policies + 19 Preferences locks; canonical-verified against `mozilla.github.io/policy-templates/` + arkenfox/user.js gold-standard reference); closes the "firefox done right" envelope per `docs/users/desktop-experience.md` section 7 "Firefox telemetry is disabled at build time"
- 92335852 companion commit aligned `packages/extra/firefox/package.yml` with c4649a2b commit-body claims that initially missed landing (release 1 -> 2 + description telemetry-OFF posture callout + verify_paths hard-gate on `/usr/lib/firefox/distribution/policies.json`); an Edit-Read-first-gate error was captured in 92335852 commit body
- Option 2 alternative ("ship a package-presence-gated favorites mechanism") NOT exercised — broader concern preserved at Item I (intergenos-default-settings build.sh package-presence gating)
- Closes audit-row J-027 (firefox.desktop dock-pin gap) inline at audit doc; INFO observations preserved for Item I scope (package-presence gating)
- Original task list:
  - Remove `firefox.desktop` from favorites OR add firefox to default install
  - OR: ship a package-presence-gated favorites mechanism (welcomer asks user during first-boot)

### I. intergenos-default-settings build.sh package-presence gating

- Per audit row J-027 + D-006 cluster: SSoT package should gate which favorites/extensions appear based on what's actually installed
- Otherwise the SSoT ships broken pins for packages that may not be installed

### J. install-theming.sh full retirement (couples to J-001 + J-010)

- After J-001 (intergen-welcome divergent write block removal — pending in Bucket B), install-theming.sh has very little remaining content
- What's left:
  - Extension installer (J-010 — 23 UUIDs extraction from CACHE_DIR/extensions/*.zip)
  - 11 GTK themes install (J-014 — `tar -xJf` of each theme into `/usr/share/themes/`)
  - Cursor + icon installs (already package-handled?)
- Decision: retire install-theming.sh entirely (all themes already package-handled) — or keep as a thin GTK-theme-installer for the 11-theme bulk
- If retired: matrix:347 already says "install-theming.sh retired" — but the script still exists. Drift.

### K. THIRD-PARTY-NOTICES.md theming attribution audit

- Walk every shipped theme + icon + cursor + extension package
- Verify each has THIRD-PARTY-NOTICES.md entry with proper attribution
- Closes P-014 (Cybernetic) + any other gaps surfaced during the walk

### L. dconf system-db retirement verification

- D-006 retired dconf system-db overrides in favor of gschema SSoT
- Verify NO `/etc/dconf/db/system.d/` writes remain anywhere in the chroot-build chain
- Verify install-theming.sh doesn't reintroduce them

### M. Welcomer appearance page integration

**CLOSED at commit 638660c6 (2026-05-22) — Walk #18 ratification: 10 curated theme combos in `intergen-welcome.py` `THEME_COMBOS` + 10 deterministic SVG-rendered 1280x800 thumbnails at `assets/intergen-welcome/previews/` + reproducible `generate.py` cairosvg pipeline.**

- 10 theme combos shipped in `assets/intergen-welcome/intergen-welcome.py:THEME_COMBOS` (was 9 prior to Walk #18; new Cybernetic Blue combo added at lines 343-351 — full cybernetic aesthetic; featured alternate to default; couples with Walk #6 cybernetic-icon-theme package shipped at 563356de). 10 combos: InterGenOS (default) + Orchis Dark + WhiteSur + Catppuccin Mocha + Nordic + Graphite + Dracula + Fluent + Orchis Light + Cybernetic Blue
- 10 deterministic 1280x800 8-bit RGB non-interlaced PNG thumbnails at `assets/intergen-welcome/previews/<theme-slug>.png` (empirically verified via `file` utility); rendered via `assets/intergen-welcome/previews/generate.py` Python + cairosvg pipeline (454 lines; SVG template + ICON_FAMILIES geometry dict + per-theme color schema); same composition layout across all 10 — only colors + icon-family geometry change between them, ensuring "curated set" cohesion (vs image-gen diffusion model drift). Pipeline reproducible: `python3 assets/intergen-welcome/previews/generate.py` regenerates all N thumbnails in ~2 seconds when themes are added/removed/updated
- Icon family geometries distinguish: papirus_dark/papirus_light (flat rounded square + colored badge) + whitesur_dark (macOS squircle with gradient) + fluent (rounded square with linear gradient + Microsoft sheen) + cybernetic_blue (hexagonal HUD shape with cyan glow filter). Dracula uses signature pink/purple/cyan icon-accent triad
- `packages/desktop/intergen-welcome/package.yml` release 1 → 2; description updated to call out new combo + thumbnail pipeline
- Closes audit-prep Item M (Welcomer appearance page integration) at Walk #18 (decided 2026-05-22)
- Note: original Item M bullet "Currently coupled to D-003 (intergen-welcome tarball missing) ... needs visibility into what's actually in the tree" was a pre-D-006 / pre-USA-1-Step-2-Bucket-A concern — D-003 + J-003 tarball closure annotations (at f958e189 + 4f6fe6dc) addressed the tarball-mechanism gap separately; build/sources/intergen-welcome-1.0.tar.xz regeneration to include Walk #18 changes is Item O reproducibility-script gap (the broader cross-cutting concern)
- Original task list:
  - `intergen-welcome/build_appearance_page` should expose:
    - Icon theme alternates (Papirus-Dark default; Cybernetic Blue featured alternate; others available)
    - GTK theme alternates (InterGenOS default; 8 alternates)
    - Cursor theme alternates (Bibata-Modern-Classic default; 2 alternates)
    - Dark / light color scheme toggle
  - Currently coupled to D-003 (intergen-welcome tarball missing) and I-008 (welcomer build_intergen_page has zero intergen daemon refs); needs visibility into what's actually in the tree

### N. Visual identity asset finalization

**PARTIAL — wallpaper arm CLOSED at commit 6a5d51e0 (2026-05-22) via intergenos-wallpapers package (see Item B above). GRUB-background arm + brand-mark-variants arm + asset-surfaces-doc arm REMAIN OPEN.**

- Pick canonical wallpaper(s) from `images/wallpaper_concept_*.png` — CLOSED: ItIsOnly canonical default + Helix / Overwatch / Pulse alternates, shipped via intergenos-wallpapers package
- Pick canonical GRUB background from `images/grub_background_concept_*.png` (currently `intergenos_grub_background_1920x1080.png` may be the chosen one — verify) — OPEN
- Pick canonical brand mark variants (light / dark / colored / monochrome) — OPEN (couples to Item C brand-mark package + D-010)
- Document each asset's intended use surface — OPEN

### O. Reproducible source-tarball generation (D-017 + J-003 + J-018 follow-up)

**Folded into theming-arc 2026-05-22 (option B per the USA-1 Step 2 Bucket B walk).**

The D-017 audit row + J-003 + J-018 closures (all landed at Bucket A wave-2 + D-002 Path B atomic commit 2811bd28) cited a common follow-up: prescribed script `scripts/build-intergenos-source-tarballs.sh` was never authored. The 24 file:/// desktop package tarballs currently exist in `build/sources/` with sha256 MATCHING their `package.yml` pins (empirically verified 6/6 + 24/24 at HEAD during USA-1 Step 2 walk), but they were generated by an uncommitted ad-hoc mechanism. Reproducibility is opaque.

22 of the 24 affected packages are theming-related (themes/icons/cursors); only `forge` + `intergen-welcome` aren't. Folding into this theming-arc pass because the work overlaps heavily with item J (install-theming.sh full retirement) + the broader theming structure decisions.

**Concrete work for this item:**

- Author `scripts/build-intergenos-source-tarballs.sh` with deterministic flags:
  - `tar --sort=name --owner=0 --group=0 --numeric-owner --format=ustar --mtime='@<fixed>'`
  - Deterministic xz: `XZ_OPT='-9 -T1 --no-warn'` (single-threaded for repro; `-T1` avoids multi-thread non-determinism)
  - Document the fixed mtime choice (e.g., latest commit time of in-tree source dir)
- Run-script + update all 24 `package.yml` `sha256:` pins to the deterministic output
- Wire into orchestrator `scripts/build-intergenos.sh` as `phase_source_tarballs` (pre-build verification step)
- Add a CI-style gate that verifies `sha256sum build/sources/*.tar.* | sort` against an in-tree manifest before any chroot step proceeds
- Decision: keep `build/sources/` committed (current state) OR `.gitignore` it + always regenerate

**Couples to:**

- D-017 audit-row (closed at 2811bd28 via tarballs-present-at-HEAD; reproducibility-gap named here)
- J-003 + J-018 (same — closed at 2811bd28; same gap named here)
- Item J above (install-theming.sh full retirement may eliminate some package surfaces)
- Item M above (intergen-welcome appearance picker integration; couples to its tarball)

**Effort:** Medium. Deterministic tarball flags are well-known; reverse-engineering existing sha256 pins may or may not be possible. If not, update pins to new deterministic output (~25-file commit).

### P. Font typography (decided 2026-05-22, Walk #10 closure)

**CLOSED at commit ac527a60 (2026-05-22) — Inter 4.1 + JetBrains Mono 2.304 variable-font pair shipped; Option B, Walk #10, decided.**

Retroactive work-item entry for Walk #10 (the font switch landed without an explicit Item A-O slot at audit-prep authoring time; documented inline here for closure-tracking SSoT coherence per cross-team review observation 2026-05-22T15:31:32Z).

- Decided 2026-05-22, "shock and awe" alignment: DejaVu Sans (designed 2004-2008, dated humanist sans) replaced by Inter (clean modern geometric sans variable font, OFL-1.1) for system + document + titlebar surfaces; DejaVu Sans Mono replaced by JetBrains Mono variable (programming ligatures, OFL-1.1) for monospace surfaces. Aligns with the wordmark's restrained-geometric voice + the "deliberate craft" tier of Linux distros (Pop_OS, elementary, modernized Manjaro)
- New `packages/desktop/font-inter/` package (tier:desktop; build_style:custom; install_func:do_install; source:[]; runtime:fontconfig; asset-in-package convention) ships curated Inter 4.1 subset: `InterVariable.ttf` (~970KB), `InterVariable-Italic.ttf`, `LICENSE.txt` — bundled tarball at `assets/font-inter-4.1.tar.gz` (970,310 bytes; upstream zip is 33MB; subset drops Inter.ttc + woff-hinted web variants + static-weight individual .ttf files). verify_paths covers all 3 files
- New `packages/desktop/font-jetbrains-mono/` package (same shape) ships curated JetBrains Mono 2.304 subset: `JetBrainsMono[wght].ttf` (literal-bracket filename per upstream variable-font convention), `JetBrainsMono-Italic[wght].ttf`, `OFL.txt`, `AUTHORS.txt` — bundled tarball at `assets/font-jetbrains-mono-2.304.tar.gz` (310,977 bytes; upstream zip is 5.4MB; subset drops 16 static-weight static .ttf files + 16 "NL" no-ligature variants + woff2 web fonts). verify_paths uses `OFL.txt` + `AUTHORS.txt` as sentinels per build.sh-documented design trade-off (literal `[wght]` brackets in .ttf filenames are awkward to verify via shell)
- gschema (`config/gsettings/90_intergenos.gschema.override`) updated: `font-name='Inter 11'`, `monospace-font-name='JetBrains Mono 11'`, `document-font-name='Inter 11'`, `titlebar-font='Inter Bold 11'` (sizes preserved at 11pt; tunable later if too dense in practice)
- intergenos-theme + intergen-welcome runtime deps: + font-inter + font-jetbrains-mono (additive; font-dejavu retained as fallback for apps that explicitly request it)
- THIRD-PARTY-NOTICES.md: font-inter + font-jetbrains-mono entries added in desktop-tier alphabetical section (between font-dejavu + font-misc-misc) with OFL-1.1 license + homepage + author cites
- `docs/users/desktop-experience.md` "default visual experience" paragraph updated to call out Inter + JetBrains Mono pair
- Sourcing: tarballs downloaded from upstream releases per Option A as decided (curl from github.com/rsms/inter v4.1 + github.com/JetBrains/JetBrainsMono v2.304 release URLs); minimal subsets re-tarred to keep in-tree bundles small
- Closes: Walk #10 of theming-arc fine-tooth-comb pass (decided Option B: switch to Inter + JetBrains Mono pair)
- INFO observations preserved as a follow-up surface: (i) font-jetbrains-mono verify_paths uses sentinels not .ttf files per design trade-off; (ii) font-dejavu package description does not refresh to reflect "fallback-only" status — minor doc drift in font-dejavu's own package.yml; not landing inline pending font-dejavu package-walk

### Q. Boot splash posture (decided 2026-05-22, Walk #16 closure)

**CLOSED at commit 8de756f1 (2026-05-22) — InterGenOS deliberately does NOT ship Plymouth; lean into the systemd boot scroll as positive transparency posture.**

Retroactive work-item entry for Walk #16 (the Plymouth-state decision landed without an explicit Item A-O slot at audit-prep authoring time; documented inline here for closure-tracking SSoT coherence; consistent with Item P precedent at wave-5).

- Decided 2026-05-22: InterGenOS deliberately does NOT ship Plymouth. The kernel→systemd boot scroll is a positive transparency posture aligned with the user-control principle ("a system they understand") and the security-only posture ("a machine the user cannot trust is a machine they do not control"). Plymouth as a layer over boot output is intentional opacity; for a security-only distro that explicitly does NOT hide things from users, hiding boot is incongruent
- Quote: *"I LOVE the 'oddly satisfying' scroll of systemd waking up — and I usually make it a habit/game of trying to spot odd errors."* Error-spotting during boot is an actual security practice for catching compromise + hardware anomalies; users get that surface instead of a corporate logo
- `packages/desktop/intergenos-grub-theme/build.sh:67` changed `GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"` to `"quiet"`; release 1 → 2. `splash` cmdline parameter removed defensively to ensure even future Plymouth additions would not accidentally activate. `quiet` retained to suppress kernel printk noise — the "oddly satisfying" scroll is the systemd side, not the kernel printk side
- `docs/users/desktop-experience.md` section 7 (Deliberate Omissions): adds "No Plymouth boot splash" bullet documenting the deliberate choice + the security/transparency rationale; pairs with existing "No telemetry" + "No auto-update" + "No Snap" transparency posture bullets
- Audit-row D-030 status open → INTENTIONALLY-LEFT-OPEN-with-rationale 2026-05-22 with full rationale captured inline in the 2026-05-18 comprehensive state audit (row D-030); matches audit-doc convention for healthy-deliberate-state items (B-050 + J-014 + O-011 precedent per 8de756f1 commit body)
- Closes: Walk #16 of theming-arc fine-tooth-comb pass (decided: no Plymouth; lean into systemd boot scroll)
- INFO observation preserved as a follow-up surface: commit body framing on `splash` parameter mechanism is slightly imprecise (splash is a hint to Plymouth-if-present, not a direct systemd-status-suppression toggle) but the substantive change (removing splash defensively + lean-into-systemd-scroll posture) is empirically correct; framing-only observation, not a defect

### R. Welcomer prompt chooser page (decided 2026-05-22, Walk #19.5 closure)

**CLOSED at commit cb8be4ca (2026-05-22) — Stock-vs-Starship prompt-chooser page added to intergen-welcome as Page 5 (between Shortcuts + Meet InterGen); page count 7 → 8; D-014 ratification gap closed.**

Retroactive work-item entry for Walk #19.5 (a prompt-chooser page missed in the initial welcomer-page enumeration; surfaced by the question "where's the terminal choice: stock vs starship page?"). Walk #19.5 is OUTSIDE Item M scope (Item M is appearance-page integration; Walk #19.5 is a distinct prompt-chooser page). Documented inline here per Item P + Item Q + new-Walk-without-existing-A-Q-slot precedent (step-7 sub-rule Walk-item-mapping check).

- Gap context: the extra-tier classification documented "starship | extra | ISO | Shell prompt; required ISO-resident because first-login Welcomer presents a 'stock vs starship' prompt toggle. D-014 RATIFIED 2026-05-20 (per Q2 Welcomer-toggle resolution)" but zero `starship` references in intergen-welcome.py prior to cb8be4ca. Documented + ratified + package shipped ISO-resident but code never got the toggle. Implementation lands 2026-05-22
- `build_prompt_page()` two-card chooser (Stock | Starship) with monospace example renderings of each prompt style + radio-button selection + click-anywhere-on-row UX (matches appearance-picker pattern); ~150 lines added to `assets/intergen-welcome/intergen-welcome.py`
- `apply_prompt(prompt_id)` idempotent toggle that appends or strips a marker-delimited `eval "$(starship init bash)"` block in `~/.bashrc`. Marker-delimited (`# >>> intergen-welcome: starship >>>` ... `# <<< intergen-welcome: starship <<<`) means re-running the welcomer doesn't pile up duplicate eval lines (strips any prior block first, appends fresh if starship chosen)
- Page 5 insertion in welcomer flow (between Page 4 Shortcuts + Page 6 Meet InterGen); page count 7 → 8
- `starship` runtime dep added to `packages/desktop/intergen-welcome/package.yml`; intergen-welcome release 2 → 3 + description updated
- Footer hint on the page: "Open a new terminal after selecting to see your prompt." (bashrc sourced on shell init only)
- Default state: stock (per `/etc/skel/.bashrc` shipped at `scripts/chroot-config-ch9.sh:183` -- minimal, sources `/etc/bash.bashrc` only, no starship eval line); Welcomer flips to starship if user clicks that option; flips back to stock if user clicks Stock
- Closes: D-014 Q2 Welcomer-toggle implementation gap (requirement D-014 build-vs-ship ratified 2026-05-20 with Q2 stock-vs-starship resolution; implementation landed 2026-05-22)
- DEPENDENT FIX LANDED AT WAVE-10: `packages/extra/starship/package.yml` was missing `iso_include: true` field empirically at HEAD (other 11 ISO-resident extra-tier packages have it: firefox + tealdeer + eza + zoxide + dust + uchardet + ripgrep + bat + bottom + mpv + fd); the extra-tier classification asserts starship ISO-resident per D-014 Q2 but package.yml mechanism field was missing; without iso_include, build-vs-ship would not have included starship in ISO + Walk #19.5 welcomer prompt-chooser would have FAILED at first-boot when user clicks "Starship" (starship binary missing). Substantive drift caught + addressed inline at wave-10
- Item O coupling repeats: intergen-welcome package.yml source tarball sha256 (`f4fb89f6...`) STILL UNCHANGED at cb8be4ca; release 3 will empirically ship release 2 content unless tarball regenerated + sha256 updated. Same Item O concern as 638660c6; on operator decision surface per cross-team agreement (option a immediate-fix vs option b full Item O canonical closure; option b recommended)

### S. InterGen opt-in copy alignment across welcomer + Forge surfaces (decided 2026-05-22, Walk #20 + immediate followup closure)

**CLOSED at commits a2acc34f + fde2994b (2026-05-22) — InterGen opt-in disclosure copy aligned across all 3 user-consent surfaces (welcomer build_intergen_page + Forge GUI _build_intergen_row + Forge TUI intergen_ai_enable prompt) with canonical `intergen setup` terminal command + upcoming Walk #24 InterGen AI app GUI both-paths framing.**

Retroactive work-item entry for Walk #20 (opt-in copy alignment; surfaced when review caught the misleading "look for the icon in the top panel" line that wasn't true at first boot under D-010 opt-in posture). Walk #20 is OUTSIDE Item M scope (Item M is welcomer appearance integration; Walk #20 is welcomer Meet InterGen page + Forge GUI + Forge TUI opt-in copy alignment). Documented inline here per Item P + Q + R precedent (step-7 sub-rule (i) Walk-item-mapping check). Closes audit-row I-008 (welcomer build_intergen_page had zero intergen daemon refs — now has opt-in mechanism refs).

- a2acc34f Walk #20 substantive: (1) welcomer build_intergen_page adds opt-in disclosure block above "summon" block with D-010 opt-in framing + canonical `intergen setup` command + 4-5 GB model size disclosure + 5-30 minute setup time disclosure + local-only model framing; replaces misleading "Look for the InterGen icon in your top panel" with "Once InterGen has been enabled, his icon is always in your top panel"; (2) Forge GUI `_build_intergen_row` desc_label aligned on `intergen setup` (was `systemctl --user enable intergen.service` which only handles service-enable side); (3) Forge TUI `intergen_ai_enable` prompt same alignment
- fde2994b followup: all 3 surfaces refreshed to give users BOTH paths — "either by opening the InterGen AI app from your Applications menu OR by running `intergen setup` in a terminal". InterGen AI app reference is to upcoming Walk #24 standalone GTK4 toggle at `packages/desktop/intergen-toggle/` (decided 2026-05-22, Q2 Option A; lands before next ISO build so by ship time copy is accurate on both arms)
- Empirical at HEAD verification: all 3 surfaces consistent ("opt in later either by opening the InterGen AI app from your Applications menu OR by running `intergen setup` in a terminal. Either path enables the service and downloads the local AI model"); D-010 opt-in posture preserved (class A ship-gate at `scripts/check-d010-compliance.sh` enforces no auto-enable); security-only posture + user-control principle alignment (no AI runs without explicit consent; user control over their own machine; idempotent marker-delimited mechanism for prompt chooser preserves user-curated state)
- Walk #24 follow-up: GNOME Settings-style standalone GTK4 toggle app at `packages/desktop/intergen-toggle/`, decided 2026-05-22; provides GUI opt-in path complementing terminal `intergen setup` command; lands before next ISO build per commit body claim
- ITEM O COUPLING REPEATS (third + fourth dimensions): a2acc34f + fde2994b BOTH touch `assets/intergen-welcome/intergen-welcome.py` without regenerating the source tarball or updating the package.yml sha256 (still `f4fb89f6...` — now static across 4 commits since 638660c6: Walk #18 + Walk #19.5 + Walk #20 + Walk #20-followup); accumulating concern strengthens the cross-team convergent option (b) recommendation. ADDITIONAL: a2acc34f + fde2994b ALSO touch `installer/frontend/gui/screens/packages.py` + `installer/frontend/tui.py` which are within the `forge` package source (forge package.yml source pin `forge-1.0.0.tar.xz` sha256 `098b6ff1...`); SAME class of Item O coupling now spans BOTH intergen-welcome AND forge packages. ADDITIONAL: neither a2acc34f nor fde2994b bumped intergen-welcome release (stayed at 3) OR forge release (stayed at 1) — though if Item O option (b) canonical closure mechanism is the path forward, release bumps + sha256 updates would land coherently as part of that mechanism
- INFO observation preserved as a follow-up surface: text-alignment commits without release bump may be a pacing decision (small text-edits don't warrant bump given pending Item O decision); on operator decision surface

### T. Welcome page voice + persistent header-bar wordmark (decided 2026-05-22, Walk #21 closure)

**CLOSED at commit 461abad1 (2026-05-22) — Welcome page (Page 1) "shock and awe" voice copy refresh + Adw.HeaderBar left-aligned wordmark via pack_start.**

Retroactive work-item entry for Walk #21 (first-page voice alignment + persistent brand surface). Walk #21 is OUTSIDE existing Item A-S scope. Documented inline per Item P + Q + R + S precedent (step-7 sub-rule (i) Walk-item-mapping check).

- Welcome page copy refresh aligned on the security-only posture + user-control voice without being preachy: was "Your system is ready. Let's make it yours. The next few steps will help you choose your look, pick your tools, and learn the shortcuts."; now "You're now running a system built from source -- every package compiled deliberately, every default chosen by someone who actually uses it. The next few steps make it yours: appearance, extensions, terminal prompt, your local AI assistant." References from-source-build pedigree + previews upcoming walk surfaces (appearance + extensions + prompt + InterGen AI assistant)
- Adw.HeaderBar persistent wordmark: Gtk.Picture-based wordmark in pack_start slot (left-aligned, as decided); loads from canonical install path `/usr/share/intergenos/intergenos_wordmark_transparent.png` shipped via intergenos-default-settings release 4 at Walk #2 (f0edda75); size 170x46 fits standard Adw.HeaderBar height with native aspect 1.95; HiDPI handled by Gtk.Picture automatically; wrapped in `if os.path.exists(wordmark_path)` defensive check so welcomer continues to function even if intergenos-default-settings isn't installed (degraded-gracefully fallback). pack_start preserves centered title-widget slot for per-page page-title rendering
- Couples to Walk #2 (wordmark canonical install path provenance per f0edda75 Tier 2 GDM customization commit body)
- Closes: Walk #21 of theming-arc fine-tooth-comb pass (first-page voice + persistent brand surface)
- Item O coupling 5th instance: 461abad1 touches assets/intergen-welcome/intergen-welcome.py without source tarball regeneration or sha256 update (same Item O concern as 638660c6 + cb8be4ca + a2acc34f + fde2994b); 5-instance accumulation on intergen-welcome package

### U. Done page reconfiguration-surfaces accuracy refresh (decided 2026-05-22, Walk #23 closure -- final welcomer walk)

**CLOSED at commit 06e0a9f3 (2026-05-22) — Done page closer refreshed to enumerate all 4 post-install reconfiguration surfaces accurately + "Enjoy your machine" user-control 3-word closer preserved intact.**

Retroactive work-item entry for Walk #23 (the final welcomer walk; closes the walk-through-welcomer-pages enumeration as a series after Walks #18 + #19.5 + #20 + #21 already landed). Walk #23 is OUTSIDE existing Item A-T scope. Documented inline per precedent class.

- Done page closer reconfiguration-surfaces alignment: was "Everything you chose can be changed anytime in Settings or the Extensions app." (accurate when welcomer had only Appearance + Extensions pages); now "Anything you chose here can be changed anytime -- re-run this welcomer, or use Settings, the Extensions app, or the InterGen AI app from your Applications menu." Honest 4-surface enumeration: re-run welcomer (covers all 4 toggles especially prompt) + Settings → Appearance (themes/icons/cursor) + Extensions app (shell extensions) + InterGen AI app (Walk #24 forthcoming standalone GTK4 toggle; ships before ISO)
- "Enjoy your machine" 3-word closer kept intact -- "the system the user is in control of" voice in its most compact form
- Walk #23 marks the end of the per-welcomer-page walk-through series (Walks #18 appearance + #19.5 prompt + #20 InterGen disclosure + #21 Welcome voice + persistent wordmark + #23 Done closer = welcomer arc complete pending Walk #24 intergen-toggle GTK4 app)
- Closes: Walk #23 of theming-arc fine-tooth-comb pass (the final welcomer walk)
- Item O coupling 6th instance: 06e0a9f3 touches assets/intergen-welcome/intergen-welcome.py without source tarball regeneration or sha256 update; 6-instance accumulation on intergen-welcome package — cross-team convergent option (b) full Item O canonical closure recommendation now load-bearing operator decision

### V. InterGen AI Applications-menu toggle app (decided 2026-05-22, Walk #24 closure)

**CLOSED at commit a1a0b730 (2026-05-22) — new `packages/desktop/intergen-toggle/` standalone GTK4 + libadwaita app provides GUI opt-in/opt-out path complementing the `intergen setup` terminal command; closes the "InterGen AI app from your Applications menu" arm of dual-path welcomer + Forge copy.**

Retroactive work-item entry for Walk #24 (the standalone GUI toggle app forward-referenced from Walks #20 + #21 + #23 + Items S + T + U). Walk #24 is OUTSIDE existing Item A-U scope. Documented inline per Item P + Q + R + S + T + U precedent (step-7 sub-rule (i) Walk-item-mapping check).

- New `packages/desktop/intergen-toggle/` package (tier:desktop; build_style:custom; install_func:do_install; source:[] asset-in-package convention mirroring intergenos-grub-theme + intergenos-wallpapers + cybernetic-icon-theme + intergen-firstboot precedent; runtime deps python + gtk4 + libadwaita + intergen + gnome-terminal; verify_paths covers /usr/bin/intergen-toggle + /usr/libexec/intergen-toggle/intergen-toggle.py + /usr/share/applications/intergen-toggle.desktop)
- `assets/intergen-toggle.py` Python+GTK4+libadwaita app (~10.6KB executable): D-010 honor cite at line 8 verbatim; Adw.SwitchRow opt-in semantics (user explicitly toggles ON; never auto-enables); WORDMARK_PATH at line 31 with Walk #2 wordmark provenance cite; systemctl --user is-enabled/is-active probes at lines 45+55; Adw.HeaderBar + Gtk.Picture wordmark pack_start at lines 142-149 matching Walk #21 pattern; Adw.SwitchRow at line 188; bidirectional sync with actual service state (refreshed on window focus + 1s after toggle; handler-block prevents toggle-loop); model-present/absent branching (model present -> systemctl enable; model absent -> spawn gnome-terminal `intergen setup` which handles enable-after-download); Adw.MessageDialog error path tells user to run `intergen setup` manually as fallback
- `assets/intergen-toggle.desktop` Applications-menu integration: Name=InterGen AI + Categories=Settings;System; + Terminal=false + Icon=intergen-toggle + StartupWMClass=intergen-toggle
- D-010 + security-only posture + user-control principle alignment: SwitchRow opt-in semantics + explicit user-toggle never-auto-enable + disclosure footer + CLI fallback commands + bidirectional state sync; user-control posture preserved
- Closes: Walk #24 of theming-arc fine-tooth-comb pass (the standalone GUI toggle app) + closes the "InterGen AI app from Applications menu" forward-reference arm from Walks #20 + #21 + #23 (dual-path welcomer + Forge copy now empirically backed at both arms)
- NO Item O coupling: intergen-toggle adopts asset-in-package convention from inception (source:[]; bundled Python+desktop file shipped via tar+install pattern); the canonical-pattern-from-start that the 25 file:///*.tar.xz packages would migrate to under Item O full closure option (b). intergen-toggle is the "what good looks like" reference for future migration
- NO iso_include drift: tier:desktop = ISO-resident by default per tier-graph; iso_include field N/A (sub-rule (iii) applies only to tier:extra packages)
- Welcomer arc COMPLETE: Walks #18 appearance + #19.5 prompt + #20 InterGen disclosure + #21 Welcome voice + persistent wordmark + #23 Done closer + #24 intergen-toggle GUI = per-welcomer-page walk-through series + dual-path opt-in surface complete

---

## Hard gate for next ISO build

**The fine-tooth-comb pass must complete BEFORE the next ISO build.** Decided 2026-05-22 ~12:Z.

Closing this gate requires:
1. All items A-N walked + decided or held-with-rationale
2. All theming-related audit rows (J-001/J-003/J-008/J-009/J-010/J-014/J-027 + D-010 + P-014) either closed or moved to v1.x-named-followup with rationale documented
3. The InterGenOS "theming experience" — what a user actually sees on first boot — is a deliberate, ratified, single-source-of-truth picture across every surface

**Tracking:** This document is the canonical theming-arc work-item surface, and is the v1.0 ship-block reference when starting the pass.

---

## Provenance

- **Trigger:** decided mid-walk of audit row J-008 (USA-1 Step 2 PARTIAL walk item 9), 2026-05-22 ~12:Z
- **Authored:** 2026-05-22
- **Empirical sweep base:** master `2811bd28` (D-002 Path B atomic landed; install-theming.sh greeter-block delete pending)
- **Theming directives captured here:**
  - Papirus-Dark = default icon theme (confirmed)
  - Cybernetic Blue = included alternate + attribution required
  - Theming is CRITICAL — fine-tooth-comb mandatory before next ISO build
- **Cross-doc cites:**
  - The 2026-05-18 comprehensive state audit — Lane J rows (J-001/J-003/J-008/J-009/J-010/J-014/J-027) + D-010 + P-014
  - The 2026-05-22 Step 2 aggregate findings (Step 2 sweep where J-008 reclassified)
  - D-006 requirement (theming SSoT directive, 2026-05-18)
  - `docs/users/desktop-experience.md`, `docs/VISION.md:271`, `README.md:22` (canonical-documentation surfaces)
  - matrix:1423 (A33 2026-05-03 theme-choices ratification)
- **Related tracking entry:** theming-arc pre-ISO fine-tooth-comb pass (cross-referenced from the project tracker)

---

*"Theming is CRITICAL, to me AND the project." — 2026-05-22 ~12:Z*
