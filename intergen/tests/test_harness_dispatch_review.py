# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Harness dispatch-review seam — non-interactive approval for unattended pulls.

A dyno pull drives the in-process daemon through the real ask() path, which
in production builds the interactive zenity/notify review surface. That blocks
an unattended run (held dispatches wait on a human; privileged ones pop polkit).
The harness injects a deterministic review callback via the daemon's
_review_callback_override seam. These tests pin both halves:

  * the policy closure (intergen.tests.client._auto_approve_dispatch): deny
    PRIVILEGED_STATE_CHANGING (needs_pkexec) dispatches so they never reach
    pkexec, allow everything else;
  * the daemon seam (InterGenDaemon.ask): honour the override when set, fall
    back to the production make_review_callback when it is None.

No model or D-Bus needed — the router is stubbed and the join is pure data.
"""

from __future__ import annotations

import json
import unittest

from intergen.tests.client import _auto_approve_dispatch
from intergen.dbus_daemon import InterGenDaemon
from intergen.interfaces.types import RouteResult


class _Decision:
    """Minimal stand-in for DispatchDecision (only needs_pkexec is read)."""
    def __init__(self, needs_pkexec: bool) -> None:
        self.needs_pkexec = needs_pkexec


class AutoApprovePolicyTests(unittest.TestCase):
    def test_privileged_state_changing_is_denied(self) -> None:
        # needs_pkexec dispatches must be denied so the run never routes through
        # _dispatch_via_pkexec (which would pop the OS polkit prompt + mutate box).
        self.assertEqual(_auto_approve_dispatch(None, _Decision(True)), "deny")

    def test_non_privileged_is_allowed_once(self) -> None:
        self.assertEqual(_auto_approve_dispatch(None, _Decision(False)), "allow_once")

    def test_missing_needs_pkexec_fails_safe_to_deny(self) -> None:
        # Fail-safe (fail-closed rule 10): a decision missing the privilege flag
        # is treated as privileged and DENIED, not auto-approved — if the
        # DispatchDecision shape drifts, the harness must fail closed.
        self.assertEqual(_auto_approve_dispatch(None, object()), "deny")


class _RecordingRouter:
    """Stub router that records the review_callback ask() handed it."""
    def __init__(self) -> None:
        self.seen_callback = "UNSET"

    def route(self, message, *, review_callback=None, **kw):
        self.seen_callback = review_callback
        return RouteResult(text="ok", source="stub", handled=True)


class DaemonOverrideSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.daemon = InterGenDaemon()
        self.router = _RecordingRouter()
        self.daemon._router = self.router

    def test_default_override_is_none(self) -> None:
        # Production default: no override -> ask() builds the real review surface.
        self.assertIsNone(InterGenDaemon()._review_callback_override)

    def test_ask_uses_override_when_set(self) -> None:
        self.daemon._review_callback_override = _auto_approve_dispatch
        raw = self.daemon.ask("anything")
        self.assertEqual(json.loads(raw)["source"], "stub")
        # The exact override closure reached route() — not the interactive one.
        self.assertIs(self.router.seen_callback, _auto_approve_dispatch)

    def test_ask_builds_production_callback_when_override_none(self) -> None:
        # Override unset: route() still gets a real (non-None) callback, the one
        # make_review_callback returns — production consent path is unchanged.
        self.daemon._review_callback_override = None
        self.daemon.ask("anything")
        self.assertIsNot(self.router.seen_callback, _auto_approve_dispatch)
        self.assertTrue(callable(self.router.seen_callback))


if __name__ == "__main__":
    unittest.main()
