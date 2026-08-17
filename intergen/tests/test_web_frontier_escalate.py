# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web UI phone-a-friend frontier_escalate handler (decision #4 GUI affordance).

Tests WebServer._handle_frontier_escalate in isolation: consent Send routes to the
manager with user_consented=True; Cancel sends nothing; no manager / no provider /
empty content / manager error all return a clean frontier_response (or error), never
crash. Async handler tested via IsolatedAsyncioTestCase; consent modal + manager faked.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.web_server import WebServer
from intergen.interfaces.types import LLMResponse


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


class _FakeCtx:
    def __init__(self):
        self.ws = _FakeWS()
        self.session_history = []


class _FakeMgr:
    def __init__(self, provider="anthropic"):
        self._provider = provider
        self.calls = []

    def _primary_provider_name(self):
        return self._provider

    def escalate(self, messages, *, tools=None, reason="", user_consented=False):
        self.calls.append({"user_consented": user_consented, "messages": messages})
        return LLMResponse(text="frontier answer", model="anthropic-1", local=False)


def _server(manager):
    s = WebServer.__new__(WebServer)
    s._router = mock.Mock()
    s._router._escalation = manager
    return s


class FrontierEscalateTests(unittest.IsolatedAsyncioTestCase):
    async def test_consent_send_routes_user_consented_true(self):
        mgr = _FakeMgr()
        s = _server(mgr)
        ctx = _FakeCtx()
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True):
            await s._handle_frontier_escalate(ctx, {"content": "help me"})
        out = ctx.ws.sent[-1]
        self.assertTrue(out["sent"])
        self.assertEqual(out["content"], "frontier answer")
        self.assertEqual(len(mgr.calls), 1)
        self.assertIs(mgr.calls[0]["user_consented"], True)
        # the answer is appended to session history
        self.assertEqual(ctx.session_history[-1].content, "frontier answer")

    async def test_consent_cancel_sends_nothing(self):
        mgr = _FakeMgr()
        s = _server(mgr)
        ctx = _FakeCtx()
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=False):
            await s._handle_frontier_escalate(ctx, {"content": "secret"})
        out = ctx.ws.sent[-1]
        self.assertFalse(out["sent"])
        self.assertEqual(mgr.calls, [])

    async def test_empty_content_errors(self):
        s = _server(_FakeMgr())
        ctx = _FakeCtx()
        await s._handle_frontier_escalate(ctx, {"content": "   "})
        self.assertEqual(ctx.ws.sent[-1].get("code"), "empty_message")

    async def test_no_provider_degrades(self):
        s = _server(_FakeMgr(provider=None))
        ctx = _FakeCtx()
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True) as consent:
            await s._handle_frontier_escalate(ctx, {"content": "hi"})
        self.assertFalse(ctx.ws.sent[-1]["sent"])
        consent.assert_not_called()

    async def test_no_manager_degrades(self):
        s = _server(None)
        ctx = _FakeCtx()
        await s._handle_frontier_escalate(ctx, {"content": "hi"})
        self.assertFalse(ctx.ws.sent[-1]["sent"])

    async def test_manager_exception_degrades_not_crash(self):
        mgr = _FakeMgr()
        mgr.escalate = mock.Mock(side_effect=RuntimeError("boom"))
        s = _server(mgr)
        ctx = _FakeCtx()
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True):
            await s._handle_frontier_escalate(ctx, {"content": "hi"})
        self.assertFalse(ctx.ws.sent[-1]["sent"])
        self.assertIn("failed", ctx.ws.sent[-1]["content"].lower())


if __name__ == "__main__":
    unittest.main()
