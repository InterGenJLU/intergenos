# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Confirm screen — seventh page of the 9-screen flow.

2026-05-26 Wave-3 "shock-and-awe" rewrite — last chance before the
destructive install begins. Replaces the plain
one-Label-with-newlines summary with:

  * Hero "Ready to install" card — checklist icon on the left, big
    bold confirmation line + subtitle summarizing the install scope
    in one glance ("4 groups · 2 services · LUKS encryption"), and
    a destructive warning strip baked into the card body so the
    "wipe the disk" disclosure is impossible to miss.
  * Instruction paragraph leads the user through the review pattern.
  * Five organized Adw.PreferencesGroups showing the choices from
    the prior screens, each value rendered via the same humanizers
    Forge uses on the source pages (locale_data for locale +
    keyboard + timezone enrichment, disk-shape icon inference for
    destination, plain English for everything else): Identity →
    Region → Destination → Software → Secure Boot.

The Next button is relabeled "Install" so the user knows pressing
it commits.

Validation calls InstallerState.is_ready_for_install() — the single
audit point for "did the user actually fill everything in". If it
returns False we toast and stay on this page, mirroring the previous
contract."""

from gi.repository import Adw, GLib, Gtk

from installer.backend.bootloader import has_other_os_boot_entries
from installer.backend.packages import GROUPS
from installer.backend.secureboot import is_secure_boot_enabled, is_efi_firmware
from installer.frontend.netprobe import active_wifi_names

from .. import locale_data as ld
from ._base import _ForgePage, _toast


# Group human-label table — mirrors PackagesPage's _GROUP_LABELS so
# the rendered group names are consistent across screens.
_GROUP_LABELS = {
    "core": "Core system",
    "base": "Base CLI tools",
    "desktop-gnome": "GNOME Desktop",
    "extra": "Extras",
    "ai": "Local AI runtime",
}


class ConfirmPage(_ForgePage):
    tag = "confirm"
    title = "Confirm"

    def _build_body(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_hexpand(True)

        # ─── HERO CARD ────────────────────────────────────────────────
        self._hero_card = self._build_hero_card()
        page.append(self._hero_card)

        # ─── INSTRUCTION LINE ────────────────────────────────────────
        instruction = Gtk.Label(label=(
            "Review your choices below. If everything looks right, "
            "click 'Install' to begin. If something is wrong, click "
            "'Back' to step backwards through the prior screens and "
            "make changes — nothing is committed to disk until you "
            "click 'Install'."
        ))
        instruction.add_css_class("forge-instruction")
        instruction.set_halign(Gtk.Align.CENTER)
        instruction.set_justify(Gtk.Justification.CENTER)
        instruction.set_wrap(True)
        instruction.set_max_width_chars(72)
        page.append(instruction)

        # ─── IDENTITY GROUP ──────────────────────────────────────────
        self._id_group = Adw.PreferencesGroup()
        self._id_group.set_title("Identity")
        self._id_hostname = self._add_summary_row(
            self._id_group, "Hostname", "—",
            icon="network-server-symbolic",
        )
        self._id_username = self._add_summary_row(
            self._id_group, "Username", "—",
            icon="avatar-default-symbolic",
        )
        page.append(self._id_group)

        # ─── REGION GROUP ────────────────────────────────────────────
        self._region_group = Adw.PreferencesGroup()
        self._region_group.set_title("Region")
        self._region_language = self._add_summary_row(
            self._region_group, "Language", "—",
            icon="preferences-desktop-locale-symbolic",
        )
        self._region_keyboard = self._add_summary_row(
            self._region_group, "Keyboard", "—",
            icon="input-keyboard-symbolic",
        )
        self._region_timezone = self._add_summary_row(
            self._region_group, "Timezone", "—",
            icon="preferences-system-time-symbolic",
        )
        page.append(self._region_group)

        # ─── DESTINATION GROUP ───────────────────────────────────────
        self._dest_group = Adw.PreferencesGroup()
        self._dest_group.set_title("Destination")
        self._dest_disk = self._add_summary_row(
            self._dest_group, "Disk", "—",
            icon="drive-harddisk-symbolic",
        )
        self._dest_encryption = self._add_summary_row(
            self._dest_group, "Full-disk encryption", "—",
            icon="channel-secure-symbolic",
        )
        self._dest_dualboot = self._add_summary_row(
            self._dest_group, "Dual-boot detection", "—",
            icon="system-search-symbolic",
        )

        # D1 / work-plan 1.25 (decided 2026-07-08, Prime Directive):
        # default-boot-target ask. Built here, hidden; on_load reveals it ONLY
        # when the live UEFI NVRAM already holds another installed OS's boot
        # entry (has_other_os_boot_entries() is True). Otherwise it stays hidden
        # and the backend keeps efibootmgr --create's prepend — there is no prior
        # default to respect on a single-OS / non-EFI machine, so no ask. The
        # switch defaults OFF (keep the machine's existing default): on multi-OS
        # metal InterGenOS becomes the default only when the user affirmatively
        # asks, never silently.
        self._dest_make_default = Adw.SwitchRow()
        self._dest_make_default.set_title("Make InterGenOS the default boot target")
        self._dest_make_default.set_subtitle(
            "Another operating system is installed on this machine. Leave OFF to "
            "keep your current default and boot InterGenOS from the firmware boot "
            "menu; turn ON to boot InterGenOS by default."
        )
        self._dest_make_default.add_prefix(
            Gtk.Image.new_from_icon_name("computer-symbolic")
        )
        self._dest_make_default.set_active(False)
        self._dest_make_default.connect(
            "notify::active", self._on_make_default_toggled)
        self._dest_group.add(self._dest_make_default)
        self._dest_make_default.set_visible(False)

        # Wi-Fi carry (2026-07-11): revealed by on_load only when the live
        # session has an active Wi-Fi connection. Defaults ON — the user
        # connected THIS machine to that network on purpose, so arriving on
        # it at first boot is the expected outcome; opting out is one flick.
        self._dest_carry_wifi = Adw.SwitchRow()
        self._dest_carry_wifi.set_title("Carry Wi-Fi connection")
        self._dest_carry_wifi.add_prefix(
            Gtk.Image.new_from_icon_name("network-wireless-symbolic")
        )
        self._dest_carry_wifi.set_active(True)
        self._dest_carry_wifi.connect(
            "notify::active", self._on_carry_wifi_toggled)
        self._dest_group.add(self._dest_carry_wifi)
        self._dest_carry_wifi.set_visible(False)

        page.append(self._dest_group)

        # ─── SOFTWARE GROUP ──────────────────────────────────────────
        self._sw_group = Adw.PreferencesGroup()
        self._sw_group.set_title("Software")
        self._sw_groups = self._add_summary_row(
            self._sw_group, "Package groups", "—",
            icon="package-x-generic-symbolic",
        )
        self._sw_services = self._add_summary_row(
            self._sw_group, "Optional services", "—",
            icon="emblem-system-symbolic",
        )
        page.append(self._sw_group)

        # ─── SECURE BOOT GROUP ───────────────────────────────────────
        self._sb_group = Adw.PreferencesGroup()
        self._sb_group.set_title("Secure Boot")
        self._sb_mok = self._add_summary_row(
            self._sb_group, "MOK enrollment", "—",
            icon="security-medium-symbolic",
        )

        # D-2 (HARD BLOCK): Secure Boot-aware MOK-skip BLOCK notice. Built
        # hidden; on_load reveals it only when Secure Boot is enforcing AND the
        # user skipped MOK enrollment (state.mok_install_blocked()). It is a
        # BLOCKING notice — there is no acknowledge-and-proceed. The Install
        # button stays disabled (state.is_complete() returns False) until the
        # user enrolls a MOK or disables Secure Boot. We will not let the user
        # create a system that can't boot.
        self._sb_block_row = Adw.ActionRow()
        self._sb_block_row.set_title(
            "Secure Boot is ON — install blocked to prevent an unbootable system"
        )
        self._sb_block_row.set_subtitle(
            "MOK enrollment is skipped, so this install would NOT boot. To "
            "continue, either go Back to the Secure Boot screen and set a MOK "
            "password to enroll it, or disable Secure Boot in firmware and "
            "restart the installer."
        )
        self._sb_block_row.add_prefix(
            Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        )
        self._sb_block_row.add_css_class("forge-confirm-warning")
        self._sb_group.add(self._sb_block_row)
        self._sb_block_row.set_visible(False)

        page.append(self._sb_group)

        return page

    # ─── HERO CARD ────────────────────────────────────────────────────
    def _build_hero_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("forge-confirm-hero")
        card.set_hexpand(True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        self._hero_icon = Gtk.Image.new_from_icon_name(
            "object-select-symbolic"
        )
        self._hero_icon.set_pixel_size(76)
        self._hero_icon.add_css_class("forge-confirm-icon")
        self._hero_icon.set_valign(Gtk.Align.CENTER)
        self._hero_icon.set_halign(Gtk.Align.CENTER)
        self._hero_icon.set_size_request(108, 108)
        top_row.append(self._hero_icon)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._hero_title = Gtk.Label()
        self._hero_title.add_css_class("forge-confirm-title")
        self._hero_title.set_halign(Gtk.Align.START)
        self._hero_title.set_label("Ready to install")
        info.append(self._hero_title)

        self._hero_subtitle = Gtk.Label()
        self._hero_subtitle.add_css_class("forge-confirm-subtitle")
        self._hero_subtitle.set_halign(Gtk.Align.START)
        self._hero_subtitle.set_wrap(True)
        self._hero_subtitle.set_label("Review your choices below.")
        info.append(self._hero_subtitle)

        top_row.append(info)
        card.append(top_row)

        # Gold review-notice strip — sits ABOVE the red warning (caution
        # before destruction, one severity ladder). Built hidden; on_load
        # reveals it in lockstep with the default-boot SwitchRow, so it can
        # never point at a switch that isn't on the page.
        self._hero_notice = Gtk.Label()
        self._hero_notice.add_css_class("forge-confirm-notice")
        self._hero_notice.set_halign(Gtk.Align.CENTER)
        self._hero_notice.set_use_markup(True)
        self._hero_notice.set_wrap(True)
        self._hero_notice.set_visible(False)
        card.append(self._hero_notice)

        # Destructive warning strip — visible whenever a disk is
        # selected (i.e. almost always on this page).
        self._hero_warning = Gtk.Label()
        self._hero_warning.add_css_class("forge-confirm-warning")
        self._hero_warning.set_halign(Gtk.Align.CENTER)
        self._hero_warning.set_use_markup(True)
        self._hero_warning.set_wrap(True)
        card.append(self._hero_warning)

        return card

    def _update_hero(self, state) -> None:
        # Build the one-glance summary line.
        group_count = sum(
            1 for g in state.package_groups if g in GROUPS
        )
        service_count = (
            int(state.intergen_ai_enable)
            + int(state.ssh_server_enable)
        )
        bits = [
            f"{group_count} groups",
            f"{service_count} service{'s' if service_count != 1 else ''}",
        ]
        if state.luks_enabled:
            bits.append("LUKS encryption")
        self._hero_subtitle.set_label("   ·   ".join(bits))

        # Destructive warning strip — quote the target disk path so
        # the user sees exactly which device is about to be erased.
        target = state.target_disk or "the selected disk"
        self._hero_warning.set_markup(
            f"<span weight='bold'>⚠   Installing onto "
            f"{GLib.markup_escape_text(target)} will erase ALL "
            "existing data on that disk.</span>"
        )

    # ─── SUMMARY ROW HELPER ───────────────────────────────────────────
    def _add_summary_row(self, group: Adw.PreferencesGroup,
                         title: str, subtitle: str,
                         icon: str | None = None) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.add_css_class("forge-confirm-row")
        if icon:
            icon_widget = Gtk.Image.new_from_icon_name(icon)
            icon_widget.set_pixel_size(22)
            icon_widget.add_css_class("forge-confirm-row-icon")
            row.add_prefix(icon_widget)
        group.add(row)
        return row

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    def on_load(self, state):
        # Bind state first so widget signal handlers wired below (e.g. the
        # default-boot SwitchRow's notify::active) always see it.
        self._state = state
        # Hero card
        self._update_hero(state)

        # Identity
        self._id_hostname.set_subtitle(state.hostname or "—")
        self._id_username.set_subtitle(state.username or "—")

        # Region — use locale_data humanizers so the values match what
        # the source pages render (e.g. "English (United States)" not
        # "en_US.UTF-8"; "Chicago, United States · UTC-5" not
        # "America/Chicago").
        locale_h = ld.humanize_locale(state.locale)
        self._region_language.set_subtitle(
            f"{locale_h['flag']}  {locale_h['primary']}   ·   "
            f"{locale_h['raw']}"
        )
        kb_h = ld.humanize_keyboard(state.keymap)
        self._region_keyboard.set_subtitle(
            f"{kb_h['flag']}  {kb_h['primary']}   ·   {kb_h['raw']}"
        )
        tz_h = ld.humanize_timezone(state.timezone)
        offset = ld.format_offset(state.timezone)
        local_time = ld.format_local_time(state.timezone)
        tz_location = (
            f"{tz_h['city']}, {tz_h['country_name']}"
            if tz_h["country_name"] else tz_h["city"]
        )
        self._region_timezone.set_subtitle(
            f"{tz_h['flag']}  {tz_location}   ·   "
            f"{offset}   ·   {local_time}   ·   {tz_h['raw']}"
        )

        # Destination
        self._dest_disk.set_subtitle(state.target_disk or "(not selected)")

        if state.luks_enabled:
            extras = []
            if state.tpm2_enabled:
                extras.append("TPM2 (experimental)")
            if state.fido2_enabled:
                extras.append("FIDO2 (experimental)")
            tail = f" · {' + '.join(extras)}" if extras else ""
            self._dest_encryption.set_subtitle(f"Enabled (LUKS2){tail}")
        else:
            self._dest_encryption.set_subtitle("Disabled")

        if state.detect_other_oses:
            self._dest_dualboot.set_subtitle(
                "Enabled — GRUB will scan for Windows / other Linux installs."
            )
        else:
            self._dest_dualboot.set_subtitle(
                "Disabled — single-boot posture (smaller GRUB attack surface)."
            )

        # D1 / work-plan 1.25: reveal the default-boot-target ask only when the
        # machine already has another installed OS's UEFI boot entry. On first
        # reveal, seed make_default_boot to False (respect the existing default)
        # if the user hasn't chosen yet (None); a prior choice survives Back/Next.
        if has_other_os_boot_entries() is True:
            if state.make_default_boot is None:
                state.make_default_boot = False
            self._dest_make_default.set_active(bool(state.make_default_boot))
            self._dest_make_default.set_visible(True)
            # Gold hero notice (decided 2026-07-15): the switch defaults OFF
            # and lives mid-page — without a top-of-page pointer a user can
            # commit without ever seeing the boot-order choice. Revealed only
            # alongside the switch, hidden with it.
            self._hero_notice.set_markup(
                "<span weight='bold'>Review a choice below:</span> another "
                "operating system was detected on this machine. Check the "
                "“Make InterGenOS the default boot target” switch "
                "under Destination before you install."
            )
            self._hero_notice.set_visible(True)
        else:
            # Single-OS / non-EFI / undeterminable — no ask; leave the choice
            # unset so to_install_io() omits the key and the backend prepends.
            state.make_default_boot = None
            self._dest_make_default.set_visible(False)
            self._hero_notice.set_visible(False)

        # Wi-Fi carry (2026-07-11): reveal only when the live session has an
        # active Wi-Fi connection. Seed ON at first reveal (intent: the user
        # joined this network on this machine deliberately); a prior choice
        # survives Back/Next. No connection / inconclusive probe — no ask,
        # tri-state stays None so to_install_io() omits the key.
        wifi_names = active_wifi_names()
        if wifi_names:
            if state.carry_wifi is None:
                state.carry_wifi = True
            self._dest_carry_wifi.set_subtitle(
                f"Your machine will already be connected to "
                f"{' + '.join(wifi_names)} on first boot. Turn OFF to start "
                f"the installed system with no saved Wi-Fi networks."
            )
            self._dest_carry_wifi.set_active(bool(state.carry_wifi))
            self._dest_carry_wifi.set_visible(True)
        else:
            state.carry_wifi = None
            self._dest_carry_wifi.set_visible(False)

        # Software — humanize group keys via _GROUP_LABELS for the
        # same display the Packages page uses.
        if state.package_groups:
            group_labels = [
                _GROUP_LABELS.get(g, g)
                for g in state.package_groups
                if g in GROUPS
            ]
            self._sw_groups.set_subtitle("   ·   ".join(group_labels))
        else:
            self._sw_groups.set_subtitle("(none)")

        services = []
        if state.intergen_ai_enable:
            services.append("InterGen (autostart)")
        if state.ssh_server_enable:
            ssh_str = "SSH server"
            if state.ssh_public_key:
                ssh_str += " (keys-only)"
            services.append(ssh_str)
        if services:
            self._sw_services.set_subtitle("   ·   ".join(services))
        else:
            self._sw_services.set_subtitle("(none enabled)")

        # Secure Boot MOK (D-2: SB-state-aware skip guard).
        # Probe the live Secure Boot state and record it so the gate logic
        # (state.mok_skip_needs_ack) and the ack widget agree on one source.
        self._state = state
        state.secure_boot_enabled = is_secure_boot_enabled()
        state.firmware_is_efi = is_efi_firmware()
        if state.mok_password:
            self._sb_mok.set_subtitle(
                "Queued — MokManager will prompt at first boot."
            )
            self._sb_mok.remove_css_class("forge-confirm-warning")
            self._sb_block_row.set_visible(False)
        elif state.mok_install_blocked():
            # Secure Boot is enforcing and the user skipped MOK enrollment:
            # the boot chain would be signed with an un-enrolled MOK ->
            # unbootable under SB, with mokutil recovery unreachable. HARD
            # BLOCK — surface the consequence; the Install button stays disabled
            # (state.is_complete() is False) until the user enrolls a MOK or
            # disables Secure Boot. No acknowledge-and-proceed.
            self._sb_mok.set_subtitle(
                "⚠ Skipped while Secure Boot is ON — install BLOCKED. Enroll a "
                "MOK or disable Secure Boot to continue."
            )
            self._sb_mok.add_css_class("forge-confirm-warning")
            self._sb_block_row.set_visible(True)
        elif state.mok_skip_sb_unknown_on_efi():
            # UEFI host but the Secure Boot state could not be read (rare —
            # the installer normally runs as root). SB *might* be enforcing,
            # so a silent benign skip could brick. Surface a SOFTER
            # informational note (no hard ack-gate — that's reserved for
            # KNOWN-enforcing). D-2 hardening (reviewed).
            self._sb_mok.set_subtitle(
                "Skipped — Secure Boot state could not be determined. If it "
                "is ON, this system will not boot until you enroll a MOK or "
                "disable Secure Boot."
            )
            self._sb_mok.remove_css_class("forge-confirm-warning")
            self._sb_block_row.set_visible(False)
        elif state.mok_skip_efi_sb_off():
            # D2 / work-plan 1.22 (PI-Z18): UEFI host, Secure Boot currently OFF,
            # MOK enrollment skipped. This boots fine NOW, so it is NOT blocked —
            # but pre-D2 it was lumped with the BIOS "benign" note, hiding a real
            # footgun: enabling Secure Boot later leaves the signed chain
            # unvalidatable until a manual mokutil --import. State the
            # consequence LOUDLY; skipping stays the user's valid choice.
            self._sb_mok.set_subtitle(
                "⚠ Skipped on a UEFI system with Secure Boot OFF. It boots fine "
                "now — but if you later turn Secure Boot ON in firmware, this "
                "install will NOT boot until you enroll a key by hand "
                "(mokutil --import). Go Back and set a MOK password to stage "
                "enrollment now, or leave it blank to accept this."
            )
            self._sb_mok.add_css_class("forge-confirm-warning")
            self._sb_block_row.set_visible(False)
        else:
            # Non-EFI (BIOS) install — Secure Boot / MOK does not apply, so
            # skipping is genuinely benign (no future-SB-flip footgun exists).
            self._sb_mok.set_subtitle(
                "Skipped — this is a non-UEFI (BIOS) install, so Secure Boot "
                "and MOK enrollment do not apply."
            )
            self._sb_mok.remove_css_class("forge-confirm-warning")
            self._sb_block_row.set_visible(False)

        # Relabel the Next button so the user knows what they're
        # committing to.
        self.next_button.set_label("Install")
        self.next_button.add_css_class("destructive-action")

    def _on_make_default_toggled(self, switch, _pspec) -> None:
        # D1 / work-plan 1.25: record the default-boot-target choice. Only
        # reachable while the row is visible (a foreign OS entry was detected),
        # so writing a concrete True/False here is always meaningful.
        if getattr(self, "_state", None) is not None:
            self._state.make_default_boot = switch.get_active()

    def _on_carry_wifi_toggled(self, switch, _pspec) -> None:
        # Wi-Fi carry (2026-07-11): record the choice. Only reachable while
        # the row is visible (an active Wi-Fi connection exists), so a
        # concrete True/False here is always meaningful.
        if getattr(self, "_state", None) is not None:
            self._state.carry_wifi = switch.get_active()

    def on_next(self, state):
        if not state.is_ready_for_install():
            errors = state.validation_errors()
            if errors:
                _toast(self._window, f"Cannot install: {errors[0]}")
            else:
                _toast(self._window,
                       "Some required fields are missing — go back.")
            return False
        return True
