"""Forge GUI — InterGenOS visual-language CSS layer.

Per `docs/VISUAL_LANGUAGE.md`:
  * § 4 palette: `--bg-void` `#050810`, `--accent` `#0099FF`, three-tier text
                 (`#e2e8f0` / `#7a8ba8` / `#3d4f6a`), border alpha progression
                 (0.08 → 0.22 → 0.40)
  * § 5 typography: Inter primary, Cantarell fallback; weights are semantic
                    (400 body, 500 light emphasis, 600 active/heading, 700 title)
  * § 7 corner-radius scale: 6/8/10/12/14/16/18/20/22/24/26/9999 — never invent
                             new radii
  * § 10 interaction: borders are inert by default and intensify on
                      hover/active/focus

Phase 6 scope: CSS-layer pass only — palette tokens + structural border
alphas + typography + corner-radius scale. Full glow protocol with
per-widget hover-corona + 150-400ms ease-out transitions is its own
visual-polish phase post-bootable-ISO. We don't pull rendering
correctness into Phase 6; this CSS is best-effort baseline so the
installer doesn't render in upstream-default GTK/Adwaita colours.

Apply via `apply_intergenos_style()` once at application activate.
"""

from gi.repository import Gdk, Gtk


_INTERGENOS_CSS = """
/*
 * InterGenOS palette tokens
 *
 * GTK CSS doesn't support custom-properties (--bg-void style) the way web
 * CSS does — the values below are inlined directly. Keep in sync with
 * VISUAL_LANGUAGE.md § 4 if you touch them.
 *
 * --bg-void      #050810
 * --bg-surface   #0a0e1a
 * --bg-card      #0f1525
 * --bg-view      #080c18
 * --bg-sidebar   #030609
 * --accent       #0099FF
 * --text         #e2e8f0
 * --text-dim     #7a8ba8
 */

window,
window.background,
.background {
  background-color: #050810;
  color: #e2e8f0;
  font-family: 'Inter', 'Cantarell', sans-serif;
}

headerbar,
.titlebar {
  background-color: #0a0e1a;
  color: #e2e8f0;
  border-bottom: 1px solid rgba(0, 153, 255, 0.08);
}

button {
  background-color: rgba(15, 21, 37, 0.5);
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 8px;
  padding: 6px 16px;
  font-weight: 500;
}

button:hover {
  border-color: rgba(0, 153, 255, 0.22);
  background-color: rgba(0, 153, 255, 0.06);
  color: #ffffff;
}

button:active,
button:checked {
  border-color: rgba(0, 153, 255, 0.40);
  background-color: rgba(0, 153, 255, 0.12);
  color: #ffffff;
}

button:disabled {
  background-color: rgba(15, 21, 37, 0.25);
  color: #3d4f6a;
  border-color: rgba(0, 153, 255, 0.04);
}

button.suggested-action {
  background-color: rgba(0, 153, 255, 0.18);
  color: #ffffff;
  border-color: rgba(0, 153, 255, 0.40);
  font-weight: 600;
}

button.suggested-action:hover {
  background-color: rgba(0, 153, 255, 0.30);
  border-color: rgba(0, 153, 255, 0.65);
}

entry,
passwordentry,
.entry {
  background-color: #0f1525;
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 10px;
  padding: 6px 10px;
  caret-color: #0099FF;
}

entry:focus,
passwordentry:focus {
  border-color: rgba(0, 153, 255, 0.40);
  background-color: #0a0e1a;
}

entry:disabled,
passwordentry:disabled {
  color: #3d4f6a;
}

label {
  color: #e2e8f0;
}

label.title-1 {
  font-weight: 700;
  font-size: 1.6em;
  color: #e2e8f0;
}

label.title-2 {
  font-weight: 600;
  font-size: 1.3em;
}

label.dim-label,
.dim-label {
  color: #7a8ba8;
  font-size: 0.85em;
}

label.warning,
.warning {
  color: #f59e0b;
}

label.error,
.error {
  color: #ef4444;
}

label.success,
.success {
  color: #10b981;
}

checkbutton check,
checkbutton radio {
  border: 1px solid rgba(0, 153, 255, 0.22);
  background-color: #0f1525;
  border-radius: 4px;
}

checkbutton:checked check,
checkbutton:checked radio {
  background-color: #0099FF;
  border-color: #0099FF;
}

progressbar {
  color: #e2e8f0;
}

progressbar trough {
  background-color: #0f1525;
  border: 1px solid rgba(0, 153, 255, 0.08);
  border-radius: 9999px;
  min-height: 12px;
}

progressbar progress {
  background-color: #0099FF;
  border-radius: 9999px;
  min-height: 12px;
}

statuspage > scrolledwindow > viewport > box > .icon {
  color: #0099FF;
}

statuspage .title {
  color: #e2e8f0;
  font-weight: 700;
}

statuspage .description {
  color: #7a8ba8;
}

toast {
  background-color: #0a0e1a;
  color: #e2e8f0;
  border: 1px solid rgba(0, 153, 255, 0.22);
  border-radius: 14px;
}
"""


def apply_intergenos_style(display=None):
    """Install the InterGenOS CSS provider on the GDK display.

    Called once at application activate; the provider is registered with
    APPLICATION priority so it overrides theme defaults but stays below
    user-supplied gtk.css overrides.

    Returns the provider so callers can hold a reference (preventing GC)
    and/or replace it later. Returns None if no display is available
    (headless smoke-test path).

    Compatibility: GTK4.12+ ships `Gtk.CssProvider.load_from_string` while
    older GTK4 (4.0-4.11) only has `load_from_data(bytes, length)`. We
    try the new API first, fall back to the old one.
    """
    if display is None:
        display = Gdk.Display.get_default()
    if display is None:
        return None

    provider = Gtk.CssProvider()
    try:
        provider.load_from_string(_INTERGENOS_CSS)
    except (AttributeError, TypeError):
        css_bytes = _INTERGENOS_CSS.encode("utf-8")
        provider.load_from_data(css_bytes, len(css_bytes))

    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return provider
