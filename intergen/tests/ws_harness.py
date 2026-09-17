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
import os
import pwd
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


# Where the panel token is looked for, in order, when no directory is given.
# Named here so the failure below can print the list it actually tried.
TOKEN_FILE_ENV = "INTERGEN_WS_TOKEN_FILE"


def _token_candidates() -> list[Path]:
    """Every place the panel token is looked for, in order.

    `Path.home()` alone is not enough, and the reason is a collision between two
    correct things. conftest.py redirects HOME and every XDG base to a throwaway
    directory before any intergen import, deliberately: a suite run must not
    touch the invoking user's own files (measured 2026-08-24, a run changed the
    modes of three directories and six files under the real home). The LIVE
    cells here, by design, drive the REAL daemon — whose token lives in the real
    home. So under pytest the redirection moved the token out of reach and the
    opt-in those cells advertise could not be taken: enabling
    INTERGEN_WS_HARNESS=1 produced four FileNotFoundError failures rather than
    four runs (measured 2026-09-16 on intergenos-192-r001-2, identically at
    a51039c4e, so not a regression — the opt-in had never been reachable under
    pytest).

    The order below fixes that without weakening the isolation. The isolation
    exists to stop a test run WRITING to the real home; every path here is READ,
    and only when a caller has asked to drive the live daemon.

      1. INTERGEN_WS_TOKEN_FILE, when set — an explicit path, which is the
         answer when the daemon is not this user's or not on this machine.
      2. The invoking user's home from the PASSWD DATABASE. os.getuid() is the
         real user either way; pwd does not read $HOME, so the redirection does
         not move it.
      3. Path.home() — unchanged behaviour outside pytest, and the path a
         caller who has set HOME on purpose means.
    """
    candidates: list[Path] = []
    explicit = os.environ.get(TOKEN_FILE_ENV)
    if explicit:
        candidates.append(Path(explicit))
    try:
        candidates.append(
            Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / "intergen"
            / "web-token")
    except (KeyError, OSError):
        pass
    candidates.append(Path.home() / ".config" / "intergen" / "web-token")
    seen: set[str] = set()
    ordered: list[Path] = []
    for c in candidates:
        key = str(c)
        if key not in seen:
            seen.add(key)
            ordered.append(c)
    return ordered


def default_token(config_dir: Path | None = None) -> str:
    """Read the panel web token the daemon writes for the local UI.

    `config_dir` names the directory to read, exactly as before. With none, the
    candidates above are tried in order and the first readable one wins. When
    none is readable the error NAMES every path tried and the environment
    variable that overrides them, because the bare FileNotFoundError this used
    to raise pointed at a throwaway pytest directory and said nothing about why
    the token was not there.
    """
    if config_dir is not None:
        return (Path(config_dir) / "web-token").read_text().strip()
    tried = _token_candidates()
    for path in tried:
        try:
            token = path.read_text().strip()
        except OSError:
            continue
        if token:
            return token
    raise FileNotFoundError(
        "no panel web token could be read. The daemon writes it when it "
        "starts; these paths were tried, in order: "
        + ", ".join(str(p) for p in tried)
        + f". Set {TOKEN_FILE_ENV} to read it from somewhere else.")


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
        self._host = host
        self._port = port
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
        """Send one message on a FRESH connection and collect the turn.

        Every call opens its own socket, so every call is its own conversation
        on the server (the browser server keeps one conversation per connected
        client). A turn that must see a prior turn goes through
        :class:`WSConversation` instead.

        Args:
            query: the user message.
            gate_decision: allow | allow_conversation | deny — the decision to
                send if a gate_prompt fires (when gate_action == "respond").
            gate_action: "respond" (send gate_decision), "ignore" (never
                respond — exercises the gate timeout path), or "cancel"
                (drop the connection on the gate prompt).
            deadline_s: client-side ceiling; on expiry closed_by="deadline".
        """
        async with WSConversation(host=self._host, port=self._port,
                                  token=self._token) as conv:
            return await conv.turn(
                query, gate_decision=gate_decision, gate_action=gate_action,
                deadline_s=deadline_s)


class WSConversation:
    """ONE open socket, many turns — the conversation a browser tab has.

    The server binds a conversation to a connection, so a follow-up that needs
    the prior turn ("who created it?" after "what year was Linux released?")
    must be sent on the SAME socket the first turn used. ``WSGateClient``
    opens a socket per turn and therefore cannot ask a follow-up; this holds
    the socket open across ``turn()`` calls and drains to the ``connected``
    handshake exactly once. The per-turn collection is shared with
    ``run_turn`` (``_drive_turn``), so the two read the frames identically.
    """

    def __init__(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 token: str | None = None) -> None:
        self._token = token or default_token()
        self._url = (f"ws://{host}:{port}/ws"
                     f"?token={self._token}&source_interface=web")
        self._session: aiohttp.ClientSession | None = None
        self._ws: Any = None
        # Every frame the server sent on this socket, across every turn, in
        # order — a battery seals this whole, not a per-turn slice.
        self.all_messages: list[dict[str, Any]] = []

    async def __aenter__(self) -> "WSConversation":
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            self._url, protocols=["intergen", f"bearer.{self._token}"])
        # Drain to the connected handshake before the first send (rule out a
        # send-before-ready race).
        for _ in range(20):
            m = await asyncio.wait_for(self._ws.receive(), timeout=10)
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                self.all_messages.append(d)
                if d.get("type") == "connected":
                    break
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
        finally:
            if self._session is not None:
                await self._session.close()

    async def turn(
        self,
        query: str,
        *,
        gate_decision: str | None = None,
        gate_action: str = "respond",
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> WSTurnResult:
        """Send one message on the open socket and collect its turn."""
        if self._ws is None:
            raise RuntimeError("WSConversation.turn() outside its context")
        r = WSTurnResult(query=query, gate_decision_sent=(
            gate_decision if gate_action == "respond" else None))
        await self._ws.send_json({"type": "message", "content": query})
        await _drive_turn(self._ws, r, gate_decision=gate_decision,
                          gate_action=gate_action, deadline_s=deadline_s)
        self.all_messages.extend(r.messages)
        return r


async def _drive_turn(ws: Any, r: WSTurnResult, *,
                      gate_decision: str | None, gate_action: str,
                      deadline_s: float) -> WSTurnResult:
    """Collect one turn's frames off ``ws`` into ``r`` (the message is already
    sent). Shared by the per-turn client and the multi-turn conversation so
    both read the protocol the same way."""
    t0 = time.monotonic()

    def _now() -> float:
        return round(time.monotonic() - t0, 2)

    while time.monotonic() - t0 < deadline_s:
        remaining = deadline_s - (time.monotonic() - t0)
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
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
