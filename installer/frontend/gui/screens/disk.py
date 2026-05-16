"""Disk screen — third page of the 7-screen flow.

Disk-before-User per Q-GUI-SCREENS=7 modal pattern (Calamares / Pop!_OS /
Ubiquity all do this): destructive disk decision before user account so
the user is never confused about WHICH machine they're configuring an
account for.

Real disk-detection. Reads `backend.disks.detect_disks()` and renders
the result as a `Gtk.ListBox` of `path | size | model | removable-flag`
rows. Selection drives `state.target_disk`. If detection returns empty
(dev/test environments without a writable disk), falls back to a free-
form text entry so manual override still works. Path normalization (a
typed `sda` becomes `/dev/sda`) and block-device existence check are
applied regardless of which input path the user took, before commit.

The confirm-destructive checkbox stays — the goal is to make
wrong-disk wipe HARDER to do accidentally, not impossible.
"""

from pathlib import Path

from gi.repository import Gtk

from installer.backend.disks import detect_disks

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
            label="The selected disk WILL BE ERASED. Pick from the list "
                  "below; the disk where this live ISO is running is "
                  "filtered out automatically."
        )
        warning.set_wrap(True)
        warning.set_xalign(0)
        warning.add_css_class("warning")
        box.append(warning)

        # Detected-disks list. Populated in on_load so each page entry
        # gets fresh enumeration (USB hot-plug, etc.).
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_row_selected)

        list_frame = Gtk.Frame()
        list_frame.set_child(self._listbox)
        box.append(list_frame)

        # Manual-override row, surfaced as a stable secondary path. Hidden
        # when detect_disks() returned a usable list; shown when the list
        # is empty (dev/test environment) or when the user explicitly
        # wants to type a path the detector missed.
        self._manual_toggle = Gtk.CheckButton(
            label="Type a disk path manually (advanced / fallback)"
        )
        self._manual_toggle.connect("toggled", self._on_manual_toggled)
        box.append(self._manual_toggle)

        self._manual_entry = Gtk.Entry(placeholder_text="/dev/sda or /dev/nvme0n1")
        self._manual_entry.set_visible(False)
        box.append(_labeled("Disk path", self._manual_entry))

        self._confirm_check = Gtk.CheckButton(
            label="I understand all data on the selected disk will be erased."
        )
        box.append(self._confirm_check)

        return box

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_row_label(self, disk):
        """Single human-readable line per detected disk.

        Example: `/dev/nvme0n1 — 500.0 GiB — Samsung SSD 980 — fixed`
        """
        removable_tag = "removable" if disk.removable else "fixed"
        return (
            f"{disk.path}  —  {disk.size_human}  —  "
            f"{disk.model}  —  {removable_tag}"
        )

    def _populate_list(self):
        """Refresh the ListBox from detect_disks()."""
        # Drop prior children — gtk4 idiom.
        child = self._listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._listbox.remove(child)
            child = next_child

        self._detected = detect_disks()
        for disk in self._detected:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=self._format_row_label(disk))
            label.set_xalign(0)
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            label.set_margin_start(12)
            label.set_margin_end(12)
            row.set_child(label)
            # Stash the Disk object on the row for retrieval at on_next.
            row._disk = disk
            self._listbox.append(row)

        if not self._detected:
            # No disks → force manual entry path.
            self._manual_toggle.set_active(True)
            self._manual_toggle.set_sensitive(False)
            self._manual_toggle.set_label(
                "Type a disk path manually (no disks auto-detected)"
            )
        else:
            self._manual_toggle.set_sensitive(True)
            self._manual_toggle.set_label(
                "Type a disk path manually (advanced / fallback)"
            )

    def _on_row_selected(self, _listbox, row):
        # Selecting a row clears any manual entry so the two inputs
        # cannot disagree.
        if row is not None and self._manual_entry.get_text():
            self._manual_entry.set_text("")

    def _on_manual_toggled(self, toggle):
        active = toggle.get_active()
        self._manual_entry.set_visible(active)
        if active:
            # Switching to manual entry deselects the list.
            self._listbox.unselect_all()

    def _normalize_path(self, raw):
        """Turn a user-typed disk path into a canonical /dev/<name>.

        - Strips whitespace.
        - Prepends `/dev/` if absent.
        - Rejects path-traversal (`..`) and any path that resolves
          outside `/dev/`.
        """
        path = (raw or "").strip()
        if not path:
            return None
        if not path.startswith("/dev/"):
            # Reject embedded slashes that would create a non-/dev/ path.
            if "/" in path:
                return None
            path = f"/dev/{path}"
        # Path-traversal guard — `/dev/../etc/shadow` would normalize via
        # Path.resolve() to outside /dev/; reject up front.
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return None
        if not str(resolved).startswith("/dev/"):
            return None
        return str(resolved)

    # ------------------------------------------------------------------
    # _ForgePage hooks
    # ------------------------------------------------------------------

    def on_load(self, state):
        self._populate_list()

        # If state already has a target_disk (back-then-forward), try to
        # re-select the matching row; if not in the list, fall through
        # to manual entry pre-filled with the prior value.
        if state.target_disk:
            matched = False
            child = self._listbox.get_first_child()
            while child is not None:
                if getattr(child, "_disk", None) and child._disk.path == state.target_disk:
                    self._listbox.select_row(child)
                    matched = True
                    break
                child = child.get_next_sibling()
            if not matched:
                self._manual_toggle.set_active(True)
                self._manual_entry.set_text(state.target_disk)

        self._confirm_check.set_active(state.confirm_destructive)

    def on_next(self, state):
        # Resolve target_disk from list selection (preferred) or manual entry.
        selected = self._listbox.get_selected_row()
        if self._manual_toggle.get_active():
            target = self._normalize_path(self._manual_entry.get_text())
            if target is None:
                _toast(self._window,
                       "Please enter a valid /dev/* disk path "
                       "(e.g. /dev/sda or /dev/nvme0n1).")
                return False
        elif selected is not None and getattr(selected, "_disk", None):
            target = selected._disk.path
        else:
            _toast(self._window,
                   "Please select a disk from the list "
                   "(or check the manual-entry box).")
            return False

        # Block-device existence check. On dev hosts the path may not
        # exist; on real targets it must. The orchestrator also re-checks,
        # but surfacing here keeps the user on the disk screen for the
        # correction instead of crashing partway through partition phase.
        try:
            p = Path(target)
            exists_as_block = p.exists() and p.is_block_device()
        except OSError:
            exists_as_block = False
        if not exists_as_block:
            _toast(self._window,
                   f"Path {target} is not a block device on this system.")
            return False

        if not self._confirm_check.get_active():
            _toast(self._window,
                   "Confirm the destructive operation to continue.")
            return False

        state.target_disk = target
        state.confirm_destructive = True
        return True
