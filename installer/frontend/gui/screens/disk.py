"""Disk screen — third page of the 7-screen flow.

Disk-before-User per Q-GUI-SCREENS=7 modal pattern (Calamares / Pop!_OS /
Ubiquity all do this): destructive disk decision before user account so
the user is never confused about WHICH machine they're configuring an
account for.

Phase 6 scope: text entry for the device path + a confirm-destructive
checkbox. Real disk-detection (lsblk parse → table widget with size +
model + filesystem) lands in a later visual-polish phase. The TUI's
`prompt_install_io` does the same for now (with a `disks.list_candidates()`
fallback to text input).
"""

from gi.repository import Gtk

from ._base import _ForgePage, _labeled, _toast


class DiskPage(_ForgePage):
    tag = "disk"
    title = "Disk"

    def _build_body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        heading = Gtk.Label(label="Choose a disk")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        box.append(heading)

        warning = Gtk.Label(
            label="The selected disk WILL BE ERASED. Real disk-detection + "
                  "partition-view widget lands in a later phase. For now, "
                  "type the device path."
        )
        warning.set_wrap(True)
        warning.set_xalign(0)
        warning.add_css_class("warning")
        box.append(warning)

        self._disk_entry = Gtk.Entry(placeholder_text="/dev/sda")
        box.append(_labeled("Target disk", self._disk_entry))

        self._confirm_check = Gtk.CheckButton(
            label="I understand all data on this disk will be erased."
        )
        box.append(self._confirm_check)

        return box

    def on_load(self, state):
        if state.target_disk:
            self._disk_entry.set_text(state.target_disk)
        self._confirm_check.set_active(state.confirm_destructive)

    def on_next(self, state):
        path = self._disk_entry.get_text().strip()
        if not path:
            _toast(self._window, "Please enter a target disk path.")
            return False
        if not self._confirm_check.get_active():
            _toast(self._window, "Confirm the destructive operation to continue.")
            return False
        state.target_disk = path
        state.confirm_destructive = True
        return True
