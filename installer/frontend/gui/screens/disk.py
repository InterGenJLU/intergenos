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

        # ------------------------------------------------------------------
        # D-001 LUKS-at-install opt-in
        # ------------------------------------------------------------------
        # Encryption is opt-in per D-001 ratified semantics ("opt-in not
        # default"). When the checkbox is active the passphrase entry +
        # confirm entry become visible. The passphrase is captured here
        # and threaded through state.luks_passphrase → install_io →
        # disks.partition_disk where it pipes to cryptsetup via stdin
        # (never argv) and is zeroized after use. The plaintext is held
        # in state only as long as the install is running; ProgressPage's
        # clear_sensitive_data() drops it on completion (success or
        # failure path).
        luks_separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        luks_separator.set_margin_top(12)
        box.append(luks_separator)

        luks_heading = Gtk.Label(label="Full-disk encryption (LUKS)")
        luks_heading.add_css_class("title-4")
        luks_heading.set_halign(Gtk.Align.START)
        luks_heading.set_margin_top(6)
        box.append(luks_heading)

        self._luks_check = Gtk.CheckButton(
            label="Encrypt the root filesystem with LUKS2."
        )
        self._luks_check.connect("toggled", self._on_luks_toggled)
        box.append(self._luks_check)

        luks_hint = Gtk.Label(
            label="If enabled, you will be asked for a passphrase at every "
                  "boot. If you forget the passphrase, your data is "
                  "unrecoverable."
        )
        luks_hint.set_wrap(True)
        luks_hint.set_xalign(0)
        luks_hint.add_css_class("dim-label")
        box.append(luks_hint)

        self._luks_passphrase_entry = Gtk.PasswordEntry()
        self._luks_passphrase_entry.set_show_peek_icon(True)
        self._luks_passphrase_row = _labeled(
            "Passphrase", self._luks_passphrase_entry
        )
        self._luks_passphrase_row.set_visible(False)
        box.append(self._luks_passphrase_row)

        self._luks_passphrase_confirm_entry = Gtk.PasswordEntry()
        self._luks_passphrase_confirm_entry.set_show_peek_icon(True)
        self._luks_passphrase_confirm_row = _labeled(
            "Confirm passphrase", self._luks_passphrase_confirm_entry
        )
        self._luks_passphrase_confirm_row.set_visible(False)
        box.append(self._luks_passphrase_confirm_row)

        self._luks_strength_label = Gtk.Label()
        self._luks_strength_label.set_wrap(True)
        self._luks_strength_label.set_xalign(0)
        self._luks_strength_label.add_css_class("dim-label")
        self._luks_strength_label.set_visible(False)
        box.append(self._luks_strength_label)

        # Live strength feedback as the user types.
        self._luks_passphrase_entry.connect(
            "notify::text", self._on_luks_passphrase_changed
        )

        # ------------------------------------------------------------------
        # D-001 EXPERIMENTAL unlock methods (operator Option A 2026-05-18T22:52Z)
        # ------------------------------------------------------------------
        # Sub-checkboxes for TPM2 + FIDO2 unlock. Both compose with LUKS
        # (the passphrase remains the canonical fallback at boot per
        # fde-init.sh's TPM2 → FIDO2 → passphrase chain). Sub-checkboxes
        # are sensitive only when the parent LUKS checkbox is active;
        # toggling LUKS off clears the EXPERIMENTAL selections.
        from installer.backend import disks as _disks
        self._tpm2_present = _disks.tpm2_present()
        self._tpm2_tools_ok = _disks.tpm2_tools_available()
        self._fido2_tools_ok = _disks.fido2_tools_available()

        self._tpm2_check = Gtk.CheckButton(
            label="Unlock with TPM2 (EXPERIMENTAL)"
        )
        if not self._tpm2_present or not self._tpm2_tools_ok:
            self._tpm2_check.set_sensitive(False)
            reason = (
                "no TPM2 device detected on this hardware"
                if not self._tpm2_present
                else "tpm2-tools-static not installed in the live ISO"
            )
            self._tpm2_check.set_tooltip_text(reason)
        self._tpm2_check.set_visible(False)
        box.append(self._tpm2_check)

        self._fido2_check = Gtk.CheckButton(
            label="Unlock with FIDO2 token (EXPERIMENTAL)"
        )
        if not self._fido2_tools_ok:
            self._fido2_check.set_sensitive(False)
            self._fido2_check.set_tooltip_text(
                "fido2-tools-static not installed in the live ISO"
            )
        self._fido2_check.set_visible(False)
        box.append(self._fido2_check)

        self._experimental_hint = Gtk.Label(
            label="EXPERIMENTAL: failure modes documented in "
                  "docs/users/full-disk-encryption.md. "
                  "Passphrase always remains a valid unlock path."
        )
        self._experimental_hint.set_wrap(True)
        self._experimental_hint.set_xalign(0)
        self._experimental_hint.add_css_class("dim-label")
        self._experimental_hint.set_visible(False)
        box.append(self._experimental_hint)

        return box

    def _on_luks_toggled(self, check):
        active = check.get_active()
        self._luks_passphrase_row.set_visible(active)
        self._luks_passphrase_confirm_row.set_visible(active)
        # D-001 EXPERIMENTAL sub-checkboxes — only meaningful when LUKS
        # is active. Toggle visibility together; clear selection when
        # LUKS toggles off so reopening doesn't carry stale opt-in state.
        self._tpm2_check.set_visible(active)
        self._fido2_check.set_visible(active)
        self._experimental_hint.set_visible(active)
        if not active:
            # Drop any captured plaintext when toggling off so the next
            # toggle-on starts from a clean state. Same idiom as the
            # manual-disk-entry deselection above.
            self._luks_passphrase_entry.set_text("")
            self._luks_passphrase_confirm_entry.set_text("")
            self._luks_strength_label.set_visible(False)
            self._tpm2_check.set_active(False)
            self._fido2_check.set_active(False)
        else:
            self._luks_passphrase_entry.grab_focus()

    def _on_luks_passphrase_changed(self, _entry, _pspec):
        pp = self._luks_passphrase_entry.get_text()
        if not pp:
            self._luks_strength_label.set_visible(False)
            return
        warning = _luks_passphrase_strength(pp)
        if warning:
            self._luks_strength_label.set_label(warning)
            self._luks_strength_label.add_css_class("warning")
            self._luks_strength_label.set_visible(True)
        else:
            self._luks_strength_label.set_label("Passphrase length looks reasonable.")
            self._luks_strength_label.remove_css_class("warning")
            self._luks_strength_label.set_visible(True)

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

        # LUKS state restoration. Restore the checkbox + visibility, but
        # NEVER pre-fill the passphrase entries — back-and-forth
        # navigation should re-prompt for the secret to avoid stale
        # plaintext persisting across screen visits. The user types it
        # again; state.luks_passphrase will be updated on next-button
        # the next time through.
        self._luks_check.set_active(state.luks_enabled)
        self._luks_passphrase_row.set_visible(state.luks_enabled)
        self._luks_passphrase_confirm_row.set_visible(state.luks_enabled)
        self._luks_passphrase_entry.set_text("")
        self._luks_passphrase_confirm_entry.set_text("")
        self._luks_strength_label.set_visible(False)

        # D-001 EXPERIMENTAL — restore opt-in state for sub-checkboxes
        # alongside LUKS visibility. Hardware/tools sensitivity already
        # set in _build_body; back-then-forward navigation only changes
        # the checked state.
        self._tpm2_check.set_visible(state.luks_enabled)
        self._fido2_check.set_visible(state.luks_enabled)
        self._experimental_hint.set_visible(state.luks_enabled)
        # Don't restore tpm2/fido2 checked state if hardware/tools went
        # absent since last visit (e.g. operator unplugged FIDO2 token).
        self._tpm2_check.set_active(
            state.tpm2_enabled and self._tpm2_present and self._tpm2_tools_ok
        )
        self._fido2_check.set_active(
            state.fido2_enabled and self._fido2_tools_ok
        )

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

        # D-001 LUKS validation. When opt-in is active, passphrase must
        # be non-empty + match its confirm. Soft strength warning was
        # already surfaced inline; we don't block on it (operator's
        # call) but we do block on hard-empty + mismatch.
        luks_enabled = self._luks_check.get_active()
        luks_passphrase = ""
        if luks_enabled:
            pp = self._luks_passphrase_entry.get_text()
            confirm = self._luks_passphrase_confirm_entry.get_text()
            if not pp:
                _toast(self._window,
                       "Enter a LUKS passphrase, or uncheck encryption.")
                return False
            if pp != confirm:
                _toast(self._window,
                       "LUKS passphrases don't match. Re-enter both.")
                return False
            luks_passphrase = pp

        state.target_disk = target
        state.confirm_destructive = True
        state.luks_enabled = luks_enabled
        state.luks_passphrase = luks_passphrase
        state.luks_passphrase_confirm = luks_passphrase if luks_enabled else ""

        # D-001 EXPERIMENTAL — capture sub-checkbox state. Only meaningful
        # when LUKS active; backend validates the composition and would
        # reject tpm2/fido2 without luks anyway.
        state.tpm2_enabled = bool(luks_enabled and self._tpm2_check.get_active())
        state.fido2_enabled = bool(luks_enabled and self._fido2_check.get_active())
        return True


def _luks_passphrase_strength(passphrase):
    """Mirror of installer.frontend.tui._luks_passphrase_warning.

    Returns a single human-readable warning string for a weak LUKS
    passphrase, or empty string if no warning fires. Kept in sync with
    the TUI version (same heuristics so both frontends surface the
    same guidance).
    """
    if not passphrase:
        return "Empty passphrases are not accepted."
    if len(passphrase) < 8:
        return (
            f"Passphrase is {len(passphrase)} characters — well under the "
            "8-character floor. Short passphrases fall to dictionary "
            "attack quickly even with argon2id KDF cost."
        )
    classes = sum(
        bool(any(test(c) for c in passphrase))
        for test in (str.isupper, str.islower, str.isdigit,
                     lambda c: not c.isalnum())
    )
    if len(passphrase) < 12 and classes < 2:
        return (
            f"Passphrase is {len(passphrase)} characters with only one "
            "character class. Consider lengthening it or mixing types."
        )
    return ""
