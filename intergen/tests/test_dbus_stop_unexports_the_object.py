# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An in-process daemon restart must not lose D-Bus.

THE OBSERVED FAILURE. An in-process stop->start — what the scenario harness's
restart_daemon does — logs:

    D-Bus export failed: g-io-error-quark: An object is already exported for the
    interface com.intergenos.InterGen at /com/intergenos/InterGen (2). Running
    without D-Bus.

seen x10 in the 146 battery and x10 again in the 35B re-drive on dev 1b0872734.
The daemon then serves the REST of that life with no D-Bus at all, and the only
sign is one warning line: a silent degrade, not a crash.

WHY IT HAPPENS, read in the code and then MEASURED against the real session bus
(see the probe capture in this cut's evidence directory):

  * InterGenDaemon.__init__ sets self._bus = None and does NOT define
    self._reg_id or self._owner_id at all.
  * _export_dbus takes the SESSION connection with Gio.bus_get_sync, registers
    the object (-> self._reg_id) and owns the name (-> self._owner_id).
  * stop_service tears down the web server, caches, watchdogs and llama and
    nulls the router, but NEVER unregisters the object, NEVER unowns the name,
    and never drops self._bus.
  * Gio.bus_get_sync returns the connection SHARED BY THE PROCESS, so the second
    start re-registers the same path on the still-live connection and GDBus
    raises. _export_dbus catches it, logs the line above and sets self._bus =
    None — so the failure is recorded but never acted on.

Measured at the base commit with the real bus: after stop_service, self._bus was
NOT None and _reg_id / _owner_id were both still 1 — nothing had been released.

WHY THE CONNECTION HERE IS A STUB AND NOT THE REAL BUS. A live InterGen daemon
owns com.intergenos.InterGen on this machine's session bus, and the suite must
never touch a live daemon. The stub below is not a guess at GDBus's behaviour: it
reproduces the one contract this defect turns on — a second registration of a
path already exported on the SAME connection raises, and the connection is shared
across calls — which was measured against the real bus before this file was
written. The real-bus reproduction rides in the evidence directory, not in the
suite.

TIER SCOPE (the 2026-08-26 all-tiers amendment). This path is TIER-INDEPENDENT BY
CONSTRUCTION: dbus_daemon._export_dbus and dbus_daemon.stop_service contain no
tier term of any kind. The daemon's only tier state is self._hardware_tier, set in
start_service's hardware-detection step and read by Status reporting and model
selection — never by the export or the teardown. The tests below still run under
all three serving tiers, because a claim that something does not depend on a value
is worth measuring rather than asserting.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import dbus_daemon as dd

# The three serving tiers, named as the battery names them, in the shape
# start_service stores (dbus_daemon.py: self._hardware_tier = {"level": ...}).
TIERS = (("2B", 1), ("9B", 2), ("35B", 3))

# The exact GDBus message the battery recorded, so the stub cannot drift into
# failing for some other reason and still look like this defect.
ALREADY_EXPORTED = (
    "g-io-error-quark: An object is already exported for the interface "
    "com.intergenos.InterGen at /com/intergenos/InterGen (2)")


class _FakeConnection:
    """The one GDBus contract this defect turns on, and nothing more.

    Registrations are keyed by object path, so a second registration of a path
    this connection still holds raises — exactly as measured against the real
    session bus at the base commit."""

    def __init__(self):
        self.exported: dict[str, int] = {}
        self._next = 1
        self.unregistered: list[int] = []

    def register_object_with_closures2(self, path, interface, on_call,
                                       get_prop, set_prop):
        if path in self.exported:
            raise Exception(ALREADY_EXPORTED)
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
    """Stands in for the module-level Gio calls _export_dbus makes. ONE
    connection instance is handed out for the life of the fake, because
    Gio.bus_get_sync returns the connection shared by the process — that sharing
    is the mechanism of the defect, so the fake must have it."""

    def __init__(self):
        self.connection = _FakeConnection()
        self.owned: list[int] = []
        self.unowned: list[int] = []
        self._next_owner = 1

    def bus_get_sync(self, *a, **kw):
        return self.connection

    def own_name_on_connection(self, *a, **kw):
        oid = self._next_owner
        self._next_owner += 1
        self.owned.append(oid)
        return oid

    def unown_name(self, owner_id):
        self.unowned.append(owner_id)


def _daemon(tier_level: int):
    """A bare daemon carrying only what _export_dbus and stop_service read.

    object.__new__, not the constructor: building a real daemon starts hardware
    detection, a model and a web server, none of which this path touches."""
    d = object.__new__(dd.InterGenDaemon)
    d._bus = None
    # __init__ defines these beside _bus, and object.__new__ skips __init__, so
    # the helper must set them or it would be modelling a daemon that cannot
    # exist. That __init__ really does define them is pinned separately by
    # ExportIdentifiersAreInitialisedTests, reading __init__'s own source —
    # so this line cannot quietly paper over a regression there.
    d._reg_id = None
    d._owner_id = None
    d._running = False
    d._hardware_tier = {"level": tier_level}
    for attr in ("_web_server", "_web_thread", "_web_loop", "_state_cache",
                 "_watchdog", "_embed_watchdog", "_llama", "_embed_llama",
                 "_router", "_llm", "_matcher", "_tools", "_governance",
                 "_health_agg"):
        setattr(d, attr, None)
    return d


class _StubbedBusCase(unittest.TestCase):
    def _run_with_stub(self, body):
        fake = _FakeBus()
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        class _Node:
            interfaces = [object()]

        with mock.patch.object(Gio, "bus_get_sync", fake.bus_get_sync), \
             mock.patch.object(Gio, "bus_own_name_on_connection",
                               fake.own_name_on_connection), \
             mock.patch.object(Gio, "bus_unown_name", fake.unown_name), \
             mock.patch.object(Gio.DBusNodeInfo, "new_for_xml",
                               staticmethod(lambda xml: _Node())):
            return body(fake)


class RestartKeepsDbusTests(_StubbedBusCase):
    def test_stop_then_start_exports_cleanly(self):
        """The defect itself: start -> stop -> start must export, not degrade."""
        for name, level in TIERS:
            with self.subTest(tier=name):
                def body(fake):
                    d = _daemon(level)
                    d._export_dbus()
                    self.assertIsNotNone(
                        d._bus, "the FIRST export failed; the stub is wrong")
                    d.stop_service()
                    d._export_dbus()
                    self.assertIsNotNone(
                        d._bus,
                        "an in-process restart left the daemon with no D-Bus: "
                        "stop_service did not release what _export_dbus took")
                    self.assertEqual(
                        list(fake.connection.exported), [dd.OBJECT_PATH],
                        "exactly one live registration is expected after a "
                        "restart")
                self._run_with_stub(body)

    def test_stop_releases_the_registration_and_the_name(self):
        """Named separately from the symptom: the release is the fix, and a
        later refactor that made the restart pass some other way would still
        have to keep this true."""
        for name, level in TIERS:
            with self.subTest(tier=name):
                def body(fake):
                    d = _daemon(level)
                    d._export_dbus()
                    reg, own = d._reg_id, d._owner_id
                    d.stop_service()
                    self.assertIn(reg, fake.connection.unregistered,
                                  "stop_service left the object exported")
                    self.assertIn(own, fake.unowned,
                                  "stop_service left the bus name owned")
                    self.assertIsNone(d._bus,
                                      "stop_service kept the connection")
                self._run_with_stub(body)

    def test_a_single_start_is_unchanged(self):
        """The guard: one clean export, no teardown, nothing released."""
        for name, level in TIERS:
            with self.subTest(tier=name):
                def body(fake):
                    d = _daemon(level)
                    d._export_dbus()
                    self.assertIsNotNone(d._bus)
                    self.assertEqual(list(fake.connection.exported),
                                     [dd.OBJECT_PATH])
                    self.assertEqual(fake.connection.unregistered, [])
                    self.assertEqual(fake.unowned, [])
                self._run_with_stub(body)

    def test_stop_without_any_export_is_quiet(self):
        """The guard: a daemon that never exported must still stop cleanly —
        the teardown may not assume the ids exist."""
        for name, level in TIERS:
            with self.subTest(tier=name):
                def body(fake):
                    d = _daemon(level)
                    d.stop_service()
                    self.assertIsNone(d._bus)
                    self.assertEqual(fake.unowned, [])
                self._run_with_stub(body)


class ExportIdentifiersAreInitialisedTests(unittest.TestCase):
    """_reg_id and _owner_id were never defined until a successful export, so
    every reader had to guess with getattr. A teardown that must run whether or
    not the export happened cannot rest on that."""

    def test_the_export_identifiers_exist_before_any_export(self):
        src = dd.InterGenDaemon.__init__.__doc__ or ""
        del src
        import inspect
        init = inspect.getsource(dd.InterGenDaemon.__init__)
        self.assertIn("self._reg_id", init,
                      "_reg_id is not initialised in __init__")
        self.assertIn("self._owner_id", init,
                      "_owner_id is not initialised in __init__")


class FailedExportLeavesNothingBehindTests(_StubbedBusCase):
    """If the export fails PART WAY — the object registered, then owning the
    name raised — the object is still exported on a shared connection. Clearing
    the identifier without releasing it would strand that registration and
    reproduce this very defect on the next start, with the handle to fix it
    thrown away."""

    def test_a_failure_after_registration_releases_the_registration(self):
        for name, level in TIERS:
            with self.subTest(tier=name):
                def body(fake):
                    def boom(*a, **kw):
                        raise Exception("name ownership refused")
                    import gi
                    from gi.repository import Gio
                    with mock.patch.object(Gio, "bus_own_name_on_connection",
                                           boom):
                        d = _daemon(level)
                        d._export_dbus()
                    self.assertIsNone(d._bus)
                    self.assertEqual(
                        fake.connection.exported, {},
                        "a part-way export left the object exported; the next "
                        "start will fail with 'already exported'")
                    d2 = _daemon(level)
                    d2._export_dbus()
                    self.assertIsNotNone(
                        d2._bus,
                        "a later start could not export after a failed one")
                self._run_with_stub(body)


if __name__ == "__main__":
    unittest.main()
