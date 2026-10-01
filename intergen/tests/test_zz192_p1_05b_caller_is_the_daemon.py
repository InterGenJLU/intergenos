# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Item 192, P1-05 — the capture's caller is the daemon, not a name's owner.

The first pass on this surface replaced "every peer on the session bus" with "the
peer the bus reports as owning the daemon's well-known name". A second,
independent read reproduced a way past it: a well-known name is not an identity,
it is a claim. The name belongs to whoever asks for it first and is free the
moment nobody holds it, so while the daemon is not running any peer in the
session may take it — and the extension's own name watcher then reports that peer
as the owner and the surface answers it with a full-frame grab.

BusOwnershipIsNotIdentityTests drives that on a real session bus: two independent
connections, the name unheld, the peer claiming it, the watcher naming the
claimer. It is the control for this case, and it keeps passing after the fix
because it is a statement about the bus's own rules. Its second half shows what
the bus DOES vouch for and the name cannot: the peer's own uid and pid, which is
what the corrected guard reads before it admits a call.

WHAT THIS CASE DOES NOT DO. It does not execute the GJS handler: extension.js
imports resource:///org/gnome/shell modules that exist only inside the running
compositor, so no test on this machine can call _onShellMethodCall. The pins
below hold the handler to the corrected constructs; the handler itself running is
the installed-system leg at the chain's landing tier, recorded as that item's own
finding. A pid is also not a permanent identity — a peer that exits frees it —
and that residue is named in this lane's delivery rather than hidden here.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

_EXT_JS = (Path(__file__).resolve().parents[1]
           / "panel" / "extension" / "extension.js")

# A name of this project's own namespace that nothing ships and nothing owns.
# The daemon's real name is never claimed, watched or disturbed by these cases.
_PROBE_NAME = "com.intergenos.InterGenShellCallerProbe"

# The shape the daemon's tool creates, so the probe paths are the ones the
# surface would accept (tempfile.mkstemp(prefix=..., suffix=...)).
_SHOT_PREFIX = "intergen-shot-"
_SHOT_SUFFIX = ".png"


def _read_source() -> str:
    if not _EXT_JS.is_file():
        raise AssertionError(f"extension source not found at {_EXT_JS}")
    return _EXT_JS.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The text of one top-level `function name(...) { ... }`, braces balanced.

    The pins below are about what ONE function does, so they must not be
    satisfied by a token somewhere else in a 600-line file.
    """
    start = source.find(f"function {name}(")
    if start < 0:
        raise AssertionError(f"{name} is not in {_EXT_JS.name}")
    open_brace = source.find("{", start)
    depth = 0
    for i in range(open_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"{name}'s body is not brace-balanced")


class BusOwnershipIsNotIdentityTests(unittest.TestCase):
    """The caller boundary, on a real session bus.

    Skipped only where there is no session bus to ask — a headless builder. The
    assertion is about the bus's own rules, so it needs no compositor.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:                       # pragma: no cover
            raise unittest.SkipTest(f"no session bus on this machine: {e}")
        if not cls.addr:                              # pragma: no cover
            raise unittest.SkipTest("no session bus address on this machine")

    def _connection(self):
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        conn = Gio.DBusConnection.new_for_address_sync(self.addr, flags, None, None)
        self.addCleanup(lambda: conn.close_sync(None))
        return conn

    def test_a_peer_that_is_not_the_daemon_becomes_the_name_the_watcher_reports(self) -> None:
        """RED on the first pass: the guard's whole test is a claimable name."""
        peer = self._connection()
        watcher = self._connection()
        self.assertNotEqual(peer.get_unique_name(), watcher.get_unique_name())

        seen = {}
        loop = GLib.MainLoop()

        def appeared(_conn, _name, owner):
            seen["owner"] = owner
            loop.quit()

        watch_id = Gio.bus_watch_name_on_connection(
            watcher, _PROBE_NAME, Gio.BusNameWatcherFlags.NONE,
            appeared, lambda *_a: None)
        self.addCleanup(Gio.bus_unwatch_name, watch_id)
        own_id = Gio.bus_own_name_on_connection(
            peer, _PROBE_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        self.addCleanup(Gio.bus_unown_name, own_id)

        timeout_id = GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        GLib.source_remove(timeout_id)

        self.assertEqual(seen.get("owner"), peer.get_unique_name(),
                         "the watcher reported the claiming peer as the owner of "
                         "a name the daemon never held")

    def test_the_bus_does_hold_an_identity_for_that_peer(self) -> None:
        """What the corrected guard reads instead: the peer's own uid and pid."""
        peer = self._connection()
        asker = self._connection()
        for method, vtype, expected in (
                ("GetConnectionUnixProcessID", "(u)", os.getpid()),
                ("GetConnectionUnixUser", "(u)", os.getuid())):
            reply = asker.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", method,
                GLib.Variant("(s)", (peer.get_unique_name(),)),
                GLib.VariantType(vtype), Gio.DBusCallFlags.NONE, -1, None)
            self.assertEqual(reply.unpack()[0], expected,
                             f"{method} names this process, not a claimed name")


