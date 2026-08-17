# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen test client — replaces a prior WebSocket-based client implementation.

Sends messages to InterGen via D-Bus (Ask method) or direct Python
call (for testing without D-Bus daemon running). Returns structured
responses for the assertion engine.

Usage:
    client = InterGenTestClient()
    response = client.ask("What packages are installed?")
    print(response.text, response.source, response.tool_calls)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── the two bus-call bounds, and why the reset's is derived from the ask's ──
#
# A turn's Ask is bounded at ASK_CALL_TIMEOUT_MS. When that bound expires the
# client STOPS WAITING and returns an error-shaped result, but the daemon does
# not stop generating — it finishes the turn it was given. So a long generation
# can legitimately still be running when the runner has already walked away from
# it and advanced to the next scenario, whose first act is a ResetConversation.
#
# The reset's PER-ATTEMPT bound is deliberately short (a healthy idle daemon
# answers it immediately), but a short per-attempt bound must not be mistaken for
# the reset's total patience. Sizing the reset's patience independently of the
# ask bound is what broke a live run: the ask walked away at 120s on a 137.6s
# generation, the next scenario's reset hit the still-busy daemon, gave up after
# its own single short timeout, and the contamination guard aborted a run in
# which nothing was actually wrong — the daemon was healthy throughout and the
# reset landed the moment the generation finished.
#
# So the reset's TOTAL budget is derived from the ask bound, not chosen: the
# reset may not give up before the turn the runner already abandoned can
# possibly finish. The margin covers the daemon's own overrun past the bound
# (the generation that provoked this ran 17.6s past it).
ASK_CALL_TIMEOUT_MS = 120_000
RESET_CALL_TIMEOUT_MS = 10_000
RESET_BUSY_MARGIN_S = 30.0
RESET_BUSY_BUDGET_S = ASK_CALL_TIMEOUT_MS / 1000.0 + RESET_BUSY_MARGIN_S
RESET_BUSY_RETRY_DELAY_S = 1.0


def serving_readiness(status: dict[str, Any],
                      endpoint_healthy: bool) -> tuple[bool, str]:
    """Is this daemon status a SERVING-healthy daemon? -> (ready, blocking reason).

    Pure decision function, split out of the readiness gates so the degraded-up
    shape is testable with no bus and no daemon.

    "Bus-present" is not "serving". A daemon whose model server lost the port
    race comes up with its router built and its bus name claimed, answers
    deterministic turns from the keyword/template path, and reports
    ``components.llama_server`` False — the exact shape that a router-only gate
    accepted and then graded a persistence corpus against. Every condition below
    is a signal the daemon already publishes on its own Status method; nothing
    here infers.

    Conditions, in the order they are reported:

    1. the status read itself succeeded (a bus error is not a verdict);
    2. ``components.router`` — the router is built;
    3. ``components.llama_server`` — the daemon's OWN model server is running,
       which is precisely what a lost port race does not have;
    4. ``model_server_integrity_failure`` is unset — the daemon flags a refusal
       to serve here, and a flagged daemon must never be graded against;
    5. ``endpoint_healthy`` — the endpoint inference actually hits answers
       /health (the same signal the direct gate has always required).

    Conditions 3 and 5 are BOTH required deliberately. The endpoint alone can be
    held by a departing instance's server that is moments from exit, and the
    handle alone does not prove the port answers.
    """
    if not isinstance(status, dict) or status.get("error"):
        err = status.get("error") if isinstance(status, dict) else status
        return False, f"status unreadable ({err})"
    components = status.get("components") or {}
    if not components.get("router"):
        return False, "router not built"
    if not components.get("llama_server"):
        return False, ("the daemon's own model server is not running "
                       "(bus-present but model-degraded)")
    integrity = status.get("model_server_integrity_failure")
    if integrity:
        return False, f"model-server integrity failure reported: {integrity}"
    if not endpoint_healthy:
        return False, "model endpoint does not answer /health"
    return True, ""


