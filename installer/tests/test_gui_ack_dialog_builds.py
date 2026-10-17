# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Construct the acknowledgment dialog with the installed GTK toolkit."""

import unittest
import warnings

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    _TOOLKIT_READY = bool(Gtk.init_check())
    if _TOOLKIT_READY:
        Adw.init()
    _WHY = ""
except Exception as exc:
    _TOOLKIT_READY = False
    _WHY = f"{type(exc).__name__}: {exc}"


@unittest.skipUnless(
    _TOOLKIT_READY,
    "the installed GTK4/libadwaita toolkit cannot start, so the real dialog "
    f"cannot be checked here ({_WHY or 'Gtk.init_check() returned false'})",
)
class TestAcknowledgmentDialogBuilds(unittest.TestCase):
    def test_the_real_dialog_constructs_with_its_entry(self):
        from installer.frontend.gui import integrity_dialog

        self.assertIs(integrity_dialog.Gtk, Gtk)
        parent = Adw.Window()
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", message="g_object_ref:")
                integrity_dialog._show_ack_dialog_async(
                    parent, "example-package", "example phrase", lambda phrase: None,
                )
            dialogs = [
                w for w in Gtk.Window.list_toplevels()
                if isinstance(w, Adw.MessageDialog)
                and w.get_transient_for() is parent
            ]
            self.assertEqual(len(dialogs), 1)
            dialog = dialogs[0]
            self.assertTrue(dialog.get_visible())
            entry = dialog.get_extra_child()
            self.assertIsInstance(entry, Gtk.Entry)
            self.assertIsInstance(entry.get_delegate(), Gtk.Text)
            self.assertIsNone(entry.get_extra_menu())
            self.assertFalse(dialog.get_response_enabled("submit"))
        finally:
            # Own fixture windows only; construction failures also leave no UI.
            for dialog in Gtk.Window.list_toplevels():
                if dialog.get_transient_for() is parent:
                    dialog.destroy()
            parent.destroy()
