# GTK4/libadwaita Window Controls: Oval Buttons Problem

**For review by:** Gemini, ChatGPT, DeepSeek
**Date:** April 11, 2026
**Project:** InterGenOS — custom LFS-based Linux distribution
**Author:** Claude (working on the InterGenOS GNOME shell theme)

---

## The Problem

**Goal:** Make the window control buttons (minimize, maximize, close) in libadwaita apps — specifically **Nautilus (Files)** — render as perfect circles.

**Symptom:** The buttons render as horizontal ovals (wider than tall) regardless of what CSS I apply. They resist all attempts to constrain them to a square shape. The rest of the InterGenOS GTK4 theme works perfectly for every other app (Settings, Tweaks, Text Editor, Extensions, etc.) — this is the only remaining issue.

**Observation:** The issue is worse in some apps than others. Settings and Tweaks show proper circles. Nautilus (Files) consistently shows ovals. Extensions app also shows slightly oval buttons.

---

## Environment

- **OS:** InterGenOS 1.0-dev (LFS 13.0 based, built from source)
- **GNOME Shell:** 49.x
- **libadwaita:** 1.8.4
- **GTK:** 4.x (bundled with libadwaita)
- **Display server:** Wayland (GNOME session)
- **Theme name:** `InterGenOS` (custom theme I'm building)
- **CSS locations** (both kept in sync, both deployed, same MD5):
  - `~/.config/gtk-4.0/gtk.css` (user level)
  - `/etc/gtk-4.0/gtk.css` (system level)
- **Theme directory:** `/usr/share/themes/InterGenOS/` (contains `gtk-4.0/gtk.css`, `gtk-3.0/gtk.css`, `gnome-shell/gnome-shell.css`, `index.theme`)
- **Active GTK theme:** `gsettings get org.gnome.desktop.interface gtk-theme` returns `'InterGenOS'`
- **Color scheme:** `prefer-dark`

---

## What I Know About libadwaita's Default Window Controls

I extracted libadwaita 1.8.4's embedded default CSS from `/usr/lib/libadwaita-1.so.0` via:
```bash
gresource extract /usr/lib/libadwaita-1.so.0 /org/gnome/Adwaita/styles/default.css
```

The default rules for `windowcontrols`:

```css
/********************* GtkWindowControls * */
windowcontrols { border-spacing: 3px; }

windowcontrols > button { min-width: 24px; padding: 5px; }

windowcontrols > button:not(.raised):not(.suggested-action):not(.destructive-action):not(.opaque).image-button,
windowcontrols > button:not(.raised):not(.suggested-action):not(.destructive-action):not(.opaque).image-button:hover,
windowcontrols > button:not(.raised):not(.suggested-action):not(.destructive-action):not(.opaque).image-button:active {
    background: none;
    box-shadow: none;
}

windowcontrols > button > image {
    background-color: color-mix(in srgb, currentColor 10%, transparent);
    border-radius: 100%;
    padding: 4px;
    transition: background 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94),
                box-shadow 200ms cubic-bezier(0.25, 0.46, 0.45, 0.94);
}

windowcontrols > button:hover > image {
    background-color: color-mix(in srgb, currentColor 15%, transparent);
}

windowcontrols > button:active > image {
    background-color: color-mix(in srgb, currentColor 30%, transparent);
}

windowcontrols > .icon { margin: 9px; }
```

**Key observations from the default CSS:**

1. `windowcontrols > button` has `min-width: 24px` **with NO matching `min-height`**. The button's minimum width is 24px + 10px padding = 34px, but its height is determined by... something else (probably the headerbar's content area).

2. **The visible circle is rendered on the IMAGE CHILD**, not the button. The image has `background-color`, `border-radius: 100%`, and `padding: 4px`. Presumably the image is rendered at its native size (symbolic 16×16 PNG) plus 4px padding = 24×24 circle.

3. There's ALSO an alternate rule for `headerbar.default-decoration` which DOES set equal min dimensions:
```css
headerbar.default-decoration windowcontrols > button {
    min-height: 22px;
    min-width: 22px;
    padding: 4px;
}
headerbar.default-decoration windowcontrols > button > image { padding: 3px; }
```

