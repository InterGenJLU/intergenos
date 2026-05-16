"""Forge GUI — shared base class + UI helpers for the 7-screen flow.

`_ForgePage` is the Adw.NavigationPage subclass every screen extends. It
owns:
  * the toolbar + headerbar layout
  * the back / next footer buttons + their click→on_next/on_back wiring
  * the body-content margins (24/24/48/48 — generous internal padding per
    VISUAL_LANGUAGE.md § 9 "negative space is active")

Subclasses must:
  * set `tag` (Adw.NavigationView routing identifier) + `title`
  * implement `_build_body()` returning the screen's content widget
  * optionally override `on_load(state)` (entry hook) and `on_next(state)`
    (validation + state mutation; return True to advance)

`_labeled` and `_toast` are extracted here so screens can stay focused on
their content.
"""

from gi.repository import Adw, Gtk

from ..state import InstallerState


class _ForgePage(Adw.NavigationPage):
    """Base class for all 7 screens. See module docstring."""

    tag: str = ""
    title: str = ""

    def __init__(self, window):
        super().__init__(title=self.title, tag=self.tag)
        self._window = window  # reverse-ref so screens can advance via NavigationView

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        body = self._build_body()
        body.set_margin_top(24)
        body.set_margin_bottom(24)
        body.set_margin_start(48)
        body.set_margin_end(48)
        body.set_vexpand(True)
        toolbar.set_content(body)

        # Footer: Back + Next on every screen (subclasses can override visibility).
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer.set_halign(Gtk.Align.END)
        footer.set_margin_top(12)
        footer.set_margin_bottom(12)
        footer.set_margin_end(24)

        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", self._on_back_clicked)
        footer.append(self.back_button)

        self.next_button = Gtk.Button(label="Next")
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", self._on_next_clicked)
        footer.append(self.next_button)

        toolbar.add_bottom_bar(footer)

        self.set_child(toolbar)

    # ---- overrides ----

    def _build_body(self) -> Gtk.Widget:
        raise NotImplementedError

    def on_load(self, state: InstallerState):  # pragma: no cover  (subclass hook)
        """Called when this page becomes visible. Default: no-op."""
        return None

    def on_next(self, state: InstallerState) -> bool:
        """Return True to advance, False to stay (e.g. validation failure).
        Default: advance."""
        return True

    # ---- private handlers ----

    def _on_back_clicked(self, _button):
        self._window.navigate_back()

    def _on_next_clicked(self, _button):
        if self.on_next(self._window.state):
            self._window.navigate_next()


def _labeled(label_text: str, widget: Gtk.Widget) -> Gtk.Widget:
    """Return a vertical box: small dim label above + widget below.

    Used in screens that prompt for typed input (keymap/locale/timezone,
    disk path, hostname, username, passwords). The dim-label class is
    styled by the InterGenOS CSS layer (style.py) — slate text against
    the void background.
    """
    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label = Gtk.Label(label=label_text)
    label.set_xalign(0)
    label.add_css_class("dim-label")
    container.append(label)
    container.append(widget)
    return container


def _toast(window, text: str):
    """Surface a transient message via the window's overlay.

    Headless test surface: when `window.toast_overlay` is None (smoke
    test path that constructs screens without a display), the message
    appends to `window.test_toasts` instead of writing to stdout. Tests
    assert against `window.test_toasts` directly; no stdout pollution,
    no print-capture fixture needed. The attribute is auto-created on
    first toast so existing test fixtures don't need to pre-initialize.
    """
    if getattr(window, "toast_overlay", None) is None:
        toasts = getattr(window, "test_toasts", None)
        if toasts is None:
            toasts = []
            try:
                window.test_toasts = toasts
            except AttributeError:
                # Read-only window proxy (unlikely but possible in some
                # mock fixtures) — silently drop rather than crash.
                return
        toasts.append(text)
        return
    toast = Adw.Toast(title=text, timeout=4)
    window.toast_overlay.add_toast(toast)
