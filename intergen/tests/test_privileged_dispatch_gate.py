# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-6 (option iii) — registry-level privileged dispatch gate tests.

Proves the user-side gate behavior wired into ToolRegistry.execute():

  1. The PRIVILEGED tier ALWAYS routes through the human review modal, even when
     the provenance gate would return `execute` (USER_DIRECT). With no review UI
     the action fails closed (implicit deny) — the inert gate can never
     silently dispatch a privileged call.
  2. On human approval a single-use token is minted that BINDS to this exact
     (tool, args, uid) and is threaded to the pkexec runner.
  3. allow_conversation is NOT cached for the privileged tier (AI-14 closed): a
     second privileged call in the same conversation prompts again.
  4. If the signing key is unavailable the approved action still REFUSES (fail
     closed) and never reaches pkexec.

pkexec cannot run in the test env, so _dispatch_via_pkexec is mocked to capture
the token; the signing key is injected via patch so no real key file is touched.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from intergen import dispatch_token as dt
from intergen.tool_registry import ToolRegistry
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.interfaces.provenance import Provenance, ConversationTrustState


class PrivilegedDispatchGateTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.args = {"action": "restart", "unit": "sshd"}
        self.uid = os.getuid()
        # Inject a deterministic signing key so mint_token does not need a real
        # ~/.config/intergen/dispatch-key on the test host.
        self._key = "11" * dt.KEY_BYTES
        self._key_patch = mock.patch.object(dt, "load_dispatch_key", return_value=self._key)
        self._key_patch.start()

    def tearDown(self):
        self._key_patch.stop()

    def _call(self):
        # USER_DIRECT: the gate would return `execute` (privileged x user_direct
        # -> execute) — so this is the case where the always-prompt rule matters.
        return ToolCall(
            name="manage_services",
            arguments=dict(self.args),
            source_of_request=Provenance.USER_DIRECT,
        )

    def _ok_pkexec(self, capture=None):
        def _fake(call, tool_name, arguments, dispatch_token=None):
            if capture is not None:
                capture["token"] = dispatch_token
                capture["arguments"] = arguments
            return ToolResult(
                call_id=call.call_id, name=tool_name, content="ok", success=True,
            )
        return _fake

    def test_privileged_always_prompts_even_on_user_direct(self):
        # No review UI -> implicit deny, despite the gate's execute decision.
        result = self.registry.execute(self._call(), review_callback=None)
        self.assertFalse(result.success)
        self.assertIn("review", result.content.lower())

    def test_approval_mints_token_bound_to_call(self):
        captured = {}
        with mock.patch.object(
            self.registry, "_dispatch_via_pkexec",
            side_effect=self._ok_pkexec(captured),
        ):
            result = self.registry.execute(
                self._call(), review_callback=lambda c, d: "allow_once",
            )
        self.assertTrue(result.success)
        token = captured["token"]
        self.assertIsNotNone(token)
        # The minted token verifies against THIS tool + args + uid.
        payload = dt.verify_token(
            token, "manage_services", self.args, self.uid, key=self._key,
        )
        self.assertEqual(payload.tool, "manage_services")
        self.assertEqual(payload.uid, self.uid)

    def test_token_does_not_verify_for_different_args(self):
        captured = {}
        with mock.patch.object(
            self.registry, "_dispatch_via_pkexec",
            side_effect=self._ok_pkexec(captured),
        ):
            self.registry.execute(
                self._call(), review_callback=lambda c, d: "allow_once",
            )
        # A token minted for restart-sshd must not verify for stop-sshd.
        with self.assertRaises(dt.BindingMismatch):
            dt.verify_token(
                captured["token"], "manage_services",
                {"action": "stop", "unit": "sshd"}, self.uid, key=self._key,
            )

    def test_no_conversation_caching_for_privileged(self):
        prompts = []

        def cb(c, d):
            prompts.append(1)
            return "allow_conversation"

        trust = ConversationTrustState()
        with mock.patch.object(
            self.registry, "_dispatch_via_pkexec", side_effect=self._ok_pkexec(),
        ):
            self.registry.execute(self._call(), trust_state=trust, review_callback=cb)
            self.registry.execute(self._call(), trust_state=trust, review_callback=cb)
        # Prompted BOTH times — allow_conversation was not cached for privileged.
        self.assertEqual(len(prompts), 2)

    def test_mint_failure_fails_closed(self):
        # Signing key unavailable -> approved privileged action still refuses and
        # never reaches pkexec.
        with mock.patch.object(dt, "load_dispatch_key", side_effect=dt.KeyError_("no key")):
            with mock.patch.object(self.registry, "_dispatch_via_pkexec") as pk:
                result = self.registry.execute(
                    self._call(), review_callback=lambda c, d: "allow_once",
                )
        self.assertFalse(result.success)
        pk.assert_not_called()
        self.assertIn("token", result.content.lower())

    def test_tokenless_pkexec_dispatch_fails_closed(self):
        # Direct call to _dispatch_via_pkexec with no token must refuse before
        # invoking pkexec (defense-in-depth on the invariant).
        call = self._call()
        result = ToolRegistry._dispatch_via_pkexec(
            call, "manage_services", self.args, None,
        )
        self.assertFalse(result.success)
        self.assertIn("without a human-approval token", result.content)


if __name__ == "__main__":
    unittest.main()
