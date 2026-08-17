# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WebSocket eval-harness — drives the REAL panel path (ws://<host>:8089/ws).

Why this exists
---------------
The direct/dbus dyno (`tests/client.py`) runs in-process with a SYNCHRONOUS
auto-approve review callback. That bypasses the web `gate_future` bridge
entirely, so it structurally CANNOT reproduce a web-path gate defect — which
is exactly how the F2 deny-hang shipped untested. This harness drives the real
`/ws` surface, RESPONDS to a `gate_prompt` with a controllable per-scenario
decision (allow / allow_conversation / deny / ignore→timeout / cancel), and
records every server message — the "real-round-trip gate responder" of the
eval-harness PR1 design.

Universal per-turn liveness invariant
-------------------------------------
Every turn driven through here is checked for the structural property that the
F2 hang violated: it reached a TERMINAL state within a hard deadline AND
returned a non-empty user-visible response. `WSTurnResult.liveness_ok` is that
invariant; `assert_live()` raises with the full event trace on violation. A
wedged turn (no terminal, or a server-side socket close with no reply) fails it
even when no deny scenario is authored — the catch is structural, not
scenario-specific.

This is async and dependency-light (aiohttp, already a runtime dep). It needs a
LIVE daemon + model; pytest cells that use it skip themselves when one is not
reachable (see test_ws_gate_lifecycle.py).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8089
# Real-HW liveness ceiling, grounded against the slowest target hardware: a
# legitimate 2B synthesis on the slowest box (the AMD floor) already exceeded a
# ~25s call bound in the embedding proof, so the ceiling must clear that with
# ample headroom while staying far below the production implicit-deny — it is a
# wedge backstop, not a latency gate. 120s = ~5x the known slow-legit bound.
# The ceiling is context-dependent, never one global value: the mock/unit
# deadlock test uses a ~1s ceiling instead (a deadlock hangs far past 1s).
DEFAULT_DEADLINE_S = 120.0

# Server→client message types that END a turn from the client's point of view.
# "response" is the non-streaming fast-path reply (P0/P1) — it IS terminal; the
# original ws_gate_probe omitted it and so mis-read a clean fast answer as a
# hang. stream_end is the streaming terminal; error is a terminal failure.
_TERMINAL_TYPES = frozenset({
    "stream_end", "response", "response_complete", "turn_complete", "done",
    "error",
})


def default_token(config_dir: Path | None = None) -> str:
    """Read the panel web token the daemon writes for the local UI."""
    base = config_dir or (Path.home() / ".config" / "intergen")
    return (base / "web-token").read_text().strip()


@dataclass
class WSTurnResult:
    """Structured outcome of one turn driven over the real WS path."""
    query: str
    terminal: bool = False
    text: str = ""
    saw_gate: bool = False
    gate_decision_sent: str | None = None
    gate_resolved_decision: str | None = None
    elapsed_s: float = 0.0
    gate_prompt_at: float | None = None
    closed_by: str = "client"          # client | server | deadline
    events: list[tuple[float, str]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def liveness_ok(self) -> bool:
        """The universal invariant: reached a terminal state AND said something.

        A turn that the server silently dropped (closed_by='server' with no
        terminal message) or that we waited out (closed_by='deadline') fails,
        as does a terminal-but-empty turn.
        """
        return self.terminal and bool(self.text.strip())

    def assert_live(self) -> "WSTurnResult":
        """Raise AssertionError with the full trace if the invariant fails."""
        if not self.liveness_ok:
            raise AssertionError(
                f"LIVENESS FAIL for {self.query!r}: terminal={self.terminal} "
                f"text={self.text[:120]!r} closed_by={self.closed_by} "
                f"elapsed={self.elapsed_s}s events={self.events}"
            )
        return self


class WSGateClient:
    """Minimal real-WS client with a per-scenario gate responder."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 token: str | None = None) -> None:
        self._token = token or default_token()
        self._url = (f"ws://{host}:{port}/ws"
                     f"?token={self._token}&source_interface=web")

    async def run_turn(
        self,
        query: str,
        *,
        gate_decision: str | None = None,
        gate_action: str = "respond",
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> WSTurnResult:
        """Send one message and collect the turn.

        Args:
            query: the user message.
            gate_decision: allow | allow_conversation | deny — the decision to
                send if a gate_prompt fires (when gate_action == "respond").
            gate_action: "respond" (send gate_decision), "ignore" (never
                respond — exercises the gate timeout path), or "cancel"
                (drop the connection on the gate prompt).
            deadline_s: client-side ceiling; on expiry closed_by="deadline".
        """
        r = WSTurnResult(query=query, gate_decision_sent=(
            gate_decision if gate_action == "respond" else None))
        t0 = time.monotonic()

        def _now() -> float:
            return round(time.monotonic() - t0, 2)

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._url, protocols=["intergen", f"bearer.{self._token}"],
            ) as ws:
                # Drain to the connected handshake before sending (rule out a
                # send-before-ready race).
                for _ in range(20):
                    m = await asyncio.wait_for(ws.receive(), timeout=10)
                    if (m.type == aiohttp.WSMsgType.TEXT
                            and json.loads(m.data).get("type") == "connected"):
                        break

                await ws.send_json({"type": "message", "content": query})

                while time.monotonic() - t0 < deadline_s:
                    remaining = deadline_s - (time.monotonic() - t0)
                    try:
                        msg = await asyncio.wait_for(ws.receive(),
                                                     timeout=remaining)
                    except asyncio.TimeoutError:
                        r.closed_by = "deadline"
                        break

                    if msg.type in (aiohttp.WSMsgType.CLOSED,
                                    aiohttp.WSMsgType.CLOSING,
                                    aiohttp.WSMsgType.ERROR):
                        r.closed_by = "server"
                        r.events.append((_now(), f"WS_{msg.type.name}"))
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue

                    d = json.loads(msg.data)
                    t = d.get("type", "")
                    r.events.append((_now(), t))
                    r.messages.append(d)

                    if t == "gate_prompt":
                        r.saw_gate = True
                        r.gate_prompt_at = _now()
                        if gate_action == "cancel":
                            await ws.close()
                            r.closed_by = "client"
                            break
                        if gate_action == "respond" and gate_decision:
                            await ws.send_json({
                                "type": "gate_decision",
                                "tool_call_id": d.get("tool_call_id"),
                                "decision": gate_decision,
                            })
                        # gate_action == "ignore": deliberately say nothing.
                    elif t == "gate_resolved":
                        r.gate_resolved_decision = d.get("decision")
                    elif t == "stream_token":
                        r.text += d.get("token", "")
                    elif t == "response":
                        r.text += d.get("content", "") or d.get("text", "")
                        r.terminal = True
                        break

                    if t in _TERMINAL_TYPES and t != "response":
                        if t == "error":
                            r.text += "[error] " + json.dumps(d)
                        r.terminal = True
                        break

        r.elapsed_s = round(time.monotonic() - t0, 2)
        return r


def daemon_reachable(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                     timeout_s: float = 2.0) -> bool:
    """True iff the panel HTTP endpoint answers — gate for live cells."""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/", timeout=timeout_s) as resp:
            return getattr(resp, "status", 200) < 500
    except Exception:  # noqa: BLE001 — unreachable => skip live cells
        return False
