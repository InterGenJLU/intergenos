# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Daemon Escalate method — phone-a-friend show-before-send wiring (design plan §4).

Unit-tests InterGenDaemon.escalate() in isolation (no full daemon startup): the
consent modal gates the send; on Send the EscalationManager is called with
user_consented=True (the genuine initial human-authorized hop, NOT egress-scanned);
on Cancel nothing is sent; no manager / no provider degrade to a clean note. Runs on
any host (consent modal + manager are mocked/faked).
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon
from intergen.interfaces.types import LLMResponse


class _FakeMgr:
    def __init__(self, provider="anthropic"):
        self._provider = provider
        self.escalate_calls = []

    def _primary_provider_name(self):
        return self._provider

    def escalate(self, messages, *, tools=None, reason="", user_consented=False):
        self.escalate_calls.append({"messages": messages, "reason": reason,
                                    "user_consented": user_consented})
        return LLMResponse(text="frontier answer", model="anthropic-1", local=False)


def _daemon(mgr):
    # Bypass __init__ (heavy: models, dbus, tools) — exercise escalate() alone.
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._escalation = mgr
    return d


def _status_daemon(**over):
    """Minimal __new__-built daemon with only the attrs status() reads set."""
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._running = True
    d._hardware_tier = None
    d._model_loaded = None
    d._requests_handled = 0
    d._last_error = None
    d._model_server_integrity_failure = None
    d._llama = None
    d._router = None
    d._matcher = None
    d._tools = None
    d._memory = None
    d._watchdog = None
    d._metrics = None
    d._review_autopilot = None   # F1 (03cd6769): status() reads it; None in production
    # Game-launch pause (2026-08-04): status() reports whether the model servers
    # are stopped on purpose for a running game, and which games hold that.
    d._paused = False
    d._pause_holds = []
    for k, v in over.items():
        setattr(d, "_" + k, v)
    return d


class DaemonIntegrityStatusTests(unittest.TestCase):
    """The chat-server integrity failure surfaces as ONE conspicuous, queryable
    status field — distinct from last_error and the benign no-model case."""

    def test_integrity_failure_surfaces_distinctly(self):
        d = _status_daemon(model_server_integrity_failure=(
            "TOOLS_NOT_ADVERTISED: toolless template loaded"))
        s = json.loads(d.status())
        self.assertEqual(
            s["model_server_integrity_failure"],
            "TOOLS_NOT_ADVERTISED: toolless template loaded")
        # NOT blended into last_error or the no-model degrade.
        self.assertIsNone(s["last_error"])
        self.assertIsNone(s["model"])

    def test_healthy_has_no_integrity_failure(self):
        s = json.loads(_status_daemon().status())
        self.assertIn("model_server_integrity_failure", s)
        self.assertIsNone(s["model_server_integrity_failure"])


class WatchdogGiveupTests(unittest.TestCase):
    """A RUNTIME capability degradation (watchdog restart give-up over an
    integrity failure) surfaces via the conspicuous status, like the boot path."""

    def _daemon_with_llama(self, last_failure):
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._last_error = None
        d._model_server_integrity_failure = None
        llama = mock.MagicMock()
        llama.last_failure = last_failure
        llama.last_error = "served model does not advertise vision"
        d._llama = llama
        return d

    def test_integrity_giveup_sets_conspicuous_status(self):
        from intergen.interfaces.types import StartFailure
        d = self._daemon_with_llama(StartFailure.VISION_NOT_ADVERTISED)
        d._on_watchdog_giveup("watchdog: max restarts exceeded")
        self.assertEqual(d._last_error, "watchdog: max restarts exceeded")
        self.assertIsNotNone(d._model_server_integrity_failure)
        self.assertIn("VISION_NOT_ADVERTISED", d._model_server_integrity_failure)
        self.assertIn("watchdog", d._model_server_integrity_failure)

    def test_operational_giveup_only_last_error(self):
        from intergen.interfaces.types import StartFailure
        d = self._daemon_with_llama(StartFailure.UNHEALTHY)
        d._on_watchdog_giveup("watchdog: max restarts exceeded")
        self.assertEqual(d._last_error, "watchdog: max restarts exceeded")
        # operational failure → NOT routed to the conspicuous integrity status.
        self.assertIsNone(d._model_server_integrity_failure)

    def test_no_llama_only_last_error(self):
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._last_error = None
        d._model_server_integrity_failure = None
        d._llama = None
        d._on_watchdog_giveup("watchdog gave up")
        self.assertEqual(d._last_error, "watchdog gave up")
        self.assertIsNone(d._model_server_integrity_failure)


class DaemonEscalateTests(unittest.TestCase):
    def test_consent_send_calls_escalate_user_consented_true(self):
        mgr = _FakeMgr()
        d = _daemon(mgr)
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True) as consent:
            out = json.loads(d.escalate("please help with X"))
        self.assertTrue(out["sent"])
        self.assertEqual(out["response"], "frontier answer")
        # the consent modal was shown the outbound content + provider
        self.assertEqual(consent.call_args[0][0], "please help with X")
        self.assertEqual(consent.call_args[0][1], "anthropic")
        # the genuine initial hop is consented -> NOT scanned
        self.assertEqual(len(mgr.escalate_calls), 1)
        self.assertIs(mgr.escalate_calls[0]["user_consented"], True)

    def test_consent_cancel_sends_nothing(self):
        mgr = _FakeMgr()
        d = _daemon(mgr)
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=False):
            out = json.loads(d.escalate("secret data"))
        self.assertFalse(out["sent"])
        self.assertEqual(mgr.escalate_calls, [])  # adapter never reached

    def test_no_manager_degrades(self):
        d = _daemon(None)
        out = json.loads(d.escalate("hi"))
        self.assertFalse(out["sent"])
        self.assertIn("not available", out["response"].lower())

    def test_no_provider_degrades_without_consent_prompt(self):
        mgr = _FakeMgr(provider=None)
        d = _daemon(mgr)
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True) as consent:
            out = json.loads(d.escalate("hi"))
        self.assertFalse(out["sent"])
        self.assertIn("no frontier model", out["response"].lower())
        consent.assert_not_called()  # never prompt when there's nowhere to send

    def test_escalate_exception_degrades_not_crash(self):
        mgr = _FakeMgr()
        mgr.escalate = mock.Mock(side_effect=RuntimeError("boom"))
        d = _daemon(mgr)
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True):
            out = json.loads(d.escalate("hi"))
        self.assertFalse(out["sent"])
        self.assertIn("failed", out["response"].lower())


if __name__ == "__main__":
    unittest.main()
