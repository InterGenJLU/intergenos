"""Welcome screen — first page of the 7-screen flow.

Single Adw.StatusPage; no input collected, just acknowledged. Back button
hidden (this is the first page; nowhere to go back to).
"""

from gi.repository import Adw, Gtk

from ._base import _ForgePage


class WelcomePage(_ForgePage):
    tag = "welcome"
    title = "Welcome"

    def _build_body(self) -> Gtk.Widget:
        page = Adw.StatusPage()
        page.set_icon_name("system-software-install-symbolic")
        page.set_title("Welcome to InterGenOS")
        page.set_description(
            "Forge will guide you through the installation in seven short steps. "
            "You can go back at any time before the install begins."
        )
        return page

    def __init__(self, window):
        super().__init__(window)
        self.back_button.set_visible(False)

    def on_load(self, state):
        state.welcome_acked = True
