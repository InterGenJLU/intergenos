"""Done screen — final page of the 7-screen flow.

Adw.StatusPage with success/failure messaging. Back hidden, Next becomes
"Quit" — clicking closes the window. The Progress screen sets
`state.install_failed` + `state.install_error_message` on failure so we
can render an error path here too.
"""

from gi.repository import Adw, Gtk

from ._base import _ForgePage


class DonePage(_ForgePage):
    tag = "done"
    title = "Done"

    def _build_body(self) -> Gtk.Widget:
        self._status = Adw.StatusPage()
        # Defaults — overwritten in on_load based on install outcome.
        self._status.set_icon_name("emblem-ok-symbolic")
        self._status.set_title("Install complete")
        self._status.set_description(
            "Reboot, remove the install media, and (if EFI) follow the "
            "MokManager prompts to enroll the InterGenOS vendor cert."
        )
        return self._status

    def __init__(self, window):
        super().__init__(window)
        self.back_button.set_visible(False)
        self.next_button.set_label("Quit")

    def on_load(self, state):
        if state.install_failed:
            self._status.set_icon_name("dialog-error-symbolic")
            self._status.set_title("Install failed")
            err = state.install_error_message or "(no detail captured)"
            self._status.set_description(
                f"The install did not complete cleanly:\n\n{err}\n\n"
                "Reboot to the live media to retry, or open a terminal in the "
                "live session to investigate."
            )
        else:
            self._status.set_icon_name("emblem-ok-symbolic")
            self._status.set_title("Install complete")
            self._status.set_description(
                "Reboot, remove the install media, and (if EFI) follow the "
                "MokManager prompts to enroll the InterGenOS vendor cert."
            )

    def _on_next_clicked(self, _button):  # noqa: override
        self._window.close()
