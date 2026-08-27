# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""D-Bus daemon skeleton — com.intergenos.InterGen service.

Exposes InterGen over D-Bus so the GNOME panel applet, CLI tools,
and other desktop applications can communicate with the AI assistant.

Service name: com.intergenos.InterGen
Interface:    com.intergenos.InterGen
Object path:  /com/intergenos/InterGen

Methods:
  Ask(message: str) -> str
  Status() -> str (JSON)
  GetTier() -> str (JSON)

Skeleton — the conversation router wires into this once router work
lands.

Runs as: systemd user service (intergen.service)
Requires: dbus-python (or pydbus/dasbus)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Callable

from intergen import eval_consent, glass, private_state, safety
from intergen.interfaces.dbus import InterGenDBusInterface
from intergen.interfaces.types import (
    AnswerLinkage,
    HardwareTierLevel,
    StartFailure,
)

log = logging.getLogger(__name__)

SERVICE_NAME = "com.intergenos.InterGen"
OBJECT_PATH = "/com/intergenos/InterGen"
INTERFACE_NAME = "com.intergenos.InterGen"

# Web-server bind watchdog (the cold-boot greeter port-8089 collision): retry
# the bind on an interval until the greeter session tears down and frees the
# port, bounded so a permanently-held port logs a loud give-up rather than
# spinning forever. ~5 min of teardown headroom, matching the embed/chat
# recovery posture.
WEB_BIND_RETRY_INTERVAL = 5     # seconds between rebind attempts
WEB_BIND_MAX_ATTEMPTS = 60      # ~5 min total

# Largest game identifier accepted by PauseForGame / ResumeAfterGame. The value
# is a window class (steam_app_620, gamescope, …), so 256 bytes is far more than
# any real one needs while still bounding what a local caller can hand the
# daemon to log and report.
GAME_NAME_MAX_BYTES = 256

# D-Bus introspection XML for the interface
INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="Ask">
      <arg direction="in" name="message" type="s"/>
      <arg direction="out" name="response" type="s"/>
    </method>
    <method name="Escalate">
      <arg direction="in" name="message" type="s"/>
      <arg direction="out" name="response" type="s"/>
    </method>
    <method name="Status">
      <arg direction="out" name="status" type="s"/>
    </method>
    <method name="GetTier">
      <arg direction="out" name="tier" type="s"/>
    </method>
    <method name="ResetConversation">
      <arg direction="out" name="result" type="s"/>
    </method>
    <method name="PauseForGame">
      <arg direction="in" name="game" type="s"/>
      <arg direction="out" name="result" type="s"/>
    </method>
    <method name="ResumeAfterGame">
      <arg direction="in" name="game" type="s"/>
      <arg direction="out" name="result" type="s"/>
    </method>
  </interface>
