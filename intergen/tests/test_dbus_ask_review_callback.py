# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The D-Bus Ask path must supply a real human-review surface.

Without a review_callback, ToolRegistry.execute() fail-closed-DENIES any held /
privileged dispatch silently (safe, but the user can never approve). ask() must
build review_modal.make_review_callback and pass it to route(), so the D-Bus /
CLI one-shot path gets the same Allow/Deny consent surface as the panel + TUI.

We verify the WIRING (callback built + forwarded), not the dialog itself — the
zenity/notify-send/1-hour-implicit-deny/headless-fail-closed behavior is
review_modal's own (tested) contract, and invoking it here would block on the
libnotify session-wait.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon


def _fake_route_result():
    r = mock.Mock()
    r.text = "ok"
    r.source = "test"
    r.handled = True
    r.tool_calls = []
    r.used_llm = False
    r.escalated = False
    r.escalation_offer = None
    return r


class TestDbusAskReviewCallback(unittest.TestCase):
    def _daemon_with_mock_router(self):
        daemon = InterGenDaemon()
        daemon._router = mock.Mock()
        daemon._router.route.return_value = _fake_route_result()
        return daemon

    def test_ask_builds_and_forwards_make_review_callback(self):
        daemon = self._daemon_with_mock_router()
        sentinel_cb = lambda call, decision: "deny"  # noqa: E731
        with mock.patch(
            "intergen.review_modal.make_review_callback",
            return_value=sentinel_cb,
        ) as mk:
            daemon.ask("install firefox")
        # make_review_callback was constructed...
        mk.assert_called_once()
        # ...and its result was passed to route() as review_callback (not None).
        _, kwargs = daemon._router.route.call_args
        self.assertIs(kwargs.get("review_callback"), sentinel_cb)

    def test_ask_never_passes_none_callback(self):
        # The whole point: the D-Bus path must NOT leave review_callback=None
        # (that is the silent fail-closed-deny we are eliminating).
        daemon = self._daemon_with_mock_router()
        daemon.ask("show me the firewall rules")
        _, kwargs = daemon._router.route.call_args
        self.assertIsNotNone(kwargs.get("review_callback"))
        self.assertTrue(callable(kwargs.get("review_callback")))

    def test_ask_startup_guard_when_no_router(self):
        # Before start_service wires the router, ask() returns a startup note and
        # does not blow up (router is None).
        daemon = InterGenDaemon()  # _router stays None
        import json
        out = json.loads(daemon.ask("hello"))
        self.assertFalse(out["handled"])
        self.assertEqual(out["source"], "startup")


if __name__ == "__main__":
    unittest.main()
