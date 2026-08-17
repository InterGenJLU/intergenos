# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Disk screen — third page of the 9-screen flow.

2026-05-25 Wave-3 "shock-and-awe" rewrite. Same bar as KeyboardLocale
(decided: "look at Ubuntu, look at Calamares, look at Apple,
look at Windows, look at Pop!_OS — we need THAT level of 'wow'"):

  * Hero **Destination** card at the top — disk-shape icon on the left
    (Adwaita symbolic: drive-harddisk-solidstate / drive-harddisk /
    drive-harddisk-usb depending on inference), path in big bold,
    model + bus + size in subtitle, and a red destructive WARNING
    strip baked into the card itself so the "this will be erased"
    message is impossible to miss while looking at the selection.
  * Instruction paragraph below the hero — lead first-time users to
    the picker and the security options.
  * **Destination** PreferencesGroup — each detected disk renders as
    an Adw.ActionRow with disk-shape prefix icon, path as title,
    "{model} · {bus} · {size} · {fixed|removable}" subtitle, and a
    Gtk.CheckButton in a shared group as the activatable suffix. The
    manual-entry path is a final row in the same group, revealing a
    Gtk.Entry when activated. Confirm-destructive checkbox at the
    bottom of this group.
  * **Full-disk encryption** PreferencesGroup — Adw.SwitchRow for
    the LUKS toggle (clearer affordance than a checkbox); reveals
    Adw.PasswordEntryRow (passphrase) + Adw.PasswordEntryRow (confirm)
    + a strength-feedback line + Adw.SwitchRows for TPM2 / FIDO2
    EXPERIMENTAL unlock methods. Hardware/tools sensitivity preserved
    from the previous implementation (D-001 contract).
  * **Boot options** PreferencesGroup — Adw.SwitchRow for dual-boot
    detection (Option C 2026-05-24).

D-001 contract preserved end-to-end. All validation, path
normalization, block-device existence check, passphrase-strength
feedback, and back/forward state restoration kept verbatim from the
previous implementation — this is a presentation rewrite, not a
behavior change. The on_next return values + state updates remain
byte-equivalent.
"""

from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from installer.backend.disks import detect_disks

from ._base import _ForgePage, _toast


# ──────────────────────────────────────────────────────────────────────
#  Disk-shape inference — pick an Adwaita symbolic that matches the
#  device. Falls back to generic harddisk for unknown shapes.
# ──────────────────────────────────────────────────────────────────────
def _disk_icon_name(disk) -> str:
    path = (disk.path or "").lower()
    model = (disk.model or "").lower()
    if disk.removable:
        return "drive-harddisk-usb-symbolic"
    if "nvme" in path or "nvme" in model or "ssd" in model:
        return "drive-harddisk-solidstate-symbolic"
    return "drive-harddisk-symbolic"


def _disk_bus_label(disk) -> str:
    """Best-effort bus-type label for the row subtitle. We never lie
    about what we don't know — if we can't infer, return ''."""
    path = (disk.path or "").lower()
    model = (disk.model or "").lower()
    if "nvme" in path:
        return "NVMe"
    if disk.removable:
        return "USB"
    if "ssd" in model:
        return "SSD"
    if path.startswith("/dev/sd") or path.startswith("/dev/hd"):
        return "SATA"
    return ""


def _disk_subtitle(disk) -> str:
    """Compose the row subtitle: '{model} · {bus} · {size} · {kind}'.
    Parts that are empty get dropped — no leading separators."""
    bits = []
    if disk.model:
        bits.append(disk.model)
    bus = _disk_bus_label(disk)
    if bus:
        bits.append(bus)
    if disk.size_human:
        bits.append(disk.size_human)
    bits.append("removable" if disk.removable else "fixed")
    return "   ·   ".join(bits)


# ──────────────────────────────────────────────────────────────────────
#  DiskPage
# ──────────────────────────────────────────────────────────────────────
class DiskPage(_ForgePage):
    tag = "disk"
    title = "Disk"

    def _build_body(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_hexpand(True)

        # ─── HERO DESTINATION CARD ───────────────────────────────────
        self._hero_card = self._build_hero_card()
        page.append(self._hero_card)

        # ─── INSTRUCTION LINE ────────────────────────────────────────
        instruction = Gtk.Label(label=(
            "Pick the disk InterGenOS will be installed onto. The "
            "selected disk will be completely erased — all existing "
            "data will be unrecoverable. Scroll down for encryption "
            "and boot options before clicking 'Next' to advance."
        ))
        instruction.add_css_class("forge-instruction")
        instruction.set_halign(Gtk.Align.CENTER)
        instruction.set_justify(Gtk.Justification.CENTER)
        instruction.set_wrap(True)
        instruction.set_max_width_chars(72)
        page.append(instruction)

        # ─── DESTINATION GROUP ───────────────────────────────────────
        self._dest_group = Adw.PreferencesGroup()
        self._dest_group.set_title("Destination")
        self._dest_group.set_description(
            "All detected disks are listed below. The disk where this "
            "live ISO is running is filtered out automatically."
        )
        # Populated in _populate_destination_group via on_load + the
        # post-construct refresh path so each visit re-enumerates
        # (USB hot-plug, etc.).
        page.append(self._dest_group)

        # ─── ENCRYPTION GROUP ────────────────────────────────────────
        # D-001 LUKS-at-install opt-in. Reveal pattern: switching the
        # parent on shows passphrase fields + sub-toggles; switching off
        # clears any captured plaintext (mirrors the previous
        # implementation's contract).
        #
        # Flagged EXPERIMENTAL (decided 2026-05-26): the
        # LUKS-at-install + TPM2 + FIDO2 chain is new in v1.0; failure
        # modes are documented in docs/users/full-disk-encryption.md
        # and the passphrase always remains a valid unlock path. Placed
        # as the LAST option on the page so users aren't pushed toward
        # an experimental feature in the primary flow.
        self._enc_group = Adw.PreferencesGroup()
        self._enc_group.set_title("Full-disk encryption (EXPERIMENTAL)")
        self._enc_group.set_description(
            "Opt-in only. When enabled, you will be asked for a "
            "passphrase at every boot. If you forget the passphrase, "
            "your data is unrecoverable."
        )

        # GBC001.5: an always-visible amber EXPERIMENTAL badge at the very top
        # of the group. The prior "(EXPERIMENTAL)" in the group title read as
        # non-descript; this makes the experimental nature unmistakable BEFORE
        # the user toggles anything.
        self._enc_experimental_badge = Adw.ActionRow()
        self._enc_experimental_badge.set_title(
            "⚠   EXPERIMENTAL — full-disk encryption is new in v1.0"
        )
        self._enc_experimental_badge.set_subtitle(
            "Opt-in only and not yet fully hardened. Your passphrase always "
            "remains a valid unlock path. Leave off if unsure."
        )
        self._enc_experimental_badge.add_css_class("forge-experimental-badge")
        self._enc_group.add(self._enc_experimental_badge)

        self._luks_switch = Adw.SwitchRow()
        self._luks_switch.set_title("Encrypt the root filesystem with LUKS2")
        self._luks_switch.set_subtitle(
            "Industry-standard full-disk encryption — protects data at "
            "rest if the machine is lost or stolen."
        )
        self._luks_switch.connect("notify::active", self._on_luks_toggled)
        self._enc_group.add(self._luks_switch)

        self._luks_passphrase_row = Adw.PasswordEntryRow()
        self._luks_passphrase_row.set_title("Passphrase")
        self._luks_passphrase_row.set_show_apply_button(False)
        self._luks_passphrase_row.connect(
            "notify::text", self._on_luks_passphrase_changed
        )
        self._luks_passphrase_row.set_visible(False)
        self._enc_group.add(self._luks_passphrase_row)

        self._luks_confirm_row = Adw.PasswordEntryRow()
        self._luks_confirm_row.set_title("Confirm passphrase")
        self._luks_confirm_row.set_show_apply_button(False)
        self._luks_confirm_row.set_visible(False)
        self._enc_group.add(self._luks_confirm_row)

        # Strength feedback as a dedicated subtle row. Visibility +
        # text driven by _on_luks_passphrase_changed.
        self._luks_strength_row = Adw.ActionRow()
        self._luks_strength_row.set_title("")
        self._luks_strength_row.set_subtitle("")
        self._luks_strength_row.add_css_class("forge-strength-row")
        self._luks_strength_row.set_visible(False)
        self._enc_group.add(self._luks_strength_row)

        # D-001 EXPERIMENTAL unlock methods (TPM2 + FIDO2). Both compose
        # with LUKS — passphrase remains canonical fallback at boot.
        from installer.backend import disks as _disks
        self._tpm2_present = _disks.tpm2_present()
        self._tpm2_tools_ok = _disks.tpm2_tools_available()
        self._fido2_tools_ok = _disks.fido2_tools_available()

        self._tpm2_switch = Adw.SwitchRow()
        self._tpm2_switch.set_title("Unlock with TPM2 (EXPERIMENTAL)")
        if not self._tpm2_present:
            self._tpm2_switch.set_subtitle(
                "No TPM2 device detected on this hardware."
            )
            self._tpm2_switch.set_sensitive(False)
        elif not self._tpm2_tools_ok:
            self._tpm2_switch.set_subtitle(
                "tpm2-tools-static not installed in this live ISO."
            )
            self._tpm2_switch.set_sensitive(False)
        else:
            self._tpm2_switch.set_subtitle(
                "Uses your computer's built-in TPM2 security chip for "
                "passwordless unlock when the chassis is intact."
            )
        self._tpm2_switch.set_visible(False)
        self._enc_group.add(self._tpm2_switch)

        self._fido2_switch = Adw.SwitchRow()
        self._fido2_switch.set_title("Unlock with FIDO2 token (EXPERIMENTAL)")
        if not self._fido2_tools_ok:
            self._fido2_switch.set_subtitle(
                "fido2-tools-static not installed in this live ISO."
            )
            self._fido2_switch.set_sensitive(False)
        else:
            self._fido2_switch.set_subtitle(
                "Hardware security key (e.g. YubiKey, SoloKey) — touch "
                "the token at boot to unlock."
            )
        self._fido2_switch.set_visible(False)
        self._enc_group.add(self._fido2_switch)

        # EXPERIMENTAL warning footer — visible whenever LUKS is on, so
        # the user sees the failure-mode disclosure right at the moment
        # they're about to opt in. Pulls the same warn-row CSS as the
        # passphrase strength feedback so it reads as a sibling caution.
        self._experimental_hint_row = Adw.ActionRow()
        self._experimental_hint_row.set_title("⚠   EXPERIMENTAL feature")
        self._experimental_hint_row.set_subtitle(
            "Failure modes documented in docs/users/full-disk-encryption.md. "
            "Your passphrase always remains a valid unlock path even if "
            "TPM2 / FIDO2 enrollment fails or the chassis is opened."
        )
        self._experimental_hint_row.add_css_class("forge-strength-row")
        self._experimental_hint_row.add_css_class("forge-strength-warn")
        self._experimental_hint_row.set_visible(False)
        self._enc_group.add(self._experimental_hint_row)

        # ─── BOOT OPTIONS GROUP ──────────────────────────────────────
        # Option C 2026-05-24 — GRUB_DISABLE_OS_PROBER is written
        # PERMANENTLY to the installed system's /etc/default/grub based
        # on this choice. Default ON (recommended for dual-boot).
        boot_group = Adw.PreferencesGroup()
        boot_group.set_title("Boot options")
        boot_group.set_description(
            "GRUB's behavior when scanning the disk during install + "
            "every kernel update."
        )
        self._dualboot_switch = Adw.SwitchRow()
        self._dualboot_switch.set_title(
            "Detect other operating systems on adjacent partitions"
        )
        self._dualboot_switch.set_subtitle(
            "Recommended for dual-boot installs. GRUB scans adjacent "
            "partitions on every kernel update and keeps Windows / "
            "other-Linux boot entries fresh. Disable for single-boot "
            "for a smaller GRUB attack surface."
        )
        self._dualboot_switch.set_active(True)  # default ON
        boot_group.add(self._dualboot_switch)

        # Page assembly order — Destination first, Boot options next,
        # Encryption LAST (decided 2026-05-26: LUKS is
        # EXPERIMENTAL so it shouldn't sit in the primary flow path).
        page.append(boot_group)
        page.append(self._enc_group)

        return page

    # ─── HERO CARD ────────────────────────────────────────────────────
    def _build_hero_card(self) -> Gtk.Widget:
        """Disk destination summary card — analogous to KeyboardLocale's
        region card. Big icon on the left, path/model/size on the right,
        destructive warning strip baked in below."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("forge-destination-hero")
        card.set_hexpand(True)

        # Top row: icon + info
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        # Big disk icon — uses Gtk.Image with an Adwaita symbolic.
        # set_pixel_size makes the symbolic render at the requested size.
        self._hero_icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        self._hero_icon.set_pixel_size(76)
        self._hero_icon.add_css_class("forge-destination-icon")
        self._hero_icon.set_valign(Gtk.Align.CENTER)
        self._hero_icon.set_halign(Gtk.Align.CENTER)
        self._hero_icon.set_size_request(108, 108)
        top_row.append(self._hero_icon)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._hero_path = Gtk.Label()
        self._hero_path.add_css_class("forge-destination-path")
        self._hero_path.set_halign(Gtk.Align.START)
        self._hero_path.set_label("No disk selected")
        info.append(self._hero_path)

        self._hero_subtitle = Gtk.Label()
        self._hero_subtitle.add_css_class("forge-destination-subtitle")
        self._hero_subtitle.set_halign(Gtk.Align.START)
        self._hero_subtitle.set_label(
            "Pick a disk from the list below to begin."
        )
        info.append(self._hero_subtitle)

        spacer = Gtk.Box()
        spacer.set_size_request(-1, 4)
        info.append(spacer)

        self._hero_size = Gtk.Label()
        self._hero_size.add_css_class("forge-destination-size")
        self._hero_size.set_halign(Gtk.Align.START)
        self._hero_size.set_label("")
        info.append(self._hero_size)

        top_row.append(info)
        card.append(top_row)

        # Destructive warning strip — visible only when a disk IS
        # selected (no point warning about erasing 'no disk').
        self._hero_warning = Gtk.Label()
        self._hero_warning.add_css_class("forge-destination-warning")
        self._hero_warning.set_halign(Gtk.Align.CENTER)
        self._hero_warning.set_use_markup(True)
        self._hero_warning.set_visible(False)
        card.append(self._hero_warning)

        return card

    def _update_hero_card(self) -> None:
        """Repaint the hero card based on current selection."""
        disk = self._selected_disk()
        if disk is None:
            # Manual-entry mode: synthesize a placeholder hero
            manual = (self._manual_entry.get_text() or "").strip() if hasattr(self, "_manual_entry") else ""
            if manual:
                self._hero_icon.set_from_icon_name("drive-harddisk-symbolic")
                self._hero_path.set_label(manual)
                self._hero_subtitle.set_label("Manual entry — unverified until 'Next'")
                self._hero_size.set_label("")
                self._hero_warning.set_markup(
                    "<span weight='bold'>⚠   All data on this disk will be "
                    "ERASED</span>"
                )
                self._hero_warning.set_visible(True)
                return
            # Truly empty
            self._hero_icon.set_from_icon_name("drive-harddisk-symbolic")
            self._hero_path.set_label("No disk selected")
            self._hero_subtitle.set_label(
                "Pick a disk from the list below to begin."
            )
            self._hero_size.set_label("")
            self._hero_warning.set_visible(False)
            return

        self._hero_icon.set_from_icon_name(_disk_icon_name(disk))
        self._hero_path.set_label(disk.path)

        # Subtitle: model + bus type, never duplicating the size (which
        # gets its own line below).
        sub_bits = []
        if disk.model:
            sub_bits.append(disk.model)
        bus = _disk_bus_label(disk)
        if bus:
            sub_bits.append(bus)
        sub_bits.append("removable" if disk.removable else "fixed")
        self._hero_subtitle.set_label("   ·   ".join(sub_bits))

        self._hero_size.set_label(disk.size_human or "")

        self._hero_warning.set_markup(
            "<span weight='bold'>⚠   All data on this disk will be "
            "ERASED</span>"
        )
        self._hero_warning.set_visible(True)

    # ─── DESTINATION GROUP — disks as ActionRows in a shared group ─
    def _populate_destination_group(self):
        """Re-enumerate detected disks + build a fresh set of rows."""
        # Tear down old rows. PreferencesGroup keeps rows in its internal
        # listbox; remove() each child via the group API.
        for row in list(self._dest_rows):
            self._dest_group.remove(row)
        self._dest_rows = []

        # Manual-entry row also rebuilt fresh.
        if hasattr(self, "_manual_row"):
            try:
                self._dest_group.remove(self._manual_row)
            except Exception:
                pass

        if hasattr(self, "_manual_reveal"):
            try:
                self._dest_group.remove(self._manual_reveal)
            except Exception:
                pass

        if hasattr(self, "_confirm_row"):
            try:
                self._dest_group.remove(self._confirm_row)
            except Exception:
                pass

        self._detected = detect_disks()

        # Build a CheckButton group leader — all per-disk check buttons
        # share this group, giving radio-style mutual exclusion.
        self._row_group_leader: Gtk.CheckButton | None = None

        for disk in self._detected:
            row = Adw.ActionRow()
            row.set_title(disk.path)
            row.set_subtitle(_disk_subtitle(disk))

            icon = Gtk.Image.new_from_icon_name(_disk_icon_name(disk))
            icon.set_pixel_size(28)
            icon.add_css_class("forge-disk-row-icon")
            row.add_prefix(icon)

            check = Gtk.CheckButton()
            if self._row_group_leader is None:
                self._row_group_leader = check
            else:
                check.set_group(self._row_group_leader)
            check.add_css_class("selection-mode")
            row.add_suffix(check)
            row.set_activatable_widget(check)

            # Stash on the row for retrieval in on_next + hero updates
            row._disk = disk
            row._check = check
            check.connect("toggled", self._on_row_toggled, row)

            self._dest_group.add(row)
            self._dest_rows.append(row)

        # Manual-entry: a final ActionRow that reveals a Gtk.Entry below
        # when toggled. Same CheckButton group so it's mutually exclusive
        # with the detected disks (you pick exactly one path source).
        self._manual_row = Adw.ActionRow()
        self._manual_row.set_title("Type a disk path manually")
        self._manual_row.set_subtitle(
            "Advanced / fallback — use only if your disk isn't listed "
            "above (e.g. unusual RAID or virtio device)."
        )
        manual_icon = Gtk.Image.new_from_icon_name(
            "document-edit-symbolic"
        )
        manual_icon.set_pixel_size(28)
        manual_icon.add_css_class("forge-disk-row-icon")
        self._manual_row.add_prefix(manual_icon)

        self._manual_check = Gtk.CheckButton()
        if self._row_group_leader is None:
            self._row_group_leader = self._manual_check
        else:
            self._manual_check.set_group(self._row_group_leader)
        self._manual_check.add_css_class("selection-mode")
        self._manual_check.connect("toggled", self._on_manual_toggled)
        self._manual_row.add_suffix(self._manual_check)
        self._manual_row.set_activatable_widget(self._manual_check)
        self._dest_group.add(self._manual_row)

        # Reveal row hosting the actual entry field. Visibility tied
        # to the manual checkbox.
        self._manual_reveal = Adw.ActionRow()
        self._manual_reveal.set_title("Disk path")
        self._manual_entry = Gtk.Entry()
        self._manual_entry.set_placeholder_text(
            "/dev/sda or /dev/nvme0n1"
        )
        self._manual_entry.set_valign(Gtk.Align.CENTER)
        self._manual_entry.set_hexpand(True)
        self._manual_entry.connect("changed", self._on_manual_entry_changed)
        self._manual_reveal.add_suffix(self._manual_entry)
        self._manual_reveal.set_visible(False)
        self._dest_group.add(self._manual_reveal)

        # Confirm-destructive checkbox — final row in the Destination
        # group. Phrased so a normal person understands what they're
        # agreeing to.
        self._confirm_row = Adw.ActionRow()
        self._confirm_row.set_title("⚠   I understand — erase this disk")
        self._confirm_row.set_subtitle(
            "Required before continuing. The selected disk's partitions, "
            "filesystems, and contents will be wiped. Tick this box to "
            "enable 'Next'."
        )
        # GBC001.5: tint the whole row red so the destructive acknowledgement
        # stands out — users were missing the plain row until 'Next' blocked.
        self._confirm_row.add_css_class("forge-confirm-row")
        self._confirm_check = Gtk.CheckButton()
        self._confirm_check.add_css_class("forge-confirm-destructive")
        self._confirm_check.set_valign(Gtk.Align.CENTER)
        self._confirm_row.add_suffix(self._confirm_check)
        self._confirm_row.set_activatable_widget(self._confirm_check)
        self._dest_group.add(self._confirm_row)

        # Empty-list path: if detection returned nothing, force manual
        # entry on so the user has a path forward.
        if not self._detected:
            self._manual_check.set_active(True)
            self._manual_check.set_sensitive(False)
            self._manual_row.set_subtitle(
                "No disks were auto-detected — manual entry required."
            )

    # ─── HANDLERS ─────────────────────────────────────────────────────
    def _selected_disk(self):
        """Return the currently-selected Disk dataclass, or None if
        manual-entry / no-selection."""
        for row in getattr(self, "_dest_rows", []):
            if row._check.get_active():
                return row._disk
        return None

    def _on_row_toggled(self, check, row):
        # Mutual exclusion is handled by the CheckButton group. When a
        # row toggles ON, refresh the hero.
        if check.get_active():
            # User picked a detected disk → clear manual entry contents
            # so the two inputs never disagree
            if hasattr(self, "_manual_entry"):
                self._manual_entry.set_text("")
            self._update_hero_card()

    def _on_manual_toggled(self, check):
        active = check.get_active()
        self._manual_reveal.set_visible(active)
        if active:
            self._manual_entry.grab_focus()
        self._update_hero_card()

    def _on_manual_entry_changed(self, _entry):
        if self._manual_check.get_active():
            self._update_hero_card()

    def _on_luks_toggled(self, switch_row, _pspec):
        active = switch_row.get_active()
        self._luks_passphrase_row.set_visible(active)
        self._luks_confirm_row.set_visible(active)
        from installer.backend import disks as _disks
        _offered = _disks.EXPERIMENTAL_UNLOCK_OFFERED
        self._tpm2_switch.set_visible(active and _offered)
        self._fido2_switch.set_visible(active and _offered)
        self._experimental_hint_row.set_visible(active and _offered)
        if not active:
            # Drop captured plaintext when toggling off — same
            # zeroize-on-deselect contract as the previous
            # implementation.
            self._luks_passphrase_row.set_text("")
            self._luks_confirm_row.set_text("")
            self._luks_strength_row.set_visible(False)
            self._tpm2_switch.set_active(False)
            self._fido2_switch.set_active(False)

    def _on_luks_passphrase_changed(self, entry_row, _pspec):
        pp = entry_row.get_text()
        if not pp:
            self._luks_strength_row.set_visible(False)
            return
        warning = _luks_passphrase_strength(pp)
        if warning:
            self._luks_strength_row.set_title("⚠   Weak passphrase")
            self._luks_strength_row.set_subtitle(warning)
            self._luks_strength_row.remove_css_class("forge-strength-ok")
            self._luks_strength_row.add_css_class("forge-strength-warn")
        else:
            self._luks_strength_row.set_title("✓   Passphrase looks reasonable")
            self._luks_strength_row.set_subtitle(
                "Length passes the minimum recommendation."
            )
            self._luks_strength_row.remove_css_class("forge-strength-warn")
            self._luks_strength_row.add_css_class("forge-strength-ok")
        self._luks_strength_row.set_visible(True)

    # ─── HELPERS ──────────────────────────────────────────────────────
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
            if "/" in path:
                return None
            path = f"/dev/{path}"
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return None
        if not str(resolved).startswith("/dev/"):
            return None
        return str(resolved)

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    def __init__(self, window):
        # Sentinels populated by _build_body via _populate_destination_group
        # (called below in on_load); seed lists here so reentry-safe
        # operations don't AttributeError.
        self._dest_rows: list = []
        self._detected: list = []
        super().__init__(window)

    def on_load(self, state):
        self._populate_destination_group()

        # State restoration. If state.target_disk is set, try to re-select
        # the matching detected-disk row. If the target isn't in the
        # current detection (e.g. USB unplugged between visits), fall
        # through to the manual entry path pre-filled with the prior
        # value.
        if state.target_disk:
            matched = False
            for row in self._dest_rows:
                if row._disk.path == state.target_disk:
                    row._check.set_active(True)
                    matched = True
                    break
            if not matched:
                self._manual_check.set_active(True)
                self._manual_entry.set_text(state.target_disk)

        self._confirm_check.set_active(state.confirm_destructive)
        self._luks_switch.set_active(state.luks_enabled)

        # Option C 2026-05-24 — restore dual-boot detection choice.
        self._dualboot_switch.set_active(state.detect_other_oses)

        # Passphrase fields: NEVER pre-fill on back-then-forward
        # navigation (same contract as previous implementation —
        # re-prompt for the secret each visit).
        self._luks_passphrase_row.set_visible(state.luks_enabled)
        self._luks_confirm_row.set_visible(state.luks_enabled)
        self._luks_passphrase_row.set_text("")
        self._luks_confirm_row.set_text("")
        self._luks_strength_row.set_visible(False)

        # D-001 EXPERIMENTAL — restore opt-in alongside LUKS visibility,
        # but only if the hardware/tools are still present.
        from installer.backend import disks as _disks
        _offered = _disks.EXPERIMENTAL_UNLOCK_OFFERED
        self._tpm2_switch.set_visible(state.luks_enabled and _offered)
        self._fido2_switch.set_visible(state.luks_enabled and _offered)
        self._experimental_hint_row.set_visible(state.luks_enabled and _offered)
        self._tpm2_switch.set_active(
            state.tpm2_enabled and self._tpm2_present and self._tpm2_tools_ok
        )
        self._fido2_switch.set_active(
            state.fido2_enabled and self._fido2_tools_ok
        )

        # Hero card refresh — must happen AFTER row checks are set so
        # _selected_disk() sees the right state.
        self._update_hero_card()

    def on_next(self, state):
        # Resolve target_disk from row selection (preferred) or manual entry.
        selected = self._selected_disk()
        if self._manual_check.get_active():
            target = self._normalize_path(self._manual_entry.get_text())
            if target is None:
                _toast(self._window,
                       "Please enter a valid /dev/* disk path "
                       "(e.g. /dev/sda or /dev/nvme0n1).")
                return False
        elif selected is not None:
            target = selected.path
        else:
            _toast(self._window,
                   "Please pick a disk from the list "
                   "(or use the manual-entry option).")
            return False

        # Block-device existence check — orchestrator re-checks but
        # surfacing here keeps the user on the disk screen for the
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
                   "Check the 'I understand' box to confirm the "
                   "destructive operation.")
            return False

        # D-001 LUKS validation. When opt-in is active, passphrase must
        # be non-empty + match its confirm. Soft strength warning was
        # already surfaced inline; we don't block on it but we do block
        # on hard-empty + mismatch.
        luks_enabled = self._luks_switch.get_active()
        luks_passphrase = ""
        if luks_enabled:
            pp = self._luks_passphrase_row.get_text()
            confirm = self._luks_confirm_row.get_text()
            if not pp:
                _toast(self._window,
                       "Enter a LUKS passphrase, or turn encryption off.")
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

        # D-001 EXPERIMENTAL — capture sub-switch state. Only meaningful
        # when LUKS active; backend validates the composition.
        state.tpm2_enabled = bool(luks_enabled and self._tpm2_switch.get_active())
        state.fido2_enabled = bool(luks_enabled and self._fido2_switch.get_active())

        # Option C 2026-05-24 — capture dual-boot-detection choice.
        state.detect_other_oses = self._dualboot_switch.get_active()
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
