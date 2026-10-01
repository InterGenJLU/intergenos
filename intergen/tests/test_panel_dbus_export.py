"""The panel's dock-mode D-Bus export, pinned against the code and not the host.

The exporter shipped a GJS idiom (``Gio.DBus.session``) that does not exist in
PyGObject, so every dock-mode activate raised AttributeError inside
_export_dbus — never seen in the field only because the default window mode
skips the export entirely. That is the first thing pinned here.

WHY THIS FILE IS HERMETIC NOW. It used to call ``p._export_dbus()`` unguarded,
which took the AMBIENT session connection, registered an object on it and then
requested the PRODUCTION well-known name — the name the shipped panel itself
owns in dock mode — and then asserted only that three integers were non-zero.
Under G_BUS_NAME_OWNER_FLAGS_NONE a request for a name another connection
already holds is not an error: the daemon replies IN_QUEUE and the call still
returns a non-zero owner id, because acquisition is reported only through the
name_acquired closure, which that case passed as None while running no main
loop. So its verdict was a reading of whether this machine's session already
owned the panel name, not of the registration path; on a host with no
resolvable session bus it errored on the venue; and when the export failed part
way it left the object registered on the connection the whole process shares.

Every other D-Bus unit test in this tree fakes the bus at Gio.bus_get_sync
(test_dbus_stop_unexports_the_object.py, test_take_screenshot.py,
test_the_record_read_states_and_the_version_display.py,
tests/welcome/test_ready_state.py), and the one real-bus case is gated behind
unittest.skipUnless and takes a process-unique name (test_integration.py). This
file follows the first convention: the three Gio entry points on the export
path are replaced, so no bus, socket, daemon, display or network is touched and
the production name is never requested of a live bus. The real-bus reproduction
rides in the evidence directory, not in the suite.

Gio.DBusNodeInfo.new_for_xml is left REAL: it only parses the interface XML in
process, so leaving it real keeps more of the path under test.
"""

from __future__ import annotations

import unittest
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

OBJECT_PATH = "/org/intergenos/InterGenPanel"
PANEL_NAME = "org.intergenos.InterGenPanel"


class _FakeConnection:
    """Only what _export_dbus and the teardown call on a connection.

    ONE instance per fake bus, because the connection Gio hands back is shared
    by the whole process — that sharing is why a stranded registration outlives
    the case that made it.
    """

    def __init__(self) -> None:
        self.exported: dict[str, int] = {}
        self.unregistered: list[int] = []
        # Not 1: a teardown that released the wrong handle must not pass by
        # coincidence against a first id that happens to be truthy.
        self._next = 7

    def register_object_with_closures2(self, path, interface, on_call,
                                      get_prop, set_prop):
        if path in self.exported:
            raise Exception(
                "g-io-error-quark: An object is already exported for the "
                f"interface at {path} (2)")
        reg_id = self._next
        self._next += 1
        self.exported[path] = reg_id
        return reg_id

    def unregister_object(self, reg_id):
        for path, rid in list(self.exported.items()):
            if rid == reg_id:
                del self.exported[path]
                self.unregistered.append(reg_id)
                return True
        raise Exception(f"no such registration: {reg_id}")


class _FakeBus:
    """The three module-level Gio calls the export path makes, and no more.

    ``grant`` decides which closure the stand-in invokes, which is how a real
    bus reports the outcome: name_acquired when this connection is given the
    name, name_lost when the request is refused (DO_NOT_QUEUE) or the name is
    taken away later.
    """

    def __init__(self, *, grant: bool = True, own_raises=None,
                 get_raises=None) -> None:
        self.connection = _FakeConnection()
        self.requested: list[tuple[str, int]] = []
        self.unowned: list[int] = []
        self._grant = grant
        self._own_raises = own_raises
        self._get_raises = get_raises
        self._next_owner = 11

    def bus_get_sync(self, *a, **kw):
        if self._get_raises is not None:
            raise self._get_raises
        return self.connection

    def own_name_on_connection(self, connection, name, flags,
                               acquired=None, lost=None):
        if self._own_raises is not None:
            raise self._own_raises
        owner_id = self._next_owner
        self._next_owner += 1
        self.requested.append((name, int(flags)))
        # A real bus reports the outcome through exactly one of these.
        if self._grant:
            if acquired is not None:
                acquired(connection, name)
        elif lost is not None:
            lost(connection, name)
        return owner_id

    def unown_name(self, owner_id):
        self.unowned.append(owner_id)


def _a_bare_panel():
    """A panel instance with no Gtk.Application behind it.

    __new__, not __init__: the constructor builds a Gtk.Application, which a
    headless suite run must not require. _export_dbus reads only module
    constants and binds its own methods, so a bare instance exercises exactly
    the registration path under test.
    """
    from intergen import panel as panel_mod
    return object.__new__(panel_mod.PanelWindow)


