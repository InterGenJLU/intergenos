# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The review bridge's own deadline ends its card on the page.

The web review bridge (_make_web_review_callback) blocks the registry's worker
thread on the card for at most _GATE_BRIDGE_TIMEOUT_S. When that wait gave up,
the bridge answered review_failed and the registry ran nothing — but the card's
coroutine went on running: the card stayed open on the page with live buttons
and no resolution frame, and an Approve pressed afterwards resolved it as
"allow" for an action the registry had already refused to run.

At its deadline the bridge now ends the card as a conversation boundary does:
the card leaves the connection's open cards, its waiter is resolved, and its
one resolution frame goes to the page with the reason. A decision that arrives
later finds no card.

The case drives the real ToolRegistry through the real bridge and the real card
on one connection, the registry in a worker thread as the web server runs it,
with the bridge's deadline made short and the card's own five minutes left as
they are — the order in which the bridge's deadline is the one that fires.
Nothing privileged runs: the root dispatch is a recorder, the dispatch token a
fixed value, and the audit writer a list.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen import tool_registry as registry_module
from intergen import web_server as web_server_module
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.tool_registry import REVIEW_FAILED, ToolRegistry
from intergen.web_server import ConnectionContext, WebServer

_ARGS = {"path": "/tmp/review-bridge-deadline-case-never-written.txt",
         "content": "x"}


class _RecordingWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.sent.append(frame)

    async def close(self, *a, **k) -> None:
        return None


class TheBridgesDeadlineEndsItsCard(unittest.TestCase):

    def setUp(self) -> None:
        self.records: list = []
        self.dispatched: list = []
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        for patcher in (
            mock.patch.object(registry_module, "write_record",
                              side_effect=lambda r, *a, **k:
                              self.records.append(r) or True),
            mock.patch.object(registry_module, "mint_token",
                              return_value="case-token"),
            mock.patch.object(self.registry, "_dispatch_via_pkexec",
                              side_effect=self._dispatch),
            mock.patch.object(web_server_module, "_GATE_BRIDGE_TIMEOUT_S", 0.5),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _dispatch(self, call, *a, **k) -> ToolResult:
        self.dispatched.append(call.arguments)
        return ToolResult(call_id=call.call_id, name=call.name, content="ok",
                          success=True)

    def test_a_card_the_bridge_gave_up_on_is_ended_on_the_page(self):
        server = WebServer()
        server._tools = self.registry
        server._governance = None
        ws = _RecordingWS()
        ctx = ConnectionContext(client_id="bridge-deadline-case",
                                source_interface="web", ws=ws)
        call = ToolCall(name="write_file", arguments=dict(_ARGS),
                        call_id="late", source_of_request=Provenance.USER_DIRECT)

        async def scenario():
            loop = asyncio.get_running_loop()
            bridge = server._make_web_review_callback(ctx, "turn-1", loop)
            result = await loop.run_in_executor(
                None, lambda: self.registry.execute(call,
                                                    review_callback=bridge))
            still_open = "late" in ctx.open_gates
            # The person presses Approve on whatever the page still shows.
            await server._handle_gate_decision(
                ctx, {"tool_call_id": "late", "decision": "allow"})
            for _ in range(20):
                await asyncio.sleep(0.01)
            return result, still_open

        result, still_open = asyncio.run(scenario())

        # The bridge's verdict, as before: the review failed, nothing ran.
        self.assertEqual(self.dispatched, [], "the held call ran")
        self.assertFalse(result.executed)
        self.assertEqual(self.records[0].user_decision, REVIEW_FAILED)

        prompts = [f for f in ws.sent if f.get("type") == "gate_prompt"]
        resolved = [f for f in ws.sent if f.get("type") == "gate_resolved"]
        self.assertEqual(len(prompts), 1, ws.sent)
        self.assertFalse(
            still_open,
            "the bridge gave up on the card and the card was still open on the "
            "connection: its buttons stayed live after its review had failed")
        self.assertEqual(
            len(resolved), 1,
            "a card the bridge gave up on must be resolved on the page once, "
            f"with its reason, and never by a later answer: {resolved!r}")
        self.assertNotEqual(
            resolved[0].get("decision"), "allow",
            "an Approve pressed after the bridge gave up resolved the card as "
            "allowed, for an action that did not run")
        self.assertTrue(resolved[0].get("reason"),
                        f"the card was ended without its reason: {resolved!r}")


if __name__ == "__main__":
    unittest.main()
