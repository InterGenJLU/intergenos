# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Forge GUI — shared base class + UI helpers for the 9-screen flow.

`_ForgePage` is the Adw.NavigationPage subclass every screen extends. It
owns the per-screen frame:

  ┌─────────────────────────────────────────────────────────────┐
  │ [wordmark]    Step ●━○━○━○━○━○━○━○    [windowctl]           │  Adw.HeaderBar
  ├─────────────────────────────────────────────────────────────┤
  │                                                              │
  │  ░░░░░░░░░░░░░░░░ pre-clamp slot (full width) ░░░░░░░░░░░░░  │
  │                                                              │
  │           ┌──────────────────────────────────────┐           │
  │           │                                      │           │
  │           │   <body — built by _build_body()>    │           │  ScrolledWindow >
  │           │                                      │           │   Box >
  │           └──────────────────────────────────────┘           │    Adw.Clamp(720)
  │                                                              │
  ├─────────────────────────────────────────────────────────────┤
  │                                       [ Back ] [ Next ]      │  footer toolbar
  └─────────────────────────────────────────────────────────────┘

Foundation pieces shared by every screen:

  * Two-tier content stack inside the ScrolledWindow:
      1. PRE-CLAMP slot — full window width. Subclasses opt-in by
         overriding `_build_pre_clamp()` (default None = unused). This
         is where edge-to-edge hero banners live: the Welcome page's
         pulse-forge hero, future per-screen hero illustrations, etc.
      2. Adw.Clamp(720) — body content cap. Title / tagline / form
         widgets / nav-content live here so line length stays
         comfortable regardless of window width.

  * `Gtk.ScrolledWindow(NEVER, AUTOMATIC)` wraps both tiers. Screens
    whose total height exceeds the window scroll the whole stack —
    hero scrolls off-screen along with body when the user scrolls
    down. Footer (Back/Next) stays pinned to the window edge.

  * `forge-header-wordmark`: the InterGenOS wordmark in the headerbar's
    left-aligned slot.

  * `forge-step-indicator`: dot+dash glyphs in the headerbar's centered
    title-widget slot, showing which step of the install flow.

Subclasses must:
  * set `tag` (Adw.NavigationView routing identifier) + `title`
  * implement `_build_body()` returning the screen's content widget
  * optionally override `_build_pre_clamp()` returning a full-width
    widget (hero banner, etc.); default returns None.
  * optionally override `on_load(state)` (entry hook) and `on_next(state)`
    (validation + state mutation; return True to advance)
"""

import os

from gi.repository import Adw, GLib, Gtk

from ..state import InstallerState

WORDMARK_PATH = "/usr/share/intergenos/intergenos_wordmark_header.png"


def _build_step_indicator(current_index: int, total: int) -> Gtk.Widget:
    """Build the dot+dash step indicator for the headerbar title slot."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    row.add_css_class("forge-step-indicator")
    for i in range(total):
        dot = Gtk.Label()
        if i == current_index:
            dot.set_label("●")
            dot.add_css_class("forge-step-dot-active")
        else:
            dot.set_label("○")
            dot.add_css_class("forge-step-dot-inactive")
        row.append(dot)
        if i != total - 1:
            dash = Gtk.Label(label="─")
            dash.add_css_class("forge-step-dash")
            row.append(dash)
    return row