def _auto_approve_dispatch(call: Any, decision: Any) -> str:
    """Non-interactive review surface for unattended harness runs.

    Matches ToolRegistry.execute()'s review_callback contract
    (call, decision) -> one of allow_once / allow_conversation / deny /
    deny_conversation, replacing the interactive zenity modal so a pull
    never blocks waiting for a human.

    Policy:
      - PRIVILEGED_STATE_CHANGING dispatches (decision.needs_pkexec) are
        DENIED. Allowing them would route through pkexec (tool_registry
        _dispatch_via_pkexec), which pops the OS polkit auth prompt — a
        hard block for an unattended run — and would actually mutate the
        box. Denying keeps the run non-interactive and side-effect-free,
        and the model's handling of the denial is itself a behaviour the
        dyno measures (honest-escalation vs fabricated success).
      - Everything else is approved "allow_once" so read-only / safe
        dispatches execute and the run measures the real acted-on result.
    The command safety denylist runs independently of this callback, so
    destructive commands (e.g. dd) stay blocked regardless of approval.

    Fail-safe: a decision object MISSING needs_pkexec is treated as privileged
    (deny), not approved — a consent closure must fail closed even in the
    harness (fail-closed rule 10, when in doubt deny). So if the DispatchDecision
    shape ever drifts, the harness denies rather than silently auto-approving
    privileged dispatches.
    """
    if getattr(decision, "needs_pkexec", True):
        return "deny"
    return "allow_once"


