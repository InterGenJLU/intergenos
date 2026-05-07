"""Keyboard / Locale / Timezone — second page of the 7-screen flow.

Captures `keymap` + `locale` + `timezone` into the shared InstallerState.
For Phase 6 these are plain text entries; later phases may surface a
keymap chooser, locale list (mirroring TUI.LOCALES), and IANA timezone
picker. Defaults match the TUI walking sequence proposal so a user who
hits Next through this screen lands on en_US.UTF-8 / UTC / us keymap.
"""

from gi.repository import Gtk

from ._base import _ForgePage, _labeled


class KeyboardLocalePage(_ForgePage):
    tag = "keyboard_locale"
    title = "Keyboard, Locale, Timezone"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Keyboard, Locale, and Timezone")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        intro = Gtk.Label(
            label="Pre-filled with the InterGenOS defaults. Hit Next if these "
                  "are correct, or override before continuing."
        )
        intro.add_css_class("dim-label")
        intro.set_wrap(True)
        intro.set_xalign(0)
        box.append(intro)

        self._keymap_entry = Gtk.Entry(placeholder_text="us")
        self._locale_entry = Gtk.Entry(placeholder_text="en_US.UTF-8")
        self._tz_entry = Gtk.Entry(placeholder_text="UTC")

        box.append(_labeled("Keymap (xkb code, e.g. us, gb, de)", self._keymap_entry))
        box.append(_labeled("Locale (e.g. en_US.UTF-8)", self._locale_entry))
        box.append(_labeled("Timezone (IANA tz, e.g. America/Chicago)", self._tz_entry))

        return box

    def on_load(self, state):
        self._keymap_entry.set_text(state.keymap)
        self._locale_entry.set_text(state.locale)
        self._tz_entry.set_text(state.timezone)

    def on_next(self, state):
        state.keymap = self._keymap_entry.get_text() or "us"
        state.locale = self._locale_entry.get_text() or "en_US.UTF-8"
        state.timezone = self._tz_entry.get_text() or "UTC"
        return True