4. **GTK4 CSS does NOT support `max-width` or `max-height`** on widgets. Only `min-width` and `min-height`. This means I can set a floor but not a ceiling on widget dimensions.

5. The window control icons are **symbolic PNG assets**, not SVGs. According to a GNOME Discourse thread, `-gtk-icon-size` can resize them but scaling produces thicker line weights. Their native size is effectively fixed.

---

## Research Done

### Primary sources:

1. **[GNOME Discourse: GTK4 Resized Window Control Buttons](https://discourse.gnome.org/t/gtk4-resized-window-control-buttons/23354)** — showed a working CSS pattern:
```css
windowcontrols button {
    outline-offset: 1px;
    padding: 0;
    margin: 0;
    min-width: 32px;
    min-height: 32px;
    border-radius: 0;
}

windowcontrols button > image {
    -gtk-icon-size: 24px;
    font-weight: 400;
    padding: 0;
    margin: 0;
    min-width: 24px;
    min-height: 24px;
}
```
**Note:** No `!important` anywhere. Both button and image are explicitly sized. `outline-offset: 1px` mentioned. The Discourse user was resizing buttons LARGER, not smaller.

2. **[GNOME Discourse: Set exact button size](https://discourse.gnome.org/t/set-exact-button-size/8091)** — key insight:
   > "Removing the `.text-button` class solved their problem. This class adds padding to space out the text, which distorts sizing."
   
   So some libadwaita buttons carry a `.text-button` class that adds horizontal padding. This is a possible culprit but I haven't confirmed whether Nautilus's windowcontrols buttons carry this class.

3. **[GTK4 CSS Properties docs](https://docs.gtk.org/gtk4/css-properties.html)** — confirmed:
   - Only `min-width`/`min-height` are supported (no `max-*`)
   - GTK4 CSS is a subset of web CSS with custom `-gtk-*` extensions

4. **Gemini's suggestion** (from a previous user consultation):
```css
windowcontrols button {
    padding: 0;
    min-width: 24px;
    min-height: 24px;
    border-radius: 50%;
    margin: 4px;
    border: 2px solid @accent_color;
}
windowcontrols button.close { border-color: #f35b53; }
windowcontrols button.minimize { border-color: #f6c03e; }
windowcontrols button.maximize { border-color: #31c944; }
```
**Note:** Simple, no `!important`, equal `margin: 4px` on all sides.

---

## Approaches Tried (all failed to produce perfect circles in Nautilus)

### Attempt 1: Aggressive `!important` with `min-width: 24px`
```css
windowcontrols > button {
    min-width: 24px !important;
    min-height: 24px !important;
    padding: 0 !important;
    margin: 6px 3px !important;
    border-radius: 9999px !important;
    background: rgba(15, 21, 37, 0.4);
    border: 1px solid rgba(0, 153, 255, 0.10);
}
```
**Result:** Buttons rendered as ovals. Clearly wider than tall.

### Attempt 2: Size via image child
Idea: make the button transparent, let the image child's size + margin dictate button size.
```css
windowcontrols > button {
    background: none !important;
    padding: 0 !important;
    min-width: 0 !important;
    min-height: 0 !important;
    margin: 4px 3px;
    border: none !important;
}
windowcontrols > button > image {
    background-color: rgba(15, 21, 37, 0.5) !important;
    border-radius: 100% !important;
    padding: 4px !important;
    min-width: 16px !important;
    min-height: 16px !important;
}
```
**Result:** Buttons still oval, now with visible color on both the button AND the image child (two overlapping shapes).

### Attempt 3: Kill background on button, style image only
```css
windowcontrols > button {
    background: none !important;
    background-color: transparent !important;
    padding: 0 !important;
    min-width: 0 !important;
    margin: 0 !important;
    border: none !important;
}
windowcontrols > button > image {
    background-color: rgba(15, 21, 37, 0.5) !important;
    border-radius: 100% !important;
    padding: 4px !important;
    min-width: 16px !important;
    min-height: 16px !important;
}
```
**Result:** Still oval in Nautilus/Extensions. Worked fine in Settings (proper circles there).

### Attempt 4: Force min-width == min-height on button, strip image styling
```css
windowcontrols > button,
windowcontrols button,
windowcontrols button.image-button,
windowcontrols button.titlebutton,
headerbar windowcontrols > button,
/* ... many more selectors */ {
    min-width: 24px !important;
    min-height: 24px !important;
    padding-top: 0 !important;
    padding-right: 0 !important;
    padding-bottom: 0 !important;
    padding-left: 0 !important;
    margin: 6px 3px !important;
    border-radius: 9999px !important;
    background-color: rgba(15, 21, 37, 0.5) !important;
    border: 1px solid rgba(0, 153, 255, 0.12) !important;
}
windowcontrols button image,
windowcontrols > button > * {
    background: none !important;
    border-radius: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    min-width: 14px !important;
    min-height: 14px !important;
    -gtk-icon-size: 14px !important;
}
```
**Result:** Still oval. My CSS IS applying (background colors and hover states work), but the button remains wider than tall.

### Attempt 5: Match GNOME Discourse working pattern exactly, no `!important`
```css
windowcontrols button {
    outline-offset: 1px;
    padding: 0;
    margin: 6px 3px;
    min-width: 24px;
    min-height: 24px;
    border-radius: 100%;
    background-color: rgba(15, 21, 37, 0.5);
    border: 1px solid rgba(0, 153, 255, 0.12);
}
windowcontrols button > image {
    -gtk-icon-size: 14px;
    font-weight: 400;
    padding: 0;
    margin: 0;
    min-width: 14px;
    min-height: 14px;
}
```
**Result:** Buttons became tiny (24×24) but still oval. Worse-looking than before.

### Attempt 6 (CURRENT): Equal margin on all sides
Following Gemini's suggestion, using `margin: 4px` (equal all sides) instead of asymmetric 6px 3px. Thought: asymmetric margins might be constraining the button vertically but not horizontally.

```css
windowcontrols button {
    padding: 0;
    margin: 4px;
    min-width: 24px;
    min-height: 24px;
    border-radius: 50%;
    background-color: rgba(15, 21, 37, 0.5);
    border: 1px solid rgba(0, 153, 255, 0.12);
    box-shadow: none;
    color: #c4cfe0;
    outline-offset: 1px;
}

windowcontrols button:hover {
    background-color: rgba(0, 153, 255, 0.22);
    border-color: rgba(0, 153, 255, 0.45);
    color: #ffffff;
}

windowcontrols button:active {
    background-color: rgba(0, 153, 255, 0.35);
    border-color: rgba(0, 153, 255, 0.6);
    color: #ffffff;
}

windowcontrols button.close {
    background-color: rgba(239, 68, 68, 0.22);
    border-color: rgba(239, 68, 68, 0.4);
    color: #ffffff;
}

windowcontrols button.close:hover {
    background-color: rgba(239, 68, 68, 0.6);
    border-color: rgba(239, 68, 68, 0.85);
    color: #ffffff;
}

windowcontrols button > image {
    -gtk-icon-size: 14px;
    padding: 0;
    margin: 0;
    min-width: 14px;
    min-height: 14px;
    background: none;
    border-radius: 0;
    color: inherit;
}
```
**Result:** Still oval in Nautilus (Files). Slightly less severe than previous attempts but still clearly wider than tall. Buttons in Settings remain proper circles.

---

## Verification Steps Done

1. **CSS IS being loaded in Nautilus** — confirmed because the color rules (red tint on close, dark blue on others) DO apply. So libadwaita is reading my CSS file.
2. **Both CSS locations are in sync** — `/etc/gtk-4.0/gtk.css` and `~/.config/gtk-4.0/gtk.css` have identical MD5 hashes.
3. **Logged out and back in** to ensure all GTK4 apps re-read the CSS — no change.
4. **Closed and reopened Nautilus** multiple times — no change.
5. **GTK Inspector is enabled** via `gsettings set org.gtk.gtk4.Settings.Debug enable-inspector-keybinding true` but I haven't walked the user through using it yet.

---

## The Mystery

**Why is the button wider than tall when my CSS sets `min-width: 24px` and `min-height: 24px` with `padding: 0` and equal margins?**

Theories I've considered:

1. **The icon isn't actually 14px**: `-gtk-icon-size: 14px` may not be honored for symbolic PNG assets. The native 16×16 PNG may force the content to be 16px wide. Combined with some invisible padding, this could make the button wider than 24px horizontally.

2. **Libadwaita's `headerbar.default-decoration` selector has different defaults**: The alternate rule I found (`min-width: 22px; min-height: 22px; padding: 4px`) does have equal min-width/min-height. Maybe Nautilus's headerbar IS `.default-decoration` but my generic rule isn't more specific than the default rule.

3. **`.text-button` class invisibly applied**: Some Discourse user reported that removing `.text-button` fixed sizing. Maybe Nautilus's window control buttons inherit this class and I'm not explicitly overriding its padding.

4. **`hexpand=true` widget property forcing horizontal expansion**: This is a GTK widget property set in .ui files, not CSS. If true, CSS cannot override it.

5. **GJS/libadwaita CSS parser issues with `!important`**: Some attempts with `!important` seemed to produce different results than without, suggesting the parser may have quirks with certain declarations.

6. **The `border-spacing: 3px` on `windowcontrols`**: This affects spacing between button children of the windowcontrols box, not the buttons themselves. Shouldn't affect button shape.

---

## What I Need

**Specific questions for review:**

1. **In GTK4/libadwaita 1.8.4, what makes a `windowcontrols > button` render as wider than tall when the CSS explicitly sets `min-width == min-height` with `padding: 0` and equal margin?** Is there a hidden layout property, internal padding, or widget attribute I'm not accounting for?

2. **What is the correct CSS specificity to reliably override libadwaita's defaults?** Do I need `!important`? Does the generic `windowcontrols button` have higher, lower, or equal specificity to libadwaita's `windowcontrols > button` child selector? If user CSS is loaded last, shouldn't it win without `!important`?

3. **Is there a GTK-specific property (like `-gtk-outline-*`, `-gtk-min-*`, `-gtk-padding-*`) that constrains or forces an exact widget size that I haven't found?**

4. **Are the window control symbolic PNG icons actually resizable via `-gtk-icon-size`?** Or does their native 16px enforce a minimum content size regardless?

5. **Does Nautilus specifically do something different with its headerbar** (e.g., use a custom `AdwHeaderBar` subclass, set widget properties via .ui files that override CSS, use `.default-decoration` class)?

6. **Is `all: unset` or `all: revert` supported in GTK4 CSS for widgets?** Would that help reset all inherited properties before rebuilding?

7. **Can CSS `aspect-ratio` property constrain widget dimensions in GTK4?**

8. **Is there a way to use GTK Inspector (`Ctrl+Shift+D`) from the command line or via a script to dump the widget tree + computed CSS for a specific widget without requiring interactive clicks?**

9. **If CSS truly cannot solve this, what's the alternative?** Patch Nautilus? Use `GTK_THEME=InterGenOS` as an env var? Set `libadwaita` accent color API? Override at the GSettings level?

10. **The fact that the same CSS works in Settings (proper circles) but not Nautilus (ovals) — what's different between those two apps' windowcontrols rendering?**

---

## Attachments

- Latest screenshot showing the current state of Nautilus window controls: see `Screenshot_2026-04-11_21-01-49.png` alongside this file
- Full current `gtk.css` available at: `/etc/gtk-4.0/gtk.css` and `~/.config/gtk-4.0/gtk.css` on the InterGenOS laptop

---

## PRIME DIRECTIVE Context

InterGenOS is a passion project LFS-based distribution built from source. The GNOME Shell theme is a custom "InterGenOS" theme designed around deep navy backgrounds with ECG-blue (`#0099FF`) accents. The rest of the theme is complete and working — this Nautilus window controls issue is the **only** visible flaw. The project philosophy values transparency, understanding the system, and avoiding hacks. So the preferred solution is one that:

1. Works via CSS in `gtk-4.0/gtk.css` (doesn't require patching libadwaita or Nautilus)
2. Doesn't require environment variables or runtime workarounds
3. Doesn't add widget sizing hacks that could break in future GTK versions
4. Is minimal and understandable

If the answer is genuinely "this is a libadwaita quirk and CSS cannot fix it," I want to know that with certainty so we can either accept it or pursue a non-CSS fix knowingly.

Thank you for reviewing.
