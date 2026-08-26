# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Transport layer — the daemon transports behind one interface.

The harness drives the assistant over its D-Bus surface. Two transports sit
behind one `ScenarioTransport` interface:

  * direct — an in-process daemon (no systemd / no session bus needed), with
             dispatch review auto-approved, memory isolated to a throwaway DB,
             and a fail-closed readiness gate.
  * dbus   — the live persistent daemon on the session bus.

Both are provided by the existing `InterGenTestClient`; this module wraps it so
the harness talks to ONE interface regardless of transport, and so a mock
transport can stand in for the daemon in the harness's own self-tests (which
must run with no model and no bus). The daemon-restart / new-session primitives
that the between-sessions memory axis needs are declared on the interface but
wired by a later phase; a transport that has not implemented them raises a clear
NotImplementedError rather than silently no-op'ing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


def _tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Best-effort extraction of dispatched tool names from a call list.

    A tool call dict has historically keyed the name under 'name' or 'tool';
    tolerate both (and skip a malformed entry) so a schema drift downgrades to a
    missing name rather than an exception mid-run.
    """
    names: list[str] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        name = call.get("name") or call.get("tool") or call.get("tool_name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


@dataclass
class TurnResult:
    """The transport-level result of one turn.

    Normalizes a daemon Ask reply into the fields the grader and logger consume.
    `tools_called` is the derived list of dispatched tool names (the fabrication
    guard reads it); `trace_id` is the join key to the always-on glass trace the
    grader's trace pass reads; `raw` is the full reply, preserved so a later
    field the daemon adds is captured without a transport change.
    """
    text: str
    source: str = ""
    handled: bool = False
    tools_called: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False
    escalated: bool = False
    trace_id: str = ""
    elapsed_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    # The offer to consult a frontier model, as the router decided it
    # (RouteResult.escalation_offer). Carried as its own field because it is a
    # DECISION, not a sentence in the answer — assertions read it here rather
    # than pattern-matching the text. Empty string, never None: the daemon's
    # field is `str | None` and normalizing at the edge means no assertion has to
    # know which of the two absences it is looking at. Before this field existed
    # the value was dropped between the daemon and the grader, so an offer that
    # fired correctly — and one that fired when it should not have — were both
    # invisible to every scenario.
    escalation_offer: str = ""


class ScenarioTransport(ABC):
    """One interface over every way of driving the assistant.

    Contract: `ask` sends a turn and returns a normalized TurnResult; `reset`
    clears per-conversation state between scenarios; `await_ready` blocks until
    the daemon can actually serve (fail-closed — it raises rather than let a
    not-ready daemon grade a pile of startup stubs as data). `restart_daemon`
    and `new_session` are the session-boundary primitives the memory axis needs;
    the base class raises NotImplementedError so an un-wired transport fails loud
    instead of silently skipping a boundary.
    """

    @abstractmethod
    def ask(self, message: str) -> TurnResult:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...

    @abstractmethod
    def await_ready(self, timeout_s: float | None = None) -> None:
        ...

    def status(self) -> dict[str, Any]:
        raise NotImplementedError("this transport does not expose status")

    def memory_db_path(self) -> str | None:
        """Path to the isolated memory DB the run's snapshot / delta-cleanup /
        leak / memory-write-gap checks read, or None when no DB is available to
        snapshot (the checks then no-op rather than guess). Default: none."""
        return None

    def restart_daemon(self) -> None:
        """Restart the daemon so durable memory must survive a real process
        lifecycle — the true between-sessions signal. A transport that does not
        override this fails loud rather than silently skipping the boundary (a
        skipped restart would let an in-memory store pass a persistence check)."""
        raise NotImplementedError(
            "restart_daemon is not wired for this transport")

    def new_session(self) -> None:
        """Begin a fresh session without restarting the daemon — the lighter
        boundary. A transport that does not override this fails loud."""
        raise NotImplementedError(
            "new_session is not wired for this transport")

    def close(self) -> None:
        ...

    def __enter__(self) -> "ScenarioTransport":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class ClientTransport(ScenarioTransport):
    """Real transport — wraps the existing InterGenTestClient (direct or dbus).

    Construction starts the daemon (direct) or connects to the bus (dbus) and,
    in direct mode, already blocks on the readiness gate. `await_ready` re-checks
    on demand. The wrapped client owns dispatch auto-approval, memory isolation,
    and the fail-closed reset — this class only adapts its shape to the
    ScenarioTransport interface, so there is one code path for both transports.
    """

    def __init__(self, mode: str = "direct") -> None:
        if mode not in ("direct", "dbus"):
            raise ValueError(f"unknown transport mode: {mode!r} (use 'direct' or 'dbus')")
        # Imported lazily: the client pulls in gi/the daemon, which the harness's
        # own self-tests (mock transport) must not require.
        from intergen.tests.client import InterGenTestClient
        self._mode = mode
        self._client = InterGenTestClient(mode=mode)

    def ask(self, message: str) -> TurnResult:
        resp = self._client.ask(message)
        return TurnResult(
            text=resp.text,
            source=resp.source,
            handled=resp.handled,
            tools_called=_tool_names(resp.tool_calls),
            tool_calls=resp.tool_calls,
            tool_results=resp.tool_results,
            used_llm=resp.used_llm,
            escalated=resp.escalated,
            trace_id=resp.trace_id,
            elapsed_ms=resp.elapsed_ms,
            raw=resp.raw,
            escalation_offer=getattr(resp, "escalation_offer", "") or "",
        )

    def reset(self) -> None:
        self._client.reset_conversation()

    def await_ready(self, timeout_s: float | None = None) -> None:
        # Route to the MODE-APPROPRIATE readiness gate on the wrapped client: the
        # dbus gate probes the live session-bus daemon (_status_dbus + a non-stub
        # probe turn), the direct gate probes the in-process daemon. The client
        # exposes BOTH as private methods, so selecting by a single name always
        # resolved the direct gate first — and in dbus mode that dereferences the
        # None in-process daemon (self._daemon.status()), failing closed only
        # after the full timeout instead of using the bus. (Surfaced on the first
        # live dbus run: a ready daemon RAISED after the timeout with
        # "'NoneType' object has no attribute 'status'".) A not-ready daemon must
        # still fail loud here, never grade startup stubs.
        gate_name = "_await_ready_dbus" if self._mode == "dbus" else "_await_ready"
        awaiter = getattr(self._client, gate_name, None)
        if callable(awaiter):
            awaiter(timeout_s)
            return
        status = self._client.status()
        if not status.get("components", {}).get("router"):
            raise RuntimeError(
                f"daemon not ready (router not built): {status.get('components', {})}")

    def status(self) -> dict[str, Any]:
        return self._client.status()

    def memory_db_path(self) -> str | None:
        getter = getattr(self._client, "memory_db_path", None)
        return getter() if callable(getter) else None

    def restart_daemon(self) -> None:
        """Real restart: bounce the daemon (direct: re-instantiate in-process
        against the SAME on-disk DB; dbus: bounce the service), then re-block on
        the fail-closed readiness gate so the next turn never hits a startup
        stub."""
        self._client.restart()
        self.await_ready()

    def new_session(self) -> None:
        self._client.new_session()

    def close(self) -> None:
        self._client.close()


class MockTransport(ScenarioTransport):
    """In-memory transport for the harness's own self-tests.

    Returns pre-scripted replies keyed by message (or a default), records the
    reset/ready calls, and needs no daemon, no bus, and no model. This is what
    lets the schema/loader/transport contract be tested deterministically; the
    real ClientTransport is exercised against a live daemon by the seed-scenario
    runs in a later work package.
    """

    def __init__(
        self,
        replies: dict[str, TurnResult] | None = None,
        default: TurnResult | None = None,
        memory_db_path: str | None = None,
    ) -> None:
        self._replies = replies or {}
        self._default = default or TurnResult(text="ok", source="mock")
        self._memory_db_path = memory_db_path
        self.asked: list[str] = []
        self.reset_count = 0
        self.ready_calls = 0
        self.restart_count = 0
        self.new_session_count = 0
        self.boundaries: list[str] = []
        # Interleaved call log. The per-call counters record HOW MANY; ordering
        # between reset and a session boundary is its own contract (a reset
        # issued into a daemon about to be restarted is what raced a live run),
        # and only a single ordered log can pin it.
        self.calls: list[str] = []
        self.closed = False

    def ask(self, message: str) -> TurnResult:
        self.asked.append(message)
        self.calls.append(f"ask:{message}")
        return self._replies.get(message, self._default)

    def reset(self) -> None:
        self.reset_count += 1
        self.calls.append("reset")

    def await_ready(self, timeout_s: float | None = None) -> None:
        self.ready_calls += 1
        self.calls.append("await_ready")

    def status(self) -> dict[str, Any]:
        return {"components": {"router": True}, "mock": True}

    def memory_db_path(self) -> str | None:
        return self._memory_db_path

    def restart_daemon(self) -> None:
        # No real process here; record the boundary so the runner's ordering and
        # boundary bookkeeping are testable with no daemon. A durable store
        # backed by a real DB survives untouched (the mock never wipes it).
        self.restart_count += 1
        self.boundaries.append("restart-before")
        self.calls.append("restart_daemon")

    def new_session(self) -> None:
        self.new_session_count += 1
        self.boundaries.append("new-session-before")
        self.calls.append("new_session")

    def close(self) -> None:
        self.closed = True