@dataclass
class TestResponse:
    """Structured response from InterGen for test assertions."""
    text: str
    source: str = ""
    handled: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False
    escalated: bool = False
    trace_id: str = ""
    elapsed_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class InterGenTestClient:
    """Test client for InterGen — D-Bus or direct mode.

    D-Bus mode: calls com.intergenos.InterGen.Ask() on the session bus.
    Direct mode: instantiates the daemon in-process (no D-Bus required).

    Direct mode is the default for testing since it doesn't require
    the daemon to be running as a systemd service.
    """

    def __init__(self, mode: str = "direct") -> None:
        """Initialize the test client.

        Args:
            mode: "direct" (in-process) or "dbus" (session bus).
        """
        self._mode = mode
        self._daemon = None
        self._dbus_available = False

        if mode == "direct":
            self._init_direct()
        elif mode == "dbus":
            self._init_dbus()
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def ask(self, message: str) -> TestResponse:
        """Send a message to InterGen and return structured response."""
        t0 = time.time()

        if self._mode == "dbus":
            raw = self._ask_dbus(message)
        else:
            raw = self._ask_direct(message)

        elapsed = (time.time() - t0) * 1000  # ms

        return TestResponse(
            text=raw.get("response", raw.get("text", "")),
            source=raw.get("source", ""),
            handled=raw.get("handled", False),
            tool_calls=raw.get("tool_calls", []),
            tool_results=raw.get("tool_results", []),
            used_llm=raw.get("used_llm", False),
            escalated=raw.get("escalated", False),
            trace_id=raw.get("trace_id", ""),
            elapsed_ms=elapsed,
            raw=raw,
        )

    def status(self) -> dict[str, Any]:
        """Get InterGen daemon status."""
        if self._mode == "dbus":
            return self._status_dbus()
        return self._status_direct()

    def reset_conversation(self) -> None:
        """Reset the daemon's per-conversation router state between tests.

        This is the canonical per-conversation isolation for BOTH modes — the
        parity the daemon's ResetConversation D-Bus method exists to provide.
        It clears trust posture, the ingress watermark, conversation history,
        ALL offer slots, and the preventive-grounding window (TTL + topic terms),
        so a prior conversation's staged offer / trust state can never leak into
        the next and contaminate the honesty battery (the cross-conversation
        over-steer root-caused in PI-Z29).

          - direct: call the in-process router's reset_conversation_state().
          - dbus:   call com.intergenos.InterGen.ResetConversation() on the bus,
                    which runs the SAME reset inside the persistent daemon.

        The dbus path is the root-cause fix: before it, a dbus-mode run never
        reset the persistent daemon's router between conversations (the old
        inline runner reset only ever reached the direct-mode in-process router),
        so the persistent daemon carried a prior conversation's state forward.
        """
        if self._mode == "dbus":
            self._reset_conversation_dbus()
            return
        # Direct mode: reset the in-process router. getattr-guarded for the
        # partial-construction path (same convention as _isolate_memory_db).
        router = getattr(self._daemon, "_router", None) if self._daemon else None
        if router is not None and hasattr(router, "reset_conversation_state"):
            router.reset_conversation_state()

    def memory_db_path(self) -> str | None:
        """Path to the memory DB the run's snapshot / delta-cleanup / leak /
        write-gap checks measure against.

        * direct mode: the isolated throwaway DB (memory poisoning is impossible;
          the whole store is disposable).
        * dbus mode: the live daemon's REAL per-user memory.db. Exposed for TEST
          OBSERVABILITY — so the run's snapshot/delta/leak/write-gap can actually
          measure what a live turn wrote (a store scenario that persists a fact,
          the linked-pair forget that must sweep it). This is the development
          store the fleet exercises on a dev box (standing order: daemon data
          stores are dev instruments, not protected user data); the delta cleanup
          removes ONLY rows the run created (created_at>=cutoff, with the
          baseline-id safety belt that never touches a pre-existing row), so it is
          a scoped measurement, not a wipe. Returning None here (the prior
          behaviour) silently no-op'd every isolation check in dbus mode, so a
          memory scenario ran blind. Resolves the SAME default path the daemon
          uses when ``memory.db_path`` is unset (the shipped default); a daemon
          started with a config-override path is not reflected — a known limit,
          not a silent wrong answer (the default is the shipped case).
        """
        test_mem_dir = getattr(self, "_test_mem_dir", None)
        if test_mem_dir:
            from pathlib import Path
            return str(Path(test_mem_dir) / "memory.db")
        if getattr(self, "_mode", None) == "dbus":
            from intergen.memory import _default_db_path
            return str(_default_db_path())
        return None

    def new_session(self) -> None:
        """Begin a fresh session without restarting the daemon — the lighter
        between-sessions boundary (exercises session scoping without the restart
        cost). At the current daemon surface the fresh-session boundary IS a
        conversation reset (there is no separate session-id-rotation method to
        drive without a daemon change), so this maps to reset_conversation() and
        leaves the durable memory DB untouched. The heavier, honest process
        boundary is restart()."""
        self.reset_conversation()

    def restart(self) -> None:
        """Restart the daemon so durable memory must survive a real process
        lifecycle — the true between-sessions signal. A fact stored before the
        restart has to be read back from the on-disk store after the process
        that wrote it is gone; an in-memory-only store cannot fake this."""
        if self._mode == "dbus":
            self._restart_dbus()
        else:
            self._restart_direct()

    def _restart_direct(self) -> None:
        """Tear down the in-process daemon and re-instantiate it against the
        SAME isolated on-disk memory DB (never a fresh throwaway — that would
        make persistence unmeasurable). Re-arms dispatch auto-approval and the
        fail-closed readiness gate exactly as first construction did."""
        keep_mem_dir = getattr(self, "_test_mem_dir", None)
        if self._daemon is not None:
            self._daemon.stop_service()
            self._daemon = None
        from intergen.dbus_daemon import InterGenDaemon
        self._daemon = InterGenDaemon()
        self._daemon._review_callback_override = _auto_approve_dispatch
        self._daemon.start_service()
        self._isolate_memory_db(reuse_dir=keep_mem_dir)
        self._await_ready()

    def _restart_dbus(self) -> None:
        """Bounce the persistent session-bus daemon and re-await readiness.

        The daemon runs as the session service the stack ships; a real process
        bounce is the honest restart, so this drives the service manager to
        restart the unit and then blocks on the fail-closed readiness gate. If
        the unit cannot be restarted (no service manager on this session), it
        fails LOUD rather than pretending a process boundary occurred — a faked
        restart would let an in-memory store pass the persistence check.

        The unit is ``Type=dbus``, so ``systemctl restart`` returns the moment the
        bus NAME is acquired — long before the daemon can serve. Readiness is
        therefore awaited with ``require_settled=True``: not just serving-healthy
        but STILL THE SAME PROCESS a moment later, because a restart that lost the
        model port race comes up degraded and bounces again underneath the caller.
        """
        import subprocess
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "intergen"],
                check=True, capture_output=True, timeout=60)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            raise RuntimeError(
                "dbus restart primitive could not bounce the daemon "
                f"('systemctl --user restart intergen' failed: {e}) — refusing "
                "to pretend a process boundary happened, which would let an "
                "in-memory store pass the between-sessions persistence check.") from e
        self._await_ready_dbus(require_settled=True)

    @staticmethod
    def _unit_main_pid() -> int | None:
        """The service unit's current MainPID, or None when it cannot be read.

        Used only as a SETTLE signal after a restart: two equal, non-zero reads a
        few seconds apart mean no further bounce is in flight. None (systemctl
        absent or erroring) is reported as unobservable so the caller can say so
        rather than infer stability it did not measure.
        """
        import subprocess
        try:
            out = subprocess.run(
                ["systemctl", "--user", "show", "intergen", "-p", "MainPID",
                 "--value"],
                check=True, capture_output=True, timeout=10, text=True)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return None
        raw = (out.stdout or "").strip()
        return int(raw) if raw.isdigit() else None

    def _unit_settled(self, window_s: float = 3.0) -> tuple[bool, str]:
        """True when the unit's MainPID is non-zero and unchanged over ``window_s``.

        The observed failure needs this: a restart whose daemon lost the model
        port race comes up bus-present but model-degraded, and the unit bounces
        AGAIN while the caller has already been told it is ready. Sampling the
        MainPID across a short window is what distinguishes "restarted and
        holding" from "still churning".
        """
        first = self._unit_main_pid()
        if first is None:
            return False, "unit MainPID is not observable (systemctl unavailable)"
        if first == 0:
            return False, "unit has no MainPID yet (still starting)"
        time.sleep(window_s)
        second = self._unit_main_pid()
        if second != first:
            return False, (f"unit bounced during the readiness check "
                           f"(MainPID {first} -> {second})")
        return True, ""

    def _await_ready_dbus(self, timeout_s: float | None = None,
                          require_settled: bool = False) -> None:
        """Fail-closed readiness gate for the persistent daemon.

        Mirrors ``_await_ready`` over the bus — and now mirrors ALL of it. The
        direct gate has always required the model ENDPOINT to be serving on top
        of a built router; this gate checked the router alone, so it returned on
        a daemon whose own model server never came up. That gap is the whole
        defect: the deterministic answer path replies to the probe turn without a
        model, so ``source != "startup"`` held while the daemon could not serve,
        and the gate said ready within milliseconds of the bus name appearing.

        Readiness now means SERVING health (see :func:`serving_readiness`): router
        built, the daemon's own model server running, no model-server integrity
        failure, the endpoint answering /health, and a probe turn coming back
        non-stub. With ``require_settled`` the unit must additionally still be the
        same process a few seconds later, so a daemon that is about to bounce
        again is never handed to the caller as ready.

        The budget (INTERGEN_TEST_READY_TIMEOUT, default 240s) is what absorbs the
        model port-release delay — the ladder waits for a prior socket to release,
        so a restart legitimately needs tens of seconds to reach serving health.
        Nothing here shortens or masks that; it waits for the real state.
        """
        if timeout_s is None:
            import os
            timeout_s = float(os.environ.get("INTERGEN_TEST_READY_TIMEOUT", "240"))
        deadline = time.monotonic() + timeout_s
        status: dict[str, Any] = {}
        reason = "no status read yet"
        while time.monotonic() < deadline:
            try:
                status = self._status_dbus()
            except Exception as e:  # noqa: BLE001 — status may race the restart
                status = {"error": str(e)}
            ready, reason = serving_readiness(status, self._model_endpoint_healthy())
            if ready:
                probe = self._ask_dbus("ping")
                if probe.get("source") != "startup":
                    if not require_settled:
                        return
                    settled, why = self._unit_settled()
                    if settled:
                        return
                    reason = why
                else:
                    reason = "probe turn still returns the 'starting up' stub"
            time.sleep(2.0)
        raise RuntimeError(
            f"dbus daemon not serving-ready within {timeout_s:.0f}s — refusing to "
            f"grade against it. Blocking condition: {reason}. A daemon that is "
            "bus-present but model-degraded answers deterministic turns and would "
            "grade as data. Last status components: "
            f"{status.get('components', status)}")

    @staticmethod
    def _check_reset_result(payload_str: str) -> None:
        """Fail LOUD on a non-affirmative ResetConversation reply.

        {"reset": true} is the only clean outcome. {"reset": false, ...} means
        the persistent daemon's router is not started, so per-conversation state
        was NOT reset — a harness ERROR, never a silent skip that would grade a
        contaminated run as clean (the exact 'looks-like-data but the reset never
        happened' hazard the eval-hygiene lessons warn against)."""
        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(
                f"InterGen ResetConversation returned an unparseable reply "
                f"{payload_str!r}: {e}") from e
        if not payload.get("reset", False):
            raise RuntimeError(
                "InterGen ResetConversation returned reset=false (reason: "
                f"{payload.get('reason', 'unknown')}) — the persistent daemon's "
                "router is not started, so per-conversation state cannot be "
                "reset. Aborting rather than grading a contaminated run.")

    def _reset_conversation_dbus(self) -> None:
        """Invoke ResetConversation() on the session bus, fail-closed on error.

        TOLERATES A BUSY DAEMON, REFUSES A BROKEN ONE. A daemon still finishing a
        generation the runner already walked away from (see the bounds at the top
        of this module) cannot answer a bus call until that turn completes. That
        is a BUSY daemon, not a failed reset, and giving up on it aborted a live
        run whose daemon was healthy the whole time.

        So a timeout is retried until RESET_BUSY_BUDGET_S — a budget derived from
        the ask bound, so the reset cannot give up before the abandoned turn can
        possibly finish — and only then fails loud. Every other bus-level failure
        (no such name, disconnected, a daemon that is genuinely gone) is fatal
        IMMEDIATELY, with no waiting: those are not busy, and burning the budget
        on them would turn a fast, honest abort into a two-minute stall.

        The contamination contract is unchanged and inviolable: if the reset has
        not demonstrably succeeded, this raises rather than let a contaminated
        conversation be graded. This extends the guard's PATIENCE, never its
        permissiveness.
        """
        from gi.repository import Gio, GLib

        deadline = time.monotonic() + RESET_BUSY_BUDGET_S
        attempts = 0
        while True:
            attempts += 1
            try:
                result = self._bus.call_sync(
                    "com.intergenos.InterGen",
                    "/com/intergenos/InterGen",
                    "com.intergenos.InterGen",
                    "ResetConversation",
                    None,
                    GLib.VariantType("(s)"),
                    Gio.DBusCallFlags.NONE,
                    RESET_CALL_TIMEOUT_MS,
                )
            except Exception as e:
                busy = isinstance(e, GLib.Error) and e.matches(
                    Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT)
                remaining = deadline - time.monotonic()
                if busy and remaining > 0:
                    log.info("ResetConversation timed out (attempt %d) — the "
                             "daemon is still busy with a turn the run walked "
                             "away from; %.0fs of budget left", attempts,
                             remaining)
                    time.sleep(min(RESET_BUSY_RETRY_DELAY_S, max(remaining, 0)))
                    continue
                detail = ("still busy after {:.0f}s".format(RESET_BUSY_BUDGET_S)
                          if busy else "bus-level failure")
                raise RuntimeError(
                    f"InterGen ResetConversation D-Bus call failed ({detail}, "
                    f"attempt {attempts}): {e} — cannot reset per-conversation "
                    "state; aborting rather than grading a contaminated run."
                ) from e
            break
        if attempts > 1:
            log.info("ResetConversation succeeded on attempt %d once the daemon "
                     "finished the abandoned turn", attempts)
        self._check_reset_result(result.unpack()[0])

    def close(self) -> None:
        """Clean up resources."""
        if self._daemon is not None:
            self._daemon.stop_service()
            self._daemon = None
        test_mem_dir = getattr(self, "_test_mem_dir", None)
        if test_mem_dir:
            import shutil
            shutil.rmtree(test_mem_dir, ignore_errors=True)
            self._test_mem_dir = None

    # --- Direct mode ---

    def _init_direct(self) -> None:
        """Initialize direct (in-process) mode."""
        from intergen.dbus_daemon import InterGenDaemon
        self._daemon = InterGenDaemon()
        # Inject a deterministic, non-interactive review surface BEFORE the
        # router runs any turn: an unattended dyno pull must never block on the
        # zenity approval modal (a held dispatch would otherwise hang the run
        # up to the 1-hour implicit-deny timeout). This auto-approves held
        # dispatches so the run measures what the floor DOES when allowed to
        # act; the command safety denylist still blocks destructive dispatches
        # regardless (e.g. dd), so the approval is bounded by the safety layer.
        self._daemon._review_callback_override = _auto_approve_dispatch
        self._daemon.start_service()
        self._isolate_memory_db()
        self._await_ready()
        log.info("Test client: direct mode initialized "
                 "(dispatch review auto-approved for unattended runs, "
                 "memory isolated to a temp DB, daemon confirmed ready)")

    def _await_ready(self, timeout_s: float | None = None) -> None:
        """Fail-closed readiness gate — refuse to run the corpus until the
        daemon can actually serve.

        A not-ready daemon answers EVERY turn with the 'InterGen is starting up'
        stub (source='startup'): either the router was never built, or
        start_service hit its single-instance guard and returned early because
        another InterGen owns the D-Bus name. The grader then scores a whole
        pull of identical stubs as a confident pile of pass/mixed/fail — a
        harness that LIES about what the floor did (the exact 'looks-like-data
        but answers nothing' failure the eval-hygiene lessons warn against).

        Readiness signal: router built AND the model ENDPOINT is healthy AND a
        real turn comes back non-stub. We check the endpoint (the fixed
        127.0.0.1:8080 llm.py talks to), NOT the daemon's managed-server handle
        (self._llama): inference works whenever a healthy llama-server owns that
        port — a server the daemon reused, or one still warming when the managed
        start's 60s budget lapsed (its slow cold load on a CPU-only box is the
        known TIER_2 threshold mismatch). Gating on the managed handle would
        wrongly reject a working warm server; gating on a non-stub turn ALONE
        would wrongly pass when the router is up but the model endpoint is down
        (freeform would route but error). So require both: endpoint up + real
        turn. Timeout is generous and env-overridable (cold loads are slow).
        """
        if timeout_s is None:
            import os
            timeout_s = float(os.environ.get("INTERGEN_TEST_READY_TIMEOUT", "240"))
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                last = self._status_direct().get("components", {})
            except Exception as e:  # noqa: BLE001 — status may race startup
                last = {"error": str(e)}
            if last.get("router") and self._model_endpoint_healthy():
                # Router built + model endpoint serving; confirm a real turn
                # does not come back as the startup stub.
                probe = self._ask_direct("ping")
                if probe.get("source") != "startup":
                    log.info("Daemon ready: router up, model endpoint serving "
                             "(probe source=%s)", probe.get("source"))
                    return
            time.sleep(2.0)
        raise RuntimeError(
            f"InterGen daemon not ready after {timeout_s:.0f}s — refusing to run "
            "the corpus against a not-ready daemon (every turn would return the "
            "'starting up' stub and grade as meaningless pass/fail). Likely "
            "causes: another InterGen owns the D-Bus name (single-instance "
            "guard), or the model endpoint never came up (cold 2B load can "
            "exceed the managed-start budget — raise INTERGEN_TEST_READY_TIMEOUT "
            f"or pre-warm the llama-server). Last components: {last}")

    def _model_endpoint_healthy(self) -> bool:
        """True iff the model endpoint llm.py uses answers /health.

        Mirrors llm.py's fixed chat endpoint and swaps the path for /health, so
        it tracks the SAME server inference actually hits — whoever owns the
        port. Independent of the daemon's managed-server handle.
        """
        import urllib.request
        llm = getattr(self._daemon, "_llm", None)
        endpoint = getattr(llm, "_endpoint",
                           "http://127.0.0.1:8080/v1/chat/completions")
        health_url = endpoint.rsplit("/v1/", 1)[0] + "/health"
        try:
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                return getattr(resp, "status", 200) == 200
        except Exception:  # noqa: BLE001 — unreachable/unhealthy => not ready
            return False

    def _isolate_memory_db(self, reuse_dir: str | None = None) -> None:
        """Point the daemon's memory at a throwaway temp DB.

        A dyno stores/recalls/forgets facts; without isolation those writes land
        in the user's REAL per-user memory.db and poison live state (and leak
        between tests). This is the crash-safe form of the classic
        save-state-then-restore: there is no real DB to restore because we never
        write to it. Cleaned up in close().

        When ``reuse_dir`` is supplied (a restart), the daemon is re-pointed at
        the SAME on-disk DB instead of a fresh throwaway, so a fact written
        before the restart must be read back from disk after the writing process
        is gone — the honest between-sessions persistence signal (an
        in-memory-only store cannot fake surviving a process boundary).
        """
        import tempfile
        from pathlib import Path
        from intergen.memory import MemoryManager
        try:
            self._test_mem_dir = reuse_dir or tempfile.mkdtemp(prefix="intergen-test-mem-")
            test_mem = MemoryManager(str(Path(self._test_mem_dir) / "memory.db"))
            self._daemon._memory = test_mem
            router = getattr(self._daemon, "_router", None)
            if router is not None:
                router._memory = test_mem
            log.info("Test memory isolated at %s", self._test_mem_dir)
        except Exception as e:
            # Fail CLOSED: an isolation failure leaves the daemon pointed at the
            # user's REAL ~/.local/share/intergen/memory.db, so a dyno that
            # stores/recalls/forgets facts would write live user state — the
            # exact poisoning this isolation exists to prevent. Per the
            # fail-closed posture (rule 10, when in doubt deny), ABORT
            # the run rather than silently writing the real DB. Clean up any
            # partial temp dir first so a half-made isolation leaves no litter.
            partial = getattr(self, "_test_mem_dir", None)
            if partial:
                import shutil
                shutil.rmtree(partial, ignore_errors=True)
            self._test_mem_dir = None
            raise RuntimeError(
                "Test memory isolation FAILED — refusing to run against the real "
                f"per-user memory DB (would poison live user state): {e}") from e

    def _ask_direct(self, message: str) -> dict[str, Any]:
        """Ask via direct Python call."""
        response = self._daemon.ask(message)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"response": response, "source": "direct"}

    def _status_direct(self) -> dict[str, Any]:
        """Get status via direct call."""
        return json.loads(self._daemon.status())

    # --- D-Bus mode ---

    def _init_dbus(self) -> None:
        """Initialize D-Bus mode."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            self._dbus_available = True
            log.info("Test client: D-Bus mode initialized")
        except Exception as e:
            log.warning("D-Bus not available: %s. Falling back to direct.", e)
            self._mode = "direct"
            self._init_direct()

    def _ask_dbus(self, message: str) -> dict[str, Any]:
        """Ask via D-Bus."""
        from gi.repository import Gio, GLib

        try:
            result = self._bus.call_sync(
                "com.intergenos.InterGen",
                "/com/intergenos/InterGen",
                "com.intergenos.InterGen",
                "Ask",
                GLib.Variant("(s)", (message,)),
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                ASK_CALL_TIMEOUT_MS,
            )
            response_str = result.unpack()[0]
            return json.loads(response_str)
        except Exception as e:
            return {"response": f"D-Bus error: {e}", "source": "error"}

    def _status_dbus(self) -> dict[str, Any]:
        """Get status via D-Bus."""
        from gi.repository import Gio, GLib

        try:
            result = self._bus.call_sync(
                "com.intergenos.InterGen",
                "/com/intergenos/InterGen",
                "com.intergenos.InterGen",
                "Status",
                None,
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                5000,
            )
            return json.loads(result.unpack()[0])
        except Exception as e:
            return {"error": str(e)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