class TheCallerIsCheckedByItsCredentialsTests(unittest.TestCase):
    """The caller guard, pinned in the handler that authorises the call."""

    def test_the_handler_asks_the_bus_who_the_peer_is(self) -> None:
        body = _function_body(_read_source(), "_onShellMethodCall")
        self.assertTrue(
            "_senderIsTheDaemon" in body or "GetConnectionCredentials" in body
            or "GetConnectionUnixProcessID" in body,
            "the handler must establish the caller from the bus's own view of "
            "the connection, not from who holds a claimable name")

    def test_the_identity_check_reads_the_peers_pid_and_its_program(self) -> None:
        source = _read_source()
        body = _function_body(source, "_senderIsTheDaemon")
        self.assertIn("GetConnectionUnixProcessID", body,
                      "the peer's pid is what the bus can vouch for")
        self.assertIn("GetConnectionUnixUser", body,
                      "a peer of another user is refused before anything else")
        self.assertIn("INTERGEN_DAEMON_PROGRAM", body,
                      "the program it must be running is named once, not inline")
        # The pid is resolved to its program. That read may sit in a helper the
        # identity check names — one hop a reader follows — but the helper must
        # exist, the check must call it, and the helper must be the thing that
        # reads the pid's own argument vector. A file-wide search for "/proc"
        # would pass on any mention anywhere, so both halves are pinned.
        helper = re.search(r"const\s+(\w+)\s*=\s*_(\w+)\(pid\)", body)
        self.assertIsNotNone(
            helper, "the identity check must resolve the pid to its program")
        resolver = "_" + helper.group(2)
        self.assertIn(resolver + "(pid)", body,
                      "the identity check itself calls the resolver")
        resolver_body = _function_body(source, resolver)
        self.assertRegex(
            resolver_body, r"/proc/\"?\s*\+\s*pid|/proc/\$\{pid\}",
            f"{resolver} must read the pid's own /proc entry")
        self.assertIn("cmdline", resolver_body,
                      f"{resolver} must read the argument vector, not the exe "
                      "link: the daemon's program is a script, so the exe link "
                      "reads as the interpreter on every machine")

    def test_the_name_owner_alone_no_longer_admits_a_call(self) -> None:
        """The name may still be the cheap first gate; it may not be the last."""
        body = _function_body(_read_source(), "_onShellMethodCall")
        admits_on_owner_alone = re.search(
            r"sender\s*!==\s*_intergenOwner\s*\)\s*\{", body)
        if admits_on_owner_alone:
            self.assertIn(
                "_senderIsTheDaemon", body,
                "an owner comparison is allowed to stand as a first refusal, but "
                "the call must not be admitted on it alone")


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
