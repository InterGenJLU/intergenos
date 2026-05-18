"""Confirm screen — fifth page of the 7-screen flow.

Last chance before the destructive install begins. Renders a summary of
all collected state from prior screens, plus a warning, and changes the
Next button label to "Install" so the user knows pressing it is committing.

Validation: defers to InstallerState.is_ready_for_install() — that method
is the single audit point for "did the user actually fill everything in."
If it returns False, we toast and stay on this page rather than transition
to Progress with incomplete state.
"""

from gi.repository import Gtk

from ._base import _ForgePage, _toast


class ConfirmPage(_ForgePage):
    tag = "confirm"
    title = "Confirm"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        heading = Gtk.Label(label="Confirm install")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._summary_label = Gtk.Label(label="(populated at on_load)")
        self._summary_label.set_wrap(True)
        self._summary_label.set_xalign(0)
        self._summary_label.set_selectable(True)
        box.append(self._summary_label)

        warning = Gtk.Label(
            label="Clicking Install starts the install. This cannot be undone."
        )
        warning.add_css_class("warning")
        warning.set_wrap(True)
        warning.set_xalign(0)
        box.append(warning)

        return box

    def on_load(self, state):
        if state.luks_enabled:
            extras = []
            if state.tpm2_enabled:
                extras.append("TPM2-EXPERIMENTAL")
            if state.fido2_enabled:
                extras.append("FIDO2-EXPERIMENTAL")
            tail = f" + {' + '.join(extras)}" if extras else ""
            luks_line = f"ENABLED{tail}"
        else:
            luks_line = "disabled"
        text = (
            f"Disk: {state.target_disk}\n"
            f"Full-disk encryption (LUKS): {luks_line}\n"
            f"Hostname: {state.hostname}\n"
            f"Username: {state.username}\n"
            f"Keymap: {state.keymap}\n"
            f"Locale: {state.locale}\n"
            f"Timezone: {state.timezone}\n"
            f"Package groups: {', '.join(state.package_groups)}\n"
            f"Secure Boot enrollment: "
            f"{'queued' if state.mok_password else 'skipped'}"
        )
        self._summary_label.set_label(text)
        self.next_button.set_label("Install")

    def on_next(self, state):
        if not state.is_ready_for_install():
            errors = state.validation_errors()
            if errors:
                _toast(self._window, f"Cannot install: {errors[0]}")
            else:
                _toast(self._window, "Some required fields are missing — go back.")
            return False
        return True
