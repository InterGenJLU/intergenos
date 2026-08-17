"""The panel's dock-mode D-Bus export must actually execute under PyGObject.

The exporter shipped a GJS idiom (``Gio.DBus.session``) that does not exist
in PyGObject, so every dock-mode activate raised AttributeError inside
_export_dbus — never seen in the field only because the default window mode
skips the export entirely. This pins the registration path itself: a real
session-bus registration via register_object_with_closures2 against the
method's own code, then a clean teardown so the test leaves nothing owned.
"""

import unittest


class PanelDbusExportTests(unittest.TestCase):
    def test_export_dbus_registers_on_session_bus(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio
        from intergen import panel as panel_mod

        # __new__, not __init__: the constructor builds a Gtk.Application,
        # which a headless suite run must not require. _export_dbus reads
        # only module constants and binds self._on_dbus_call, so a bare
        # instance exercises exactly the registration path under test.
        p = object.__new__(panel_mod.PanelWindow)
        p._export_dbus()
        try:
            self.assertGreater(p._dbus_id, 0)
            self.assertGreater(p._bus_name_id, 0)
            self.assertIsNotNone(p._dbus_conn)
        finally:
            Gio.bus_unown_name(p._bus_name_id)
            p._dbus_conn.unregister_object(p._dbus_id)


if __name__ == "__main__":
    unittest.main()
