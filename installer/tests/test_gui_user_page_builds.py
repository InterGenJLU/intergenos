# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The user screen builds, with the signing-key rows on it.

This exists because of a mistake made while adding those rows: the new group was
attached with the wrong call for the container it was going into, and every unit
test still passed, because nothing in the suite ever built the page. The error
appeared only when the real page was constructed.

So the test builds the real page against the real toolkit. It is skipped, loudly,
where the toolkit cannot start at all; the suites are run with -rs so a skip is
visible rather than silent.
"""

import unittest

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    _TOOLKIT_READY = bool(Gtk.init_check())
    if _TOOLKIT_READY:
        Adw.init()
    _WHY = ""
except Exception as exc:  # noqa: BLE001 — any toolkit failure is a skip reason
    _TOOLKIT_READY = False
    _WHY = f"{type(exc).__name__}: {exc}"


@unittest.skipUnless(
    _TOOLKIT_READY,
    f"this machine cannot start GTK4/libadwaita, so the real page cannot be "
    f"built here ({_WHY or 'Gtk.init_check() returned false'})")
class TestTheUserPageBuilds(unittest.TestCase):

    def test_it_builds_and_carries_the_signing_key_rows(self):
        from installer.frontend.gui.screens.user import UserPage
        page = UserPage(None)
        self.assertEqual(page._key_pw_row.get_title(), "Signing key passphrase")
        self.assertEqual(page._key_confirm_row.get_title(),
                         "Confirm signing key passphrase")

    def test_the_two_secrets_are_presented_as_different_things(self):
        """A person told 'another password' twice learns nothing about either."""
        from installer.frontend.gui.screens.user import UserPage
        page = UserPage(None)
        self.assertNotEqual(page._key_pw_row.get_title(),
                            page._mok_pw_row.get_title())
        description = page._key_group.get_description() or ""
        self.assertIn("sign", description.lower())


if __name__ == "__main__":
    unittest.main()
