# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The web turn-lifecycle contract: acknowledge, bound, always terminate.

The defect these tests pin down
-------------------------------
A web turn sends NOTHING to the browser between "message received" and
"routing finished". The browser arms a whole-turn failsafe when the user
presses send (``RESPONSE_TIMEOUT_MS`` in intergen/web/app.js) and disarms it
only on ``stream_start`` or a terminal frame. The server has no routing
deadline of its own, so whenever routing outlives that failsafe the browser
hides the thinking indicator, shows "InterGen didn't respond — reconnecting…"
and force-closes the socket — while the server is still working on the turn.
The user cannot tell a dead assistant from a busy one, and the answer, when it
finally exists, has nowhere to go.

This is not hypothetical. Two turns in a live session recorded a
``route/turn_start`` event and then no further event of any kind; the browser
socket dropped about thirty seconds later and each session file held exactly
one message — the user's.

The three properties asserted here are the contract a turn must keep:

  1. ACKNOWLEDGE — a frame reaches the client before routing finishes, and
     within a small bound, so "received and working" is distinguishable from
     silence.
  2. BOUND — the server's own routing deadline is strictly shorter than the
     client's failsafe. Whichever side gives up first decides what the user
     sees, and it must be the side that knows why.
  3. TERMINATE — every disposition ends with an explicit terminal frame:
     a completed answer, a crash, and a routing stall alike.

Property 2 is checked against the SHIPPED client by parsing app.js, so the
invariant cannot rot by editing either side alone.
"""

from __future__ import annotations

import asyncio
import re
import time
import unittest
from pathlib import Path

from intergen import web_server as web_server_module
from intergen.web_server import WebServer, ConnectionContext

_APP_JS = Path(web_server_module.__file__).parent / "web" / "app.js"

# Frames that close a turn out for the client: each runs stopThinking() in
# app.js, releasing the thinking indicator and disarming the failsafe.
_TERMINAL_FRAMES = frozenset({"response", "stream_end", "error",
                              "frontier_response"})


class _RecordingWS:
    """A WebSocket stand-in that records every frame with its arrival time."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, dict]] = []
        self.closed = False

    async def send_json(self, obj: dict) -> None:
        self.frames.append((time.monotonic(), obj))

    async def close(self, *a, **k) -> None:
        self.closed = True

    # -- convenience -------------------------------------------------------
    @property
    def types(self) -> list[str]:
        return [f.get("type") for _, f in self.frames]

    def first_time_of(self, frame_type: str) -> "float | None":
        for at, f in self.frames:
            if f.get("type") == frame_type:
                return at
        return None


def _ctx(ws: _RecordingWS) -> ConnectionContext:
    return ConnectionContext(client_id="lifecycle-test",
                             source_interface="web", ws=ws)


class _Result:
    """The shape _handle_client_message expects back from route()."""

    def __init__(self, source: str = "keyword", text: str = "late answer"):
        self.source = source
        self.text = text
        self.handled = True
        self.tool_results: list = []
        self.full_output = ""
        self.reoffer_reminder = None
        self.answer_linkage = None


class _StallingRouter:
    """A router whose route() blocks in the worker thread, like a starved
    embedding round-trip behind an abandoned bulk batch."""

    def __init__(self, stall_s: float):
        self._stall = stall_s
        self.route_calls = 0

    def route(self, user_msg, decide_only=True, review_callback=None):
        self.route_calls += 1
        time.sleep(self._stall)
        return _Result()

    def last_route_confidence(self):
        return None

    def _append_history(self, *a, **k):
        return None


def _client_failsafe_seconds() -> float:
    """The whole-turn failsafe the SHIPPED browser client arms on send."""
    src = _APP_JS.read_text(encoding="utf-8")
    match = re.search(r"RESPONSE_TIMEOUT_MS\s*=\s*(\d+)", src)
    if match is None:
        raise AssertionError(
            f"RESPONSE_TIMEOUT_MS not found in {_APP_JS} — the client failsafe "
            "moved or was renamed; this invariant must be re-pointed, never "
            "quietly dropped.")
    return int(match.group(1)) / 1000.0


class DeadlineInvariantTests(unittest.TestCase):
    """Property 2 — the server must give up first, and knowingly."""

    def test_the_shipped_client_declares_a_whole_turn_failsafe(self):
        # Control: if this ever fails, the parse below is measuring nothing.
        self.assertGreater(_client_failsafe_seconds(), 0.0)

    def test_server_route_deadline_is_declared(self):
        deadline = getattr(web_server_module, "SERVER_ROUTE_DEADLINE_S", None)
        self.assertIsNotNone(
            deadline,
            "web_server declares no server-side routing deadline, so routing "
            "can outlive the browser's whole-turn failsafe and the user is "
            "shown a reconnect while the server is still working.")

    def test_server_route_deadline_is_strictly_shorter_than_the_client_failsafe(self):
        deadline = getattr(web_server_module, "SERVER_ROUTE_DEADLINE_S", None)
        self.assertIsNotNone(deadline, "no server-side routing deadline")
        client = _client_failsafe_seconds()
        self.assertLess(
            float(deadline), client,
            f"server routing deadline {deadline}s must be strictly shorter "
            f"than the client failsafe {client}s, or the browser gives up "
            "first and the server's explanation never reaches the user.")


