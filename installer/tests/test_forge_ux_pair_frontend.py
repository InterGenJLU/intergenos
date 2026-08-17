# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Frontend coverage for the Forge install-time UX pair (D1/1.25 default boot
target + D2/1.22 MOK-staging nudge), across the state model, the TUI pure
helpers, and — where a display backend exists — the GTK Confirm screen's real
on_load branch selection.
"""
from __future__ import annotations

import unittest

from installer.frontend.gui.state import InstallerState
from installer.frontend import tui


# ─── D1 + D2 state model ──────────────────────────────────────────────────
class MakeDefaultBootThreadingTests(unittest.TestCase):
    def test_none_omits_key(self):
        # Not asked (single-OS / non-EFI) -> key absent -> backend keeps prepend.
        st = InstallerState(make_default_boot=None)
        self.assertNotIn("make_default_boot", st.to_install_io())

    def test_true_threads_true(self):
        st = InstallerState(make_default_boot=True)
        self.assertEqual(st.to_install_io()["make_default_boot"], True)

    def test_false_threads_false(self):
        st = InstallerState(make_default_boot=False)
        self.assertEqual(st.to_install_io()["make_default_boot"], False)

    def test_default_is_none(self):
        self.assertIsNone(InstallerState().make_default_boot)


class MokSkipEfiSbOffTests(unittest.TestCase):
    def _st(self, sb, efi, mok=""):
        return InstallerState(secure_boot_enabled=sb, firmware_is_efi=efi,
                              mok_password=mok)

    def test_efi_sb_off_no_mok_is_true(self):
        self.assertTrue(self._st(False, True).mok_skip_efi_sb_off())

    def test_bios_sb_off_is_false(self):
        # Non-EFI: Secure Boot can never apply -> genuinely benign, not this case.
        self.assertFalse(self._st(False, False).mok_skip_efi_sb_off())

    def test_sb_on_is_false(self):
        self.assertFalse(self._st(True, True).mok_skip_efi_sb_off())

    def test_sb_unknown_is_false(self):
        self.assertFalse(self._st(None, True).mok_skip_efi_sb_off())

    def test_mok_set_is_false(self):
        self.assertFalse(self._st(False, True, mok="pw").mok_skip_efi_sb_off())


# ─── D2 TUI: MOK prompt text ──────────────────────────────────────────────
class MokPromptTextTests(unittest.TestCase):
    def test_sb_on_is_mandatory(self):
        t = tui._mok_prompt_text(True)
        self.assertIn("Secure Boot is ENABLED", t)
        self.assertIn("MUST enroll", t)

    def test_sb_off_is_loud_about_future_flip(self):
        t = tui._mok_prompt_text(False)
        # The D2 consequence must be explicit + loud, not the old benign note.
        self.assertIn("Secure Boot", t)
        self.assertIn("later enable Secure Boot", t)
        self.assertIn("mokutil --import", t)

    def test_sb_unknown_notes_unreadable(self):
        self.assertIn("could not be read", tui._mok_prompt_text(None))


# ─── D1 TUI: default-boot ask gate ────────────────────────────────────────
class ResolveMakeDefaultBootTests(unittest.TestCase):
    def test_non_efi_never_asks(self):
        called = []
        r = tui._resolve_make_default_boot(
            is_efi=False, has_other_os=True, ask_fn=lambda: called.append(1) or True)
        self.assertIsNone(r)
        self.assertEqual(called, [])

    def test_no_foreign_os_never_asks(self):
        called = []
        r = tui._resolve_make_default_boot(
            is_efi=True, has_other_os=False, ask_fn=lambda: called.append(1) or True)
        self.assertIsNone(r)
        self.assertEqual(called, [])

    def test_inconclusive_probe_never_asks(self):
        r = tui._resolve_make_default_boot(
            is_efi=True, has_other_os=None, ask_fn=lambda: True)
        self.assertIsNone(r)

    def test_efi_multi_os_asks_and_returns_choice(self):
        self.assertIs(
            tui._resolve_make_default_boot(True, True, lambda: True), True)
        self.assertIs(
            tui._resolve_make_default_boot(True, True, lambda: False), False)


# ─── GTK Confirm screen — real on_load branch selection (display-gated) ────
def _gtk_available():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk
        return bool(Gtk.init_check())
    except Exception:
        return False


@unittest.skipUnless(_gtk_available(), "no GTK4/Adw display backend")
class ConfirmScreenOnLoadTests(unittest.TestCase):
    def _page_and_state(self):
        import types
        from installer.frontend.gui.screens import confirm as confirm_mod
        win = types.SimpleNamespace(_screens=[], state=None)
        page = confirm_mod.ConfirmPage(win)
        st = InstallerState()
        st.target_disk = "/dev/sda"
        st.username = "user"
        st.hostname = "intergenos"
        return confirm_mod, page, st

    def _patch(self, mod, has_other, sb, efi):
        mod.has_other_os_boot_entries = lambda: has_other
        mod.is_secure_boot_enabled = lambda: sb
        mod.is_efi_firmware = lambda: efi

    def test_multi_os_reveals_switch_defaults_off(self):
        mod, page, st = self._page_and_state()
        self._patch(mod, has_other=True, sb=False, efi=True)
        page.on_load(st)
        self.assertTrue(page._dest_make_default.get_visible())
        self.assertIs(st.make_default_boot, False)  # respect existing default
        # Toggling the switch updates state (the user opts INTO being default).
        page._dest_make_default.set_active(True)
        self.assertIs(st.make_default_boot, True)

    def test_single_os_hides_switch_leaves_choice_unset(self):
        mod, page, st = self._page_and_state()
        self._patch(mod, has_other=False, sb=False, efi=True)
        page.on_load(st)
        self.assertFalse(page._dest_make_default.get_visible())
        self.assertIsNone(st.make_default_boot)

    def test_efi_sb_off_mok_note_is_loud(self):
        mod, page, st = self._page_and_state()
        self._patch(mod, has_other=False, sb=False, efi=True)
        st.mok_password = ""
        page.on_load(st)
        sub = page._sb_mok.get_subtitle()
        self.assertIn("Secure Boot OFF", sub)
        self.assertIn("mokutil --import", sub)
        self.assertTrue(page._sb_mok.has_css_class("forge-confirm-warning"))

    def test_bios_mok_note_is_benign(self):
        mod, page, st = self._page_and_state()
        self._patch(mod, has_other=False, sb=None, efi=False)
        st.mok_password = ""
        page.on_load(st)
        sub = page._sb_mok.get_subtitle()
        self.assertIn("BIOS", sub)
        self.assertFalse(page._sb_mok.has_css_class("forge-confirm-warning"))


if __name__ == "__main__":
    unittest.main()
