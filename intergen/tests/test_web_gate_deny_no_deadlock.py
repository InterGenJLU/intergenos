# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Regression: a WS turn paused on an interactive gate must NOT deadlock the
receive loop (the F2 deny-hang).

Root cause it guards against
----------------------------
The WebSocket receive loop (`_dispatch_loop`) reads ONE message at a time and,
historically, awaited each handler inline. A chat turn that hit a gated action
suspended at `await gate_future` inside `_evaluate_tool_with_gate`. While
suspended, the loop never called `receive()` again — so the client's
`gate_decision` was never read, the future never resolved, and aiohttp reaped
the starved socket as a ~45s heartbeat pong-timeout close. The deny
friendly-refusal never sent. Same wedge for EVERY gate (allow too).

The fix runs turns as a background task so the loop stays free to dispatch
`gate_decision` concurrently. These tests drive the REAL `_dispatch_loop` and
the REAL `_evaluate_tool_with_gate`; the only stub is the turn body (a tiny
stand-in for the LLM stream that issues one gate-eligible ToolCall), so the
loop↔gate concurrency — the thing that broke — is exercised end to end.

Under the OLD inline-await code these tests TIME OUT (the deadlock); under the
fix they complete in well under a second. The 1.0s wait_for ceiling is the
liveness invariant: a denied turn terminates promptly, never wedges.
"""

from __future__ import annotations

import asyncio
import json
import types
import unittest

from aiohttp import WSMsgType

from intergen.web_server import WebServer, ConnectionContext
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance


def _text(obj: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(obj))


class _FakeWS:
    """Minimal aiohttp-WSResponse stand-in.

    Yields a `message`, then — only AFTER the server emits `gate_prompt` —
    yields the client's `gate_decision`. If the receive loop were blocked
    awaiting the turn (the bug), the second message could never be read and
    the gate_prompt event would never be set: deadlock.
    """

    def __init__(self, query: str, decision: str):
        self._query = query
        self._decision = decision
        self.sent: list[dict] = []
        self.closed = False
        self._gate_prompt = asyncio.Event()
        self._gate_tool_call_id: str | None = None
        self._step = 0

    # -- server -> client --------------------------------------------------
    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)
        if obj.get("type") == "gate_prompt":
            self._gate_tool_call_id = obj.get("tool_call_id")
            self._gate_prompt.set()

    # -- client -> server (async iteration) --------------------------------
    def __aiter__(self):
        return self

    async def __anext__(self):
        self._step += 1
        if self._step == 1:
            return _text({"type": "message", "content": self._query})
        if self._step == 2:
            # Block until the turn has actually popped the gate. With the bug
            # this await never returns (the loop is wedged in the turn), and
            # the enclosing wait_for trips the timeout — exactly the failure
            # we are pinning down.
            await self._gate_prompt.wait()
            return _text({
                "type": "gate_decision",
                "tool_call_id": self._gate_tool_call_id,
                "decision": self._decision,
            })
        raise StopAsyncIteration

    async def close(self, *a, **k):
        self.closed = True


# The deterministic friendly refusal mirrors web_server.py:1487-1491.
_FRIENDLY_DENY = (
    "I'm not able to do that from here right now. "
    "If you'd like, I can look something up for you or walk "
    "you through how to do it instead."
)


class _GateHarness(unittest.TestCase):
    """Shared drive-the-real-gate helpers (no tests of its own)."""

    def _make_server_and_ctx(self, decision: str):
        # All deps None: the gate path needs neither governance nor a tool
        # registry to reach the await (governance None -> no hard-deny;
        # tool None -> _classify_risk_tier returns a state-changing tier, so
        # the gate fires rather than auto-approving).
        server = WebServer()
        ws = _FakeWS("remove firefox", decision)
        ctx = ConnectionContext(
            client_id="test-client", source_interface="web", ws=ws,
        )
        return server, ws, ctx

    def _patched_turn(self, server, recorder: dict):
        """Stand in for _handle_client_message: issue ONE gate-eligible tool
        call through the REAL gate, then take the same deny branch the real
        stream does (web_server.py:1476-1498)."""
        tool_name = recorder.get("_tool_name", "manage_packages")
        tool_args = recorder.get("_tool_args",
                                 {"action": "remove", "name": "firefox"})

        async def fake_turn(ctx, data):
            tc = ToolCall(
                name=tool_name,
                arguments=dict(tool_args),
                source_of_request=Provenance.USER_DIRECT,
            )
            result = await server._evaluate_tool_with_gate(ctx, "turn1", tc)
            recorder["gate_result"] = result
            if result == "denied":
                await ctx.ws.send_json({
                    "type": "stream_token", "turn_id": "turn1",
                    "token": _FRIENDLY_DENY,
                })
            await ctx.ws.send_json({"type": "stream_end", "turn_id": "turn1"})
        return fake_turn

    async def _run(self, decision: str, tool_name: str = "manage_packages",
                   tool_args: dict | None = None) -> tuple[dict, list[dict]]:
        server, ws, ctx = self._make_server_and_ctx(decision)
        recorder: dict = {"_tool_name": tool_name}
        if tool_args is not None:
            recorder["_tool_args"] = tool_args
        server._handle_client_message = self._patched_turn(server, recorder)

        # 1.0s ceiling = the liveness invariant. Old code deadlocks here.
        await asyncio.wait_for(server._dispatch_loop(ctx), timeout=1.0)
        if ctx.turn_task is not None:
            await asyncio.wait_for(ctx.turn_task, timeout=1.0)
        return recorder, ws.sent


class WebGateDenyNoDeadlockTests(_GateHarness):
    def test_deny_resolves_turn_and_does_not_wedge(self):
        recorder, sent = asyncio.run(self._run("deny"))
        types_sent = [m.get("type") for m in sent]
        # The gate prompted, then resolved as a deny.
        self.assertIn("gate_prompt", types_sent)
        self.assertEqual(recorder.get("gate_result"), "denied")
        resolved = [m for m in sent if m.get("type") == "gate_resolved"]
        self.assertTrue(resolved, "no gate_resolved emitted")
        self.assertEqual(resolved[-1].get("decision"), "deny")
        # The friendly refusal reached the client, and the turn terminated.
        tokens = [m.get("token") for m in sent
                  if m.get("type") == "stream_token"]
        self.assertIn(_FRIENDLY_DENY, tokens)
        self.assertIn("stream_end", types_sent)

    def test_deny_no_wedge_across_all_gated_tools(self):
        # The deny no-wedge guarantee is tool-agnostic: every gated/mutating
        # tool must resolve a deny to the friendly refusal + clean terminal,
        # never a deadlock. Covers EVERY gated tool whose live deny is not
        # corpus-drivable on the shipped 2B — write_file, run_command, and the
        # consent-gated privacy tool take_screenshot — at the unit level
        # (deterministic; no live model, no box mutation/capture, no corpus).
        # take_screenshot is here so its gate-deny mechanism is REALLY covered,
        # not just annotated away: its inventory deny note cites this test.
        # (WC corpus-complete red-team, 2026-06-29.)
        gated = {
            "manage_packages": {"action": "remove", "name": "firefox"},
            "manage_services": {"action": "restart", "unit": "sshd"},
            "write_file": {"path": "/etc/hosts", "content": "x"},
            "run_command": {"command": "rm -rf /tmp/x"},
            "take_screenshot": {"source": "screenshot"},
        }
        for tool, args in gated.items():
            recorder, sent = asyncio.run(
                self._run("deny", tool_name=tool, tool_args=args))
            types_sent = [m.get("type") for m in sent]
            self.assertIn("gate_prompt", types_sent,
                          f"{tool}: no gate fired")
            self.assertEqual(recorder.get("gate_result"), "denied",
                             f"{tool}: gate did not resolve to denied")
            self.assertIn(_FRIENDLY_DENY,
                          [m.get("token") for m in sent
                           if m.get("type") == "stream_token"],
                          f"{tool}: friendly refusal not sent")
            self.assertIn("stream_end", types_sent,
                          f"{tool}: turn did not terminate")

    def test_allow_resolves_turn_and_does_not_wedge(self):
        # The same loop↔gate concurrency must work for an approval, too — the
        # deadlock was never deny-specific.
        recorder, sent = asyncio.run(self._run("allow"))
        self.assertEqual(recorder.get("gate_result"), "approved")
        self.assertIn("gate_prompt", [m.get("type") for m in sent])
        self.assertIn("stream_end", [m.get("type") for m in sent])

    def test_a_second_message_while_busy_is_rejected_not_dropped(self):
        # Turn-vs-turn serialization: a new message while one turn is in
        # flight gets a clean "busy" notice rather than racing or silently
        # dropping. Drives the busy guard added alongside the task spawn.
        server = WebServer()

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_turn(ctx, data):
            started.set()
            await release.wait()

        server._handle_client_message = slow_turn

        class _TwoMsgWS(_FakeWS):
            async def __anext__(self):
                self._step += 1
                if self._step == 1:
                    return _text({"type": "message", "content": "one"})
                if self._step == 2:
                    await started.wait()           # first turn is running
                    return _text({"type": "message", "content": "two"})
                if self._step == 3:
                    release.set()                  # let the first turn finish
                    raise StopAsyncIteration
                raise StopAsyncIteration

        ws = _TwoMsgWS("one", "allow")
        ctx = ConnectionContext(
            client_id="busy-client", source_interface="web", ws=ws,
        )

        async def drive():
            await asyncio.wait_for(server._dispatch_loop(ctx), timeout=1.0)
            if ctx.turn_task is not None:
                await asyncio.wait_for(ctx.turn_task, timeout=1.0)

        asyncio.run(drive())
        errors = [m for m in ws.sent if m.get("type") == "error"]
        self.assertTrue(any(m.get("code") == "busy" for m in errors),
                        f"expected a busy error, got {ws.sent}")


class WebGateRiskTierTruthTests(_GateHarness):
    """The consent card must state the tier the system actually computed.

    web_server historically hardcoded "user_scope_state_changing" into every
    gate_prompt while classifying the real tier internally — so a privileged
    action's card claimed a milder class than the one being enforced. These
    pin emitted == computed through the REAL gate path, using a pair that
    diverges under the classifier: manage_packages (privileged) vs
    take_screenshot (user-scope), both with tool_obj=None as in this harness.
    """

    def _emitted_tier(self, tool_name: str, tool_args: dict) -> tuple[str, str]:
        from intergen.tool_registry import _classify_risk_tier
        _, sent = asyncio.run(
            self._run("deny", tool_name=tool_name, tool_args=tool_args))
        prompts = [m for m in sent if m.get("type") == "gate_prompt"]
        self.assertEqual(len(prompts), 1, f"{tool_name}: expected one gate_prompt")
        computed = _classify_risk_tier(None, tool_args, tool_name).value
        return prompts[0].get("risk_tier"), computed

    def test_gate_prompt_risk_tier_matches_computed_privileged(self):
        emitted, computed = self._emitted_tier(
            "manage_packages", {"action": "remove", "name": "firefox"})
        self.assertEqual(computed, "privileged_state_changing")
        self.assertEqual(emitted, computed,
                         "gate card misstates the computed risk tier")

    def test_gate_prompt_risk_tier_matches_computed_user_scope(self):
        emitted, computed = self._emitted_tier(
            "take_screenshot", {"source": "screenshot"})
        self.assertEqual(computed, "user_scope_state_changing")
        self.assertEqual(emitted, computed,
                         "gate card misstates the computed risk tier")


if __name__ == "__main__":
    unittest.main()
