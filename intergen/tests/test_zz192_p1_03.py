# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A gate decision must resolve the review card it NAMES — and only that card.

What this pins
--------------
``_render_review_card`` records the call it is waiting for and then waits for
that person's answer (web_server.py:2898-2900)::

    # Wait for user decision via Future
    ctx.pending_gate = tool_call_id
    ctx.gate_future = asyncio.get_running_loop().create_future()

``pending_gate`` and ``gate_future`` are SINGLE per-connection slots
(ConnectionContext, web_server.py:183-184) and the card rewrites both every
time it is entered, unconditionally. Two cards can be open on one connection at
the same time: ``_spawn_tool_task`` (web_server.py:1685-1698) deliberately
detaches each user-invoked tool into ``ctx.tool_tasks`` — a SET — and the
receive loop's busy guard covers only ``type: "message"``, so a second
``/screenshot`` or a ``/file <path>`` (both gate: they are INGRESS_TOOLS_V1, so
the read-only FREE short-circuit never applies to them) renders its own card
while the first one is still on screen. The client appends a card per
``gate_prompt`` and each card's buttons carry their own ``data-tcid``
(web/app.js handleGatePrompt / the .gate-btn delegate), so the person really
can answer the FIRST card while the SECOND is showing.

``_handle_gate_decision`` (web_server.py:1533-1566) reads ``tool_call_id`` for
its missing-field check, its log line and its no-waiter echo, and then resolves
whatever future happens to be in the slot::

    if ctx.gate_future is not None and not ctx.gate_future.done():
        ctx.gate_future.set_result(decision)

It never compares that id to ``ctx.pending_gate``. So one action's Approve
authorises a different action, and the card the person actually answered is
left waiting on an orphaned future until its 300 s ``wait_for`` expires and
reports a denial for something they allowed.

These cells drive the REAL gate path (``_evaluate_tool_with_gate`` ->
``_render_review_card``) and the REAL ``_handle_gate_decision`` on one
ConnectionContext. Deps are all None, as in the deterministic companion
test_web_gate_deny_no_deadlock.py: governance None -> no hard-deny; tools None
-> a state-changing tier, so the card fires rather than auto-approving. No
daemon, no socket, no model, no capture.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen import web_server as ws_mod
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall
from intergen.web_server import ConnectionContext, WebServer

# A gated action, classified state-changing with tool_obj=None (the harness
# convention of test_web_gate_deny_no_deadlock.py).
_TOOL = "manage_packages"
_ARGS = {"action": "remove", "name": "firefox"}

# Generous next to the loop tick a resolved card needs; small enough that a
# never-resolved card is observed as unresolved instead of hanging the run.
_SETTLE_S = 0.5