</node>
"""


class InterGenDaemon(InterGenDBusInterface):
    """D-Bus service skeleton for InterGen.

    The daemon initializes subsystems in order:
      1. Hardware detection → tier assignment
      2. Model download/verification
      3. llama-server startup
      4. (future: semantic matcher, tool registry, MCP)
      5. D-Bus interface export

    Currently a skeleton — subsystem wiring happens after merge.
    """

    def __init__(self, eval_consent_marker: str | None = None) -> None:
        # EVAL-CONSENT DENY-AND-RECORD (unattended baseline runs). Armed ONLY
        # from this constructor — the daemon's own launch — and only by the
        # explicit marker the eval harness supplies (main() maps the
        # --eval-consent-deny argument to it; the in-process harness passes it
        # directly). Deliberately NOT an env var and NOT a config file: neither
        # can be tied to a specific invocation, and both could arm a production
        # daemon without anyone having asked for it at launch. There is likewise
        # NO D-Bus setter — the standing invariant that a live daemon's consent
        # posture can never be flipped over the bus is preserved unchanged.
        # Fail-closed: an absent or malformed marker leaves the responder
        # disarmed, which IS production behavior (real gates). The responder can
        # only ever answer deny, so arming is monotonically restrictive.
        if eval_consent_marker is not None:
            eval_consent.arm(eval_consent_marker)
        self._running = False
        # M1 Glass Pipeline: one boot "turn" id threads every warmup row;
        # _boot_t0 anchors time-to-ready.
        self._boot_turn = f"boot-{int(time.time())}"
        self._boot_t0 = time.monotonic()
        self._bus = None
        # The two handles _export_dbus takes on that connection: the object
        # registration and the bus-name ownership. Initialised HERE, not on
        # first export, so the teardown below can run unconditionally instead of
        # guessing with getattr — a teardown that must work whether or not the
        # export happened cannot rest on an attribute that may not exist.
        self._reg_id = None
        self._owner_id = None
        # Set True by the early single-instance guard whenever this process did
        # NOT confirm sole ownership of the D-Bus name; start_service then binds
        # nothing and main() exits before the loop. Paired with
        # _bus_verify_failed to pick the exit code: a benign duplicate (another
        # owner) exits 0; a fail-closed verify fault exits non-zero + loud.
        self._duplicate_instance = False
        # Set True by the guard ONLY when it could not VERIFY ownership at all
        # (gi/Gio unavailable, or the session bus was unreachable) — an
        # environment fault, distinct from a benign duplicate. Drives main()'s
        # non-zero, loud exit so systemd's Restart=on-failure retries instead of
        # a masked exit 0. Security-only: never proceed to bind a resource unless
        # sole ownership is proven.
        self._bus_verify_failed = False
        self._hardware_tier: dict[str, Any] | None = None
        self._model_loaded: str | None = None
        # The resolved chat-model sha (used when reporting what is being served).
        self._chat_model_sha256: str = ""
        # Runtime engine-health reaction ladder: the monitor (counter) and the
        # reported finding when corruption is sustained. The finding is a REPORT
        # — the daemon no longer changes the engine behind the user's back.
        self._engine_health = None
        self._engine_health_flagged: str | None = None
        # What model tiers this box can run + whether the GPU driver makes that
        # unknowable, surfaced so the Welcomer's setup flow can offer the choice.
        self._model_offer: dict | None = None
        self._requests_handled = 0
        self._last_error: str | None = None
        # Conspicuous, queryable state set when the chat llama-server refuses to
        # start because a DECLARED capability was not honored (toolless template,
        # missing projector, tools/vision not advertised) — an integrity failure
        # distinct from the benign no-model-downloaded degrade. None when healthy
        # or when the failure was the ordinary "server didn't come up" class.
        self._model_server_integrity_failure: str | None = None
        # The chat model server is not running, and why. Distinct from the
        # integrity failure above (a declared capability the server did not
        # honor) and from the no-model-downloaded degrade: this is "the model
        # server was supposed to be up and is not". None = it is up.
        self._model_server_down: str | None = None
        self._router = None
        # The desktop bus's own conversation, made once the router exists.
        self._conversation = None
        self._llm = None
        self._tools = None
        self._matcher = None
        self._llama = None
        self._embed_llama = None  # AI-12: CPU-pinned embedding-only llama-server (RETAINED for the daemon's lifetime even if start fails — see Step 3b)
        self._embed_model_path = ""  # last-resolved embed GGUF path (informational; the path is re-resolved fresh each (re)start)
        self._mm = None  # ModelManager handle, retained so the embed watchdog can re-resolve the model path as it provisions
        self._embed_watchdog = None  # Step 10b: self-heals the embed server (the GDM-greeter cold-boot port collision)
        self._watchdog = None
        # Game-launch pause state. While paused the daemon holds NO model: both
        # llama-servers are stopped, so their video memory and their system
        # memory go back to the machine for whatever the user launched.
        #
        # Each entry is one HOLD: the identifier the caller supplied (the game
        # window's class) and the bus name of the caller that placed it. Holds
        # are a list because more than one game can be open at once — the daemon
        # resumes only when the LAST hold goes. Resuming on the first exit while
        # another game is still running would hand the accelerator back
        # mid-session, which is the failure this list prevents.
        self._paused = False
        self._pause_holds: list[dict[str, str | None]] = []
        # Bus name -> the watch id keeping an eye on it. A hold belongs to a
        # living caller: if the process that placed it disappears (the desktop
        # shell restarting or crashing mid-game is the real case), its holds are
        # released and InterGen comes back. Without this, one crash would leave
        # the assistant paused with nothing left alive to un-pause it.
        self._pause_owner_watches: dict[str, int] = {}
        # Serialises pause/resume against the watchdog threads. Both watchdogs
        # restart their server from their own thread, so without this a restart
        # decided a moment before a pause could bring a server back up behind
        # the pause and leave the machine in a state nobody asked for.
        self._pause_lock = threading.Lock()
        self._metrics = None
        self._events = None
        self._governance = None
        self._memory = None
        self._state_cache = None
        self._web_server = None
        self._health_agg = None
        self._web_thread: threading.Thread | None = None
        self._web_loop = None
        # Test/harness seam: a non-interactive review surface the dyno injects so
        # an unattended pull never blocks on the zenity approval modal. ALWAYS
        # None in production -> ask() builds the real make_review_callback below,
        # so production consent behaviour is byte-for-byte unchanged. The harness
        # (intergen.tests.client._init_direct) sets it to a deterministic
        # auto-approve closure; the command safety denylist still blocks
        # destructive dispatches regardless (proven: dd is blocked even approved).
        self._review_callback_override: Callable[..., str] | None = None
        # Launch-time TEST-REVIEW AUTOPILOT (F1 fix). When the daemon PROCESS is
        # started with INTERGEN_TEST_REVIEW_AUTOPILOT=allow|deny, wire a
        # deterministic non-interactive review surface so the unattended dbus
        # eval-harness never wedges the single-threaded loop on the GTK consent
        # modal. Security-only HARD constraints: read ONCE here, from the process
        # env at construction — there is NO D-Bus setter, so a live daemon's
        # consent posture can never be flipped over the bus (that would be a
        # consent-bypass / privilege-escalation surface). It answers the review
        # QUESTION only: it does NOT widen what is dispatchable — the command
        # safety denylist and the dispatch_policy lockdown are untouched, and a
        # privileged (pkexec) dispatch is DENIED even in allow mode (fail-closed,
        # side-effect-free unattended runs). Every verdict is glass-logged and
        # `intergen status` banners it loudly.
        self._review_autopilot: str | None = None
        _ap = os.environ.get("INTERGEN_TEST_REVIEW_AUTOPILOT", "").strip().lower()
        if _ap in ("allow", "deny"):
            self._review_autopilot = _ap
            self._review_callback_override = self._make_autopilot_review_callback(_ap)
            log.warning(
                "REVIEW AUTOPILOT ACTIVE (%s) — non-interactive test-review mode; "
                "held dispatches are auto-answered without a human. NEVER launch a "
                "production daemon with INTERGEN_TEST_REVIEW_AUTOPILOT set.", _ap)

    @staticmethod
    def _make_autopilot_review_callback(mode: str) -> Callable[..., str]:
        """Build the launch-time autopilot review closure (F1).

        mode='allow' mirrors the harness auto-approve policy: allow_once for an
        ordinary held dispatch, but DENY a privileged (needs_pkexec) one —
        allowing it would pop the OS polkit prompt (a hard block for an
        unattended run) and actually mutate the box. mode='deny' denies every
        held dispatch (exercises the deny-recovery paths). Either way the closure
        fails CLOSED: a decision object missing needs_pkexec is treated as
        privileged and denied. Every verdict is glass-logged with the tool it
        answered. The command safety denylist runs independently of this
        callback, so destructive dispatches stay blocked regardless of verdict.
        """
        def _callback(call: Any, decision: Any) -> str:
            if mode == "deny":
                verdict = "deny"
            elif getattr(decision, "needs_pkexec", True):
                verdict = "deny"  # fail-closed: privileged (or unknown shape) → deny
            else:
                verdict = "allow_once"
            try:
                glass.emit("decision", "review_autopilot", detail={
                    "mode": mode, "verdict": verdict,
                    "tool": getattr(call, "name", "?"),
                    "args": getattr(call, "arguments", None),
                })
            except Exception:  # noqa: BLE001 — logging must never break dispatch
                pass
            return verdict
        return _callback

    def ask(self, message: str) -> str:
        """Process a user message and return the response."""
        self._requests_handled += 1
        log.info("Ask: %s", message[:100])

        if self._paused:
            # Say what is actually happening rather than failing through the
            # router into a generic engine error: the model is not loaded, and
            # the reason is one the user set up on purpose.
            games = ", ".join(self._held_game_names()) or "a game"
            return json.dumps({
                "response": (
                    f"InterGen is paused while {games} is running, so no model "
                    "is loaded right now. Close the game and InterGen loads "
                    "again by itself — or change what happens at game launch in "
                    "the launch-monitor settings."),
                "source": "paused",
                "handled": False,
            })

        if self._router is None:
            return json.dumps({
                "response": "InterGen is starting up, please wait.",
                "source": "startup",
                "handled": False,
            })

        try:
            # Confirmation-UX: supply a real human-review surface for any tool
            # dispatch the provenance gate holds for review (or privileged/pkexec
            # dispatch) on the D-Bus Ask path. Without this the registry's
            # review_callback=None contract fail-closed-DENIES silently — safe,
            # but with no way for the user to approve. make_review_callback gives
            # the same Allow-once / Allow-for-conversation / Deny surface the
            # panel + TUI get (zenity primary, notify-send fallback, 1-hour
            # implicit-deny, session-detect, headless fail-closed) so consent is
            # consistent across every surface.
            if self._review_callback_override is not None:
                # Harness/dyno path: a deterministic, non-interactive review
                # surface (set by the test client) so an unattended pull does
                # not block on the modal. Never set in production.
                review_cb = self._review_callback_override
            elif eval_consent.is_armed():
                # Unattended baseline path: answer the review gate with an
                # immediate recorded DENY. Ordered after the harness override
                # (which the in-process dyno owns) and before the production
                # modal, which is what an unarmed daemon still builds below —
                # so shipped consent behavior is unchanged when disarmed.
                review_cb = eval_consent.make_review_callback()
            else:
                from intergen.review_modal import make_review_callback
                review_cb = make_review_callback(
                    source_attribution="D-Bus Ask request",
                    reasoning=f"Requested while handling: {message[:200]}",
                )
            # M1 Glass Pipeline: one turn id threads every router/decomposer/
            # memory/llm emission this turn shares. The route() below is the full
            # (non-decide_only) path, so it reaches _record — the streamed web
            # path is threaded separately in web_server.
            _gturn = glass.new_turn_id()
            with glass.turn(_gturn, "dbus"):
                glass.emit("route", "turn_start", detail={"user_msg": message})
                result = self._router.route(
                    message,
                    conversation=self._conversation,
                    review_callback=review_cb)
                # Deterministic identity guard (last mile): InterGen is the
                # assistant, never the OS. A 2B occasionally slips "I am
                # InterGenOS" on ambiguous input despite the positive-framed
                # prompt; resolve that hard constraint here, outside the LLM.
                from intergen.router import correct_identity_collision
                response_text = correct_identity_collision(result.text)
                # ANSWER->DISPATCH LINKAGE on every delivered row: what the
                # reply was actually composed from, or an explicit
                # "undeclared" when the composing route recorded nothing. An
                # uninstrumented path must be VISIBLE here, never indistinguish-
                # able from a code-owned answer.
                _link = getattr(result, "answer_linkage", None)
                if not isinstance(_link, AnswerLinkage):
                    # Only a real AnswerLinkage may speak for the answer. A
                    # foreign object in this slot is an undeclared path, not a
                    # linkage — treating it as one would put an unverified
                    # claim on the trace (and, for a non-serialisable stand-in,
                    # break the reply outright).
                    _link = None
                # M8-2 RESULT DELIVERY INVARIANT: a dispatch that succeeded but whose
                # value did not reach response_text is a NAMED, LOUD defect (never
                # silent) — the dispatched-but-discarded class, asserted per turn.
                #
                # AND NOW REPAIRED, not merely named. Naming it left the user with
                # the wrong answer and the truth in a log nobody reads. Where the
                # value is genuinely missing, the tool's own output IS the answer and
                # is carried into it (safety.carry_result_into_answer); where it
                # cannot be — a substitution the linkage cannot distinguish from a
                # summarizer answering off an authoritative live source, or an answer
                # that already states the result — the row is emitted and the text is
                # left exactly as composed.
                #
                # THIS RUNS BEFORE THE delivery/final ROW ON PURPOSE. M1 requires the
                # bytes the user received to be reconstructible from the trace alone,
                # so the row has to carry the repaired text, not the draft that was
                # replaced.
                for _tr, _reason in safety.find_unconsumed_dispatches(
                        response_text, result.tool_results, _link):
                    _carried = safety.carry_result_into_answer(
                        response_text, _tr, _reason)
                    glass.emit("delivery", "dispatch_unconsumed", detail={
                        "tool": _tr.name, "reason": _reason, "iface": "dbus",
                        "repaired": _carried is not None})
                    if _carried is not None:
                        log.warning(
                            "M8-2: dispatch %s succeeded but its result did not reach "
                            "the delivered answer (%s) — carried the result into the "
                            "answer", _tr.name, _reason)
                        response_text = _carried
                    else:
                        log.warning(
                            "M8-2: dispatch %s succeeded and the delivered answer is "
                            "wrong about it (%s) — not rewritten, see the glass row",
                            _tr.name, _reason)
                glass.emit("delivery", "final", detail={
                    "text": response_text, "source": result.source,
                    "handled": result.handled, "used_llm": result.used_llm,
                    "answer_linkage": (_link.as_detail() if _link is not None
                                       else {"kind": "undeclared"})})
                return json.dumps({
                    "response": response_text,
                    # The unsummarised original behind response_text (raw tool
                    # output). Carried so the CLI can offer `intergen last --raw`
                    # — the summariser is never the only witness of the ground
                    # truth. Empty when there is no richer raw than the answer.
                    "full_output": result.full_output,
                    "source": result.source,
                    "handled": result.handled,
                    "tool_calls": [
                        {"name": tc.name, "arguments": tc.arguments}
                        for tc in result.tool_calls
                    ],
                    # ADDITIVE (existing fields untouched): the dispatch RESULTS,
                    # so a consumer can read outcome + output instead of scraping
                    # the answer for them. Previously the payload carried the
                    # calls but not what came back, so a reply could not be
                    # checked against its own dispatch without the glass rows.
                    # `content` is the full payload the transcript already shows;
                    # call_id is the join key to the linkage above.
                    "tool_results": [
                        {"call_id": tr.call_id, "name": tr.name,
                         "success": tr.success, "executed": tr.executed,
                         "blocked": tr.blocked, "content": tr.content}
                        for tr in result.tool_results
                    ],
                    "answer_linkage": (_link.as_detail() if _link is not None
                                       else {"kind": "undeclared"}),
                    "used_llm": result.used_llm,
                    "escalated": result.escalated,
                    "escalation_offer": result.escalation_offer,
                    # The join key to the always-on glass trace (design §4.2).
                    # ``result.trace_id`` is the dev-gated tracer id — EMPTY unless
                    # --observe/INTERGEN_TRACE is on (router stamps it from the
                    # active tracer span, else ""). Glass, however, threads THIS
                    # turn's ``_gturn`` into every row it emits, so _gturn is the
                    # id a reply must carry for a consumer to join the reply back
                    # to its glass rows. Fall back to _gturn when the tracer id is
                    # absent so the join works in normal operation (it was empty
                    # before, which silently broke every live reply→glass join);
                    # keep the tracer id when --observe is on so a decisions.jsonl
                    # capture still joins on it.
                    "trace_id": result.trace_id or _gturn,
                })
        except Exception as e:
            log.error("Ask failed: %s", e)
            self._last_error = str(e)
            if self._metrics:
                self._metrics.record_error(str(e))
            return json.dumps({
                "response": f"I encountered an error: {e}",
                "source": "error",
                "handled": False,
            })

    def escalate(self, message: str) -> str:
        """Phone-a-friend: send a message to the configured frontier model after
        explicit show-before-send consent (Sentinel design plan §4).

        This is the user-invoked affordance half of decision #4 (GUI button / CLI
        subcommand): the human deliberately asked to reach their frontier model, so
        it is the GENUINE INITIAL human-authorized hop. The flow is:
          1. show-before-send consent modal (the user sees the exact outbound text +
             provider, and must click Send) — decision #6's safety basis for not
             scanning the consented hop;
          2. only on Send, escalate(user_consented=True) — NOT egress-scanned, since
             the human just reviewed it. (A derived/agentic follow-on hop would call
             escalate with the default user_consented=False and BE scanned — that is
             the router's job, never this direct affordance.)
        Fail-safe: no manager / no provider / declined consent / error all return a
        clean JSON note; nothing is sent without an explicit Send.
        """
        from intergen.consent_modal import prompt_send_consent
        from intergen.interfaces.types import Message, MessageRole

        if self._escalation is None:
            return json.dumps({
                "response": "Phone-a-friend is not available (no escalation manager).",
                "source": "escalation", "sent": False,
            })
        provider = self._escalation._primary_provider_name()
        if provider is None:
            return json.dumps({
                "response": ("No frontier model is configured. Add a provider to "
                             "~/.config/intergen/ (the human-only config) to use "
                             "phone-a-friend."),
                "source": "escalation", "sent": False,
            })
        # Show-before-send: the human must SEE the outbound content and click Send.
        if not prompt_send_consent(message, provider,
                                   reason="you asked to reach your frontier model"):
            return json.dumps({
                "response": "Cancelled — nothing was sent to the frontier model.",
                "source": "escalation", "sent": False,
            })
        try:
            messages = [Message(role=MessageRole.USER, content=message)]
            result = self._escalation.escalate(
                messages, reason="user-invoked phone-a-friend", user_consented=True,
            )
            return json.dumps({
                "response": result.text,
                "source": f"frontier:{provider}",
                "sent": True,
            })
        except Exception as e:  # noqa: BLE001 — never crash the daemon on escalation
            log.error("Escalate failed: %s", type(e).__name__)
            return json.dumps({
                "response": f"Escalation failed: {type(e).__name__}",
                "source": "error", "sent": False,
            })

    def _model_server_down_now(self) -> "str | None":
        """Why the chat model cannot answer right now, or None when it can.

        Reads the CURRENT state rather than only the startup record, so a server
        that died after a healthy start is reported too — the startup record
        alone would say "up" for the rest of the session. Deliberately silent
        while InterGen is paused for a game: the server is down on purpose there
        and calling that a failure would be a lie in the other direction.
        """
        # Read every field through getattr: status() is called on daemons that
        # were only partially constructed (the status tests build one with
        # __new__ and set the handful of fields they care about, and start_service
        # can fail part-way through), and a status read that raises turns a
        # degraded daemon into an unqueryable one — the opposite of what this
        # field is for. The router makes the same choice at its own lockdown gate.
        if getattr(self, "_paused", False):
            return None
        recorded = getattr(self, "_model_server_down", None)
        llama = getattr(self, "_llama", None)
        if llama is None:
            return recorded
        try:
            if llama.is_running():
                return None
        except Exception:  # noqa: BLE001 — a status read never raises
            pass
        return (recorded
                or f"the chat model server is not running: "
                   f"{getattr(llama, 'last_error', '') or 'no reason recorded'}")

    def status(self) -> str:
        """Return JSON-encoded status."""
        status = {
            "running": self._running,
            "tier": self._hardware_tier,
            "model": self._model_loaded,
            "requests_handled": self._requests_handled,
            "last_error": self._last_error,
            # Distinct from last_error / the no-model case: set only when the
            # chat server refused to start over a declared-but-unhonored
            # capability (see _model_server_integrity_failure). None = healthy.
            "model_server_integrity_failure": self._model_server_integrity_failure,
            # The chat model server is down, and why — the failure class and the
            # server's own last words. None when it is up. A unit that reports
            # itself active while nothing can generate a reply is the state this
            # field exists to end.
            "model_server_down": self._model_server_down_now(),
            "version": "0.1.0",
            # Game-launch pause: True while the model servers are deliberately
            # stopped so a running game has the machine's memory. Reported as a
            # first-class field because "no model loaded" and "model paused on
            # purpose" must never look the same to anything reading status.
            "paused": self._paused,
            "paused_for": self._held_game_names(),
            # F1: loud-banner the launch-time test-review autopilot whenever it
            # is active (None in production) so a non-interactive consent posture
            # is never silent — the same never-silent discipline as glass below.
            "review_autopilot": self._review_autopilot,
            # Eval-consent deny-and-record posture. Reported so an unattended
            # harness can VERIFY it is armed before grading a run (and fail
            # closed if it is not) instead of discovering it by wedging on a
            # modal. Disarmed in production, where armed is False and the
            # roll-up is empty.
            "eval_consent": eval_consent.observation_summary(),
            # M1 loud-kill-switch rider: a disabled glass is never silent.
            "glass": glass.glass_enabled(),
            "components": {
                "hardware_detector": self._hardware_tier is not None,
                "model_manager": self._model_loaded is not None,
                "llama_server": self._llama is not None and self._llama.is_running(),
                "router": self._router is not None,
                "semantic_matcher": self._matcher is not None,
                "tools": self._tools is not None,
                "memory": self._memory is not None,
                "watchdog": self._watchdog is not None and self._watchdog.is_running,
            },
        }
        # PI-Z26: surface the chat server's serving reality (backend + requested/
        # actual GPU offload) so a silent CPU fallback is queryable, not inferred.
        if self._llama is not None:
            status["offload"] = self._llama.offload_report()
        # Sustained runtime corruption, when the health monitor has reported it.
        # A REPORT, not an action — the engine is untouched (see
        # _on_engine_health_degraded), so this field is the whole user surface
        # for the condition and has to be queryable.
        if getattr(self, "_engine_health_flagged", None):
            status.setdefault("offload", {})["health_flagged"] = (
                self._engine_health_flagged)
        # What this box can run, and whether its GPU driver hides that. The
        # Welcomer's InterGen setup flow reads this to offer the model choice.
        if getattr(self, "_model_offer", None):
            status["model_offer"] = self._model_offer
        if self._metrics:
            status["metrics"] = self._metrics.get_status()
        if self._router:
            # Bound so the conversation-scoped numbers (history length, session
            # memory) are this conversation's rather than "none was named".
            with self._router.bind_conversation(self._conversation):
                router_status = self._router.get_status()
            status["router_status"] = router_status
            # M2b (design D5): lift the session-memory INDEX's serving reality to
            # a FIRST-CLASS top-level field — the same discipline as `offload`
            # above — so the user surface (`intergen status`) renders it LOUD on
            # degradation instead of digging into the router_status blob. This is
            # the session-recall index, distinct from components.memory (the
            # persistent Fact store). Single source of truth: the router owns the
            # state; this only surfaces it.
            status["memory_index"] = {
                "enabled": bool(router_status.get("memory_enabled", False)),
                "degraded": bool(router_status.get("memory_degraded", False)),
                # Whether the embedder has been OBSERVED to answer. "enabled"
                # only ever meant an index object was wired.
                "verified": bool(router_status.get("memory_verified", False)),
            }
        return json.dumps(status, indent=2)

    def _on_engine_health_degraded(self) -> None:
        """Sustained runtime corruption (runs on the monitor's background thread).

        REPORT, do not re-decide. Three flagged generations in a five-generation
        window is a real signal and it is made loud — a journal line, a glass
        event, and a queryable Status field — but the engine is left exactly
        where the user put it.

        This handler previously restarted the engine, re-ran a GPU audition, and
        could move the served model onto the CPU permanently. That whole
        mechanism was removed (decided 2026-07-31): the machine does not get to
        overrule the user's choice of model and accelerator on the strength of a
        heuristic. A user whose output looks wrong now has the evidence to act on
        and the controls to act with (``llama_server.gpu_layers`` pins the
        offload; ``intergen setup`` re-offers the model choice).
        """
        if self._engine_health_flagged:
            # Already reported for this engine; do not re-emit on every window.
            return
        # The count that FIRED this, not the count in the window now. The
        # monitor clears its window on trigger and this handler runs afterwards
        # on another thread, so asking for the live window printed "(0 in the
        # recent five-generation window)" on a real user's machine — an alarm
        # whose own number said nothing was wrong.
        snap = (self._engine_health.last_trigger()
                if self._engine_health else None)
        if snap is None or snap.flagged < snap.threshold:
            # Reached without a trigger at or above the threshold. Say so at
            # WARNING and raise no alarm: a loud line that its own number
            # contradicts costs the reader more than silence.
            log.warning(
                "engine-health escalation reached the daemon with no trigger "
                "at or above its threshold (%s) — no alarm raised", snap)
            return
        msg = (f"{snap.flagged} of the last {snap.window} served generations "
               f"were flagged (threshold {snap.threshold} of {snap.window})")
        self._engine_health_flagged = msg
        log.error("ENGINE-HEALTH: sustained semantic-corruption flags — %s. The "
                  "served output is being flagged as incoherent. The engine has "
                  "NOT been changed. If this box's GPU is the suspect, pin "
                  "llama_server.gpu_layers to 0 to serve on the CPU, or re-run "
                  "'intergen setup' to choose a different model.", msg)
        glass.emit("engine", "health_degraded", iface="daemon",
                   detail={"flagged": snap.flagged,
                           "threshold": snap.threshold,
                           "window": snap.window,
                           "action": "reported"})

    def _on_watchdog_giveup(self, msg: str) -> None:
        """Watchdog exhausted its restart budget for the chat server.

        Record the error, and if the give-up was a declared-capability
        INTEGRITY failure (a RUNTIME degradation — corrupted projector, chat
        template drift, tools/vision no longer advertised after a restart),
        surface it via the conspicuous model_server_integrity_failure status
        too, not only _last_error. A runtime capability degradation must be as
        visible as a boot-time integrity failure; without this the watchdog
        give-up was quieter than the initial-start path that classifies the
        same StartFailure reason-codes.
        """
        self._last_error = msg
        llama = self._llama
        if llama is not None and llama.last_failure.is_integrity:
            self._model_server_integrity_failure = (
                f"{llama.last_failure.name}: {llama.last_error} "
                "(watchdog restart give-up)"
            )

    def _resolve_embed_model_path(self) -> str:
        """Resolve the embedding GGUF path FRESH on each call — env override
        first, else the ModelManager's on-disk copy. Returns "" when the model
        is not yet present on disk.

        Re-resolving (rather than caching the startup value) is what makes the
        embed watchdog provisioning-aware: on first boot the GGUF lands minutes
        after the daemon starts, so a frozen path would stay empty forever. This
        lets the watchdog hold quietly until the model appears and then bring the
        embedder up — closing the provisioning-window case without depending on
        the one-time setup-flow daemon restart.
        """
        import os
        from pathlib import Path as _Path
        override = os.environ.get("INTERGEN_EMBED_MODEL_PATH")
        if override:
            return override if _Path(override).exists() else ""
        if self._mm is None:
            return ""
        try:
            info = self._mm.get_embedding_model()
            path = info.local_path or ""
        except Exception as e:  # noqa: BLE001 — lookup failure degrades to "absent"
            log.warning("Embedding model lookup failed: %s", e)
            return ""
        return path if path and _Path(path).exists() else ""

    def _start_embed_server(self) -> bool:
        """(Re)start the embedding-only llama-server on THIS retained manager.

        Used for the initial start AND as the embed watchdog's restart_action.
        Re-resolves the model path each call (so a model that provisions after
        startup is picked up) and calls start() fresh so the pre-launch
        bind-ownership check re-runs: while the GDM greeter session still holds
        the port this fails cleanly, and once the greeter frees it the bind
        succeeds. NEVER reassigns self._embed_llama — the matcher/router hold its
        .embed bound method, so the same instance coming up live is what makes
        Layer-2 self-heal. Returns False (without launching) when the model is
        not yet present; the watchdog's precondition holds in that window so this
        does not burn the restart budget.
        """
        if self._embed_llama is None:
            return False
        model_path = self._resolve_embed_model_path()
        if not model_path:
            return False
        self._embed_model_path = model_path
        return self._embed_llama.start(
            model_path,
            port=self._config.get("llama_server.embedding_port", 8081),
            context_size=self._config.get("models.embedding_context", 2048),
            gpu_layers=0,        # CPU-pinned
            parallel=1,
            jinja=False,
            embedding=True,
        )

    def _start_embed_server_and_recover_intents(self) -> bool:
        """Embed-watchdog restart_action: bring the embed server up AND recover
        the matcher's intent corpus, so the self-heal is end-to-end.

        Starting the server again is necessary but NOT sufficient: when the
        embedder was down at daemon startup (the greeter cold-boot collision, or
        the provisioning window), register_all_intents could not embed the nine
        embedding-based tool-intents and held them PENDING. The live embed
        callable the matcher holds is useless without those vectors. So on a
        successful (re)start we ask the matcher to embed and register its pending
        intents — restoring the full eighteen with no daemon restart.

        Runs on the watchdog thread. refresh_pending_intents is itself
        thread-safe, retry-safe, and best-effort, but we still guard here so a
        recovery hiccup never escapes into the watchdog loop.
        """
        ok = self._start_embed_server()
        if ok and self._matcher is not None:
            try:
                n = self._matcher.refresh_pending_intents()
                if n:
                    log.info(
                        "Embedding self-heal: re-registered %d pending embedding "
                        "intent(s) after the embedder recovered; matcher now %d "
                        "intents", n, self._matcher.get_intent_count())
            except Exception as e:
                log.warning("Embedding intent recovery after restart failed "
                            "(retried on a subsequent recovery): %s", e)
        return ok

    # ---- game-launch pause -------------------------------------------------
    #
    # Decided 2026-08-04: when a game starts while InterGen is resident, the
    # default is that InterGen gets out of the way — it stops its model servers
    # so the accelerator's memory and the system memory they held go back to the
    # machine, and it loads them again when the game exits. Letting the kernel
    # evict the weights instead is worse in practice: the migration cost is paid
    # during play, as stutter, and eviction returns no system memory at all.
    #
    # The user's choice between pausing, staying available, and being asked is
    # declared once in the desktop settings and applied by the caller. What
    # lives here is only the mechanism: this daemon pauses when it is asked to
    # and resumes when the last caller says the reason is over. Keeping the
    # policy out of the daemon is deliberate — on a machine with more than one
    # accelerator, where the game and InterGen never share a card, staying
    # available is the honest answer, and that judgement belongs with the user's
    # declared setting rather than being second-guessed here.

    @staticmethod
    def _clean_game_name(game: str) -> str:
        """Normalise a caller-supplied game identifier for logging and display.

        Bounded on purpose: the value reaches a log line and a status field, so
        an over-long or line-broken identifier from a misbehaving caller is
        trimmed rather than carried. An empty identifier is still valid — it
        means "something is running" — and is reported as such.
        """
        if not isinstance(game, str):
            return "a game"
        cleaned = " ".join(game.split())[:128].strip()
        return cleaned or "a game"

    def _held_game_names(self) -> list[str]:
        """The identifiers currently holding the pause, oldest first."""
        return [str(h["game"]) for h in self._pause_holds]

    def _stop_servers_for_pause(self) -> dict[str, bool]:
        """Stop both model servers. Caller holds _pause_lock and has set _paused.

        Setting _paused before this runs is what makes a watchdog tick landing
        mid-stop read the pause and hold, instead of treating the stop as a
        fault worth restarting.
        """
        stopped = {"chat": False, "embedding": False}
        if self._llama is not None:
            try:
                self._llama.stop()
                stopped["chat"] = True
            except Exception as e:  # noqa: BLE001 — a pause never fails loudly
                log.warning("Pause: stopping the chat server failed: %s", e)
        if self._embed_llama is not None:
            try:
                self._embed_llama.stop()
                stopped["embedding"] = True
            except Exception as e:  # noqa: BLE001
                log.warning("Pause: stopping the embedding server failed: %s", e)
        return stopped

    def _start_servers_after_pause(self) -> dict[str, bool]:
        """Load both model servers again. Caller holds _pause_lock.

        The chat server relaunches from the configuration its last successful
        start used, deliberately NOT through restart(): a pause is not a fault,
        so it must not spend the manager's restart budget. _paused is cleared by
        the caller before this runs, so the watchdogs are the recovery path
        again if a relaunch does not take.
        """
        started = {"chat": False, "embedding": False}
        if self._llama is not None:
            try:
                started["chat"] = self._llama.start_saved_config()
            except Exception as e:  # noqa: BLE001
                log.warning("Resume: the chat server did not restart: %s", e)
        if self._embed_llama is not None:
            try:
                started["embedding"] = (
                    self._start_embed_server_and_recover_intents())
            except Exception as e:  # noqa: BLE001
                log.warning("Resume: the embedding server did not restart: %s", e)
        return started

    def pause_for_game(self, game: str, owner: str | None = None) -> str:
        """Stop the model servers so a launched game has the machine to itself.

        Returns JSON: paused, the games currently holding the pause, and which
        servers this call actually stopped.

        Repeat calls are additive, not idempotent-by-name: every call appends a
        hold, every resume releases ONE. That is a deliberate reference count —
        two windows of the same game produce two calls, and the first window to
        close must not resume InterGen while the second is still up.

        owner is the bus name of the caller when the call arrived over D-Bus.
        The hold is tied to it, so a caller that dies still holding a pause
        cannot leave InterGen paused with nothing alive to release it.
        """
        name = self._clean_game_name(game)
        with self._pause_lock:
            self._pause_holds.append({"game": name, "owner": owner})
            already = self._paused
            self._paused = True
            stopped = {"chat": False, "embedding": False}
            if not already:
                stopped = self._stop_servers_for_pause()
                log.info("Paused for %s — model servers stopped (chat=%s, "
                         "embedding=%s); memory returned to the machine",
                         name, stopped["chat"], stopped["embedding"])
                glass.emit("engine", "paused_for_game", iface="daemon",
                           detail={"game": name, "stopped": stopped})
            else:
                log.info("Already paused; %s also now holds the pause (%d "
                         "total)", name, len(self._pause_holds))
            held = self._held_game_names()
        # Outside the lock: watching a bus name calls into GLib, and nothing
        # about it needs the pause state held.
        self._watch_pause_owner(owner)
        return json.dumps({"paused": True, "games": held, "stopped": stopped})

    def resume_after_game(self, game: str) -> str:
        """Release one pause hold; load the models again once the last one goes.

        Returns JSON: whether the daemon is still paused, which games still hold
        it, and — when this call actually resumed — whether each server came
        back. A resume naming something that holds no pause is reported plainly
        rather than silently ignored, because a caller whose pause never arrived
        is a state worth seeing.
        """
        name = self._clean_game_name(game)
        with self._pause_lock:
            match = next((h for h in self._pause_holds if h["game"] == name),
                         None)
            if match is not None:
                self._pause_holds.remove(match)
            elif self._pause_holds:
                # The identifier matches no hold. Release the OLDEST one rather
                # than leaving InterGen paused forever because a caller renamed
                # a window between the two edges.
                dropped = self._pause_holds.pop(0)
                log.warning("Resume named %r, which holds no pause; released "
                            "the oldest hold (%r) instead so a renamed window "
                            "cannot strand the pause", name, dropped["game"])
            else:
                log.info("Resume named %r but nothing holds the pause — "
                         "nothing to do", name)
                return json.dumps({
                    "paused": self._paused, "games": [], "started": {},
                    "detail": "no pause was held",
                })
            if self._pause_holds:
                log.info("%s exited; %d game(s) still hold the pause — staying "
                         "paused", name, len(self._pause_holds))
                return json.dumps({
                    "paused": True, "games": self._held_game_names(),
                    "started": {},
                })
            self._paused = False
            started = self._start_servers_after_pause()
            if not started["chat"] and self._llama is not None:
                # Say so plainly and leave it to the watchdog, which is
                # unblocked again now that the pause is cleared.
                log.warning("Resume after %s: the chat server did not come back "
                            "on this attempt (%s) — the watchdog will retry",
                            name, self._llama.last_error)
            else:
                log.info("Resumed after %s — model servers loading again "
                         "(chat=%s, embedding=%s)", name, started["chat"],
                         started["embedding"])
            glass.emit("engine", "resumed_after_game", iface="daemon",
                       detail={"game": name, "started": started})
        self._unwatch_orphaned_pause_owners()
        return json.dumps({"paused": False, "games": [], "started": started})

    def _watch_pause_owner(self, owner: str | None) -> None:
        """Watch a hold-placing bus name so its death releases its holds.

        No-op without a bus (the daemon can run un-exported) or for a caller
        that supplied no bus name. Watching is best-effort: if it cannot be set
        up the pause still works exactly as asked, it simply loses the
        caller-died safety net, and that is said out loud rather than assumed.
        """
        if not owner or self._bus is None or owner in self._pause_owner_watches:
            return
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
            watch_id = Gio.bus_watch_name_on_connection(
                self._bus, owner, Gio.BusNameWatcherFlags.NONE,
                None,  # name appeared — it already has
                lambda _c, _n: self._on_pause_owner_vanished(owner),
            )
            self._pause_owner_watches[owner] = watch_id
        except Exception as e:  # noqa: BLE001
            log.warning("Could not watch the pause holder %s (%s) — the pause "
                        "itself is in force, but if that caller dies while "
                        "holding it, InterGen will stay paused until something "
                        "resumes it", owner, e)

    def _on_pause_owner_vanished(self, owner: str) -> None:
        """A caller holding a pause disappeared — release everything it held.

        The real case is the desktop shell restarting or crashing while a game
        is open. Its holds can never be released by it again, so they are
        released here and, if they were the last ones, InterGen loads again.
        """
        with self._pause_lock:
            gone = [h for h in self._pause_holds if h["owner"] == owner]
            if not gone:
                self._drop_owner_watch(owner)
                return
            self._pause_holds = [h for h in self._pause_holds
                                 if h["owner"] != owner]
            log.warning("The caller holding %d game pause(s) (%s) disappeared "
                        "— releasing its hold(s): %s", len(gone), owner,
                        ", ".join(str(h["game"]) for h in gone))
            self._drop_owner_watch(owner)
            if self._pause_holds:
                return
            self._paused = False
            started = self._start_servers_after_pause()
            log.info("Resumed after the pause holder disappeared (chat=%s, "
                     "embedding=%s)", started["chat"], started["embedding"])
            glass.emit("engine", "resumed_after_game", iface="daemon",
                       detail={"reason": "pause holder vanished",
                               "owner": owner, "started": started})

    def _drop_owner_watch(self, owner: str) -> None:
        """Stop watching a bus name we no longer hold anything for."""
        watch_id = self._pause_owner_watches.pop(owner, None)
        if not watch_id:
            return
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
            Gio.bus_unwatch_name(watch_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not drop the bus-name watch for %s: %s", owner, e)

    def _unwatch_orphaned_pause_owners(self) -> None:
        """Drop watches for bus names that no longer hold any pause."""
        with self._pause_lock:
            still_held = {h["owner"] for h in self._pause_holds}
            orphans = [o for o in self._pause_owner_watches
                       if o not in still_held]
        for owner in orphans:
            self._drop_owner_watch(owner)

    def _restart_chat_server(self) -> bool:
        """Chat-watchdog restart action, refusing to act while paused.

        The watchdog decides to restart on one thread while a pause can arrive
        on another. Taking the same lock and re-reading the pause here means a
        restart decided a moment before a pause cannot bring the chat server
        back up behind that pause. Refusing counts as an unsuccessful restart,
        which is correct: the watchdog's precondition holds it quietly from the
        next tick on, and no restart budget is spent.
        """
        with self._pause_lock:
            if self._paused:
                log.info("Watchdog restart suppressed — InterGen is paused for "
                         "%s", ", ".join(self._held_game_names()) or "a game")
                return False
            if self._llama is None:
                return False
            return self._llama.restart()

    def _restart_embed_server_watchdog(self) -> bool:
        """Embed-watchdog restart action, refusing to act while paused.

        Same reasoning as _restart_chat_server: the embedding server is stopped
        by a pause too (it holds system memory and CPU), so its watchdog must
        not resurrect it behind the pause from its own thread.
        """
        with self._pause_lock:
            if self._paused:
                return False
        return self._start_embed_server_and_recover_intents()

    def get_tier(self) -> str:
        """Return hardware tier info as JSON."""
        if self._hardware_tier is None:
            return json.dumps({"error": "Hardware not detected yet"})
        return json.dumps(self._hardware_tier, indent=2)

    def reset_conversation(self) -> str:
        """PI-Z29 (c): end the current conversation and reset the router's
        per-conversation state — trust posture, ingress watermark, conversation
        history, ALL offer slots, and the preventive-grounding window (TTL + topic
        terms). Restores dbus-mode parity with the direct-mode conversation reset:
        a caller invokes this BETWEEN conversations so a prior conversation's staged
        offer cannot leak into the next (the cross-conversation over-steer root-caused
        in PI-Z29), and a user can call it to clear session residue. The test-harness
        call site + the user-callable residue clear ride a following dispatch.
        Returns a JSON status."""
        if self._router is None:
            return json.dumps({"reset": False, "reason": "router not started"})
        # The desktop bus conversation, and only it: every browser conversation
        # belongs to whoever is typing in that tab and is not this caller's to
        # end.
        self._router.reset_conversation_state(self._conversation)
        log.info("Conversation state reset (D-Bus ResetConversation)")
        return json.dumps({"reset": True})

    def start_service(self) -> None:
        """Initialize subsystems and start serving.

        Startup order:
          1. Detect hardware tier
          2. Download/verify model if needed
          3. Start llama-server
          4. Initialize semantic matcher (future)
          5. Register tools (future)
          6. Connect MCP servers (future)
          7. Export D-Bus interface
          8. Signal ready
        """
        log.info("InterGen daemon starting...")

        # M1: the glass pipeline is ALWAYS ON; a disabled glass must be LOUD,
        # never silent (operator rider) — this banner + the `glass` field in
        # status(). INTERGEN_GLASS=0 is legitimate user control, surfaced loudly.
        if glass.glass_enabled():
            log.info("Glass pipeline ON — full-fidelity turn trace at "
                     "$XDG_STATE_HOME/intergen/glass.jsonl")
        else:
            log.warning("################################################")
            log.warning("# GLASS PIPELINE DISABLED (INTERGEN_GLASS=0):   ")
            log.warning("# turn tracing is OFF — incidents will NOT be   ")
            log.warning("# reconstructible from trace. User-control       ")
            log.warning("# override, surfaced loudly by design.           ")
            log.warning("################################################")
        # NB: start_service has a function-local `import os` further down, which
        # makes `os` a local for this whole scope — so DON'T reference os here
        # (the boot turn id already identifies this process's startup).
        glass.emit("warmup", "daemon_start", turn_id=self._boot_turn,
                   iface="daemon", detail={"boot_turn": self._boot_turn})

        # Load configuration
        from intergen.config import Config
        self._config = Config()

        # Step 0: Single-instance guard (closes the 8089 EADDRINUSE dual-launch).
        # The daemon can be started TWICE — once by the systemd user unit
        # (WantedBy=default.target) and once by D-Bus activation — and the two
        # race the 127.0.0.1:8089 web-server bind, crashing one panel web thread
        # (swallowed) while the duplicate daemon lingers. Claim the D-Bus name
        # FIRST, with DO_NOT_QUEUE, BEFORE binding any port: if another instance
        # already owns it, flag a clean exit and do NOT initialize anything
        # (no port bind, no llama-server). The surviving owner serves normally.
        if not self._claim_bus_name():
            # The guard already logged the specific reason (duplicate owner, or
            # a fail-closed verify fault). Bind NOTHING — no hardware probe, no
            # model server, no port. main() reads _bus_verify_failed to choose
            # the exit code.
            self._duplicate_instance = True
            return

        # Step 1: Hardware detection
        try:
            from intergen.hardware import HardwareDetector
            detector = HardwareDetector()
            tier = detector.detect()
            self._hardware_tier = {
                "level": tier.tier.value,
                "ram_gb": tier.ram_gb,
                "gpu_vendor": tier.gpu_vendor,
                "gpu_model": tier.gpu_model,
                "recommended_model": tier.recommended_model,
                "recommended_quant": tier.recommended_quant,
                "estimated_model_size_gb": tier.estimated_model_size_gb,
            }
            log.info("Hardware: Tier %d (%.1f GB RAM, %s)",
                     tier.tier.value, tier.ram_gb, tier.gpu_vendor or "no GPU")
            # What this box can run, plus whether its GPU driver makes that
            # unreadable. Surfaced in Status so the Welcomer's InterGen setup
            # flow can offer the model choice without re-detecting hardware,
            # and so "why am I being offered only the 2B" has a queryable
            # answer instead of being inferred.
            try:
                from intergen import model_choice
                from intergen.hardware import HardwareDetector as _HD
                _offer = model_choice.build_offer(
                    is_discrete=_HD()._is_discrete_capable(tier.gpu_vendor,
                                                           tier.gpu_vram_mb),
                    vram_mb=tier.gpu_vram_mb,
                )
                self._model_offer = _offer.to_status()
                if _offer.advisory:
                    log.warning(
                        "GPU driver advisory: an NVIDIA card is present but is "
                        "running %s, which does not report video memory — the "
                        "larger models cannot be offered until NVIDIA's drivers "
                        "are installed.", _offer.driver_state.driver or "an "
                        "open-source driver")
            except Exception as e:
                log.warning("Could not build the model offer: %s", e)
            # DISPATCH LOCKDOWN — MODEL-FIRST resolution (operator framework,
            # 2026-07-11: tiering is DATA-DECIDED). Resolve the MODEL first via the
            # ONE shared path (model_manager.resolve_for_detected — the detector's
            # recommendation with the unpinned->pinned cap, IDENTICAL to what
            # onboarding downloaded), then DERIVE the dispatch lane FROM that
            # resolved model. The lane can never disagree with the model, and the
            # daemon loads exactly the model onboarding installed: an integrated-
            # GPU Tier-2 box (the detector recommends the 2B for latency) runs the
            # LOCKED 2B floor, NOT the native 9B lane it cannot serve and never
            # downloaded (the ge9b-01 engine-never-starts defect). A dGPU 9B box
            # resolves the 9B + native. A 35B-capable box resolves the 35B — the
            # shipped manifest pins it, so the unpinned->pinned cap does not fire
            # — and runs it on the LOCKED 2B floor, because no 35B logic lane
            # ships and resolve_dispatch_for_model floors a candidate that has
            # none rather than walking down. Measured and pinned in
            # intergen/tests/test_tier3_dispatch_posture.py. Hardware detection
            # above is UNCHANGED.
            from intergen.dispatch_policy import (
                resolve_dispatch_for_model, FLOOR_TIER)
            from intergen.model_manager import ModelManager
            mm = ModelManager()
            self._mm = mm  # reused by Step 2 (model load) + the embed watchdog
            resolved_model = mm.resolve_for_detected(tier)
            self._resolved_model = resolved_model
            _model_tier = (resolved_model.tier
                           if resolved_model is not None else FLOOR_TIER)
            resolved = resolve_dispatch_for_model(
                _model_tier, detected_tier=tier.tier,
                override_tier=self._parse_dispatch_override())
            self._dispatch_resolved = resolved
            log.info(
                "Dispatch resolved: model=%s, effective Tier %d, mode=%s%s",
                resolved_model.name if resolved_model is not None else "none",
                resolved.tier.value, resolved.dispatch_mode.value,
                " (fell back to the 2B floor — no shipped logic lane for the "
                "resolved model)" if resolved.fell_back_to_floor else "")
        except Exception as e:
            self._last_error = f"Hardware detection failed: {e}"
            log.error(self._last_error)
            # Fail-closed: if detection/resolution raised, run the locked 2B
            # floor rather than leaving the dispatch posture undefined. The model
            # is left unresolved here; Step 2 fail-closes it to the floor 2B.
            from intergen.dispatch_policy import (
                FLOOR_TIER, ResolvedDispatch, DispatchMode)
            self._resolved_model = None
            self._dispatch_resolved = ResolvedDispatch(
                tier=FLOOR_TIER, dispatch_mode=DispatchMode.LOCKED_DOWN,
                detected_tier=FLOOR_TIER, override_tier=None,
                fell_back_to_floor=False)

        # Step 2: Model manager — check if model is downloaded
        # Environment override: INTERGEN_MODEL_PATH forces a specific model
        # (useful for testing with a different model than tier-recommended).
        # T0-4-D: closes I-016 adjacent — the env override path MUST pass
        # ModelManager.verify_arbitrary_path() which gates against the
        # package-shipped pin manifest. The env-var becomes a "select a
        # different PINNED model" override, not an "arbitrary path"
        # override. Any path whose filename has no pin entry OR whose
        # SHA256 mismatches the pin is refused.
        import os
        from pathlib import Path as _Path
        from intergen.model_manager import ModelManager
        mm = getattr(self, "_mm", None) or ModelManager()
        self._mm = mm  # retained so the provisioning-aware embed watchdog can re-resolve the model path
        model_path = os.environ.get("INTERGEN_MODEL_PATH")
        if model_path:
            path_obj = _Path(model_path)
            if not mm.verify_arbitrary_path(path_obj):
                log.error(
                    "INTERGEN_MODEL_PATH=%s refused by pin verification "
                    "(closes audit I-016 adjacent). Falling back to "
                    "tier-recommended model selection.",
                    model_path,
                )
                model_path = None
            else:
                self._model_loaded = path_obj.stem
                log.info("Model override (pin-verified): %s", model_path)

        # Capability descriptor for the chat-server launch, sourced from the
        # resolved ModelInfo (the SIGNED manifest via _apply_manifest). The
        # env-override path above carries no manifest entry, so these stay at
        # their text-only / no-vision defaults; tools are ALWAYS expected of the
        # chat server, so expect_tools is unconditional at the start() call.
        model_cacheable = False
        model_has_vision = False
        model_mmproj_path: str | None = None

        if not model_path:
            try:
                # The EFFECTIVE model is the one resolved in Step 1 through the
                # shared data-decided path (model_manager.resolve_for_detected):
                # the detector's recommendation with the unpinned->pinned cap,
                # IDENTICAL to what onboarding downloaded. The dispatch lane was
                # DERIVED FROM this model (Step 1), so model and posture cannot
                # drift, and the daemon no longer re-derives a DIFFERENT model from
                # a bare tier (the ge9b-01 iGPU-Tier-2 + PI-Z13 Zephyrus dead-ends).
                # If Step 1's detection/resolution failed, fail closed to the
                # verified-everywhere 2B floor.
                from intergen.dispatch_policy import FLOOR_TIER as _FLOOR
                model_info = getattr(self, "_resolved_model", None)
                if model_info is None:
                    model_info = mm.get_model_for_tier(_FLOOR)
                if model_info and model_info.downloaded:
                    # Load-time pin verification (defense-in-depth): the download
                    # was pin-verified, but re-verify the on-disk bytes before
                    # loading so an at-rest-tampered model is refused — same gate
                    # the env-override path already applies. The model feeds the
                    # LLM AND the Sentinel scanner, so a swapped file is HG-grade.
                    candidate = model_info.local_path
                    if candidate and mm.verify_arbitrary_path(_Path(candidate)):
                        model_path = candidate
                        self._model_loaded = model_info.name
                        # The signed-manifest sha256 of the weights being served,
                        # kept so what is loaded can be reported exactly.
                        self._chat_model_sha256 = model_info.sha256 or ""
                        # Carry the SIGNED-manifest capability descriptor into
                        # the launch: cacheable → --cache-reuse, has_vision +
                        # the disk-derived projector → --mmproj + the launch-time
                        # vision requirement.
                        model_cacheable = model_info.cacheable
                        model_has_vision = model_info.has_vision
                        model_mmproj_path = model_info.mmproj_local_path
                        log.info("Model ready (pin-verified): %s at %s",
                                 model_info.name, model_path)
                        glass.emit("warmup", "model_identity",
                                   turn_id=self._boot_turn, iface="daemon",
                                   detail={"model": model_info.name,
                                           "path": model_path, "pin_verified": True})
                    else:
                        log.error(
                            "Tier model %s at %s failed load-time pin "
                            "verification — refusing to load (possible tamper).",
                            model_info.name, candidate,
                        )
                else:
                    _mn = model_info.name if model_info is not None else "unknown"
                    log.warning(
                        "No model downloaded for the resolved model %s — engine "
                        "will not start until onboarding provisions it", _mn)
            except Exception as e:
                log.warning("Model manager init failed: %s", e)

        # Step 3: Start llama-server
        if model_path:
            try:
                from intergen.llama_manager import LlamaManager
                self._llama = LlamaManager()
                # Resolve the EFFECTIVE gpu_layers before launch. The contract
                # is simple and is the user's to set: an explicit integer in
                # config is honoured verbatim (decided 2026-07-31), and the
                # shipped "auto" default takes the MEASURED OFFLOAD PLAN —
                # whether the resolved model fits the detected video memory
                # (decided 2026-08-24). The tier chooses which model is served;
                # it no longer decides whether the card is used to serve it.
                # There is no audition, no probe and no speed threshold.
                from intergen.llama_manager import resolve_gpu_layers
                from intergen.serving_device import (select_serving_device,
                                                     select_serving_engine)
                _hw = self._hardware_tier or {}
                _gpu_vendor = _hw.get("gpu_vendor")
                _vulkan_present = bool(_gpu_vendor) and _gpu_vendor != "software"
                # Serving-ENGINE resolution: an explicit llama_server.engine
                # string in config is an operator pin (supreme, the same
                # user-control contract as gpu_layers); "auto" consults the
                # declared per-vendor preference table over the engine builds
                # actually present. The chosen binary both ENUMERATES devices
                # (below) and LAUNCHES — device names are backend-local, so
                # the two must be the same binary.
                _cfg_engine = self._config.get("llama_server.engine", "auto")
                _engine, _server_path = select_serving_engine(
                    vendor=_gpu_vendor if isinstance(_gpu_vendor, str) else None,
                    engine_pin=_cfg_engine if isinstance(_cfg_engine, str) else None)
                # Serving-device resolution (multi-GPU boxes): an explicit
                # llama_server.device string in config is an operator pin
                # (supreme, the same user-control contract as gpu_layers);
                # "auto" asks the selector for the most-capable discrete card's
                # ggml name so the serving model takes ONE card and the other
                # stays free (judge/eval co-residency), preferring a card that
                # is not driving a display. None = no pin (single-GPU boxes,
                # or selection unavailable).
                _cfg_device = self._config.get("llama_server.device", "auto")
                if isinstance(_cfg_device, str) and _cfg_device not in ("auto", ""):
                    _device = _cfg_device
                elif _vulkan_present:
                    _device = select_serving_device(server=_server_path)
                else:
                    _device = None
                _cfg_gpu_layers = self._config.get("llama_server.gpu_layers", "auto")
                _tier_level = _hw.get("level") if isinstance(_hw.get("level"), int) else None
                # The fit measurement: the card's detected memory, the model's
                # and projector's sizes from the signed manifest, and the layer
                # count read out of the model file's own header. Every input it
                # cannot read is carried through as unknown, never guessed.
                _vram_mb = _hw.get("gpu_vram_mb")
                if not isinstance(_vram_mb, int):
                    _vram_mb = None
                from intergen.gpu_offload import plan_for_model
                _plan = plan_for_model(vram_mb=_vram_mb, model_path=model_path,
                                       mmproj_path=model_mmproj_path)
                _eff_gpu_layers = resolve_gpu_layers(_cfg_gpu_layers,
                                                     tier_level=_tier_level,
                                                     plan=_plan)
                log.info("offload: llama_server.gpu_layers=%r (tier %s, card %s "
                         "MiB) -> %d layers, engine %s (%s)%s; %s",
                         _cfg_gpu_layers, _tier_level,
                         _vram_mb if _vram_mb is not None else "unreadable",
                         _eff_gpu_layers, _engine, _server_path,
                         f", device pin {_device}" if _device else "",
                         _plan.reason)
                glass.emit("warmup", "offload_plan", turn_id=self._boot_turn,
                           iface="daemon", detail={
                               "configured": _cfg_gpu_layers,
                               "tier_level": _tier_level,
                               "vram_mb": _vram_mb,
                               "required_mb": _plan.required_mb,
                               "total_layers": _plan.total_layers,
                               "fits": _plan.fits,
                               "effective_gpu_layers": _eff_gpu_layers,
                               "reason": _plan.reason})
                _t_load = time.monotonic()
                glass.emit("warmup", "model_load_start", turn_id=self._boot_turn,
                           iface="daemon", detail={
                               "model": self._model_loaded, "path": model_path,
                               "port": self._config.get("llama_server.port", 8080),
                               "context_size": self._config.get("llm.context_size", 16384),
                               "gpu_layers": _eff_gpu_layers,
                               "parallel": self._config.get("llama_server.parallel", 1),
                               "reasoning": self._config.get("llama_server.reasoning", "off"),
                               "jinja": self._config.get("llama_server.jinja", True)})
                # ONE ATTEMPT USED TO BE THE WHOLE STORY. A transient failure —
                # a device the previous model server released moments ago, a port
                # a departing session still holds — ended the chat model for the
                # life of the process. Retry the transient class with a bounded
                # back-off before deciding anything (llama_manager.
                # retry_transient_start); a non-transient failure returns on the
                # first attempt exactly as before, so an absent model or an
                # integrity failure still degrades immediately.
                started = self._llama.start(
                    model_path,
                    port=self._config.get("llama_server.port", 8080),
                    context_size=self._config.get("llm.context_size", 16384),
                    gpu_layers=_eff_gpu_layers,
                    parallel=self._config.get("llama_server.parallel", 1),
                    jinja=self._config.get("llama_server.jinja", True),
                    reasoning=self._config.get("llama_server.reasoning", "off"),
                    chat_template_file=self._config.get(
                        "llama_server.chat_template_file", None),
                    cacheable=model_cacheable,
                    mmproj_path=model_mmproj_path,
                    has_vision=model_has_vision,
                    expect_tools=True,  # the chat server's core contract
                    device=_device,
                    server_path=_server_path,
                )
                glass.emit("warmup", "model_load_done", turn_id=self._boot_turn,
                           iface="daemon", detail={"started": bool(started)},
                           dur_ms=(time.monotonic() - _t_load) * 1000)
                if not started and self._llama.last_failure.is_transient:
                    log.warning(
                        "chat llama-server start failed with %s (%s) — retrying, "
                        "because that failure class can succeed on a second "
                        "attempt", self._llama.last_failure.name,
                        self._llama.last_error)
                    started = self._llama.retry_transient_start()
                if started:
                    log.info("llama-server started")
                    self._model_server_integrity_failure = None
                    self._model_server_down = None
                else:
                    # Classify the failure STRUCTURALLY — not by string-matching
                    # the error text: a declared-but-unhonored
                    # capability (toolless template, missing projector,
                    # tools/vision not advertised) is an INTEGRITY failure — a
                    # signed-manifest capability the running server did not honor,
                    # which reads as tamper/corruption — NOT the benign
                    # no-model-downloaded degrade. Surface it as ONE conspicuous,
                    # queryable state (status() field), not a buried warning.
                    failure = self._llama.last_failure
                    detail = self._llama.last_error
                    if failure.is_integrity:
                        self._model_server_integrity_failure = (
                            f"{failure.name}: {detail}"
                        )
                        log.error(
                            "MODEL-SERVER INTEGRITY FAILURE [%s]: %s — the model "
                            "declared a capability the running server did not "
                            "honor; refusing to serve a silently-degraded model.",
                            failure.name, detail,
                        )
                    else:
                        log.warning("llama-server failed to start: %s", detail)
                    # Retain the manager (so the chat watchdog below is created and
                    # can recover it) ONLY for the transient port-collision case:
                    # at cold boot the GDM greeter session's own daemon holds 8080,
                    # so our pre-launch bind check refuses with PORT_IN_USE; once
                    # the greeter session tears down and frees the port the
                    # watchdog's restart() binds. Any OTHER failure (no model /
                    # binary, or a declared-capability integrity failure) is NOT
                    # transient — drop to None and degrade. (_config is set before
                    # the pre-launch check, so restart() has the config to retry.)
                    # RECORD THAT THE CHAT MODEL IS DOWN, as a first-class
                    # state. The unit reports itself active either way, and
                    # without this the only evidence a person got was a reply
                    # asking them to rephrase.
                    self._model_server_down = f"{failure.name}: {detail}"
                    # Retain the manager — and therefore the watchdog, which is
                    # built under `if self._llama:` — for every TRANSIENT failure,
                    # not for PORT_IN_USE alone. Dropping it removed the only
                    # thing that could recover the server, so a chat model that
                    # failed once stayed down until the daemon was restarted by
                    # hand. Anything non-transient still drops to None and
                    # degrades, unchanged.
                    if not failure.is_transient:
                        self._llama = None
                    else:
                        log.info("chat llama-server failed with %s — retaining "
                                 "the manager so the watchdog can recover it",
                                 failure.name)
            except Exception as e:
                log.warning("llama-server init failed: %s", e)
                self._llama = None

        # Step 3b (AI-12): the embedding-only llama-server instance — a second
        # LlamaManager, CPU-pinned (gpu_layers=0, ~80-140MB) on a distinct port
        # with --embedding, serving nomic-embed for the semantic matcher's
        # Layer 2. Replaces the in-process sentence-transformers/torch/hf stack.
        #
        # Provisioning-aware (option A): ALWAYS create + RETAIN this manager for
        # the daemon's lifetime, even when the embed model is absent at startup.
        # The matcher + router are wired with self._embed_llama.embed — a LIVE
        # bound method that checks is_running() per call — and the embed watchdog
        # (Step 10b) is the single recovery path. Two failure modes are closed by
        # always-create:
        #   * GDM-greeter cold-boot port collision: the greeter's own daemon
        #     holds 8081 first, so the initial start fails the pre-launch bind
        #     check; once the greeter tears down and frees the port the watchdog
        #     binds and Layer-2 self-heals with NO rebind.
        #   * First-boot provisioning window: the nomic GGUF lands minutes after
        #     the daemon starts. The watchdog's precondition holds quietly until
        #     _resolve_embed_model_path() finds the model, then brings the
        #     embedder up — WITHOUT depending on the one-time setup-flow daemon
        #     restart (the latent silent-degradation trap the old one-shot
        #     "disable Layer-2 if absent at startup, never re-check" code left).
        # The model path is re-resolved fresh each (re)start, NOT cached here.
        try:
            from intergen.llama_manager import LlamaManager
            self._embed_llama = LlamaManager()
            embed_model_path = self._resolve_embed_model_path()
            if embed_model_path:
                if self._start_embed_server():
                    log.info("embedding llama-server started (CPU) on port %s",
                             self._config.get("llama_server.embedding_port", 8081))
                else:
                    log.warning(
                        "embedding llama-server not up yet (%s) — the embed "
                        "watchdog will (re)start it once the port is free; "
                        "Layer-2 will self-heal (keyword + LLM layers active "
                        "meanwhile)",
                        self._embed_llama.last_error)
            else:
                log.info(
                    "embedding model not present on disk yet (first-boot "
                    "provisioning window?) — the embed watchdog holds and brings "
                    "Layer-2 up the moment the model lands; keyword + LLM layers "
                    "active meanwhile")
        except Exception as e:
            log.warning("embedding llama-server init failed: %s "
                        "(Layer-2 embeddings disabled)", e)
            self._embed_llama = None

        # Step 4: Initialize metrics and event logger
        try:
            from intergen.metrics import EventLogger, MetricsTracker
            self._events = EventLogger()
            self._metrics = MetricsTracker()
        except Exception as e:
            log.warning("Metrics init failed: %s", e)

        # Step 5: Initialize tool registry and discover tools.
        # Sentinel build seq step 3: the registry is constructed with an
        # ALWAYS-ON ScannerPolicy so every external/MCP interaction is scanned
        # on the fly (design plan §3 — "always-on by default"). The floor is the
        # deterministic, network-free LocalRulesScanner; the deep tier
        # (LocalQwen / cloud) attaches via set_deep_scanner once configured.
        try:
            from intergen.tool_registry import ToolRegistry
            from intergen.scanner.policy import ScannerPolicy, ScanDepth
            depth = (
                ScanDepth.DEEP
                if self._config.get("sentinel.scan.depth", "baseline") == "deep"
                else ScanDepth.BASELINE
            )
            self._scanner_policy = ScannerPolicy(default_depth=depth)
            self._tools = ToolRegistry(
                scanner_policy=self._scanner_policy
            )
            count = self._tools.discover_tools()
            log.info("Tool registry: %d tools discovered (Sentinel scan active, "
                     "depth=%s)", count, depth.value)
            self._attach_deep_scanner()
        except Exception as e:
            log.warning("Tool registry init failed: %s", e)

        # Step 5c: phone-a-friend EscalationManager (consent-first cloud assistance,
        # design plan §4). Built from the AI-immutable escalation:/providers: config
        # (decision #5) and injected with the SAME always-on ScannerPolicy as the
        # dispatch chokepoint, so a derived (non-consented) escalation egress is
        # scanned through the identical floor (decision #6). NO default provider —
        # with none configured, offers degrade to a "configure a provider" note.
        try:
            from intergen.escalation import EscalationManager
            self._escalation = EscalationManager.from_config(
                self._config.get("escalation"),
                self._config.get("providers"),
                scanner=getattr(self, "_scanner_policy", None),
            )
            log.info("Phone-a-friend: mode=%s, providers=%d",
                     self._escalation.get_mode().value,
                     len(self._escalation.list_providers()))
        except Exception as e:
            log.warning("Phone-a-friend init failed: %s", e)
            self._escalation = None

        # Step 5b: Ensure the AI-6 dispatch signing key exists. The daemon is the
        # token-minting authority for privileged dispatch; gen-on-first-run here
        # self-heals a pre-AI-6 install or a deleted key. Non-fatal: if it fails,
        # privileged actions fail closed at mint-time with an actionable message
        # while the rest of the assistant keeps running (graceful degradation).
        try:
            from intergen.dispatch_token import ensure_dispatch_key, dispatch_key_path
            ensure_dispatch_key()
            log.info("Dispatch signing key ready at %s", dispatch_key_path())
        except Exception as e:
            log.warning("Dispatch key init failed (privileged dispatch will "
                        "fail closed until 'intergen setup' is run): %s", e)

        # Step 6: Initialize semantic matcher and register intents
        try:
            from intergen.semantic import SemanticMatcher
            self._matcher = SemanticMatcher(
                embedder=(self._embed_llama.embed if self._embed_llama else None),
            )
            # Registration embeds every intent, so it is one of the callers
            # that legitimately waits for a model to load rather than degrading:
            # a matcher that registers its intents as PENDING here needs a
            # recovery pass later, and on an installed machine that window is
            # where retrieval quietly fell back to keyword-only. embed() itself
            # stays on its short request-path grace; this is the startup path
            # asking, once, for the readiness the server is already working on.
            if self._embed_llama is not None and self._embed_llama.is_running():
                if not self._embed_llama.wait_until_ready():
                    log.warning(
                        "embedding server did not report ready within its "
                        "model-load budget — intents register pending and the "
                        "embed watchdog's recovery pass picks them up")
            from intergen.intents import register_all_intents
            register_all_intents(self._matcher)
            log.info("Semantic matcher: %d intents registered",
                     self._matcher.get_intent_count())
        except Exception as e:
            log.warning("Semantic matcher init failed: %s (Layer 2 disabled)", e)
            # Fallback: a keyword-only matcher so the daemon still starts.
            # This MUST NOT itself raise — a second exception here would escape
            # start_service and kill the whole daemon. Previously the fallback
            # re-ran the SAME `from intergen.semantic import SemanticMatcher`
            # inside this except body with no guard, so when the import was what
            # failed (e.g. numpy absent at module load, now fixed by lazy import
            # in semantic.py) the re-import re-raised straight out of start_service.
            # Guard it: if even the bare matcher cannot be built, leave the
            # matcher unset (router init at Step 11 already guards on it) rather
            # than propagate.
            try:
                from intergen.semantic import SemanticMatcher
                self._matcher = SemanticMatcher.__new__(SemanticMatcher)
                self._matcher._keyword_intents = []
                self._matcher._embedding_intents = {}
                self._matcher._pending_embedding_intents = {}
                self._matcher._lock = threading.Lock()
                self._matcher._embedder = None
                self._matcher._model_name = "nomic-ai/nomic-embed-text-v1.5"
            except Exception as e2:
                log.error("Semantic matcher fallback also failed: %s — Layer 1/2 "
                          "matching disabled, daemon continues", e2)
                self._matcher = None

        # Step 7: Initialize LLM router
        try:
            from intergen.llm import LLMRouter
            port = self._config.get("llama_server.port", 8080)
            llm_config = {
                "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
                "tool_calling": self._llama is not None,
                "temperature": self._config.get("llm.temperature", 0.6),
                "top_p": self._config.get("llm.top_p", 0.8),
                "top_k": self._config.get("llm.top_k", 20),
                "max_tokens": self._config.get("llm.max_tokens", 4096),
                "presence_penalty": self._config.get("llm.presence_penalty", 1.5),
                # The active model's declared vision capability, from the
                # resolved ModelInfo (fail-closed False if unknown). Gates the
                # image-turn path: an image on a non-vision model gets an honest
                # code-owned reply, never a silent swallow.
                "has_vision": model_has_vision,
            }
            self._llm = LLMRouter(llm_config)
            # Wire the runtime engine-health reaction ladder. The detector (in
            # the LLM stream) feeds every generation's flags to the monitor's
            # counter via this sink; on sustained corruption the monitor runs
            # the handler on its own thread, which REPORTS the condition and
            # leaves the engine alone. The sink is cheap (a counter) and the
            # reaction never touches the request path.
            from intergen.engine_health import EngineHealthMonitor
            self._engine_health = EngineHealthMonitor(
                self._on_engine_health_degraded)
            self._llm.set_semantic_flag_sink(self._engine_health.record)
            log.info("LLM router initialized (tool_calling=%s)",
                     llm_config["tool_calling"])
        except Exception as e:
            log.warning("LLM router init failed: %s", e)

        # Step 8: Initialize memory manager
        self._memory = None
        try:
            from intergen.memory import MemoryManager
            # Unset → MemoryManager resolves a per-user XDG path. Memory is
            # per-user state; the old /var default is unwritable under the
            # hardened user service and would leak facts across users.
            db_path = self._config.get("memory.db_path", None)
            self._memory = MemoryManager(db_path)
            log.info("Memory manager initialized (%d facts stored)",
                     self._memory.count)
        except Exception as e:
            log.warning("Memory manager init failed: %s", e)

        # Step 9: Start system state cache
        self._state_cache = None
        try:
            from intergen.state_cache import StateCache
            self._state_cache = StateCache()
            self._state_cache.start()
            log.info("State cache started (%d entries)",
                     self._state_cache.entry_count)
        except Exception as e:
            log.warning("State cache init failed: %s", e)

        # Step 10: Initialize governance engine (Ring-0 enforcement)
        try:
            from intergen.governance import GovernanceEngine, AutonomyTier
            self._governance = GovernanceEngine(
                autonomy_tier=AutonomyTier.OBSERVE,
            )
            self._governance.load_tier()
            if self._governance.verify_hash():
                log.info("Governance engine initialized — hash verified, tier=%s",
                         self._governance.autonomy_tier.name)
            else:
                log.critical("Governance hash verification FAILED — "
                             "autonomous operations suspended")
        except Exception as e:
            log.warning("Governance engine init failed: %s", e)

        # Step 11: Initialize conversation router (the orchestrator)
        if self._tools and self._matcher and self._llm:
            try:
                from intergen.router import ConversationRouter
                from intergen.interfaces.types import HardwareTierLevel
                # The router runs against the EFFECTIVE resolved tier + dispatch
                # mode (dispatch lockdown), not the raw detected tier — so model
                # capability assumptions (decomposition) and the dispatch posture
                # both match the model that actually loads. Fail-closed if the
                # resolver is somehow unset: locked 2B.
                _res = getattr(self, "_dispatch_resolved", None)
                if _res is not None:
                    hw_tier = _res.tier
                    _lock_dispatch = _res.lock_dispatch
                else:
                    hw_tier = (HardwareTierLevel(self._hardware_tier.get("level", 2))
                               if self._hardware_tier else HardwareTierLevel.TIER_2)
                    _lock_dispatch = True  # fail-closed
                # Structural backstop (WC lockdown guard #2): when locked, the
                # registry refuses to hand tool schemas to the model on ANY
                # surface, so the model cannot emit a ToolCall even if a future
                # model-facing path forgets to check the lock. Code-owned
                # dispatch (get_tool/execute) is unaffected.
                self._tools.set_tool_offering_locked(_lock_dispatch)
                _router = ConversationRouter(
                    tool_registry=self._tools,
                    semantic_matcher=self._matcher,
                    llm=self._llm,
                    event_logger=self._events,
                    metrics=self._metrics,
                    hardware_tier=hw_tier,
                    lock_dispatch=_lock_dispatch,
                    memory=self._memory,
                    state_cache=self._state_cache,
                    escalation=getattr(self, "_escalation", None),
                    # Same nomic-embed embedder the semantic matcher uses — powers
                    # RAG retrieval over the teaching how-to corpus (PI-218-2). None
                    # when the embedding server is down → corpus keyword fallback.
                    embedder=(self._embed_llama.embed if self._embed_llama else None),
                )
                # The browser server is handed this same router, and it serves
                # one conversation per connected client. So the router's own
                # conversation is given up here and the desktop bus keeps one of
                # its own: from this point every turn names the conversation it
                # belongs to, and a turn that does not is refused rather than
                # served from whatever was bound last. The router is published
                # onto the daemon only once its conversation exists, so nothing
                # can reach a router that has neither.
                self._conversation = _router.new_conversation()
                _router.detach_conversation()
                self._router = _router
                log.info("Conversation router initialized")
            except Exception as e:
                log.warning("Router init failed: %s", e)
                self._last_error = f"Router init failed: {e}"

        # Step 10: Start watchdog (monitors llama-server health)
        if self._llama:
            try:
                from intergen.watchdog import Watchdog
                self._watchdog = Watchdog(
                    # Hold quietly while InterGen is paused for a game: the
                    # chat server is DOWN on purpose there, so probing it would
                    # count a failure, spend the restart budget, and eventually
                    # fire a false give-up over a state the user asked for.
                    precondition=lambda: not self._paused,
                    # Ownership-aware periodic probe (mirrors the startup
                    # bind-ownership gate): owns_port() rejects a foreign holder
                    # answering /health green on our port, so the chat watchdog
                    # rebinds rather than reading the collision as healthy.
                    health_check=lambda: self._llama.is_running()
                                         and self._llama.owns_port()
                                         and self._llama.health().running,
                    # Not self._llama.restart directly: the wrapper re-reads the
                    # pause under the lock, so a restart decided just before a
                    # pause cannot bring the server back up behind it.
                    restart_action=self._restart_chat_server,
                    on_failure=self._on_watchdog_giveup,
                )
                self._watchdog.start()
                log.info("Watchdog started")
            except Exception as e:
                log.warning("Watchdog init failed: %s", e)

        # Step 10b: watchdog for the embedding server — the SINGLE recovery path
        # for the embedder, covering BOTH the GDM-greeter cold-boot port
        # collision AND the first-boot provisioning window (option A). The
        # embed manager is always created (Step 3b), so this watchdog is always
        # set up. It restarts the embedder once the matcher's/router's live
        # self._embed_llama.embed callable can return vectors again, so Layer-2
        # comes up with no rebinding. health_check reads self._embed_llama live
        # so a None never NPEs.
        if self._embed_llama is not None:
            try:
                from intergen.watchdog import Watchdog
                self._embed_watchdog = Watchdog(
                    # Provisioning-aware precondition (option A): HOLD quietly —
                    # no health probe, no failure count, no restart-budget spend
                    # — until the embed model is present on disk. So the normal
                    # first-boot provisioning window (model lands minutes after
                    # the daemon starts) never burns retries or fires a false
                    # CRITICAL; only once the model is present does a dead
                    # embedder count as a failure. When the model lands, the next
                    # tick finds the embedder not running, fails the health
                    # check, and the bounded restart brings it up.
                    # Two things gate this watchdog. The model must be present
                    # on disk (the provisioning window below), AND InterGen must
                    # not be paused for a game — while paused the embedding
                    # server is down on purpose, so probing it would spend the
                    # restart budget on a state the user asked for.
                    precondition=lambda: (not self._paused
                                          and bool(self._resolve_embed_model_path())),
                    # Ownership-aware periodic probe (mirrors the chat watchdog):
                    # owns_port() rejects the greeter's foreign holder answering
                    # /health green on 8081 so the embedder rebinds rather than
                    # reading the collision as healthy.
                    health_check=lambda: self._embed_llama is not None
                                         and self._embed_llama.is_running()
                                         and self._embed_llama.owns_port()
                                         and self._embed_llama.health().running,
                    # Wrapped, not bare: the wrapper re-reads the pause under
                    # the lock so this thread cannot restart the embedder behind
                    # a pause that arrived after the tick began.
                    restart_action=self._restart_embed_server_watchdog,
                    # A degraded state (operator rule: log it CRITICAL with
                    # context) — with the model PRESENT (the precondition gates
                    # this), the embedder did not rebind within the restart
                    # budget, so Layer-2 stays down for the rest of this session.
                    on_failure=lambda msg: log.critical(
                        "EMBEDDING SELF-HEAL EXHAUSTED: %s — the embedding "
                        "server (port %s) did not rebind within the watchdog's "
                        "restart budget; Layer-2 semantic routing is DEGRADED to "
                        "keyword/LLM for the rest of this user session (a daemon "
                        "restart is required to recover it)",
                        msg, self._config.get("llama_server.embedding_port", 8081)),
                )
                self._embed_watchdog.start()
                log.info("Embedding watchdog started (provisioning-aware)")
            except Exception as e:
                log.warning("Embedding watchdog init failed: %s", e)

        # Step 13: Start web server (aiohttp + WebSocket bridge)
        try:
            from intergen.web_server import WebServer
            from intergen.health import HealthAggregator

            self._health_agg = HealthAggregator(
                llama_manager=self._llama,
                watchdog=self._watchdog,
                governance=self._governance,
                audit_log_count=0,
                web_connections=0,
            )

            self._web_server = WebServer(
                host="127.0.0.1",
                port=8089,
                router=self._router,
                llm=self._llm,
                tools=self._tools,
                governance=self._governance,
                metrics=self._metrics,
                event_logger=self._events,
                state_cache=self._state_cache,
                memory=self._memory,
                health_aggregator=self._health_agg,
            )
            self._web_server.mark_ready()
            self._web_thread = threading.Thread(
                target=self._run_web_server,
                name="intergen-web",
                daemon=True,
            )
            self._web_thread.start()
            log.info("Web server started on http://127.0.0.1:8089")
        except Exception as e:
            log.warning("Web server init failed: %s", e)

        # Step 14: D-Bus export
        self._export_dbus()

        # Step 15: Signal ready
        self._running = True
        log.info("InterGen daemon ready (router=%s, tools=%d, llm=%s, web=127.0.0.1:8089)",
                 self._router is not None,
                 self._tools.tool_count if self._tools else 0,
                 self._llm is not None)
        glass.emit("warmup", "daemon_ready", turn_id=self._boot_turn,
                   iface="daemon",
                   detail={"router": self._router is not None,
                           "tools": self._tools.tool_count if self._tools else 0,
                           "llm": self._llm is not None, "web": "127.0.0.1:8089",
                           "model": self._model_loaded,
                           "glass": glass.glass_enabled()},
                   dur_ms=(time.monotonic() - self._boot_t0) * 1000)

        # Step 16: Warm the model's prompt cache (background). The local 2B
        # ingests its large system prompt COLD on the first request — ~2 min on
        # slow hardware — which previously blew the LLM timeout and returned an
        # empty "didn't catch that" reply. Priming the exact freeform + tools
        # prefixes now means the user's first real query reuses a hot KV-cache
        # (~10s) instead of paying the cold ingest. Runs in a daemon thread so
        # startup returns immediately.
        self._start_warmup()

    def _warmup_skip_reason(self) -> str:
        """Why the engine is not running, stated from what was measured.

        Order matters: the RECORDED failure is preferred over any inference,
        because a recorded failure is a fact and everything else here is a
        guess. Only when nothing was recorded does this fall back to observing
        whether a model file is actually present — and even then it says what
        it looked at, so the reader can check the same thing.
        """
        llama = self._llama
        if llama is not None:
            failure = getattr(llama, "last_failure", None)
            if failure is not None and getattr(failure, "name", "NONE") != "NONE":
                detail = getattr(llama, "last_error", None)
                return (f"{failure.name}"
                        + (f": {detail}" if detail else ""))
        # Nothing recorded. Look at what is actually on disk, rather than
        # speculate about it.
        path = None
        config = getattr(llama, "_config", None) if llama is not None else None
        if config is not None:
            path = getattr(config, "model_path", None)
        if not path:
            if self._model_loaded:
                return (f"a model is selected ({self._model_loaded}) but the "
                        f"engine has not been started yet")
            return "no model has been selected yet"
        if not os.path.exists(path):
            return f"the selected model file is not on disk ({path})"
        return ("the engine has not been started yet; the model at "
                f"{path} is present")

    def _start_warmup(self) -> None:
        """Prime the llama-server prompt cache so the user's first query is fast.

        Sends a 1-token generation through the exact message prefixes the web
        path uses (conversational, then tool-enabled), discarding the output.
        KV-cache prefix reuse then makes real queries warm. Best-effort; any
        failure is logged and ignored — warmup never blocks or breaks startup.
        """
        if not (self._llm and self._router):
            return
        # G3-2: never warm (and never claim "warm") when the engine is down. On
        # first boot the model isn't downloaded yet, so llama-server is not
        # running; llm.stream() would log a connection-refused ERROR, yield
        # nothing, and the loop below would STILL log "Model prompt cache fully
        # warm — replies will be fast." — a false positive that masks the real
        # state. The warmup runs for real once the engine comes up (the
        # post-download daemon restart in `intergen setup`, G3-1).
        if not (self._llama and self._llama.is_running()):
            # Say WHY it is not running, from what was actually recorded, rather
            # than guessing. The message used to append "(no model downloaded?)"
            # unconditionally, so a machine with a verified model whose engine
            # had failed to start was told its model might be missing — sending
            # the reader to look at the one thing that was fine while the real
            # reason sat in the recorded failure.
            reason = self._warmup_skip_reason()
            log.info("Skipping prompt-cache warmup — llama-server is not "
                     "running: %s. Warmup runs when the engine starts.", reason)
            return
        import threading

        def _warm() -> None:
            _tw = time.monotonic()
            glass.emit("warmup", "cache_warm_start", turn_id=self._boot_turn,
                       iface="daemon")
            try:
                log.info("Warming model prompt cache (first query will be slow "
                         "until this finishes)…")
                with self._router.bind_conversation(self._conversation):
                    msgs = self._router._build_messages("hi", with_tools=False)
                for _ in self._llm.stream(msgs, max_tokens=1):
                    pass
                log.info("Conversational prompt cache warm.")
                if self._tools:
                    with self._router.bind_conversation(self._conversation):
                        tmsgs = self._router._build_messages(
                            "hi", with_tools=True)
                    schemas = self._tools.get_tool_schemas()
                    for _ in self._llm.stream_with_tools(
                            tmsgs, tools=schemas, max_tokens=1):
                        pass
                    log.info("Tool prompt cache warm.")
                log.info("Model prompt cache fully warm — replies will be fast.")
                glass.emit("warmup", "cache_warm_done", turn_id=self._boot_turn,
                           iface="daemon", dur_ms=(time.monotonic() - _tw) * 1000)
                glass.emit("warmup", "time_to_ready", turn_id=self._boot_turn,
                           iface="daemon",
                           dur_ms=(time.monotonic() - self._boot_t0) * 1000)
            except Exception as e:
                log.warning("Model warmup failed (non-fatal): %s", e)
                glass.emit("warmup", "cache_warm_failed", turn_id=self._boot_turn,
                           iface="daemon", detail={"error": type(e).__name__})

        threading.Thread(target=_warm, daemon=True,
                         name="intergen-warmup").start()

    def _parse_dispatch_override(self):
        """Parse the operator's manual tier override (config dispatch.tier_override).

        Accepts an int 1/2/3, "TIER_N", or a bare "N" string; null/absent/invalid
        → None (use hardware detection). Returns a HardwareTierLevel or None. The
        resolver still fails closed on top of any override (a forced bigger tier
        resolves down to the largest shipped lane at or below it, or to the locked
        2B floor when none is shipped at or below it)."""
        from intergen.interfaces.types import HardwareTierLevel
        raw = self._config.get("dispatch.tier_override") if self._config else None
        if raw is None:
            return None
        try:
            if isinstance(raw, str):
                s = raw.strip().upper().removeprefix("TIER_").removeprefix("TIER")
                raw = int(s)
            return HardwareTierLevel(int(raw))
        except (ValueError, TypeError):
            log.warning("Ignoring invalid dispatch.tier_override=%r "
                        "(expected 1, 2, or 3)", raw)
            return None

    def _attach_deep_scanner(self) -> None:
        """Attach the Sentinel deep-scan tier (LocalQwen) to the registry policy.

        Resolves the configured classifier model (sentinel.scan.qwen_model — an
        already-pinned catalog model) to its on-disk path. If the model is present
        the LocalQwenScanner is constructed (its own llama-server, on-demand
        keep-alive per ratified #3) and attached, so a floor FLAG can escalate to
        the semantic tier (and depth=deep always escalates). FAIL-SAFE: if the
        deep tier is not local-qwen, the model is absent, or anything raises, the
        registry keeps the always-on rules floor (which still FLAGs to the human
        modal) — never a worse posture than baseline.
        """
        try:
            if self._config.get("sentinel.scan.deep_scanner", "local-qwen") != "local-qwen":
                return  # cloud deep-scanner rides the substrate; wired separately
            from pathlib import Path
            model_name = self._config.get("sentinel.scan.qwen_model", "Qwen3.5-2B")
            from intergen.model_manager import ModelManager
            mm = ModelManager()
            info = mm.get_model_by_name(model_name)
            if info is None:
                log.warning("Sentinel deep scanner: model %r not in catalog; "
                            "staying on the rules floor", model_name)
                return
            # Use the model-info local path — the single source of truth the
            # main LLM load (Step 2) already uses — NOT models.path + filename.
            # models.path is the PARENT (/var/lib/intergen/models) but models
            # install under MODEL_DIR (.../models/LLM/), so parent+filename never
            # existed and the deep scanner never attached, silently degrading a
            # security-floor tier to rules-only. get_model_by_name sets
            # local_path only when the GGUF is actually present on disk.
            if not info.local_path or not Path(info.local_path).exists():
                log.info("Sentinel deep scanner: model %s not downloaded; floor-only "
                         "until it is present", model_name)
                return
            model_path = Path(info.local_path)
            # Load-time pin verification (defense-in-depth): the scanner model
            # gates security decisions, so a tampered scanner model is the worst
            # case — refuse to attach an unverified file; stay on the rules floor.
            if not mm.verify_arbitrary_path(model_path):
                log.error("Sentinel deep scanner: %s failed pin verification — "
                          "refusing to attach (possible tamper); rules floor remains.",
                          model_path)
                return
            from intergen.scanner.local_qwen import LocalQwenScanner
            scanner = LocalQwenScanner(model_path=str(model_path))
            if self._tools.attach_deep_scanner(scanner):
                log.info("Sentinel deep scanner attached (model=%s)", model_name)
        except Exception as e:
            log.warning("Sentinel deep-scanner attach failed (%s); rules floor "
                        "remains active", e)

    def stop_service(self) -> None:
        """Graceful shutdown — stop all subsystems in reverse order."""
        log.info("InterGen daemon stopping...")
        self._running = False

        if self._web_server:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._web_server.stop(),
                    self._web_loop,
                )
            except Exception:
                pass
        if self._web_thread and self._web_thread.is_alive():
            self._web_thread.join(timeout=5.0)

        if self._state_cache:
            self._state_cache.stop()
        if self._watchdog:
            self._watchdog.stop()
        if self._embed_watchdog:
            self._embed_watchdog.stop()
        if self._llama:
            self._llama.stop()
        if self._embed_llama:
            self._embed_llama.stop()

        self._teardown_dbus()

        self._router = None
        self._llm = None
        self._matcher = None
        self._tools = None
        self._governance = None
        self._web_server = None
        self._health_agg = None

        log.info("InterGen daemon stopped")

    def _teardown_dbus(self) -> None:
        """Release everything _export_dbus took, in the reverse order it took it.

        WHY THIS EXISTS. Gio.bus_get_sync(SESSION) hands back the connection
        SHARED BY THE PROCESS, so an object registered on it outlives any single
        daemon lifetime. stop_service used to leave the registration and the name
        in place, and the next in-process start met
        "An object is already exported for the interface ... (2)", caught it, and
        ran the whole of that life with self._bus = None — D-Bus silently gone,
        one warning line the only trace. Measured x10 in a battery run and x10
        again in a re-drive.

        Every step is guarded independently: a teardown is the wrong place to
        raise, and a failure to release one handle must not prevent releasing the
        others. Failures are logged rather than swallowed, because a handle that
        could not be released is exactly what will break the NEXT start.
        """
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except Exception as e:  # noqa: BLE001 — no gi means nothing was exported
            log.debug("D-Bus teardown skipped (Gio unavailable): %s", e)
            self._reg_id = None
            self._owner_id = None
            self._bus = None
            return

        if self._owner_id is not None:
            try:
                Gio.bus_unown_name(self._owner_id)
            except Exception as e:  # noqa: BLE001
                log.warning("D-Bus name release failed: %s", e)
            finally:
                self._owner_id = None

        if self._reg_id is not None:
            if self._bus is not None:
                try:
                    self._bus.unregister_object(self._reg_id)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "D-Bus object unexport failed: %s. A later in-process "
                        "start may find the path still exported.", e)
            else:
                log.warning(
                    "D-Bus registration %s cannot be released: the connection "
                    "is already gone.", self._reg_id)
            self._reg_id = None

        self._bus = None

    def _run_web_server(self) -> None:
        """Run the aiohttp web server in a dedicated thread, with a bind
        watchdog. If the web port (8089) is held at cold boot by the GDM greeter
        session's own InterGen web server, retry the bind until the greeter tears
        down and frees it — instead of crashing the thread with an unhandled
        traceback and never recovering (the web analogue of the chat/embed
        watchdogs). run_forever only starts once OUR bind actually holds the
        port; a permanently-held port logs a loud give-up and the thread exits.
        """
        self._web_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._web_loop)
        try:
            self._web_loop.run_until_complete(self._web_bind_with_watchdog())
            if self._web_server.running:
                self._web_loop.run_forever()
        except Exception:
            log.exception("Web server thread crashed")
        finally:
            self._web_loop.close()
            self._web_loop = None

    async def _web_bind_with_watchdog(self) -> None:
        """Bind the web server, retrying on a held port until it frees.

        start() returns False (a HANDLED condition, not a raise) when the port
        is held — the cold-boot greeter collision — so retry on an interval until
        OUR bind succeeds, bounded by WEB_BIND_MAX_ATTEMPTS so a permanently-held
        port logs a loud give-up instead of spinning forever. Readiness is our
        own bind (start() True), never an HTTP probe, so a foreign holder can
        never be mistaken for ours.
        """
        for attempt in range(1, WEB_BIND_MAX_ATTEMPTS + 1):
            if await self._web_server.start():
                if attempt > 1:
                    log.info("Web server bound on attempt %d (port freed)",
                             attempt)
                return
            if attempt == 1:
                log.warning(
                    "Web port held at startup; the web watchdog will rebind once "
                    "it is freed (retry every %ds, up to ~%ds).",
                    WEB_BIND_RETRY_INTERVAL,
                    WEB_BIND_RETRY_INTERVAL * WEB_BIND_MAX_ATTEMPTS)
            await asyncio.sleep(WEB_BIND_RETRY_INTERVAL)
        log.critical(
            "Web server did not bind within the watchdog budget (web port held "
            "~%ds) — the panel UI is unavailable until a daemon restart.",
            WEB_BIND_RETRY_INTERVAL * WEB_BIND_MAX_ATTEMPTS)

    def _claim_bus_name(self) -> bool:
        """Claim the well-known D-Bus name with DO_NOT_QUEUE, BEFORE any bind.

        Single-instance guard, FAIL-CLOSED. Returns True ONLY when this process
        is confirmed the sole primary owner (RequestName replied PRIMARY_OWNER
        or ALREADY_OWNER). Every other outcome returns False so the caller exits
        BEFORE binding any resource — no llama-server launch, no port bind. This
        is the security-only posture: a daemon that cannot PROVE sole ownership
        must never proceed degraded-and-masking.

        The two False cases are distinguished for main()'s exit code:
          * another connection already owns the name (RequestName EXISTS /
            IN_QUEUE) — a benign duplicate launch (D-Bus/Exec activation on a
            bus this instance was not meant to serve); main() exits 0.
          * the guard could not VERIFY ownership at all (gi/Gio unavailable, or
            the session bus unreachable) — an environment fault; the guard sets
            self._bus_verify_failed and main() exits non-zero + loud so
            Restart=on-failure retries instead of a masked exit 0.
        The winning connection is stored as self._bus and reused by
        _export_dbus, so the name stays owned for the process lifetime.
        """
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception as e:
            log.critical("Single-instance guard: gi/Gio unavailable (%s) — "
                         "cannot verify sole ownership of %s; refusing to bind "
                         "any resource and exiting (fail-closed).",
                         e, SERVICE_NAME)
            self._bus_verify_failed = True
            return False

        # D-Bus RequestName (org.freedesktop.DBus) — flags + reply codes per the
        # D-Bus specification: flag DO_NOT_QUEUE = 4; reply 1 = PRIMARY_OWNER,
        # 2 = IN_QUEUE, 3 = EXISTS, 4 = ALREADY_OWNER.
        DBUS_NAME_FLAG_DO_NOT_QUEUE = 4
        REPLY_PRIMARY_OWNER = 1
        REPLY_ALREADY_OWNER = 4
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            reply = self._bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "RequestName",
                GLib.Variant("(su)", (SERVICE_NAME, DBUS_NAME_FLAG_DO_NOT_QUEUE)),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            code = reply.unpack()[0]
        except Exception as e:
            log.critical("Single-instance guard: could not reach the session "
                         "bus to claim %s (%s) — cannot verify sole ownership; "
                         "refusing to bind any resource and exiting "
                         "(fail-closed).", SERVICE_NAME, e)
            self._bus = None
            self._bus_verify_failed = True
            return False

        if code in (REPLY_PRIMARY_OWNER, REPLY_ALREADY_OWNER):
            return True
        # EXISTS / IN_QUEUE: another live daemon already owns the name. Benign
        # duplicate launch — exit without launching any llama-server or binding
        # any port; the existing owner keeps serving.
        log.warning("Single-instance guard: %s already owned (RequestName "
                    "reply=%d) — duplicate launch; exiting WITHOUT binding any "
                    "resource. The existing owner continues serving.",
                    SERVICE_NAME, code)
        return False

    def _export_dbus(self) -> None:
        """Export the D-Bus interface via GLib/Gio.

        Uses PyGObject (gi.repository.Gio) — already installed as part
        of the GNOME desktop stack. This is the native GNOME approach,
        no extra pip packages needed.
        """
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib

            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            self._node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)

            # Maximum size accepted for the Ask method's `message` arg.
            # 4096 bytes is generous for legitimate natural-language input
            # (well-formed dictation transcripts typically fit in <2 KB);
            # caps RAM spike from a malformed/malicious local client that
            # might send multi-MB payloads.
            ASK_MESSAGE_MAX_BYTES = 4096

            def on_method_call(connection, sender, object_path, interface_name,
                               method_name, parameters, invocation):
                """Handle incoming D-Bus method calls."""
                try:
                    if method_name == "Ask":
                        message = parameters.unpack()[0]
                        # F11: enforce input size cap before forwarding.
                        # The byte length matters more than the character
                        # length here — D-Bus transports UTF-8 over the
                        # wire, and the cap is an anti-DoS guardrail.
                        message_bytes = (
                            len(message.encode("utf-8"))
                            if isinstance(message, str)
                            else len(message)
                        )
                        if message_bytes > ASK_MESSAGE_MAX_BYTES:
                            log.warning(
                                "D-Bus Ask: rejecting oversized message from "
                                "%s (size %d > %d bytes)",
                                sender, message_bytes, ASK_MESSAGE_MAX_BYTES,
                            )
                            invocation.return_dbus_error(
                                "com.intergenos.InterGen.Error",
                                f"Message too large (max {ASK_MESSAGE_MAX_BYTES} bytes).",
                            )
                            return
                        response = self.ask(message)
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "Escalate":
                        message = parameters.unpack()[0]
                        # Same anti-DoS size cap as Ask — the payload is shown in a
                        # consent modal and sent to a provider, so bound it too.
                        message_bytes = (
                            len(message.encode("utf-8"))
                            if isinstance(message, str)
                            else len(message)
                        )
                        if message_bytes > ASK_MESSAGE_MAX_BYTES:
                            log.warning(
                                "D-Bus Escalate: rejecting oversized message from "
                                "%s (size %d > %d bytes)",
                                sender, message_bytes, ASK_MESSAGE_MAX_BYTES,
                            )
                            invocation.return_dbus_error(
                                "com.intergenos.InterGen.Error",
                                f"Message too large (max {ASK_MESSAGE_MAX_BYTES} bytes).",
                            )
                            return
                        response = self.escalate(message)
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "Status":
                        response = self.status()
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "GetTier":
                        response = self.get_tier()
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "ResetConversation":
                        response = self.reset_conversation()
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name in ("PauseForGame", "ResumeAfterGame"):
                        # The argument is a window class, not prose. Cap it the
                        # same way Ask is capped so a local caller cannot spend
                        # the daemon's memory on an identifier, and reject
                        # rather than silently truncate — a caller sending an
                        # identifier this long has a defect worth seeing, and
                        # the two edges must agree on the string or the pause
                        # would never be released.
                        game = parameters.unpack()[0]
                        game_bytes = (len(game.encode("utf-8"))
                                      if isinstance(game, str) else len(game))
                        if game_bytes > GAME_NAME_MAX_BYTES:
                            log.warning(
                                "D-Bus %s: rejecting oversized game identifier "
                                "from %s (size %d > %d bytes)",
                                method_name, sender, game_bytes,
                                GAME_NAME_MAX_BYTES)
                            invocation.return_dbus_error(
                                "com.intergenos.InterGen.Error",
                                f"Game identifier too large (max "
                                f"{GAME_NAME_MAX_BYTES} bytes).")
                            return
                        # sender is the caller's unique bus name: the pause is
                        # tied to it so a caller that dies mid-game cannot leave
                        # InterGen paused with nothing alive to resume it.
                        response = (self.pause_for_game(game, owner=sender)
                                    if method_name == "PauseForGame"
                                    else self.resume_after_game(game))
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    else:
                        invocation.return_dbus_error(
                            "com.intergenos.InterGen.Error",
                            f"Unknown method: {method_name}",
                        )
                except Exception as e:
                    # F5: don't leak internal details (file paths, library
                    # exception messages, stack snippets) to whichever local
                    # process called the bus. Full exception with traceback
                    # goes to the daemon log; caller sees only a sanitized
                    # generic error string.
                    log.error(
                        "D-Bus method call error in %s from %s: %s",
                        method_name, sender, e, exc_info=True,
                    )
                    invocation.return_dbus_error(
                        "com.intergenos.InterGen.Error",
                        "Internal error — check daemon logs for details.",
                    )

            # register_object_with_closures2, not register_object: the
            # convenience wrapper rides GLib's deprecated closures variant,
            # whose invocation-reference contract was corrected in the "2"
            # entry point. Same signature; callables auto-wrap to closures.
            self._reg_id = self._bus.register_object_with_closures2(
                OBJECT_PATH,
                self._node_info.interfaces[0],
                on_method_call,
                None,  # get_property
                None,  # set_property
            )

            # Own the bus name
            self._owner_id = Gio.bus_own_name_on_connection(
                self._bus,
                SERVICE_NAME,
                Gio.BusNameOwnerFlags.NONE,
                None,  # name_acquired
                None,  # name_lost
            )

            log.info("D-Bus interface exported: %s at %s (via Gio)",
                     SERVICE_NAME, OBJECT_PATH)

        except Exception as e:
            log.warning("D-Bus export failed: %s. Running without D-Bus.", e)
            # Release whatever this attempt DID take before giving up. A partial
            # export — object registered, then owning the name raised — leaves
            # the path exported on the shared connection, and merely clearing
            # the identifier here would strand it AND throw away the only handle
            # that could release it, reproducing the already-exported failure on
            # the next start with no way back.
            self._teardown_dbus()


def main(argv: list[str] | None = None) -> None:
    """Entry point for the InterGen daemon.

    argv: daemon arguments only, WITHOUT any wrapper subcommand word. The
    packaged unit launches ``/usr/bin/intergen daemon``, and the CLI dispatch
    must pass ``sys.argv[2:]`` here — parsing the raw process argv would see
    the ``daemon`` subcommand itself and reject it (the ge9b r111 start
    failure). None means this module IS the entry point (``python -m
    intergen.dbus_daemon``) and argparse reads sys.argv as usual.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(
        prog="intergen-daemon",
        description="InterGen assistant daemon (D-Bus session service).",
    )
    # The ONLY arming channel for the eval-consent deny-and-record responder. It
    # is on this process's own command line, so it is tied to one deliberate
    # launch: the shipped service unit does not carry it, and a production daemon
    # therefore cannot pick it up. Absent flag = production consent behavior.
    parser.add_argument(
        "--eval-consent-deny",
        action="store_true",
        help=("Unattended-baseline mode: answer every consent gate with an "
              "immediate recorded DENY instead of raising a modal. Answers the "
              "consent question only — the safety denylist and dispatch lockdown "
              "are unaffected, and no verdict other than deny can be produced. "
              "Never use on a production daemon."),
    )
    args = parser.parse_args(argv)

    # Bring an EXISTING home up to owner-only before anything opens a file in
    # it. New files are created 0600/0700 by intergen.private_state, but that
    # only runs when a file does not yet exist — a home created by an earlier
    # release still holds 0755 directories and 0644 transcripts, and nothing
    # else would ever correct them. Runs before the daemon is constructed so
    # the pass never races a writer.
    #
    # ONCE PER HOME, not once per start: the call is made every time, and the
    # function itself decides, from a marker it left in the state directory.
    # After the first pass anything loose in those directories is something the
    # user or another program put there since, and re-tightening it at every
    # start would silently reverse a sharing decision that is theirs to make.
    # It reports what it actually did — the paths it changed, by name, and
    # named failures when it could not.
    private_state.harden_user_state_at_startup()

    daemon = InterGenDaemon(
        eval_consent_marker=(
            eval_consent.ARM_MARKER if args.eval_consent_deny else None
        ),
    )

    # The Gio-exported D-Bus interface dispatches method calls only while a
    # GLib main loop is running; without it the daemon initializes and exits
    # immediately. GLib is a hard runtime dependency of the D-Bus daemon — if
    # it is unavailable the daemon cannot serve anything, so fail loudly.
    try:
        from gi.repository import GLib
    except Exception as e:  # pragma: no cover - gi is a hard runtime dep
        log.error("GLib unavailable (%s); the D-Bus daemon cannot run.", e)
        daemon.stop_service()
        sys.exit(1)

    loop = GLib.MainLoop()

    # Handle signals for clean shutdown. Use a GLib-native unix signal source
    # (resolved below) rather than signal.signal: a Python-level signal handler
    # does not run promptly while
    # GLib.MainLoop is blocked in its C poll() (the signal interrupts poll()
    # with EINTR, GLib retries in C without returning to the CPython eval loop),
    # so a signal.signal-based loop.quit() can go unserviced until an unrelated
    # loop event arrives. For a mostly-idle D-Bus daemon that means `systemctl
    # stop` hangs to its SIGKILL fallback. unix_signal_add registers a GLib-
    # native signal source, so the handler runs as a loop callback and quits
    # reliably. The handler does minimal work (quit the loop); teardown runs
    # after loop.run() returns. Returning SOURCE_REMOVE tears the source down
    # after it fires (we are quitting anyway).
    def shutdown_handler(*_args: object) -> bool:
        log.info("Received shutdown signal, shutting down")
        loop.quit()
        return GLib.SOURCE_REMOVE

    # Resolve the GLib unix-signal registration function. GLib 2.80+ moved the
    # glib-unix introspected API out of the GLib-2.0 namespace into a separate
    # GLibUnix-2.0 namespace (g-ir-scanner --symbol-prefix=g_unix), so on
    # current glib `GLib.unix_signal_add` no longer exists *via introspection*
    # even though the C symbol `g_unix_signal_add` is still exported by
    # libglib-2.0.so.0. Prefer the legacy GLib name when present (older glib);
    # otherwise use GLibUnix.signal_add. Same signature + behaviour either way.
    unix_signal_add = getattr(GLib, "unix_signal_add", None)
    if unix_signal_add is None:
        try:
            import gi

            gi.require_version("GLibUnix", "2.0")
            from gi.repository import GLibUnix

            unix_signal_add = GLibUnix.signal_add
        except (ImportError, ValueError) as e:  # pragma: no cover
            log.error(
                "no glib-unix signal API available (%s); the D-Bus daemon "
                "cannot register a GLib-native signal source.", e,
            )
            daemon.stop_service()
            sys.exit(1)

    unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown_handler)
    unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown_handler)

    daemon.start_service()

    # Single-instance guard tripped: this process did not confirm sole ownership
    # of the bus name, so start_service bound nothing (no llama-server, no port).
    # Exit before the main loop. A benign duplicate (another owner) exits 0; a
    # fail-closed verify fault (session bus unreachable / gi missing) exits
    # non-zero + loud so systemd's Restart=on-failure retries rather than
    # reporting a masked success.
    if daemon._duplicate_instance:
        if daemon._bus_verify_failed:
            log.critical("Exiting (fail-closed): could not verify sole D-Bus "
                         "ownership of %s; no resource was bound. systemd will "
                         "retry per Restart=on-failure.", SERVICE_NAME)
            sys.exit(1)
        log.info("Exiting: duplicate InterGen daemon launch (bus name already "
                 "owned). The existing instance continues serving.")
        sys.exit(0)

    log.info("InterGen daemon initialized. D-Bus service: %s", SERVICE_NAME)
    log.info("Status: %s", daemon.status())

    # Block here, servicing D-Bus method calls, until a signal quits the loop.
    log.info("Entering main loop.")
    try:
        loop.run()
    finally:
        daemon.stop_service()
        log.info("InterGen daemon stopped.")


if __name__ == "__main__":
    main()
