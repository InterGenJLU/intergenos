# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""PR3 — dbus consent-surface cells (D1-D5), grounded from the fleet red-team.

The dbus Ask path does NOT share the web gate bridge: it is the SYNCHRONOUS
review_callback (make_review_callback -> prompt_review -> zenity/libnotify),
not the async gate_future. So the web no-wedge mechanism (_run_turn + the
web_server.py:1344 safety net) does not cover the dbus return path; the dbus
surface must assert its own no-wedge property — fail-closed on every degraded
path, and the correct decision on the live ones.

These are the dbus equivalents of the web deny/liveness cells, the same
deterministic-unit class as the cross-tool deny test (no live model, no real
display): mock _session_active / shutil.which / subprocess.run and parametrize
FALLBACK_TIMEOUT_SECONDS so the locked-session cell never waits the real hour.

Distinct from test_review_modal_display_env.py (which covers DISPLAY detection
+ session self-heal) — these cover the consent DECISION outcomes.

  D1  active session, user denies        -> "deny"
  D2  headless / no zenity (notify only) -> fail-closed "deny", PROMPTLY (no
                                            hour wait), held action surfaced
  D3  no notify-send at all              -> immediate fail-closed "deny"
  D4  locked session, never returns      -> implicit-deny at the timeout
  D5  locked session, then returns       -> re-prompts zenity, honors decision

(D6 — teaching phrasing must never reach the consent path — is the surface-
agnostic negative asserted by teach_create_file / teach_run_command in
conversations.py: the explain gate fires before any dispatch or surface.)
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import review_modal
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import DispatchDecision, Provenance


def _call() -> ToolCall:
    return ToolCall(
        name="write_file",
        arguments={"path": "/etc/hosts", "content": "x"},
        source_of_request=Provenance.USER_DIRECT,
    )


def _decision() -> DispatchDecision:
    return DispatchDecision(
        action="hold_for_review",
        effective_provenance=Provenance.USER_DIRECT,
        needs_pkexec=False,
        reason="test held action",
    )


def _which(present: set[str]):
    """shutil.which stand-in: a name resolves iff it is in `present`."""
    return lambda name: (f"/usr/bin/{name}" if name in present else None)


class DbusConsentSurfaceTests(unittest.TestCase):
    def _prompt(self):
        return review_modal.prompt_review(
            _call(), _decision(), source_attribution="D-Bus Ask request")

    def test_D1_active_session_deny_returns_deny(self):
        # The branded GTK dialog renders and the user denies — returned as-is.
        with mock.patch.object(review_modal, "_session_active",
                               return_value=True), \
             mock.patch.object(review_modal.consent_dialog,
                               "run_review_dialog", return_value="deny"):
            self.assertEqual(self._prompt(), "deny")

    def test_D2_headless_no_zenity_fail_closed_promptly(self):
        # No session -> libnotify fallback. notify-send present, zenity ABSENT:
        # surface the held action, then implicit-deny PROMPTLY — never loop the
        # full FALLBACK_TIMEOUT (the CLI/headless no-wedge guarantee).
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(review_modal, "_session_active",
                               return_value=False), \
             mock.patch.object(review_modal.shutil, "which",
                               side_effect=_which({"notify-send"})), \
             mock.patch.object(review_modal.subprocess, "run", run), \
             mock.patch.object(review_modal.time, "sleep") as slept:
            self.assertEqual(self._prompt(), "deny")
        run.assert_called_once()                 # the held action was surfaced
        slept.assert_not_called()                # no hour-long poll wedge

    def test_D3_no_notify_send_immediate_fail_closed(self):
        # Neither an interactive session nor notify-send: there is no surface to
        # inform the user, so deny immediately without notifying.
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(review_modal, "_session_active",
                               return_value=False), \
             mock.patch.object(review_modal.shutil, "which",
                               side_effect=_which(set())), \
             mock.patch.object(review_modal.subprocess, "run", run):
            self.assertEqual(self._prompt(), "deny")
        run.assert_not_called()                  # nothing to notify with

    def test_D4_locked_session_timeout_implicit_deny(self):
        # Session never returns active: poll until the deadline, then
        # implicit-deny. FALLBACK_TIMEOUT patched to 0 so the deadline is
        # already past — deterministic, no real wait.
        run = mock.Mock(return_value=mock.Mock(returncode=0))
        with mock.patch.object(review_modal, "_session_active",
                               return_value=False), \
             mock.patch.object(review_modal.shutil, "which",
                               side_effect=_which({"notify-send", "zenity"})), \
             mock.patch.object(review_modal.subprocess, "run", run), \
             mock.patch.object(review_modal, "FALLBACK_TIMEOUT_SECONDS", 0), \
             mock.patch.object(review_modal.time, "sleep"):
            self.assertEqual(self._prompt(), "deny")

    def test_D5_locked_then_returns_reprompts_and_honors(self):
        # Inactive at the gate, then the session returns during the poll ->
        # re-prompt the zenity modal and honor that decision (here allow_once).
        # side_effect: [gate=False, poll=True].
        with mock.patch.object(review_modal, "_session_active",
                               side_effect=[False, True]), \
             mock.patch.object(review_modal.shutil, "which",
                               side_effect=_which({"notify-send", "zenity"})), \
             mock.patch.object(review_modal.subprocess, "run",
                               mock.Mock(return_value=mock.Mock(returncode=0))), \
             mock.patch.object(review_modal, "FALLBACK_TIMEOUT_SECONDS", 30), \
             mock.patch.object(review_modal.time, "sleep"), \
             mock.patch.object(review_modal, "_prompt_review_zenity",
                               return_value="allow_once"):
            self.assertEqual(self._prompt(), "allow_once")


if __name__ == "__main__":
    unittest.main()
