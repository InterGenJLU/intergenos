# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel D-Bus export case must pin the registration path, not the host.

THE CLAIM UNDER TEST, quoted from intergen/tests/test_panel_dbus_export.py's own
module docstring (lines 6-8):

    "This pins the registration path itself: a real session-bus registration via
    register_object_with_closures2 against the method's own code, then a clean
    teardown so the test leaves nothing owned."

WHAT THAT CASE ACTUALLY DOES. Line 26 calls p._export_dbus() unguarded, and that
method (intergen/panel/__init__.py:281) takes the AMBIENT session connection with
Gio.bus_get_sync(Gio.BusType.SESSION), registers an object on it, then requests
the PRODUCTION well-known name org.intergenos.InterGenPanel -- the name the
shipped panel itself owns in dock mode -- with Gio.BusNameOwnerFlags.NONE. The
case then asserts only that three integers are non-zero. It runs no main loop and
passes None for BOTH the name_acquired and name_lost closures, so nothing in it
can observe whether the name was granted, queued or lost.

THE TWO CONVENTIONS ALREADY IN THIS TREE THAT IT DEPARTS FROM:

  * Every other D-Bus unit test here fakes the bus at Gio.bus_get_sync --
    test_dbus_stop_unexports_the_object.py, test_take_screenshot.py,
    test_the_record_read_states_and_the_version_display.py and
    tests/welcome/test_ready_state.py. The first states the rule outright: "the
    suite must never touch a live daemon ... The real-bus reproduction rides in
    the evidence directory, not in the suite."
  * The ONE real-bus case, test_integration.py, is gated behind
    unittest.skipUnless(requires_provisioned_stack) AND takes a process-unique
    name, com.intergenos.InterGenTest{os.getpid()}, patched over SERVICE_NAME --
    never the production name.

test_panel_dbus_export.py has neither the fake, nor the gate, nor a unique name.

HOW THESE CASES MEASURE IT, AND WHY THEY ARE HERMETIC. Each one RUNS the
published case inside a unittest.TestResult under a fake bus and reads back the
verdict it reached. Gio.bus_get_sync, Gio.bus_own_name_on_connection and
Gio.bus_unown_name are the only three bus entry points on that path, and all
three are replaced, so no bus, socket, daemon, display or network is touched.
Gio.DBusNodeInfo.new_for_xml is left REAL: it only parses the interface XML in
process, so leaving it real keeps more of the path under test.