class AcknowledgementTests(unittest.TestCase):
    """Property 1 — the turn is acknowledged before routing finishes."""

    @staticmethod
    async def _run_with_blocked_body():
        server = WebServer()
        ws = _RecordingWS()
        ctx = _ctx(ws)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_body(c, data):
            entered.set()
            await release.wait()
            await c.ws.send_json({"type": "response", "turn_id": "t",
                                  "content": "done"})

        server._handle_client_message = blocked_body
        started_at = time.monotonic()
        task = asyncio.create_task(
            server._run_turn(ctx, {"type": "message", "content": "hello"}))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        during_routing = list(ws.types)
        release.set()
        await asyncio.wait_for(task, timeout=2.0)
        return started_at, during_routing, ws

    def test_a_frame_reaches_the_client_before_routing_finishes(self):
        _, during_routing, _ = asyncio.run(self._run_with_blocked_body())
        self.assertTrue(
            during_routing,
            "the client received NOTHING while the turn was being routed — "
            "silence is indistinguishable from a dead server, which is what "
            "the browser's failsafe then acts on.")
        self.assertIn(
            "turn_ack", during_routing,
            "no acknowledgement frame was sent before routing finished; "
            f"frames seen during routing: {during_routing}")

    def test_the_acknowledgement_arrives_within_a_bound(self):
        started_at, _, ws = asyncio.run(self._run_with_blocked_body())
        ack_at = ws.first_time_of("turn_ack")
        self.assertIsNotNone(ack_at, "no turn_ack frame was ever sent")
        self.assertLess(
            ack_at - started_at, 1.0,
            "the acknowledgement must be effectively immediate — it exists to "
            "prove receipt, so it may not wait on any routing work.")

    def test_the_acknowledgement_carries_the_turn_id(self):
        _, _, ws = asyncio.run(self._run_with_blocked_body())
        acks = [f for _, f in ws.frames if f.get("type") == "turn_ack"]
        self.assertTrue(acks, "no turn_ack frame was ever sent")
        self.assertTrue(
            acks[0].get("turn_id"),
            "the acknowledgement must name the turn it acknowledges, or a "
            "client cannot match it to the message it sent.")


class TerminalFrameTests(unittest.TestCase):
    """Property 3 — every disposition ends with an explicit terminal frame."""

    @staticmethod
    async def _stalled_route(stall_s: float, deadline_s: float):
        server = WebServer()
        ws = _RecordingWS()
        ctx = _ctx(ws)
        server._router = _StallingRouter(stall_s)
        original = getattr(web_server_module, "SERVER_ROUTE_DEADLINE_S", None)
        web_server_module.SERVER_ROUTE_DEADLINE_S = deadline_s
        try:
            await asyncio.wait_for(
                server._run_turn(ctx, {"type": "message",
                                       "content": "what year did it start"}),
                timeout=stall_s * 0.75)
        finally:
            if original is None:
                if hasattr(web_server_module, "SERVER_ROUTE_DEADLINE_S"):
                    delattr(web_server_module, "SERVER_ROUTE_DEADLINE_S")
            else:
                web_server_module.SERVER_ROUTE_DEADLINE_S = original
        return ws

    def test_a_routing_stall_terminates_the_turn_instead_of_going_silent(self):
        # The turn must end on the SERVER's deadline, well before the stalled
        # route returns — and it must say so, rather than leaving the user
        # with a spinning indicator and an eventual forced reconnect.
        ws = asyncio.run(self._stalled_route(stall_s=4.0, deadline_s=0.2))
        self.assertTrue(
            _TERMINAL_FRAMES.intersection(ws.types),
            "a turn whose routing exceeded the server deadline sent no "
            f"terminal frame at all; frames seen: {ws.types}")

    def test_a_crashing_turn_still_terminates(self):
        # Control for the property: this backstop already exists, so a green
        # here proves the terminal-frame assertion is not vacuously failing.
        async def scenario():
            server = WebServer()
            ws = _RecordingWS()
            ctx = _ctx(ws)

            async def exploding_body(c, data):
                raise RuntimeError("turn body blew up")

            server._handle_client_message = exploding_body
            await asyncio.wait_for(
                server._run_turn(ctx, {"type": "message", "content": "x"}),
                timeout=2.0)
            return ws

        ws = asyncio.run(scenario())
        self.assertTrue(
            _TERMINAL_FRAMES.intersection(ws.types),
            f"a crashed turn left the client with no terminal frame: {ws.types}")

    def test_a_completed_turn_terminates(self):
        # Second control, same reason.
        async def scenario():
            server = WebServer()
            ws = _RecordingWS()
            ctx = _ctx(ws)

            async def quick_body(c, data):
                await c.ws.send_json({"type": "response", "turn_id": "t",
                                      "content": "answer"})

            server._handle_client_message = quick_body
            await asyncio.wait_for(
                server._run_turn(ctx, {"type": "message", "content": "x"}),
                timeout=2.0)
            return ws

        ws = asyncio.run(scenario())
        self.assertTrue(_TERMINAL_FRAMES.intersection(ws.types))


if __name__ == "__main__":
    unittest.main()