def _export(panel, fake):
    """Drive the real _export_dbus with every bus entry point replaced."""
    from gi.repository import Gio
    with mock.patch.object(Gio, "bus_get_sync", fake.bus_get_sync), \
         mock.patch.object(Gio, "bus_own_name_on_connection",
                           fake.own_name_on_connection), \
         mock.patch.object(Gio, "bus_unown_name", fake.unown_name):
        panel._export_dbus()


def _teardown(panel, fake):
    from gi.repository import Gio
    with mock.patch.object(Gio, "bus_unown_name", fake.unown_name):
        panel._teardown_dbus()


class PanelDbusExportTests(unittest.TestCase):

    def test_export_dbus_registers_on_session_bus(self):
        """The path executes under PyGObject and leaves nothing behind."""
        from gi.repository import Gio
        panel = _a_bare_panel()
        fake = _FakeBus(grant=True)

        _export(panel, fake)

        self.assertEqual({OBJECT_PATH: panel._dbus_id}, fake.connection.exported,
                         "the object was not registered on the connection the "
                         "export took")
        self.assertGreater(panel._dbus_id, 0)
        self.assertGreater(panel._bus_name_id, 0)
        self.assertIs(panel._dbus_conn, fake.connection)
        # The name is requested DO_NOT_QUEUE, so a name another connection
        # already holds is refused rather than silently queued behind it.
        self.assertEqual(
            [(PANEL_NAME, int(Gio.BusNameOwnerFlags.DO_NOT_QUEUE))],
            fake.requested,
            "the panel's own name was not requested with DO_NOT_QUEUE")

        _teardown(panel, fake)

        self.assertEqual({}, fake.connection.exported,
                         "the teardown left the object registered on a "
                         "connection the whole process shares")
        self.assertEqual([], [p for p in fake.connection.exported])
        self.assertEqual(0, panel._dbus_id)
        self.assertEqual(0, panel._bus_name_id)
        self.assertIsNone(panel._dbus_conn)


class ThePanelKnowsWhetherItGotTheNameTests(unittest.TestCase):
    """A non-zero owner id is not ownership; the closures are.

    This is the half the previous case could not see: the call returns a handle
    whether the name was granted or the request was refused, so the panel has
    to record what the bus reported.
    """

    def test_a_granted_name_is_recorded_as_owned(self):
        panel = _a_bare_panel()
        fake = _FakeBus(grant=True)
        _export(panel, fake)
        self.assertTrue(panel._bus_name_owned,
                        "the bus granted the name and the panel did not "
                        "record it")

    def test_a_refused_name_is_not_recorded_as_owned(self):
        panel = _a_bare_panel()
        fake = _FakeBus(grant=False)
        _export(panel, fake)
        self.assertGreater(
            panel._bus_name_id, 0,
            "guard: the call still returns a handle, which is the whole "
            "reason a handle cannot be read as ownership")
        self.assertFalse(
            panel._bus_name_owned,
            "the request was refused and the panel believes it owns the "
            "panel name; another process is answering its interface")


class AFailedExportLeavesNothingBehindTests(unittest.TestCase):
    """The connection is process-shared, so a stranded registration persists."""

    def test_a_name_request_that_raises_releases_the_registration(self):
        panel = _a_bare_panel()
        fake = _FakeBus(own_raises=GLib.Error(
            "g-io-error-quark: name ownership refused", "g-io-error-quark", 0))

        with self.assertRaises(GLib.Error):
            _export(panel, fake)

        self.assertEqual(
            {}, fake.connection.exported,
            "the name request failed after the object was registered and the "
            f"export left {OBJECT_PATH} registered on the connection the "
            "whole process shares; the next export of that path would raise "
            "'already exported' with no handle left to release it")
        self.assertEqual(0, panel._dbus_id)
        self.assertEqual(0, panel._bus_name_id)
        self.assertIsNone(panel._dbus_conn)
        self.assertFalse(panel._bus_name_owned)

    def test_a_host_with_no_session_bus_fails_in_one_named_place(self):
        """The export reports the venue's fault as the connection step's."""
        panel = _a_bare_panel()
        fake = _FakeBus(get_raises=GLib.Error(
            "Cannot autolaunch D-Bus without X11 $DISPLAY",
            "g-io-error-quark", 36))

        with self.assertRaises(GLib.Error):
            _export(panel, fake)

        self.assertEqual({}, fake.connection.exported,
                         "nothing may be registered when the connection could "
                         "not be obtained at all")


if __name__ == "__main__":
    unittest.main()
