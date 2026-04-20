# Window Controls Ovals — Review Synthesis & Solution

**Date:** April 11, 2026
**Status:** Diagnosis confirmed by three independent AI reviews (Gemini, ChatGPT, DeepSeek)

---

## Unanimous Diagnosis

All three reviewers reached the **same conclusion**:

**This is a GTK4 layout engine limitation, not a CSS bug.**

ChatGPT's crisp summary:
> "You are winning the cascade. You are losing the layout phase. CSS affects style, not final geometry in GTK4."

### Why Settings works but Nautilus doesn't

- **Settings** uses stock `AdwHeaderBar` with `.default-decoration` path, which applies equal `min-width: 22px; min-height: 22px; padding: 4px`
- **Nautilus** uses a custom headerbar composition with additional children (pathbar, search entry, stacked widgets). This changes baseline alignment, height negotiation, and spacing distribution
- The button's height gets constrained by the headerbar's layout box, but width expands freely to satisfy content + internal spacing
- **CSS cannot override** `hexpand`, `vexpand`, `valign`, `halign`, `baseline alignment`, or parent-imposed dimensions — those are widget properties set at the `.ui` level

### What CAN be controlled via CSS

- Button's minimum dimensions (floor only, no ceiling)
- Button's padding/margin
- Image child's size and styling
- Cascade-level style properties (color, background, border-radius, box-shadow)

### What CANNOT be controlled via CSS

- `max-width` / `max-height` on widgets (not supported in GTK4 CSS)
- `aspect-ratio` (not supported)
- `all: unset` / `all: revert` (not supported)
- Widget-level `hexpand`, `valign`, etc. (`.ui` file only)
- Final allocated geometry (determined by layout engine after cascade)

---

## The CSS-Only Solution (unified from all 3 reviews)

**Strategy:** Make the button a completely invisible wrapper. Render the entire visible circle on the IMAGE child using explicit equal dimensions + padding.

**Why this works:** The image is a leaf widget with no layout children to negotiate with. When it has explicit `min-width == min-height`, GTK's layout engine honors those dimensions directly. The button's allocated space may be oval due to layout constraints, but the image inside remains a perfect square, and since the button has no background, no border, and no shadow, you don't see the oval — only the image circle.

### The CSS (to replace current windowcontrols block)

```css
/* Button: completely invisible, shrinks freely */
windowcontrols button {
    padding: 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
    background: none;
    border: none;
    box-shadow: none;
    color: #c4cfe0;
}

/* Image: the ENTIRE visible circle renders here */
windowcontrols button > image {
    min-width: 20px;
    min-height: 20px;
    padding: 6px;
    margin: 6px 3px;
    border-radius: 999px;
    background-color: rgba(15, 21, 37, 0.5);
    border: 1px solid rgba(0, 153, 255, 0.12);
    color: inherit;
}

windowcontrols button:hover > image {
    background-color: rgba(0, 153, 255, 0.22);
    border-color: rgba(0, 153, 255, 0.45);
    color: #ffffff;
}

windowcontrols button:active > image {
    background-color: rgba(0, 153, 255, 0.35);
    border-color: rgba(0, 153, 255, 0.6);
    color: #ffffff;
}

windowcontrols button.close > image {
    background-color: rgba(239, 68, 68, 0.22);
    border-color: rgba(239, 68, 68, 0.4);
    color: #ffffff;
}

windowcontrols button.close:hover > image {
    background-color: rgba(239, 68, 68, 0.6);
    border-color: rgba(239, 68, 68, 0.85);
    color: #ffffff;
}
```

### Math

- Image min: 20×20
- Image padding: 6px all sides
- Total visible circle: 20 + 12 = **32×32 square** → perfect circle at `border-radius: 999px`
- Button: `min-width: 0, min-height: 0, padding: 0, margin: 0` — collapses to wrap the image
- Any layout-enforced oval on the button is invisible because `background: none; border: none`

### Key differences from my failed attempts

1. **No `!important`** — all three reviewers noted GJS/libadwaita parser has quirks with `!important` on certain properties. Removing it often helps.
2. **Button explicitly `min-width: 0; min-height: 0`** — I was setting `padding: 0` but not zeroing the min dimensions, so libadwaita's default `min-width: 24px` was still applied on top of my overrides.
3. **Margin on the image, not the button** — separates the visible circle from neighboring controls via image margin; button stays collapsed.
4. **Image has explicit equal dimensions** — GTK honors this directly because image is a leaf widget.

---

## Alternative (if CSS truly cannot solve it)

- **Patch Nautilus's `.ui` file** to set `hexpand="False"` on the windowcontrols box (requires building Nautilus from source — not aligned with PRIME DIRECTIVE of avoiding hacks)
- **Accept the ~5px oval** as a Nautilus-specific layout quirk

---

## Reviewer-specific additions worth noting

### Gemini
- Suggested `border-spacing: 0` on the `windowcontrols` container
- Confirmed `-gtk-icon-size` is honored but PNGs still have intrinsic 16px metrics
- Noted the `.text-button` theory is unlikely for window controls specifically
- Offered to verify via GTK Inspector "Layout" tab → `Horizontal Alignment` field (if it shows `FILL`, confirmed culprit)

### DeepSeek
- Alternative approach: make image FILL the entire button area (`-gtk-icon-size: 24px` matching button size, not smaller)
- Confirmed: `all: unset` not supported, `aspect-ratio` not supported
- Confirmed: Nautilus uses `NautilusHeaderBar` subclass (not direct `AdwHeaderBar`)

### ChatGPT
- Cleanest diagnosis of the cascade-vs-layout distinction
- Option A (implemented above) is the "best CSS-only approximation"
- Noted `GTK_DEBUG=layout` as an additional debug tool
- Clear statement: "You are not missing a property. You are hitting the boundary between CSS styling and GTK layout engine."

---

## Action

Implementing ChatGPT's Option A (image-as-entire-visible-button) synthesized with Gemini's image padding values and DeepSeek's explicit dimensions.
