# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""User screen — fourth page of the 9-screen flow.

2026-05-26 Wave-3 "shock-and-awe" rewrite — same bar as KeyboardLocale
and Disk pages. Captures hostname + username + user/root/MOK passwords
with rich visual treatment:

  * Hero "Account" card at the top — big user-avatar Adwaita symbolic
    on the left, live `username@hostname` summary in monospace on the
    right, plus a real-time status-badge row showing what's set and
    what's still needed.
  * Instruction paragraph leads first-time users through the form.
  * Four Adw.PreferencesGroups: Identity (hostname + username) →
    User account → Administrator (root) → Secure Boot enrollment
    (MOK, optional, EFI-only).
  * Inline password-strength feedback on the user + root password
    fields, using the same warn/ok row vocabulary as the LUKS
    strength row on the Disk page.
  * Adw.EntryRow / Adw.PasswordEntryRow throughout — libadwaita-
    native widgets with built-in show/hide on passwords + consistent
    chrome with the other Wave-3 pages.

D-001 / MOK contract preserved verbatim. All validation still runs in
on_next() against the backend validators; the inline feedback is an
affordance, not a gate.
"""

from gi.repository import Adw, GLib, Gtk

from installer.backend._validators import (
    validate_hostname,
    validate_mok_password,
    validate_password,
    validate_username,
)

from .. import doc_viewer
from ._base import _ForgePage, _toast


_PASSWORD_MIN_LEN = 8  # mirrors backend/_validators.py:_PASSWORD_MIN_LEN


def _password_strength_warning(pp: str) -> str:
    """Soft strength heuristic — same shape as the LUKS passphrase
    strength row on the Disk page. Returns warning text or '' (ok).

    Hard length floor is enforced by backend.validate_password() in
    on_next; this is the in-line "could be better" guidance."""
    if not pp:
        return "Empty passwords are not accepted."
    if len(pp) < _PASSWORD_MIN_LEN:
        return (
            f"Password is {len(pp)} characters — under the "
            f"{_PASSWORD_MIN_LEN}-character floor. The installer "
            "will reject it on Next."
        )
    classes = sum(
        bool(any(test(c) for c in pp))
        for test in (str.isupper, str.islower, str.isdigit,
                     lambda c: not c.isalnum())
    )
    if len(pp) < 12 and classes < 2:
        return (
            f"Password is {len(pp)} characters with only one "
            "character class. Consider lengthening it or mixing types."
        )
    return ""


class UserPage(_ForgePage):
    tag = "user"
    title = "User"

    def _build_body(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_hexpand(True)

        # ─── HERO ACCOUNT CARD ───────────────────────────────────────
        self._hero_card = self._build_hero_card()
        page.append(self._hero_card)

        # ─── INSTRUCTION LINE ────────────────────────────────────────
        instruction = Gtk.Label(label=(
            "Pick a username and set passwords. The user account is "
            "what you'll log in with day-to-day; the administrator "
            "(root) account is reserved for system maintenance. Scroll "
            "down for Secure Boot enrollment (optional, EFI only) "
            "before clicking 'Next' to advance."
        ))
        instruction.add_css_class("forge-instruction")
        instruction.set_halign(Gtk.Align.CENTER)
        instruction.set_justify(Gtk.Justification.CENTER)
        instruction.set_wrap(True)
        instruction.set_max_width_chars(72)
        page.append(instruction)

        # ─── IDENTITY GROUP ──────────────────────────────────────────
        id_group = Adw.PreferencesGroup()
        id_group.set_title("Identity")
        id_group.set_description(
            "The machine's name on the network and your login name. "
            "Both default to safe values if you leave them as-is."
        )

        self._hostname_row = Adw.EntryRow()
        self._hostname_row.set_title("Hostname")
        self._hostname_row.connect("notify::text", self._on_field_changed)
        id_group.add(self._hostname_row)

        self._username_row = Adw.EntryRow()
        self._username_row.set_title("Username")
        self._username_row.connect("notify::text", self._on_field_changed)
        id_group.add(self._username_row)

        page.append(id_group)

        # ─── USER ACCOUNT GROUP ──────────────────────────────────────
        user_group = Adw.PreferencesGroup()
        user_group.set_title("User account")
        user_group.set_description(
            "The account you'll use day-to-day. Pick something memorable "
            "but hard to guess — minimum 8 characters."
        )

        self._user_pw_row = Adw.PasswordEntryRow()
        self._user_pw_row.set_title("Password")
        self._user_pw_row.set_show_apply_button(False)
        self._user_pw_row.connect("notify::text", self._on_user_pw_changed)
        user_group.add(self._user_pw_row)

        self._user_confirm_row = Adw.PasswordEntryRow()
        self._user_confirm_row.set_title("Confirm password")
        self._user_confirm_row.set_show_apply_button(False)
        self._user_confirm_row.connect("notify::text", self._on_user_pw_changed)
        user_group.add(self._user_confirm_row)

        self._user_strength_row = Adw.ActionRow()
        self._user_strength_row.set_title("")
        self._user_strength_row.set_subtitle("")
        self._user_strength_row.add_css_class("forge-strength-row")
        self._user_strength_row.set_visible(False)
        user_group.add(self._user_strength_row)

        page.append(user_group)

        # ─── ADMINISTRATOR (ROOT) GROUP ──────────────────────────────
        root_group = Adw.PreferencesGroup()
        root_group.set_title("Administrator")
        root_group.set_description(
            "The root account, used only for system maintenance via "
            "sudo. Pick a different password from your user account; "
            "minimum 8 characters."
        )

        self._root_pw_row = Adw.PasswordEntryRow()
        self._root_pw_row.set_title("Password")
        self._root_pw_row.set_show_apply_button(False)
        self._root_pw_row.connect("notify::text", self._on_root_pw_changed)
        root_group.add(self._root_pw_row)

        self._root_confirm_row = Adw.PasswordEntryRow()
        self._root_confirm_row.set_title("Confirm password")
        self._root_confirm_row.set_show_apply_button(False)
        self._root_confirm_row.connect("notify::text", self._on_root_pw_changed)
        root_group.add(self._root_confirm_row)

        self._root_strength_row = Adw.ActionRow()
        self._root_strength_row.set_title("")
        self._root_strength_row.set_subtitle("")
        self._root_strength_row.add_css_class("forge-strength-row")
        self._root_strength_row.set_visible(False)
        root_group.add(self._root_strength_row)

        page.append(root_group)

        # ─── SECURE BOOT (MOK) GROUP ─────────────────────────────────
        # Optional + EFI-only. Setting a password turns enrollment ON;
        # an empty MOK password skips it (the user can re-enroll with
        # mokutil later). The description states the observed procedure
        # end to end: install with Secure Boot off -> set the password
        # here -> re-enable Secure Boot at the first reboot, which is
        # what triggers MokManager.
        mok_group = Adw.PreferencesGroup()
        mok_group.set_title("Secure Boot enrollment")
        mok_group.set_description(
            "Optional · EFI only. Set a password here to turn enrollment "
            "on; leave it empty to skip (you can re-enroll later with "
            "mokutil). You choose this password — it is never generated, "
            "shown back, or logged. After the install finishes, re-enable "
            "Secure Boot in your UEFI firmware setup: that is what "
            "triggers MokManager at the next boot, where you type this "
            "password to register your per-machine MOK with the firmware."
        )

        self._mok_pw_row = Adw.PasswordEntryRow()
        self._mok_pw_row.set_title("MOK enrollment password")
        self._mok_pw_row.set_show_apply_button(False)
        self._mok_pw_row.connect("notify::text", self._on_mok_pw_changed)
        mok_group.add(self._mok_pw_row)

        # Clickable docs row — opens an inline viewer dialog with the
        # secure-boot walkthrough rendered from markdown. Strong visual
        # affordance (leading book icon + ECG-blue chevron + accent CSS
        # class) so the row reads as a link, not a regular settings row.
        # Falls back to a "doc not installed" dialog with the GitHub URL
        # if the live ISO doesn't ship the doc tree (older ISOs).
        self._mok_docs_row = Adw.ActionRow()
        self._mok_docs_row.set_title("Open the first-boot walkthrough")
        self._mok_docs_row.set_subtitle(
            "Screenshots of the MokManager prompts + recovery procedure "
            "if MOK enrollment fails at first boot."
        )
        self._mok_docs_row.set_activatable(True)
        self._mok_docs_row.add_css_class("forge-docs-link")
        # Leading document icon signals "this is documentation".
        # Adwaita symbolic set has no open-book glyph; x-office-document
        # is the standard libadwaita icon for "this is a document".
        doc_icon = Gtk.Image.new_from_icon_name("x-office-document-symbolic")
        doc_icon.set_pixel_size(22)
        doc_icon.add_css_class("forge-docs-link-icon")
        self._mok_docs_row.add_prefix(doc_icon)
        # Bigger ECG-blue chevron on the right — unmistakable click hint.
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chevron.set_pixel_size(20)
        chevron.add_css_class("forge-docs-link-chevron")
        self._mok_docs_row.add_suffix(chevron)
        self._mok_docs_row.connect("activated", self._on_mok_docs_activated)
        mok_group.add(self._mok_docs_row)

        page.append(mok_group)

        return page

    # ─── HERO CARD ────────────────────────────────────────────────────
    def _build_hero_card(self) -> Gtk.Widget:
        """Account summary card — big user-default-symbolic avatar on
        the left, live username@hostname identity on the right, plus
        a status-badge row that flips as fields fill in."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("forge-account-hero")
        card.set_hexpand(True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)

        self._hero_avatar = Gtk.Image.new_from_icon_name(
            "avatar-default-symbolic"
        )
        self._hero_avatar.set_pixel_size(76)
        self._hero_avatar.add_css_class("forge-account-avatar")
        self._hero_avatar.set_valign(Gtk.Align.CENTER)
        self._hero_avatar.set_halign(Gtk.Align.CENTER)
        self._hero_avatar.set_size_request(108, 108)
        top_row.append(self._hero_avatar)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        self._hero_identity = Gtk.Label()
        self._hero_identity.add_css_class("forge-account-identity")
        self._hero_identity.set_halign(Gtk.Align.START)
        self._hero_identity.set_label("user@intergenos")
        info.append(self._hero_identity)

        self._hero_subtitle = Gtk.Label()
        self._hero_subtitle.add_css_class("forge-account-subtitle")
        self._hero_subtitle.set_halign(Gtk.Align.START)
        self._hero_subtitle.set_label("Your account on InterGenOS")
        info.append(self._hero_subtitle)

        top_row.append(info)
        card.append(top_row)

        # Status badge row — flips checkmarks as fields fill in.
        self._hero_badges = Gtk.Label()
        self._hero_badges.add_css_class("forge-account-badges")
        self._hero_badges.set_halign(Gtk.Align.START)
        self._hero_badges.set_use_markup(True)
        self._hero_badges.set_wrap(True)
        card.append(self._hero_badges)

        return card

    def _update_hero(self) -> None:
        """Repaint the identity line + status badges based on current
        field state. Cheap to call on every keystroke (~6 entries)."""
        hostname = (self._hostname_row.get_text() or "").strip() or "intergenos"
        username = (self._username_row.get_text() or "").strip() or "user"
        self._hero_identity.set_label(f"{username}@{hostname}")

        def badge(label: str, ok: bool, optional: bool = False) -> str:
            if ok:
                mark = "<span color='#34d399' weight='bold'>✓</span>"
                color = "#b6c3d2"
            elif optional:
                mark = "<span color='#7a8ba8'>⊘</span>"
                color = "#7a8ba8"
            else:
                mark = "<span color='#7a8ba8'>○</span>"
                color = "#7a8ba8"
            return (
                f"<span color='{color}'>{mark}  "
                f"{GLib.markup_escape_text(label)}</span>"
            )

        u_pw = self._user_pw_row.get_text()
        u_cf = self._user_confirm_row.get_text()
        r_pw = self._root_pw_row.get_text()
        r_cf = self._root_confirm_row.get_text()
        mok = self._mok_pw_row.get_text()

        username_filled = bool((self._username_row.get_text() or "").strip())
        user_pw_match = bool(u_pw) and u_pw == u_cf
        root_pw_match = bool(r_pw) and r_pw == r_cf

        parts = [
            badge("Username", username_filled),
            badge("User password", user_pw_match),
            badge("Admin password", root_pw_match),
            badge("MOK (optional)", bool(mok), optional=True),
        ]
        self._hero_badges.set_markup("   ·   ".join(parts))

    # ─── FIELD CHANGE HANDLERS ───────────────────────────────────────
    def _on_field_changed(self, *_):
        self._update_hero()

    def _on_user_pw_changed(self, *_):
        self._update_strength_row(
            self._user_strength_row, self._user_pw_row.get_text()
        )
        self._update_hero()

    def _on_root_pw_changed(self, *_):
        self._update_strength_row(
            self._root_strength_row, self._root_pw_row.get_text()
        )
        self._update_hero()

    def _on_mok_pw_changed(self, *_):
        self._update_hero()

    # ─── INLINE DOC VIEWER ────────────────────────────────────────────
    def _on_mok_docs_activated(self, _row):
        doc_viewer.open_doc_by_filename(
            self._window,
            filename="secure-boot-and-mok.md",
            title="Secure Boot enrollment — first-boot walkthrough",
            doc_label="Secure Boot walkthrough",
        )

    def _update_strength_row(self, row: Adw.ActionRow, pp: str) -> None:
        if not pp:
            row.set_visible(False)
            return
        warn = _password_strength_warning(pp)
        if warn:
            row.set_title("⚠   Weak password")
            row.set_subtitle(warn)
            row.remove_css_class("forge-strength-ok")
            row.add_css_class("forge-strength-warn")
        else:
            row.set_title("✓   Password looks reasonable")
            row.set_subtitle(
                "Length passes the minimum recommendation."
            )
            row.remove_css_class("forge-strength-warn")
            row.add_css_class("forge-strength-ok")
        row.set_visible(True)

    # ─── LIFECYCLE ────────────────────────────────────────────────────
    def on_load(self, state):
        # Pre-fill hostname + username from state if set. Passwords
        # NEVER pre-fill — re-prompt for the secret each visit (same
        # contract as LUKS passphrase fields).
        self._hostname_row.set_text(state.hostname or "intergenos")
        self._username_row.set_text(state.username or "")
        self._user_pw_row.set_text("")
        self._user_confirm_row.set_text("")
        self._root_pw_row.set_text("")
        self._root_confirm_row.set_text("")
        self._mok_pw_row.set_text("")
        self._user_strength_row.set_visible(False)
        self._root_strength_row.set_visible(False)
        self._update_hero()

    def on_next(self, state):
        state.hostname = (self._hostname_row.get_text() or "intergenos").strip()
        state.username = (self._username_row.get_text() or "").strip()
        state.user_password = self._user_pw_row.get_text()
        state.user_password_confirm = self._user_confirm_row.get_text()
        state.root_password = self._root_pw_row.get_text()
        state.root_password_confirm = self._root_confirm_row.get_text()
        state.mok_password = self._mok_pw_row.get_text()

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
            _toast(self._window,
                   "Both user and root passwords are required.")
            return False

        user_pw_err = validate_password(state.user_password, role="user password")
        if user_pw_err:
            _toast(self._window, user_pw_err)
            return False
        root_pw_err = validate_password(state.root_password, role="root password")
        if root_pw_err:
            _toast(self._window, root_pw_err)
            return False

        mok_pw_err = validate_mok_password(state.mok_password)
        if mok_pw_err:
            _toast(self._window, mok_pw_err)
            return False

        # Recorded only after validation passes, so an invalid passphrase
        # never registers as an enrollment choice. Re-assigned on every
        # forward transit: a user who backs up and blanks the field
        # correctly unsets it. The Done page reads this flag, not
        # mok_password (which clear_sensitive_data() scrubs first).
        state.mok_enrollment_chosen = bool(state.mok_password)

        return True
