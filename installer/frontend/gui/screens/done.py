"""Done screen — final page of the 7-screen flow.

Adw.StatusPage with success/failure messaging. Back hidden. The action
button's label + behaviour depends on install outcome:
  * success → "Reboot now" — invokes `systemctl reboot` via subprocess.
  * failure → "Quit" — closes the window; user retries via live media.

The Progress screen sets `state.install_failed` + `state.install_error_message`
on failure so we can render an error path here too.
"""

import subprocess

from gi.repository import Adw, Gtk

from ._base import _ForgePage, _toast


class DonePage(_ForgePage):
    tag = "done"
    title = "Done"

    def _build_body(self) -> Gtk.Widget:
        self._status = Adw.StatusPage()
        # Defaults — overwritten in on_load based on install outcome.
        self._status.set_icon_name("emblem-ok-symbolic")
        self._status.set_title("Install complete")
        self._status.set_description(
            "Remove the install media and click Reboot now. On EFI systems "
            "you'll be prompted to enroll the InterGenOS vendor cert via "
            "MokManager on first boot."
        )
        return self._status

    def __init__(self, window):
        super().__init__(window)
        self.back_button.set_visible(False)
        self.next_button.set_label("Reboot now")
        # Default action — overridden in on_load for the failure path.
        self._on_success_path = True

    def on_load(self, state):
        if state.install_failed:
            self._on_success_path = False
            self._status.set_icon_name("dialog-error-symbolic")
            self._status.set_title("Install failed")
            err = state.install_error_message or "(no detail captured)"
            self._status.set_description(
                f"The install did not complete cleanly:\n\n{err}\n\n"
                "Reboot to the live media to retry, or open a terminal in the "
                "live session to investigate."
            )
            self.next_button.set_label("Quit")
        else:
            self._on_success_path = True
            self._status.set_icon_name("emblem-ok-symbolic")
            self._status.set_title("Install complete")
            self._status.set_description(
                "Remove the install media and click Reboot now. On EFI "
                "systems you'll be prompted to enroll the InterGenOS "
                "vendor cert via MokManager on first boot."
            )
            self.next_button.set_label("Reboot now")

    def _on_next_clicked(self, _button):  # noqa: override
        if not self._on_success_path:
            self._window.close()
            return

        # Success path: trigger system reboot. systemctl reboot returns
        # immediately after queueing the reboot job with systemd; the
        # actual shutdown sequence runs out from under us. We toast first
        # so the user sees acknowledgment if there's a brief lag before
        # the session tears down.
        _toast(self._window, "Rebooting…")
        try:
            subprocess.Popen(
                ["systemctl", "reboot"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as e:
            _toast(self._window,
                   f"Could not invoke reboot: {e}. "
                   "Use the system menu or run `systemctl reboot` from a terminal.")
