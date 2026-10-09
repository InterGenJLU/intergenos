# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel's bus name: what the panel records, never the handle it holds.

THE FINDING. In dock mode the panel registers its interface object on the
session connection and requests its well-known name. It used to request the
name with the default flags and no closures, and to take the handle
Gio.bus_own_name_on_connection returns as the outcome. A handle comes back
either way: when another connection already holds the name, a request with the
default flags joins the queue behind it, and acquisition is reported only
through the name_acquired closure, which the panel did not supply. So a second
panel went on as though it held the name while the first kept answering Show,
Hide, Toggle and the dock calls. And when the name request raised after the
object was registered, the registration stayed live on the connection the
whole process shares.

THE TWO STAND-INS. Both return a non-zero handle, and they differ only in what
the bus reports through the closures it is handed, delivered as a bus delivers
them, on a later main-loop iteration (deliver() below):

  * the free bus: this connection is the first to ask, so name_acquired runs;
  * the occupied bus: another connection holds the name, so name_acquired
    never runs. A request made with DO_NOT_QUEUE is refused and name_lost
    runs; any other request waits in the queue and nothing runs.

WHAT IS ASSERTED, each satisfiable by the panel and each refused by the panel
module as it was before the correction:

  1. the panel's ownership record follows the closures: nothing recorded as
     held before the bus has answered, then held on the free bus and not held
     on the occupied one, with a non-zero handle in both;
  2. the request is refused rather than queued: it carries DO_NOT_QUEUE and
     supplies both closures, and on the occupied bus name_lost is what runs;
  3. an export whose name request raises leaves nothing registered on the
     shared connection and nothing recorded as held.

The control: on both stand-ins the export registers its object and reaches the
name request with a non-zero handle back, and the stand-in's request takes the
arguments the real call declares, by name and in order. Without it, the three
above could be failing because the stand-in is never reached rather than
because the panel misreads the bus.

Hermetic: Gio.bus_get_sync, Gio.bus_own_name_on_connection and
Gio.bus_unown_name are replaced, so no bus, socket, daemon, display or network
is touched and the panel's name is never requested of a live bus;
Gio.DBusNodeInfo.new_for_xml stays real, as it only parses the interface XML.
The same contract measured on a message bus private to one run rides in the
evidence, not in the suite.

NOT PINNED HERE: a name holder that goes away and the panel then acquiring the
name; the panel as a desktop application.
"""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

OBJECT_PATH = "/org/intergenos/InterGenPanel"
PANEL_NAME = "org.intergenos.InterGenPanel"
DO_NOT_QUEUE = int(Gio.BusNameOwnerFlags.DO_NOT_QUEUE)


class _Connection:
    """Only what the export and its rollback call on a connection.

    One per stand-in bus, because the connection Gio hands back is shared by
    the whole process: a registration left on it outlives the panel.
    """

    def __init__(self) -> None:
        self.exported: dict[str, int] = {}
        # Not 1: a rollback that released the wrong id must not pass by
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
                return True
        raise Exception(f"no such registration: {reg_id}")


class _Bus:
    """The three Gio entry points the export path calls, and no more.

    occupied=False is a free bus: name_acquired runs. occupied=True is a bus
    where another connection holds the name: name_acquired never runs; a
    DO_NOT_QUEUE request is refused with name_lost, and any other request waits
    in the queue with nothing run. A non-zero handle is returned either way, as
    the real call returns one. What the bus reports runs on deliver(), the
    main-loop iteration on which a bus delivers it, not inside the request.
    """

    def __init__(self, *, occupied: bool = False,
                 request_raises: Exception | None = None) -> None:
        self.connection = _Connection()
        # (name, flags, name_acquired supplied, name_lost supplied)
        self.requests: list[tuple[str, int, bool, bool]] = []
        self.ran: list[str] = []
        self._occupied = occupied
        self._request_raises = request_raises
        self._pending: list = []
        self._next_handle = 11

    def bus_get_sync(self, *a, **kw):
        return self.connection

    def own_name_on_connection(self, connection, name, flags,
                               name_acquired_closure=None,
                               name_lost_closure=None):
        if self._request_raises is not None:
            raise self._request_raises
        flags = int(flags)
        self.requests.append((name, flags, callable(name_acquired_closure),
                              callable(name_lost_closure)))
        if not self._occupied:
            outcome = ("name_acquired", name_acquired_closure)
        elif flags & DO_NOT_QUEUE:
            outcome = ("name_lost", name_lost_closure)
        else:
            outcome = ("queued", None)
        self._pending.append((outcome, connection, name))
        handle = self._next_handle
        self._next_handle += 1
        return handle

    def deliver(self) -> None:
        """The main-loop iteration: run what the bus reports, once."""
        pending, self._pending = self._pending, []
        for (kind, closure), connection, name in pending:
            if closure is not None:
                self.ran.append(kind)
                closure(connection, name)
            elif kind == "queued":
                self.ran.append(kind)
            else:
                self.ran.append(f"{kind}, with no closure supplied to run")

    def unown_name(self, handle) -> None:
        pass


def _a_bare_panel():
    """A panel instance with no Gtk.Application behind it.

    __new__, not __init__: the constructor builds the application, which a
    headless run must not require, and the export reads only module constants
    and binds the panel's own methods.
    """
    from intergen import panel as panel_mod
    return object.__new__(panel_mod.PanelWindow)


def _export(panel, bus) -> None:
    """Drive the panel's real export with every bus entry point replaced."""
    with mock.patch.object(Gio, "bus_get_sync", bus.bus_get_sync), \
         mock.patch.object(Gio, "bus_own_name_on_connection",
                           bus.own_name_on_connection), \
         mock.patch.object(Gio, "bus_unown_name", bus.unown_name):
        panel._export_dbus()