class _RecordingWS:
    """Minimal aiohttp-WSResponse stand-in: records frames, signals each
    gate_prompt by its tool_call_id."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._prompted: dict[str, asyncio.Event] = {}

    # -- server -> client --------------------------------------------------
    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)
        if obj.get("type") == "gate_prompt":
            self._event(obj.get("tool_call_id")).set()

    async def close(self, *a, **k) -> None:
        self.closed = True

    # -- test helpers ------------------------------------------------------
    def _event(self, tool_call_id: str) -> asyncio.Event:
        return self._prompted.setdefault(tool_call_id, asyncio.Event())

    async def wait_for_card(self, tool_call_id: str) -> None:
        """Return once the card for this call is on screen — and therefore its
        coroutine has reached its await on its own future."""
        await asyncio.wait_for(self._event(tool_call_id).wait(), timeout=2.0)

    def frames(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == kind]


def _call(call_id: str) -> ToolCall:
    return ToolCall(
        name=_TOOL,
        arguments=dict(_ARGS),
        call_id=call_id,
        source_of_request=Provenance.USER_DIRECT,
    )


async def _drive(card_ids: tuple[str, ...], named: str,
                 decision: str = "allow") -> dict:
    """Open one card per id on ONE connection, then deliver a single
    gate_decision naming `named`. Returns what each card did.

    No assertion runs in here: the tasks are always cleaned up first, and the
    observations are asserted by the caller.
    """
    server = WebServer()
    ws = _RecordingWS()
    ctx = ConnectionContext(
        client_id="zz192-p1-03", source_interface="web", ws=ws,
    )

    tasks: dict[str, asyncio.Task] = {}
    try:
        for cid in card_ids:
            tasks[cid] = asyncio.create_task(
                server._evaluate_tool_with_gate(ctx, f"turn-{cid}", _call(cid))
            )
            await ws.wait_for_card(cid)

        # The person answers the card named `named`; the frame carries that
        # card's own id, exactly as the panel's .gate-btn handler sends it.
        await server._handle_gate_decision(
            ctx, {"tool_call_id": named, "decision": decision})

        await asyncio.wait(set(tasks.values()), timeout=_SETTLE_S)
        return {
            "verdicts": {cid: (t.result() if t.done() else None)
                         for cid, t in tasks.items()},
            "prompted": [m.get("tool_call_id")
                         for m in ws.frames("gate_prompt")],
            "resolved": [(m.get("tool_call_id"), m.get("decision"))
                         for m in ws.frames("gate_resolved")],
        }
    finally:
        for t in tasks.values():
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)


class GateDecisionNamesItsCardTests(unittest.TestCase):

    def test_a_gate_decision_resolves_only_the_card_it_names(self):
        # Two cards open on one connection — the second one entered
        # _render_review_card while the first was still waiting.
        obs = asyncio.run(_drive(("aaa", "bbb"), named="aaa"))

        self.assertEqual(obs["prompted"], ["aaa", "bbb"],
                         "both cards must have been put on screen")
        # The person pressed Approve on "aaa". That card — and no other — is
        # what their decision authorises: "aaa" returns approved, and "bbb",
        # which nobody answered, still waits for its own decision.
        self.assertEqual(
            (obs["verdicts"]["aaa"], obs["verdicts"]["bbb"]),
            ("approved", None),
            "a decision naming 'aaa' must resolve the 'aaa' card and leave "
            f"'bbb' unanswered; got aaa={obs['verdicts']['aaa']!r} "
            f"bbb={obs['verdicts']['bbb']!r} with gate_resolved frames "
            f"{obs['resolved']!r} — the approval of one action was applied to "
            "a different action, and the answered card was left orphaned",
        )

    def test_a_decision_naming_an_unknown_card_resolves_nothing(self):
        # One card open; a decision arrives naming a call id that is not it (a
        # stale click from an already-resolved card, a replayed frame). It must
        # not stand in for the answer the open card is waiting for.
        obs = asyncio.run(_drive(("aaa",), named="zzz"))

        self.assertEqual(obs["prompted"], ["aaa"], "the card must have shown")
        self.assertIsNone(
            obs["verdicts"]["aaa"],
            "a gate_decision naming 'zzz' must not resolve the open 'aaa' "
            f"card; got aaa={obs['verdicts']['aaa']!r} with gate_resolved "
            f"frames {obs['resolved']!r}",
        )


class ACardThatTimesOutIsResolvedOnceTests(unittest.TestCase):

    def test_a_timed_out_card_sends_one_resolution_frame_with_its_reason(self):
        async def go():
            server = WebServer()
            ws = _RecordingWS()
            ctx = ConnectionContext(
                client_id="zz192-p1-03", source_interface="web", ws=ws,
            )
            with mock.patch.object(ws_mod, "_REVIEW_CARD_TIMEOUT_S", 0.05):
                verdict = await asyncio.wait_for(
                    server._evaluate_tool_with_gate(ctx, "turn-t",
                                                    _call("timed")),
                    timeout=5)
            return verdict, ws, ctx

        verdict, ws, ctx = asyncio.run(go())
        resolved = [m for m in ws.frames("gate_resolved")
                    if m.get("tool_call_id") == "timed"]
        self.assertEqual(verdict, "denied")
        self.assertEqual(
            len(resolved), 1,
            f"a card that timed out was resolved {len(resolved)} times: "
            f"{resolved!r}")
        self.assertEqual(resolved[0].get("decision"), "deny")
        self.assertIn("timed out", resolved[0].get("reason", ""))
        self.assertNotIn("timed", ctx.open_gates)


if __name__ == "__main__":
    unittest.main()
