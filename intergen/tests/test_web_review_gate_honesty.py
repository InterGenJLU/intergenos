# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web review-gate honesty wave — the deterministic units.

Covers the pieces of the single-gate / honest-handoff / loop-killer / one-source
capability work that are provable without a live model or a real box:

  * the 3-part honest handoff builder (name the action / why it can't proceed
    here / the exact command) — no "blocked", no gate/tool jargon;
  * the user-language classification translator (never the raw enum label);
  * the web review-callback BRIDGE — fail-closed on every branch, and the
    forbidden / hard-deny parity pre-check the fast/offer path relies on;
  * the streaming gate-refusal message (honest handoff for a real state-change,
    a plain refusal for a hard-BLOCKED destructive command — never "run it
    yourself" for the thing the block exists to stop);
  * the router loop-killer — a declined offer is remembered and not re-armed as
    fresh, and its deny renders the honest handoff, not the raw "denied";
  * the ONE interface-aware capability registry — the router phrase table and
    the model's system-prompt guard both read from it (no drift).
"""

from __future__ import annotations

import asyncio
import unittest

from intergen.interfaces.types import ToolCall, ToolResult
from intergen.interfaces.provenance import Provenance, ToolRiskTier
from intergen import tool_registry
from intergen import capability_registry


# ── The honest handoff + classification translator (user-language classification / the honest-handoff design) ──────────
class HonestHandoffTests(unittest.TestCase):
    def test_admin_handoff_has_sudo_command_and_no_jargon(self):
        msg = tool_registry.honest_handoff_message(
            "Refresh the package index and install available updates.",
            "pkm sync && pkm upgrade", needs_admin=True)
        self.assertIn("administrator approval", msg)
        self.assertIn("`sudo pkm sync && pkm upgrade`", msg)
        # No internal jargon anywhere in a user-facing handoff.
        low = msg.lower()
        for banned in ("blocked", "safety layer", "gate", "provenance",
                       "privileged_state_changing"):
            self.assertNotIn(banned, low, f"handoff leaked jargon: {banned!r}")

    def test_non_admin_handoff_has_no_sudo(self):
        msg = tool_registry.honest_handoff_message(
            "", "systemctl reload foo", needs_admin=False)
        self.assertNotIn("sudo", msg)
        self.assertIn("`systemctl reload foo`", msg)

    def test_empty_command_omits_command_block(self):
        msg = tool_registry.honest_handoff_message("A change.", "", needs_admin=True)
        self.assertNotIn("`", msg)          # no command block
        self.assertIn("administrator approval", msg)

    def test_classification_sentence_never_raw_label(self):
        for tier in ToolRiskTier:
            s = tool_registry.classification_sentence(tier)
            self.assertNotIn("_", s)         # no snake_case enum label leaked
            self.assertTrue(s.endswith("."))
        self.assertTrue(tool_registry.tier_needs_admin(
            ToolRiskTier.PRIVILEGED_STATE_CHANGING))
        self.assertFalse(tool_registry.tier_needs_admin(
            ToolRiskTier.USER_SCOPE_STATE_CHANGING))


# ── The web review-callback bridge (single-gate review) ────────────────────────────────────
class _RaisingTools:
    def get_tool(self, name):
        raise RuntimeError("boom")   # provoke the bridge's fail-closed path


class _NoTools:
    def get_tool(self, name):
        return None


class WebReviewBridgeTests(unittest.TestCase):
    def _server(self, tools):
        from intergen.web_server import WebServer
        s = WebServer()
        s._tools = tools
        s._governance = None
        return s

    def _ctx(self, server):
        from intergen.web_server import ConnectionContext
        return ConnectionContext(client_id="t", source_interface="web", ws=object())

    def test_bridge_fails_closed_on_exception(self):
        # ANY exception in the bridge → "deny", never a silent allow (fail-closed).
        server = self._server(_RaisingTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)
        self.assertEqual(cb(call, None), "deny")

    def test_bridge_denies_forbidden_action_before_any_card(self):
        # Parity: a forbidden Z3 action fails closed at the bridge (the fast/offer
        # path's substitute for the streaming :1752 forbidden pre-check) — and it
        # returns BEFORE ever scheduling a card. Stub the scheduler so a card
        # render would be observable if it (wrongly) reached one.
        from intergen import zones
        server = self._server(_NoTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        # Find a call zones.forbidden_reason actually refuses (self-substrate /
        # owner_only write). Skip cleanly if the zone set does not cover one here.
        candidates = [
            ToolCall(name="write_file",
                     arguments={"path": "/etc/intergen/governance.json",
                                "content": "x"},
                     source_of_request=Provenance.USER_DIRECT),
            ToolCall(name="modify_governance", arguments={"autonomy_tier": 5},
                     source_of_request=Provenance.USER_DIRECT),
        ]
        forbidden = next(
            (c for c in candidates
             if zones.forbidden_reason(c.name, c.arguments)), None)
        if forbidden is None:
            self.skipTest("no forbidden-zone call available in this build")
        scheduled = {"n": 0}
        orig = asyncio.run_coroutine_threadsafe
        try:
            def _spy(coro, loop):
                scheduled["n"] += 1
                coro.close()
                raise AssertionError("forbidden action reached the card")
            asyncio.run_coroutine_threadsafe = _spy
            self.assertEqual(cb(forbidden, None), "deny")
            self.assertEqual(scheduled["n"], 0, "forbidden action was not pre-denied")
        finally:
            asyncio.run_coroutine_threadsafe = orig

    def test_bridge_verdict_mapping(self):
        # approved → allow_once, denied → deny — with the coroutine scheduling
        # stubbed so no live loop is needed.
        server = self._server(_NoTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)

        class _Fut:
            def __init__(self, v=None, e=None):
                self._v, self._e = v, e
            def result(self, timeout=None):
                if self._e:
                    raise self._e
                return self._v

        orig = asyncio.run_coroutine_threadsafe
        try:
            for verdict, expected in (("approved", "allow_once"),
                                      ("denied", "deny")):
                def _fake(coro, loop, _v=verdict):
                    coro.close()             # avoid un-awaited-coroutine warning
                    return _Fut(v=_v)
                asyncio.run_coroutine_threadsafe = _fake
                self.assertEqual(cb(call, None), expected)
            # A scheduling exception fails closed to deny.
            def _boom(coro, loop):
                coro.close()
                return _Fut(e=TimeoutError())
            asyncio.run_coroutine_threadsafe = _boom
            self.assertEqual(cb(call, None), "deny")
        finally:
            asyncio.run_coroutine_threadsafe = orig


# ── The streaming gate-refusal message (single-gate review) ────────────────────────────────
class GateRefusalMessageTests(unittest.TestCase):
    def _server(self):
        from intergen.web_server import WebServer
        s = WebServer()
        s._tools = None          # _classify_risk_tier(None, ...) → state-changing
        s._governance = None
        return s

    def test_state_change_deny_gets_honest_handoff(self):
        server = self._server()
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)
        tr = ToolResult(call_id="", name="manage_packages", content="denied",
                        success=False, executed=False)
        msg = server._gate_refusal_message(call, tr)
        self.assertIn("pkm sync && pkm upgrade", msg)
        self.assertNotIn("blocked", msg.lower())

    def test_blocked_destructive_never_hands_over_the_command(self):
        server = self._server()
        call = ToolCall(name="run_command",
                        arguments={"command": "rm -rf /"},
                        source_of_request=Provenance.USER_DIRECT)
        tr = ToolResult(call_id="", name="run_command", content="blocked",
                        success=False, executed=False, blocked=True)
        msg = server._gate_refusal_message(call, tr)
        self.assertNotIn("rm -rf /", msg)    # never advise the blocked command
        self.assertNotIn("`", msg)           # no command block at all

    def test_handoff_command_only_for_command_backed_tools(self):
        from intergen.web_server import WebServer
        pkg = ToolCall(name="manage_packages", arguments={"action": "update"},
                       source_of_request=Provenance.USER_DIRECT)
        shot = ToolCall(name="take_screenshot", arguments={"source": "screenshot"},
                        source_of_request=Provenance.USER_DIRECT)
        self.assertTrue(WebServer._handoff_command(pkg))
        self.assertEqual(WebServer._handoff_command(shot), "")


# ── The router loop-killer (the honest-handoff work) ─────────────────────────────────────────
class _DenyTools:
    """A registry whose execute() returns a gate-refusal (not executed)."""
    def execute(self, call, *, ingress_tracker=None, trust_state=None,
                review_callback=None):
        return ToolResult(call_id="", name=getattr(call, "name", "run_command"),
                          content="Tool call denied by user via review modal.",
                          success=False, executed=False)


class LoopKillerTests(unittest.TestCase):
    def _router(self):
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._handed_off_commands = set()
        r._tools = _DenyTools()
        r._ingress_tracker = object()
        r._trust_state = object()
        r._review_callback = None
        r._filler = None
        r._pending_action_offer = None
        r._append_history = lambda a, b: None
        return r

    def test_deny_records_handoff_and_renders_honest_message(self):
        r = self._router()
        res = r._run_staged_action("pkm install zoom", "run_command", None)
        # The raw "denied" tool text is NEVER the delivered answer.
        self.assertNotIn("review modal", res.text)
        self.assertIn("pkm install zoom", res.text)      # the command handoff
        self.assertTrue(r._command_handed_off("pkm install zoom"))

    def test_declined_action_is_not_re_offered(self):
        r = self._router()
        r._note_handed_off("pkm install zoom")
        line = r._stage_action_offer_or_handoff(
            "pkm install zoom", "run_command", "install zoom")
        # No fresh offer armed, and the line is the honest handoff, not "say yes".
        self.assertIsNone(r._pending_action_offer)
        self.assertIn("pkm install zoom", line)
        self.assertNotIn("say yes", line.lower())

    def test_fresh_action_still_arms_an_offer(self):
        r = self._router()
        # A NOT-declined action arms normally (filler=None → template offer line).
        line = r._stage_action_offer_or_handoff(
            "pkm install vim", "run_command", "install vim")
        self.assertEqual(r._pending_action_offer,
                         ("pkm install vim", "run_command", "install vim"))
        self.assertIn("pkm install vim", line)


# ── The one interface-aware capability registry (the capability single-source) ───────────────────────
class CapabilityRegistrySingleSourceTests(unittest.TestCase):
    def test_router_phrase_table_reads_from_the_registry(self):
        from intergen.router import _TOOL_CAP_Q_SPECS
        for tool, _rx, phrase in _TOOL_CAP_Q_SPECS:
            self.assertTrue(phrase, f"{tool}: empty capability phrase")
            self.assertEqual(phrase, capability_registry.phrase(tool),
                             f"{tool}: router phrase drifted from the registry")

    def test_system_guard_built_from_registry_commands(self):
        from intergen import persona
        guard = persona.SYSTEM_CAPABILITY_GUARD
        self.assertIn(capability_registry.PKM_INDEX_REFRESH_CMD, guard)
        self.assertIn(capability_registry.PKM_UPGRADE_CMD, guard)
        # The guard is the assembled one, not a stale hardcoded copy.
        self.assertEqual(guard,
                         capability_registry.build_system_capability_guard())

    def test_pkm_update_command_is_the_shared_constant(self):
        self.assertEqual(capability_registry.PKM_UPDATE_COMMAND,
                         "pkm sync && pkm upgrade")

    def test_confirmation_tail_is_interface_aware(self):
        web = capability_registry.confirmation_tail("web")
        console = capability_registry.confirmation_tail("console")
        self.assertIn("card", web)
        self.assertIn("prompt", console)


if __name__ == "__main__":
    unittest.main()