def _held(panel):
    """The panel's record of holding its name; None if it keeps none."""
    return getattr(panel, "_bus_name_owned", None)


class ThePanelRecordsWhatTheBusReportsTests(unittest.TestCase):

    def test_both_stand_ins_reach_the_request_and_return_a_handle(self):
        """The control: the stand-ins are reached, and speak the real call."""
        for occupied in (False, True):
            bus_is = "the occupied bus" if occupied else "the free bus"
            panel = _a_bare_panel()
            bus = _Bus(occupied=occupied)
            _export(panel, bus)
            self.assertEqual(
                {OBJECT_PATH: getattr(panel, "_dbus_id", None)},
                bus.connection.exported,
                f"on {bus_is} the export did not register its object")
            self.assertEqual(
                [PANEL_NAME], [request[0] for request in bus.requests],
                f"on {bus_is} the export never reached the name request")
            self.assertGreater(
                getattr(panel, "_bus_name_id", 0), 0,
                f"on {bus_is} the request returned no handle")
        real = [arg.get_name()
                for arg in Gio.bus_own_name_on_connection.get_arguments()]
        stand_in = list(
            inspect.signature(_Bus.own_name_on_connection).parameters)[1:]
        self.assertEqual(real, stand_in,
                         "the stand-in's request does not take the arguments "
                         "the real call declares")

    def test_the_record_follows_the_closures_not_the_handle(self):
        readings = {}
        for occupied in (False, True):
            panel = _a_bare_panel()
            bus = _Bus(occupied=occupied)
            _export(panel, bus)
            before = _held(panel)
            bus.deliver()
            readings[occupied] = (before, _held(panel),
                                  getattr(panel, "_bus_name_id", 0), bus.ran)
        for occupied, (before, _after, handle, ran) in readings.items():
            bus_is = "the occupied bus" if occupied else "the free bus"
            self.assertIsNot(
                before, True,
                f"on {bus_is} the panel recorded the name as held before the "
                f"bus had answered (the handle is {handle})")
            self.assertGreater(
                handle, 0,
                f"guard: on {bus_is} the request returns a handle, which is "
                f"why a handle cannot be read as ownership")
        free_after, free_ran = readings[False][1], readings[False][3]
        taken_after, taken_ran = readings[True][1], readings[True][3]
        self.assertIs(
            free_after, True,
            f"the free bus reported {free_ran} and the panel's record of "
            f"holding {PANEL_NAME} is {free_after!r}")
        self.assertIs(
            taken_after, False,
            f"another connection holds {PANEL_NAME}, the bus reported "
            f"{taken_ran}, and the panel's record is {taken_after!r}: it "
            f"cannot tell that another panel answers its interface")

    def test_the_request_is_refused_rather_than_queued(self):
        panel = _a_bare_panel()
        bus = _Bus(occupied=True)
        _export(panel, bus)
        bus.deliver()
        self.assertEqual(1, len(bus.requests), bus.requests)
        _name, flags, acquired, lost = bus.requests[0]
        self.assertTrue(
            flags & DO_NOT_QUEUE,
            f"the request carried flags {flags}, so with another connection "
            f"holding {PANEL_NAME} it waits in the queue instead of being "
            f"refused")
        self.assertTrue(
            acquired and lost,
            f"the request supplied name_acquired: {acquired}, name_lost: "
            f"{lost}; without both the panel cannot hear the bus's answer")
        self.assertEqual(["name_lost"], bus.ran,
                         "the occupied bus did not refuse the request")

    def test_an_export_whose_request_raises_leaves_nothing_behind(self):
        panel = _a_bare_panel()
        bus = _Bus(request_raises=GLib.Error(
            "g-io-error-quark: name ownership refused", "g-io-error-quark", 0))
        with self.assertRaises(GLib.Error):
            _export(panel, bus)
        self.assertEqual(
            {}, bus.connection.exported,
            f"the name request raised after the object was registered and "
            f"the export left {OBJECT_PATH} registered on the connection the "
            f"whole process shares; the next export of that path would raise "
            f"'already exported' with no handle left to release it")
        self.assertIsNot(_held(panel), True,
                         "a failed export left the name recorded as held")


if __name__ == "__main__":
    unittest.main()
