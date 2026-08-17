# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Harness conversation-reset wiring — dbus-mode parity with the direct reset.

Root cause pinned here: the D-Bus test runner did NOT reset the persistent
daemon's router between conversations. The old inline runner reset reached only
`client._daemon._router` — which exists in DIRECT mode; in DBUS mode
`client._daemon` is None, so the reset silently never ran and a prior
conversation's staged offer / trust posture leaked into the next (contaminating
the honesty battery — the PI-Z29 cross-conversation over-steer, and the
contaminated pkm-invention 'before' numbers a full-battery re-proof surfaced).

The fix routes BOTH modes through `InterGenTestClient.reset_conversation()`:
  - direct: calls the in-process router's `reset_conversation_state()`.
  - dbus:   calls `com.intergenos.InterGen.ResetConversation()` on the bus,
            which runs the SAME reset inside the persistent daemon, and treats a
            {"reset": false} reply (or any bus error) as a fatal harness error —
            never a silent skip.
"""

from __future__ import annotations

import unittest

from intergen.tests.client import InterGenTestClient


class _RecordingRouter:
    """Stand-in in-process router that records reset_conversation_state calls."""

    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_conversation_state(self) -> None:
        self.reset_calls += 1


class _FakeDaemon:
    def __init__(self, router) -> None:
        self._router = router


class _FakeResult:
    """Mimics a Gio D-Bus reply: unpack() -> (payload_str,)."""

    def __init__(self, payload_str: str) -> None:
        self._payload = payload_str

    def unpack(self):
        return (self._payload,)


class _FakeBus:
    def __init__(self, payload_str: str | None = None, raise_exc=None) -> None:
        self._payload = payload_str
        self._raise = raise_exc
        self.calls: list[tuple] = []

    def call_sync(self, *args):
        self.calls.append(args)
        if self._raise is not None:
            raise self._raise
        return _FakeResult(self._payload)


def _bare_client(mode: str) -> InterGenTestClient:
    """A client with no real daemon/bus — __new__ avoids _init_direct/_init_dbus."""
    c = InterGenTestClient.__new__(InterGenTestClient)
    c._mode = mode
    c._daemon = None
    c._bus = None
    c._dbus_available = False
    return c


class DirectModeReset(unittest.TestCase):
    def test_direct_reset_calls_router_reset_state(self) -> None:
        router = _RecordingRouter()
        c = _bare_client("direct")
        c._daemon = _FakeDaemon(router)
        c.reset_conversation()
        self.assertEqual(router.reset_calls, 1)

    def test_direct_reset_is_safe_without_a_daemon(self) -> None:
        # Partial construction / not-yet-ready: no daemon => no-op, no crash.
        c = _bare_client("direct")
        c.reset_conversation()  # must not raise

    def test_direct_reset_is_safe_without_a_router(self) -> None:
        c = _bare_client("direct")
        c._daemon = _FakeDaemon(None)
        c.reset_conversation()  # router None => no-op, no crash


class ResetResultContract(unittest.TestCase):
    """The fail-loud verdict check — reset=false / unparseable is a harness ERROR."""

    def test_reset_true_is_clean(self) -> None:
        InterGenTestClient._check_reset_result('{"reset": true}')  # no raise

    def test_reset_false_raises_with_reason(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            InterGenTestClient._check_reset_result(
                '{"reset": false, "reason": "router not started"}')
        self.assertIn("router not started", str(ctx.exception))

    def test_missing_reset_key_is_treated_as_false(self) -> None:
        with self.assertRaises(RuntimeError):
            InterGenTestClient._check_reset_result('{}')

    def test_unparseable_reply_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            InterGenTestClient._check_reset_result("not json at all")


class DbusModeReset(unittest.TestCase):
    """The dbus path (the root-cause fix). gi-gated — the session-bus adapter
    needs the GObject-introspection bindings the target ships."""

    def setUp(self) -> None:
        try:
            import gi  # noqa: F401
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"gi/Gio unavailable: {e}")

    def test_dbus_reset_true_invokes_resetconversation(self) -> None:
        c = _bare_client("dbus")
        c._bus = _FakeBus('{"reset": true}')
        c.reset_conversation()  # must not raise
        # The one bus call must target the ResetConversation method.
        self.assertEqual(len(c._bus.calls), 1)
        args = c._bus.calls[0]
        self.assertIn("com.intergenos.InterGen", args)
        self.assertIn("ResetConversation", args)

    def test_dbus_reset_false_is_fatal(self) -> None:
        c = _bare_client("dbus")
        c._bus = _FakeBus('{"reset": false, "reason": "router not started"}')
        with self.assertRaises(RuntimeError) as ctx:
            c.reset_conversation()
        self.assertIn("router not started", str(ctx.exception))

    def test_dbus_bus_error_is_fatal(self) -> None:
        c = _bare_client("dbus")
        c._bus = _FakeBus(raise_exc=RuntimeError("bus went away"))
        with self.assertRaises(RuntimeError) as ctx:
            c.reset_conversation()
        self.assertIn("bus went away", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
