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


class TransportRefused(Exception):
    """The thing this transport drives gave NO RESPONSE AT ALL.

    Not a bad answer, not a failed assertion, not an error inside the product — no
    response: connection refused, the socket closed, the daemon gone, the model
    endpoint not answering. A turn that hits this measured NOTHING about the product,
    so nothing may be concluded from it in either direction.

    It is a DISTINCT TYPE on purpose. The run loop used to catch a bare ``Exception``
    around each scenario and file everything under "could not be driven", which sounds
    right and is not: a scenario that raised because the PRODUCT misbehaved is a
    finding, while a scenario that raised because the harness could not reach anything
    is a fact about the harness's environment. Only the second should stop a run.
    """


class ScenarioUndriveable(Exception):
    """A scenario was abandoned because one of its turns could not be driven.

    Carries WHICH scenario and WHICH turn, because "the run could not be driven" with
    no further detail is the report that sent someone to read a log by hand. It
    deliberately carries NO grade: a grade would be a claim about the product from a
    turn that never reached it, and inventing one is the defect this type exists to
    end (2026-08-26: four scenarios were awarded PASS with no model behind them).
    """

    def __init__(self, scenario_id: str, turn_index: int, reason: str) -> None:
        self.scenario_id = scenario_id
        self.turn_index = turn_index
        self.reason = reason
        super().__init__(
            f"scenario {scenario_id} could not be driven at turn "
            f"{turn_index + 1}: {reason}")


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

    def engine_reachable(self) -> tuple[bool, str]:
        """Can the thing that actually produces answers respond right now?

        Returns ``(reachable, reason)``; ``reason`` is empty when reachable and names
        the failure otherwise. This exists because the interesting outage is NOT the
        daemon going away — that raises, and always did. It is the daemon staying up
        while the ENGINE behind it dies: every model call gets connection refused,
        intergen/llm.py logs one line and returns nothing, the router serves a degraded
        reply, and the turn looks ordinary. Measured 2026-08-26 on the 2B laptop, that
        state graded four scenarios PASS.

        The default is fail-OPEN — a transport that cannot answer the question is not
        going to be treated as broken. That is deliberate and narrow: the mock has no
        engine to be unreachable, and a transport that DOES drive a real engine
        overrides this with a real probe. The check that consumes it is only ever an
        additional reason to refuse a verdict, never a reason to award one.
        """
        return True, ""

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

    #: The chat endpoint the daemon's model calls go to. Same default as
    #: intergen/llm.py; overridden from the daemon's own config when it exposes one,
    #: so the probe asks the address that actually failed rather than a guess.
    DEFAULT_CHAT_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"

    def _chat_endpoint(self) -> str:
        getter = getattr(self._client, "chat_endpoint", None)
        if callable(getter):
            try:
                url = getter()
                if isinstance(url, str) and url:
                    return url
            except Exception:                       # noqa: BLE001 — fall back, never fail here
                pass
        return self.DEFAULT_CHAT_ENDPOINT

    def engine_reachable(self) -> tuple[bool, str]:
        """Probe the model endpoint itself: is there anything there to answer?

        A REAL REQUEST, not a process check. ``llama_manager.is_running()`` polls the
        child process, which answers a different question — a process can be alive and
        not serving, and the state that matters is whether a call gets an HTTP
        response. The health path is used rather than a completion so the probe costs
        nothing and cannot perturb the run.

        Any HTTP response at all counts as reachable, INCLUDING an error status: a 503
        means something is there and answering, which is a different condition from
        connection refused and must not be conflated with it. Only a transport-level
        failure — refused, reset, timed out, DNS — reads as unreachable.
        """
        import urllib.error
        import urllib.request

        endpoint = self._chat_endpoint()
        health = endpoint.split("/v1/", 1)[0] + "/health"
        try:
            with urllib.request.urlopen(health, timeout=5.0):
                return True, ""
        except urllib.error.HTTPError:
            # It answered, just not with 200. Something is serving.
            return True, ""
        except Exception as exc:                    # noqa: BLE001 — the point of the probe
            return False, (f"no HTTP response from the model engine at {health} "
                           f"({type(exc).__name__}: {exc})")

    def ask(self, message: str) -> TurnResult:
        # A TRANSPORT-LEVEL FAILURE IS NOT A PRODUCT RESULT. If the call to the daemon
        # itself cannot complete, this turn measured nothing; say so in the type rather
        # than letting a generic exception reach a blanket handler that cannot tell the
        # difference between "the product broke" and "we never reached the product".
        try:
            resp = self._client.ask(message)
        except TransportRefused:
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise TransportRefused(
                f"no response from the daemon for this turn "
                f"({type(exc).__name__}: {exc})") from exc
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
        # Lets a self-test put the mock into the measured outage state without a
        # daemon: set to a reason string and every engine_reachable() call reports
        # unreachable with it. None = reachable.
        self.engine_unreachable_reason: str | None = None
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

    def engine_reachable(self) -> tuple[bool, str]:
        if self.engine_unreachable_reason:
            return False, self.engine_unreachable_reason
        return True, ""

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