class _ForgePage(Adw.NavigationPage):
    """Base class for all 9 screens. See module docstring."""

    tag: str = ""
    title: str = ""

    # Whether this page counts in the step indicator's "X of N" display.
    # Default True for user-decision pages (Welcome through Confirm).
    # ProgressPage + DonePage override to False — those are the install-
    # execution phases, not interactive decision steps, so counting
    # them as "more pages to fill out" was misleading (operator-flagged
    # 2026-05-26 on the Confirm page review). When the current page has
    # in_step_indicator=False, _refresh_step_indicator hides the entire
    # indicator widget — those pages have their own visual treatments.
    in_step_indicator: bool = True

    def __init__(self, window):
        super().__init__(title=self.title, tag=self.tag)
        self._window = window  # reverse-ref so screens can advance via NavigationView

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.add_css_class("forge-header")

        # Wordmark in the headerbar's left slot — brand-presence mirroring
        # the welcomer pattern. Falls back gracefully if the asset is
        # missing (older live ISO without intergen-mark r5+).
        if os.path.exists(WORDMARK_PATH):
            wordmark = Gtk.Picture.new_for_filename(WORDMARK_PATH)
            wordmark.set_content_fit(Gtk.ContentFit.CONTAIN)
            wordmark.set_size_request(170, 46)
            wordmark.add_css_class("forge-header-wordmark")
            header.pack_start(wordmark)

        toolbar.add_top_bar(header)

        # ─── Two-tier content stack ──────────────────────────────────
        # Vertical stack: pre-clamp slot (full width, optional) on top,
        # Adw.Clamp(720) wrapping the body content below.
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        pre_clamp = self._build_pre_clamp()
        if pre_clamp is not None:
            pre_clamp.set_halign(Gtk.Align.FILL)
            pre_clamp.set_hexpand(True)
            stack.append(pre_clamp)

        body = self._build_body()
        # Body margins: tighter top when there's a pre-clamp hero (the
        # hero is right above and visually carries its own breathing
        # room), normal otherwise. Bottom margin always conservative —
        # footer toolbar provides the visual rest below.
        body.set_margin_top(16 if pre_clamp is not None else 32)
        body.set_margin_bottom(16 if pre_clamp is not None else 32)
        body.set_margin_start(0)
        body.set_margin_end(0)
        body.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(720)
        clamp.set_tightening_threshold(640)
        clamp.set_child(body)
        clamp.set_vexpand(True)
        stack.append(clamp)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(stack)

        toolbar.set_content(scroller)

        # Footer: Back + Next on every screen (subclasses can override visibility).
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer.set_halign(Gtk.Align.END)
        footer.set_margin_top(12)
        footer.set_margin_bottom(16)
        footer.set_margin_end(24)
        footer.add_css_class("forge-footer")

        self.back_button = Gtk.Button(label="Back")
        self.back_button.add_css_class("forge-nav-button")
        self.back_button.connect("clicked", self._on_back_clicked)
        footer.append(self.back_button)

        self.next_button = Gtk.Button(label="Next")
        self.next_button.add_css_class("suggested-action")
        self.next_button.add_css_class("forge-nav-button")
        self.next_button.connect("clicked", self._on_next_clicked)
        footer.append(self.next_button)

        toolbar.add_bottom_bar(footer)

        self.set_child(toolbar)

        # Step indicator: rendered into the headerbar's centered title-widget
        # slot. The index lookup needs the screen to be registered in
        # window._screens — which doesn't happen until AFTER this __init__
        # returns (window does `[cls(self) for cls in SCREEN_ORDER]`).
        # Schedule the indicator render onto the idle queue; by the time
        # the GLib main loop pulls it, _screens is populated. on_load() is
        # ALSO a refresh point (in case _screens ever changes), but we no
        # longer rely on subclasses calling super().on_load() — the
        # indicator is wired up unconditionally here.
        self._header = header
        self._step_indicator = None
        GLib.idle_add(self._refresh_step_indicator)

    def _refresh_step_indicator(self):
        """Re-render the step indicator with the current screen's index.

        Counts only screens with `in_step_indicator=True` (Welcome through
        Confirm — the user-decision pages). ProgressPage + DonePage have
        the flag set False and don't appear in the indicator; on those
        pages the entire indicator is hidden.

        Returns False so GLib.idle_add doesn't re-schedule this callback
        (we want a single render, not a polling loop).
        """
        try:
            screens = self._window._screens
            interactive = [
                s for s in screens
                if getattr(s, "in_step_indicator", True)
            ]
            if self not in interactive:
                # Install-execution phase — no step indicator at all.
                self._header.set_title_widget(None)
                self._step_indicator = None
                return False
            idx = interactive.index(self)
            total = len(interactive)
        except (AttributeError, ValueError):
            return False
        indicator = _build_step_indicator(idx, total)
        self._header.set_title_widget(indicator)
        self._step_indicator = indicator
        return False

    # ---- overrides ----

    def _build_body(self) -> Gtk.Widget:
        raise NotImplementedError

    def _build_pre_clamp(self) -> Gtk.Widget | None:  # noqa: UP006 (Py3.10+ union ok)
        """Optional full-window-width slot above the Clamp.

        Default: None (no pre-clamp content; layout collapses to the
        Clamp-only shape). Subclasses (e.g. WelcomePage) override to
        return a Gtk.Widget — typically a hero banner Gtk.Picture —
        that should render edge-to-edge regardless of window width.
        """
        return None

    def on_load(self, state: InstallerState):  # pragma: no cover  (subclass hook)
        """Called when this page becomes visible. Default: refresh the step
        indicator. Subclasses overriding this should call super().on_load(state).
        """
        self._refresh_step_indicator()
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
    """Return a vertical box: small dim label above + widget below."""
    container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    label = Gtk.Label(label=label_text)
    label.set_xalign(0)
    label.add_css_class("dim-label")
    container.append(label)
    container.append(widget)
    return container


def _toast(window, text: str):
    """Surface a transient message via the window's overlay."""
    if getattr(window, "toast_overlay", None) is None:
        toasts = getattr(window, "test_toasts", None)
        if toasts is None:
            toasts = []
            try:
                window.test_toasts = toasts
            except AttributeError:
                return
        toasts.append(text)
        return
    toast = Adw.Toast(title=text, timeout=4)
    window.toast_overlay.add_toast(toast)