THE ONE GLib CONTRACT THE FAKE ENCODES, AND THE RESIDUE. Under
G_BUS_NAME_OWNER_FLAGS_NONE a RequestName for a name another connection already
holds is NOT an error: the daemon replies IN_QUEUE, and
g_bus_own_name_on_connection() still returns a non-zero owner id immediately,
because acquisition is reported only through the name_acquired closure. The fake
below reproduces that contract and nothing else. UNPROVEN HERE: that reply was
not re-measured against a live session bus in this run, because taking the
production panel name on this machine's session is the very act under objection.
"""

from __future__ import annotations

import unittest
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from intergen.tests import test_panel_dbus_export as published  # noqa: E402

CASE_METHOD = "test_export_dbus_registers_on_session_bus"
OBJECT_PATH = "/org/intergenos/InterGenPanel"
PRODUCTION_NAME = "org.intergenos.InterGenPanel"

# The real GLib failure a host without a resolvable session bus produces, worded
# as GLib words it so the stand-in cannot drift into failing for some other
# reason and still look like this defect.
NO_SESSION_BUS = GLib.Error(
    "Cannot autolaunch D-Bus without X11 $DISPLAY", "g-io-error-quark", 36)


class _FakeConnection:
    """Only what _export_dbus and the published case's teardown call on it.

    ONE instance per fake bus, because Gio.bus_get_sync hands back the
    connection SHARED BY THE PROCESS -- that sharing is why a registration this
    case strands outlives it for the rest of a suite run."""

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
        rid = self._next
        self._next += 1
        self.exported[path] = rid
        return rid

    def unregister_object(self, reg_id):
        for path, rid in list(self.exported.items()):
            if rid == reg_id:
                del self.exported[path]
                self.unregistered.append(reg_id)
                return True
        raise Exception(f"no such registration: {reg_id}")


class _FakeBus:
    """The three module-level Gio calls on this path, and nothing more.

    grant=False is the already-owned session: the request is QUEUED behind the
    existing owner, no error is raised, and an owner id is still returned."""

    def __init__(self, *, grant: bool = True, own_raises=None,
                 get_raises=None) -> None:
        self.connection = _FakeConnection()
        self.granted: list[str] = []
        self.queued: list[str] = []
        self.unowned: list[int] = []
        self._grant = grant
        self._own_raises = own_raises
        self._get_raises = get_raises
        self._next_owner = 11

    def bus_get_sync(self, *a, **kw):
        if self._get_raises is not None:
            raise self._get_raises
        return self.connection

    def own_name_on_connection(self, connection, name, flags, *rest):
        if self._own_raises is not None:
            raise self._own_raises
        oid = self._next_owner
        self._next_owner += 1
        (self.granted if self._grant else self.queued).append(name)
        return oid

    def unown_name(self, owner_id):
        self.unowned.append(owner_id)


def _why(result: unittest.TestResult) -> str:
    """The last line of every error and failure, for messages that say which."""
    parts: list[str] = []
    for kind, entries in (("ERROR", result.errors), ("FAILURE", result.failures)):
        for _test, tb in entries:
            parts.append(f"{kind}: {tb.strip().splitlines()[-1]}")
    return " | ".join(parts) or "(nothing reported)"


def _verdict(fake: _FakeBus) -> unittest.TestResult:
    """Run the PUBLISHED case under `fake` and hand back the verdict it reached.

    The patches go on gi.repository.Gio itself, which is the same module object
    intergen.panel bound at import and the published case imports inside its own
    method body, so both see them and neither reaches a real bus."""
    from gi.repository import Gio

    case = published.PanelDbusExportTests(CASE_METHOD)
    result = unittest.TestResult()
    with mock.patch.object(Gio, "bus_get_sync", fake.bus_get_sync), \
         mock.patch.object(Gio, "bus_own_name_on_connection",
                           fake.own_name_on_connection), \
         mock.patch.object(Gio, "bus_unown_name", fake.unown_name):
        case.run(result)
    return result


class ThePanelExportCasePinsThePathNotTheSessionTests(unittest.TestCase):

    def test_the_verdict_distinguishes_a_granted_name_from_a_queued_one(self):
        """A case that pins "a real session-bus registration" must reach a
        DIFFERENT verdict on a bus that granted the name and on a bus where the
        request only joined the queue behind an existing owner. Otherwise its
        verdict is a property of the host's session, not of the code."""
        granted = _FakeBus(grant=True)
        queued = _FakeBus(grant=False)

        on_free_bus = _verdict(granted)
        on_taken_bus = _verdict(queued)

        # Controls first, so an environment fault cannot masquerade as the
        # defect: the fake must have carried the case all the way to the name
        # request on both buses.
        self.assertEqual(
            [PRODUCTION_NAME], granted.granted,
            "the fake never reached the name request on the free bus, so this "
            f"file is not yet measuring the claim: {_why(on_free_bus)}")
        self.assertEqual(
            [PRODUCTION_NAME], queued.queued,
            "the fake never reached the name request on the taken bus, so this "
            f"file is not yet measuring the claim: {_why(on_taken_bus)}")
        self.assertTrue(
            on_free_bus.wasSuccessful(),
            "the published case could not pass even on a bus that granted the "
            f"name, so this file is not yet measuring the claim: "
            f"{_why(on_free_bus)}")

        self.assertNotEqual(
            on_free_bus.wasSuccessful(), on_taken_bus.wasSuccessful(),
            "the published case reached the SAME verdict (successful="
            f"{on_taken_bus.wasSuccessful()}) on a bus that GRANTED "
            f"{PRODUCTION_NAME} and on a bus where the request was only QUEUED "
            "behind an existing owner. It asserts _bus_name_id > 0, which "
            "g_bus_own_name_on_connection returns either way; it passes None "
            "for the name_acquired closure and runs no main loop, so nothing "
            "in it observes ownership. Its verdict is therefore a reading of "
            "whether this machine's session already owns the panel name -- it "
            "cannot pin 'a real session-bus registration', and it cannot know "
            "its teardown 'leaves nothing owned'. The tree's own real-bus case "
            "takes a per-process name instead (test_integration.py: "
            "com.intergenos.InterGenTest{os.getpid()}).")

    def test_the_case_reaches_a_verdict_where_there_is_no_session_bus(self):
        """A unit test that pins a code path must reach a verdict about that
        code, or skip. Erroring because the host has no session bus makes the
        suite's red a reading of the venue."""
        fake = _FakeBus(get_raises=NO_SESSION_BUS)

        result = _verdict(fake)

        self.assertEqual(
            {}, fake.connection.exported,
            "guard: nothing may be registered when the connection could not "
            "be obtained at all")
        self.assertEqual(
            [], result.errors,
            "on a host where the session bus cannot be resolved the published "
            "case ERRORED on its environment instead of reaching a verdict "
            f"about the registration path: {_why(result)}. Line 26 calls "
            "p._export_dbus() with no gate, so Gio.bus_get_sync raises straight "
            "through the case. Every other D-Bus unit test in this tree fakes "
            "the bus at Gio.bus_get_sync, and the one real-bus case is gated "
            "behind unittest.skipUnless -- this case has neither.")

    def test_a_part_way_export_leaves_nothing_registered(self):
        """The second half of the claim: "a clean teardown so the test leaves
        nothing owned". If the export fails AFTER the object is registered, the
        registration is still live on the connection the whole process shares."""
        fake = _FakeBus(own_raises=GLib.Error(
            "g-io-error-quark: name ownership refused", "g-io-error-quark", 0))

        result = _verdict(fake)

        # The export genuinely failed here, so the published case is EXPECTED
        # to report red. What is measured is only what it left behind.
        self.assertFalse(
            result.wasSuccessful(),
            "guard: a refused name request must not leave the case green")
        self.assertEqual(
            {}, fake.connection.exported,
            "the export failed after register_object_with_closures2 had "
            f"already succeeded, and the published case left {OBJECT_PATH} "
            "registered on the connection Gio.bus_get_sync shares across the "
            "whole process: line 26 calls p._export_dbus() OUTSIDE the try, so "
            "the finally that would release it never runs at all. The stranded "
            "registration outlives the case for the rest of the suite run and "
            "makes the next registration of that path raise 'already "
            "exported'. That contradicts 'a clean teardown so the test leaves "
            "nothing owned'. The daemon's equivalent path releases it and is "
            "pinned for exactly this "
            "(test_dbus_stop_unexports_the_object.py: "
            "FailedExportLeavesNothingBehindTests).")


if __name__ == "__main__":
    unittest.main()
