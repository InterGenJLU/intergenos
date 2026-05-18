"""User screen — fourth page of the 7-screen flow.

Captures hostname + username + (user_password, root_password, mok_password)
with separate confirmation entries for the user + root passwords. MOK
password is captured here too even though it's only used on EFI installs;
the orchestrator's `to_install_io` only forwards it when truthy, so an
empty mok_password on a BIOS install is a no-op.

Validation: username required, both password pairs must match, neither
user nor root password may be empty. MOK password is intentionally
optional — leaving it empty skips the MOK enrollment queue and the user
can re-enroll via mokutil from the running install.
"""

from gi.repository import Gtk

from installer.backend._validators import validate_hostname, validate_username

from ._base import _ForgePage, _labeled, _toast


class UserPage(_ForgePage):
    tag = "user"
    title = "User"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        heading = Gtk.Label(label="User account")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        self._hostname_entry = Gtk.Entry(placeholder_text="intergenos")
        self._username_entry = Gtk.Entry(placeholder_text="user")
        self._user_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._user_pw_confirm_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._root_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._root_pw_confirm_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self._mok_pw_entry = Gtk.PasswordEntry(show_peek_icon=True)

        box.append(_labeled("Hostname", self._hostname_entry))
        box.append(_labeled("Username", self._username_entry))
        box.append(_labeled("User password", self._user_pw_entry))
        box.append(_labeled("Confirm user password", self._user_pw_confirm_entry))
        box.append(_labeled("Root password", self._root_pw_entry))
        box.append(_labeled("Confirm root password", self._root_pw_confirm_entry))
        box.append(_labeled(
            "MOK enrollment password (one-time, EFI only — leave empty to "
            "skip MOK enrollment). Forge generates a per-machine MOK at "
            "install; this password is what you type at first boot when "
            "MokManager prompts you, to register your MOK with the "
            "firmware. See docs/users/secure-boot-and-mok.md for the full "
            "first-boot walkthrough.",
            self._mok_pw_entry,
        ))

        return box

    def on_load(self, state):
        self._hostname_entry.set_text(state.hostname)
        self._username_entry.set_text(state.username)

    def on_next(self, state):
        state.hostname = self._hostname_entry.get_text() or "intergenos"
        state.username = self._username_entry.get_text().strip()
        state.user_password = self._user_pw_entry.get_text()
        state.user_password_confirm = self._user_pw_confirm_entry.get_text()
        state.root_password = self._root_pw_entry.get_text()
        state.root_password_confirm = self._root_pw_confirm_entry.get_text()
        state.mok_password = self._mok_pw_entry.get_text()

        username_err = validate_username(state.username)
        if username_err:
            _toast(self._window, f"Username: {username_err}")
            return False

        hostname_err = validate_hostname(state.hostname)
        if hostname_err:
            _toast(self._window, f"Hostname: {hostname_err}")
            return False

        if state.user_password != state.user_password_confirm:
            _toast(self._window, "User passwords don't match.")
            return False
        if state.root_password != state.root_password_confirm:
            _toast(self._window, "Root passwords don't match.")
            return False
        if not state.user_password or not state.root_password:
            _toast(self._window, "Both user and root passwords are required.")
            return False

        return True
