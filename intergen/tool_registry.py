# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen tool registry — discovers, registers, and executes tools.

Ported from a prior internal AI assistant project. Simplified: tools are class-based
(BaseTool subclasses) rather than module-based, and safety classification
is built into each tool.

D-008 RFC v1.0 dispatcher gate integration:
every execute() call passes through `intergen.provenance.verify_tool_call`
before the tool runs. The gate enforces RFC §3 provenance taxonomy,
§5.1 ingress-tool watermark escalation, §5.3 no-fallback policy,
§6 tool risk classification, and the §7 review modal handoff via the
`review_callback` keyword argument. RFC §9 audit log is written on every
dispatch decision.

I-027 closure: the prior path classified SafetyTier.CONFIRM but never
enforced it (the gate did not exist). With this commit, CONFIRM-equivalent
behavior is delivered by the gate's hold_for_review action on the
behavior matrix. The classify_safety surface is preserved for the BLOCKED
tier (which remains a tool-level refusal independent of provenance).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import stat as stat_module
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from importlib import import_module
from typing import Any, Callable

# Canonical pkexec runner path — installed by packages/ai/intergen/build.sh
# from intergen/data/intergen-privileged-runner per the build-system
# coordinator's 49a585ca T0-4-E integration pkexec gate artifacts. The
# constant lives here so the runtime import contract is colocated with
# the dispatcher that invokes it.
_PKEXEC_RUNNER_PATH = "/usr/bin/intergen-privileged-runner"

# The tool that asks this account's own systemd user manager to start a
# short-lived unit. Decided 2026-08-24: the privileged runner is started this
# way rather than as a direct child of the daemon. The daemon's unit sets
# NoNewPrivileges=yes, which is inherited by every child and cannot be cleared,
# and which makes the kernel ignore pkexec's setuid bit — pkexec then refuses
# before PolicyKit is ever contacted. The user manager does not run under that
# flag, so a unit it starts begins from the manager's own context. Nothing about
# the daemon's hardening changes; only who starts the runner does.
_SYSTEMD_RUN = "systemd-run"

# The user manager's private socket. Its presence is a direct, cheap and
# side-effect-free measurement of "a systemd user manager is running for this
# account" — used so a dispatch that fails because no manager is reachable can
# SAY so rather than blame the runner.
_USER_MANAGER_SOCKET_RELPATH = os.path.join("systemd", "private")

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolCall, ToolResult, ToolSchema
from intergen.interfaces.provenance import (
    INGRESS_TOOLS_V1,
    ConversationTrustState,
    DispatchDecision,
    IngressTracker,
    Provenance,
    ToolRiskTier,
    UserDecision,
)
from intergen.provenance import (
    build_audit_record,
    record_user_decision,
    verify_tool_call,
)
from intergen.audit_log import write_record
from intergen.trace import get_tracer
from intergen.dispatch_token import (
    DispatchTokenError,
    mint_token,
)
from intergen.spotlighting import is_wrapped, wrap_ingress_content
from intergen import privileged_request
from intergen.interfaces.scanner import (
    ScanContext,
    ScanDirection,
    ScanDisposition,
)
from intergen.scanner.policy import ScannerPolicy

logger = logging.getLogger(__name__)


# Tools whose semantics are PRIVILEGED_STATE_CHANGING per RFC §6.
# Discovered by tool name; the registry uses this for the gate's
# tool_risk_tier argument. Tools NOT in this set are classified based on
# their own SafetyTier (AUTO -> READ_ONLY, BLOCKED -> rejected before gate
# entry; CONFIRM -> USER_SCOPE_STATE_CHANGING).
_PRIVILEGED_TOOLS: frozenset[str] = frozenset({
    # State-changing system tools that escalate via pkexec per D-007 Option A
    "manage_services",  # systemctl start/stop/restart on system units
    "manage_packages",  # pkm install/remove (root-level package state)
    "run_command",      # arbitrary shell command (may be privileged)
    "write_file",       # may target system paths (/etc/, /usr/, etc.)
})


def _classify_risk_tier(
    tool: BaseTool | None,
    arguments: dict[str, Any],
    tool_name: str,
) -> ToolRiskTier:
    """Map SafetyTier + tool-name to ToolRiskTier per RFC §6.

    PRIVILEGED_STATE_CHANGING wins over SafetyTier for tools that escalate
    via pkexec; SafetyTier.CONFIRM maps to USER_SCOPE_STATE_CHANGING
    (the gate's hold-for-review path handles what classify_safety
    historically called CONFIRM); SafetyTier.AUTO maps to READ_ONLY;
    BLOCKED tier never reaches this helper (rejected before gate entry).

    A genuinely READ-ONLY action stays READ_ONLY even on a tool in
    _PRIVILEGED_TOOLS. Reading your own machine's state — `systemctl status`,
    `pkm search`, `ls` — changes nothing, escalates nothing, and must remain
    freely available: a person is always allowed to inspect their own system.
    Gating a read behind the same elevation as a `restart` makes InterGen
    refuse benign questions and is the opposite of putting the user in control.
    Trusting AUTO here is safe by construction: every tool derives AUTO from an
    explicit, audited read-only allowlist (manage_services AUTO_ACTIONS,
    manage_packages AUTO_SUBCOMMANDS, run_command's _AUTO_COMMANDS_* with
    unknown→CONFIRM and destructive/shell-spawn→BLOCKED). Only state-changing
    actions (CONFIRM) reach the privileged/escalation path below.
    """
    if tool is None:
        # External handler or unknown — can't introspect the action. A known
        # privileged name is privileged; otherwise the safe default is user-scope.
        return (ToolRiskTier.PRIVILEGED_STATE_CHANGING
                if tool_name in _PRIVILEGED_TOOLS
                else ToolRiskTier.USER_SCOPE_STATE_CHANGING)
    safety = tool.classify_safety(arguments)
    if safety == SafetyTier.AUTO:
        return ToolRiskTier.READ_ONLY
    if tool_name in _PRIVILEGED_TOOLS:
        return ToolRiskTier.PRIVILEGED_STATE_CHANGING
    if safety == SafetyTier.CONFIRM:
        return ToolRiskTier.USER_SCOPE_STATE_CHANGING
    return ToolRiskTier.READ_ONLY


# ── User-language translation of the computed classification (2026-07-14) ────
#
# A review card / block message / honest handoff is a TRUST SURFACE: it must
# describe what the system actually classified, in the user's language — never
# the raw enum label "privileged_state_changing". This is the SINGLE source for
# that translation, consumed by the in-web review card, the desktop card, and
# the no-surface honest handoff, so every interface speaks one voice. The card's
# administrator-password footer is separate (tier_needs_admin gates it); this is
# only the "what kind of change is this" line.

def classification_sentence(tier: "ToolRiskTier") -> str:
    """Translate a computed ToolRiskTier into ONE user-language sentence.

    Never returns the raw label. Read-only actions do not gate, so their line is
    for completeness only; the two state-changing tiers are what a card or a
    handoff actually surfaces."""
    if tier is ToolRiskTier.PRIVILEGED_STATE_CHANGING:
        return ("This changes system software or settings and needs "
                "administrator approval.")
    if tier is ToolRiskTier.USER_SCOPE_STATE_CHANGING:
        return "This makes a change within your own account or session."
    return "This only reads information from your system and changes nothing."


def tier_needs_admin(tier: "ToolRiskTier") -> bool:
    """True when the tier will trigger the OS administrator (polkit/pkexec)
    prompt — the card footer and the handoff use this to state the boundary."""
    return tier is ToolRiskTier.PRIVILEGED_STATE_CHANGING


def honest_handoff_message(what: str, command: str, needs_admin: bool) -> str:
    """The 3-part honest handoff (the honest-handoff design), shown when a state-changing action
    cannot be carried out in this window — the user declined, or there is no way
    to collect approval on this surface.

    Decided 2026-07-14: this is an advisory, not an error — surfacing a
    constraint the assistant cannot circumvent, honestly, reinforces trust:
    (1) name the action in the user's own terms; (2) say, in plain language, why
    it can't proceed here — NEVER "blocked", "safety layer", or any gate/tool
    name; (3) hand over the exact command to run. Same single source for the web
    card's no-surface/deny handoff and the router's offer-accept deny path, so
    every interface advises with one voice.

    `what` is the plain-language action sentence (from the card translator);
    `command` is the concrete command; `needs_admin` states whether the OS
    administrator prompt is the real authorization boundary."""
    what = (what or "").strip()
    command = (command or "").strip()
    if needs_admin:
        why = ("This needs administrator approval, which I can't collect from "
               "here.")
        # Prefix sudo only when there is a real command — an empty command must
        # never render a bare "`sudo`".
        run_cmd = "" if not command else (
            command if command.startswith("sudo ") else f"sudo {command}")
    else:
        why = "I'm not able to make that change from here."
        run_cmd = command
    if what:
        if not what.endswith((".", "!", "?")):
            what = what + "."
        msg = f"{what} {why}"
    else:
        msg = why
    if run_cmd.strip():
        msg += ("\n\nYou can do it yourself in a terminal or the desktop app:\n\n"
                f"`{run_cmd.strip()}`")
    return msg


def _ingress_source_attribution(
    tool_name: str, arguments: dict[str, Any]
) -> tuple[str, str]:
    """Derive (source, source_type) for an ingress tool's spotlight wrapper.

    AI-2 (audit 2026-05-29): the source mapping lives in ONE central place keyed
    off the same INGRESS_TOOLS_V1 set that drives the §5.1 watermark — NOT in
    each tool (per-tool wrapping is what let the watch-list drift). The source
    string is surfaced in the review modal when ingress content motivates a held
    action; source_type tags the wrapper marker.
    """
    if tool_name in ("read_file", "analyze_file"):
        return (str(arguments.get("path", "")), "file")
    if tool_name == "web_search":
        return (str(arguments.get("query", "")), "web_search")
    if tool_name == "take_screenshot":
        return ("screen capture", "screenshot")
    return (tool_name, "untrusted")


# Sentinel build seq step 3 — on-the-fly scan of every external/MCP interaction.
# These helpers feed the ScannerPolicy at the dispatch chokepoint (design plan §6).
def _scan_surface(tool_name: str, arguments: dict[str, Any], *, external: bool) -> str:
    """Human/audit-readable surface label for a ScanContext.

    External/MCP tools register as `mcp_<server>_<tool>` (mcp_client.py); the
    rest of the external set keeps its own name. Built-in ingress tools map to
    their file/web source so an egress/ingress verdict points at WHERE the
    content crossed the boundary.
    """
    if external:
        if tool_name.startswith("mcp_"):
            return f"mcp:{tool_name[len('mcp_'):]}"
        return f"external:{tool_name}"
    source, source_type = _ingress_source_attribution(tool_name, arguments)
    return f"{source_type}:{source}" if source else source_type


def _egress_payload(arguments: dict[str, Any]) -> str:
    """Serialize a call's arguments for an EGRESS (args-leaving) scan.

    default=str so a non-JSON-native value (Path, bytes-repr, etc.) cannot
    raise and silently skip the scan — the scanner sees a faithful string.
    """
    try:
        return json.dumps(arguments, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)


def _withheld_notice(tool_name: str, reason: str) -> str:
    """The notice the LLM receives in place of content Sentinel withheld.

    Decision #2: a flagged result is DROPPED — the model is told the content
    was withheld (and why, briefly) rather than fed the poison it would have
    otherwise re-entered context as.
    """
    return (
        f"[Sentinel withheld the result of '{tool_name}': the returned content was "
        f"flagged as unsafe to re-enter context ({reason or 'policy'}). "
        "The content was NOT delivered. Do not infer its contents; ask the user "
        "how to proceed if this result is needed."
    )


class ToolRegistry:
    """Discovers, registers, and dispatches tool calls."""

    def __init__(self, scanner_policy: ScannerPolicy | None = None):
        self._tools: dict[str, BaseTool] = {}
        self._external_handlers: dict[str, Callable] = {}
        self._external_rules: dict[str, str] = {}
        self._ready = False
        # Sentinel scanner (design plan §6). The production wiring
        # (dbus_daemon) constructs the always-on policy and injects it; None
        # here means scanning is inactive (headless tooling / unit tests that
        # exercise dispatch without the scan layer). The chokepoint never
        # silently allows when a policy IS present — it fails toward the human
        # modal / withhold on FLAG/BLOCK (HG #10).
        self._scanner: ScannerPolicy | None = scanner_policy
        # Read-through cache for AUTO read-only calls (perceived-latency design):
        # serves repeated identical reads instantly, skipping the system call AND
        # the LLM re-synthesis (the cached ToolResult carries its model_summary).
        # Per-user keyed, short TTLs, invalidated by successful state-changing
        # calls. Only the tool layer populates it (Zone 3).
        from intergen.tool_cache import ToolCache
        self._cache = ToolCache()
        # Per-tool invocation tally (Usage tab → Top Tools). Plain dict to avoid
        # a new import; dispatch is single-threaded so no lock is needed.
        self._tool_call_counts: dict[str, int] = {}
        # DISPATCH LOCKDOWN structural backstop (WC lockdown red-team guard #2):
        # when True, get_tool_schemas() returns [] so the MODEL is never offered
        # tools on ANY surface (router P3, the WS streaming generate, the prompt-
        # cache warmup) and therefore cannot emit a ToolCall at all. This is the
        # second, independent guard behind the router's lock_dispatch gates — a
        # future model-facing surface that forgets the source/lock check still
        # cannot leak dispatch to the model. Defaults OFF (no behavior change for
        # tests / non-daemon callers); the daemon sets it from the tier resolver.
        # CODE-OWNED dispatch is UNAFFECTED — get_tool()/execute() (the P1/P2 +
        # route-to-tools + staged-command paths) never consult get_tool_schemas().
        self._tool_offering_locked = False

    def set_tool_offering_locked(self, locked: bool) -> None:
        """Lock/unlock model-facing tool offering (the dispatch-lockdown backstop).

        When locked, get_tool_schemas() returns [] so the model is structurally
        never offered tools. Code-owned dispatch (get_tool/execute) is unaffected.
        """
        self._tool_offering_locked = locked

    def get_tool_call_counts(self) -> dict[str, int]:
        """Cumulative per-tool invocation counts since daemon start."""
        return dict(self._tool_call_counts)

    def discover_tools(self, tools_dir: Path | None = None) -> int:
        """Auto-discover BaseTool subclasses in the tools directory.

        Scans intergen/tools/*.py for classes that subclass BaseTool.
        Each module should define a class that can be instantiated with no args.
        """
        if tools_dir is None:
            tools_dir = Path(__file__).parent / "tools"

        if not tools_dir.exists():
            logger.warning("Tools directory does not exist: %s", tools_dir)
            return 0

        count = 0
        for path in sorted(tools_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"intergen.tools.{path.stem}"
            try:
                mod = import_module(mod_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseTool)
                            and attr is not BaseTool):
                        tool = attr()
                        self.register(tool)
                        count += 1
            except Exception as e:
                logger.error("Failed to load tool module %s: %s", mod_name, e)

        self._ready = True
        logger.info("Tool registry ready — %d tools discovered", count)
        return count

    def attach_deep_scanner(self, scanner) -> bool:
        """Attach the deep-scan tier (LocalQwen / cloud) to the active policy.

        The wiring layer (dbus_daemon) calls this once the deep scanner is
        constructed, so a floor FLAG can escalate to the semantic tier (and
        depth=deep can always escalate). No-op returning False when scanning is
        inactive (no policy injected) — the floor still holds at the chokepoint.
        """
        if self._scanner is None:
            return False
        self._scanner.set_deep_scanner(scanner)
        return True

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning("Tool %s already registered — overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def register_external(self, name: str, schema: ToolSchema,
                          handler: Callable[[dict], str],
                          system_prompt_rule: str) -> None:
        """Register a tool from an external source (e.g., MCP server)."""
        self._external_handlers[name] = handler
        self._external_rules[name] = system_prompt_rule
        logger.info("Registered external tool: %s", name)

    def execute(
        self,
        call: ToolCall,
        *,
        ingress_tracker: IngressTracker | None = None,
        trust_state: ConversationTrustState | None = None,
        source_attribution: str = "",
        excerpt: str = "",
        review_callback: Callable[[ToolCall, DispatchDecision], str] | None = None,
    ) -> ToolResult:
        """Execute a tool call, wrapped in a ``tool.execute`` decision span.

        The span (a no-op unless ``INTERGEN_TRACE`` is on) gives the decision-
        trace harness a per-tool record on the path: the tool name, its argument
        shape (content-gated — args only land under INTERGEN_TRACE_CONTENT, with
        credential-shaped keys redacted), and the fired outcome
        (success/executed/blocked), nested under whatever routing span is active.
        A nested ``tool.gate`` span inside :meth:`_execute_impl` records the
        provenance-gate verdict. All dispatch logic lives in ``_execute_impl``;
        this wrapper only observes, so tracing changes no dispatch behavior.
        """
        with get_tracer().span("tool.execute", kind="tool") as _tspan:
            _tspan.set_attribute("tool_name", call.name)
            _tspan.set_content("tool_args", call.arguments)
            result = self._execute_impl(
                call,
                ingress_tracker=ingress_tracker,
                trust_state=trust_state,
                source_attribution=source_attribution,
                excerpt=excerpt,
                review_callback=review_callback,
            )
            _tspan.set_attribute("success", result.success)
            _tspan.set_attribute("executed", getattr(result, "executed", True))
            _tspan.set_attribute("blocked", getattr(result, "blocked", False))
            return result

    def _execute_impl(
        self,
        call: ToolCall,
        *,
        ingress_tracker: IngressTracker | None = None,
        trust_state: ConversationTrustState | None = None,
        source_attribution: str = "",
        excerpt: str = "",
        review_callback: Callable[[ToolCall, DispatchDecision], str] | None = None,
    ) -> ToolResult:
        """Execute a tool call through the D-008 provenance dispatcher gate.

        Per RFC docs/architecture/intergen-provenance-gate-design.md:
        every tool call passes through `verify_tool_call` before execution.

        Args:
            call: the LabelledToolCall (must have non-None
                source_of_request per §5.3; enforced by
                ToolCall.__post_init__).
            ingress_tracker: per-turn ingress-fire tracker. If None, a fresh
                empty tracker is used — appropriate for direct CLI
                invocations where no ingress context exists.
            trust_state: per-conversation symmetric allow/deny memory.
                If None, a fresh empty state is used.
            source_attribution: short label identifying the ingress source
                that motivated this call (URL / file path / etc.). Empty
                string for user-direct calls with no ingress context.
            excerpt: optional snippet of ingress content that motivated
                this call; surfaced in the review modal + audit log.
            review_callback: invoked when dispatch returns hold_for_review.
                Receives (call, decision) and must return one of
                "allow_once" | "allow_conversation" | "deny" |
                "deny_conversation". TWO-LAYER timeout architecture:

                - review_callback=None at THIS registry boundary means
                  IMMEDIATE deny (no-UI-available context — headless test
                  contexts, boot phase, automated tooling). The registry
                  does NOT itself wait for a UI to appear; it refuses on
                  the spot so a no-UI path cannot silently execute a
                  held action.

                - When a callback IS provided (the router constructs one
                  via intergen.review_modal.make_review_callback), the
                  RFC §7.2 one-hour-implicit-deny semantic lives INSIDE
                  review_modal.py: the libnotify-fallback path posts a
                  critical notification + polls _session_active in 5s
                  intervals up to FALLBACK_TIMEOUT_SECONDS=3600 + re-
                  prompts via zenity on session-return + implicit-denies
                  on timeout. The 1-hour promise is preserved through
                  review_modal.py, not through tool_registry.

        Returns:
            ToolResult with success=True on completed dispatch + tool
            success; success=False on gate-refusal, user-deny, validation
            failure, or tool exception. The result.content includes a
            human-readable reason on the failure paths.
        """
        t0 = time.monotonic()
        tool_name = call.name
        arguments = call.arguments

        if ingress_tracker is None:
            ingress_tracker = IngressTracker()
        if trust_state is None:
            trust_state = ConversationTrustState()

        tool = self._tools.get(tool_name)
        external_handler = self._external_handlers.get(tool_name)
        if tool is None and external_handler is None:
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=f"Unknown tool: {tool_name}",
                success=False,
                executed=False,
            )

        # Per-tool usage tally for the Usage tab's Top Tools chart (real tool
        # invocations only — counted after the unknown-tool guard above).
        self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1

        # Argument validation (tool-side) runs before the gate so a malformed
        # call does not waste a review-modal cycle.
        if tool is not None:
            validation_error = tool.validate_arguments(arguments)
            if validation_error:
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Validation error: {validation_error}",
                    success=False,
                    executed=False,
                )

            # BLOCKED tier remains a tool-level refusal independent of
            # provenance — does not escalate regardless of declared source.
            safety = tool.classify_safety(arguments)
            if safety == SafetyTier.BLOCKED:
                from intergen.safety import get_blocked_response
                cmd = arguments.get("command", str(arguments))
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=get_blocked_response(cmd),
                    success=False,
                    # Safety block — surface it like run_command's so the router
                    # synth-skips it (deterministic refusal, never narrated).
                    blocked=True,
                    executed=False,
                )

        # Classify tool risk tier + fire the dispatcher gate. The gate verdict
        # is the decision-trace's provenance seam (06-11 harness plan item 3):
        # a ``tool.gate`` span records the gate ACTION (execute/hold/reject),
        # the effective provenance after watermark escalation, and the risk
        # tier, so the harness can see WHY a tool did or didn't fire — not just
        # that it did. No-op unless INTERGEN_TRACE is on; the gate reason is
        # content-gated (it can quote a source attribution).
        risk_tier = _classify_risk_tier(tool, arguments, tool_name)
        with get_tracer().span("tool.gate", kind="gate") as _gspan:
            decision = verify_tool_call(
                call,
                ingress_tracker,
                trust_state,
                risk_tier,
                source_attribution,
            )
            _gspan.set_attribute("gate_action", decision.action)
            _gspan.set_attribute("risk_tier",
                                 getattr(risk_tier, "value", str(risk_tier)))
            _gspan.set_attribute("effective_provenance",
                                 getattr(decision.effective_provenance, "value",
                                         str(decision.effective_provenance)))
            _gspan.set_attribute("needs_pkexec", decision.needs_pkexec)
            _gspan.set_content("gate_reason", decision.reason)

        # Reject path — gate refused (missing source_of_request, prior
        # deny-conversation, or schema violation).
        if decision.action == "reject":
            self._audit_log_decision(
                call, decision, ingress_tracker,
                user_outcome="denied",
                exit_code=1,
                result_summary=decision.reason,
                source_attribution=source_attribution,
                excerpt=excerpt,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=f"Tool call refused by dispatcher: {decision.reason}",
                success=False,
                executed=False,
            )

        # Review path — route to review_callback (the zenity/libnotify modal
        # in production; None during headless test contexts or boot phases
        # where the modal is not yet wired).
        #
        # AI-6 (option iii, decided 2026-05-29): the PRIVILEGED tier
        # ALWAYS requires a human review-modal approval, independent of the
        # gate's provenance decision. The gate is doubly inert today (per-turn
        # taint reset + a mis-specified ingress watch-list) and therefore cannot
        # be trusted to decide which privileged calls a human sees — so every
        # privileged action prompts ("friction by design" = the HG price). A
        # human approval is then bound to THIS exact call by a freshly minted,
        # single-use dispatch token that the root boundary verifies.
        # Non-privileged calls keep the gate's semantics: prompt only on
        # hold_for_review.
        # EGRESS scan (design plan §6 step 5 + decision #6 scan-on-derivation):
        # before an external/MCP tool dispatches, scan its outbound arguments for
        # exfil (secret/credential shapes, exfil URLs) so the model cannot be
        # tricked into leaking data through a tool's args. Per decision #6 the
        # INITIAL user-authorized egress (USER_DIRECT — the human directly named
        # it, reviewed via show-before-send) is trusted at source and NOT
        # auto-scanned; everything the model DERIVED (user_implied / ingress_
        # derived) is inspected. Ingress (results coming back) is always scanned
        # regardless of provenance — content arriving from outside is never
        # user-authorized. BLOCK refuses before the args leave the machine; FLAG
        # folds into the ONE consolidated human modal (decision #1) below.
        egress_flag_reason: str | None = None
        if (
            external_handler is not None
            and self._scanner is not None
            and decision.effective_provenance is not Provenance.USER_DIRECT
        ):
            egress_verdict = self._scanner.scan(
                _egress_payload(arguments),
                ScanContext(
                    surface=_scan_surface(tool_name, arguments, external=True),
                    direction=ScanDirection.EGRESS,
                    tool_name=tool_name,
                    trust_tier=decision.effective_provenance.value,
                ),
            )
            if egress_verdict.disposition is ScanDisposition.BLOCK:
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome="egress_blocked",
                    exit_code=1,
                    result_summary=f"Sentinel egress-scan BLOCK: {egress_verdict.reason}",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        "Tool call refused: Sentinel blocked the outbound content "
                        f"before it left the machine ({egress_verdict.reason or 'exfil risk'})."
                    ),
                    success=False,
                    executed=False,
                )
            if egress_verdict.disposition is ScanDisposition.FLAG:
                egress_flag_reason = egress_verdict.reason or "outbound content flagged"

        user_outcome = "executed"
        dispatch_token_value: str | None = None
        privileged = decision.needs_pkexec
        must_review = (
            privileged
            or decision.action == "hold_for_review"
            or egress_flag_reason is not None
        )
        if must_review:
            if review_callback is None:
                # Per RFC §7.2: no review UI implies implicit refusal rather
                # than silent execute. The gate's job is intent verification;
                # without a UI to verify, the safe behavior is refusal.
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome="denied",
                    exit_code=1,
                    result_summary="No review UI available; implicit refusal",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        "Tool call held for user review but no review UI is "
                        f"available in this context. Reason: {decision.reason}"
                    ),
                    success=False,
                    executed=False,
                )

            # Decision #1 — ONE consolidated modal: when the gate AND the egress
            # scan both want sign-off on the same call, the human sees a single
            # prompt listing both reasons, one allow/deny. Fold the scan reason
            # into the decision the modal renders (its reason field is what
            # review_modal surfaces); default-deny is preserved by the modal.
            review_decision = decision
            if egress_flag_reason is not None:
                scan_note = f"Sentinel egress scan flagged outbound content: {egress_flag_reason}"
                review_decision = replace(
                    decision,
                    reason=(f"{decision.reason} | {scan_note}" if decision.reason else scan_note),
                )
            user_choice = review_callback(call, review_decision)
            if user_choice in ("deny", "deny_conversation"):
                if user_choice == "deny_conversation":
                    record_user_decision(
                        UserDecision.now_utc(user_choice),
                        call, trust_state, source_attribution,
                    )
                self._audit_log_decision(
                    call, decision, ingress_tracker,
                    user_outcome=(
                        "denied" if user_choice == "deny" else "deny_conversation"
                    ),
                    exit_code=1,
                    result_summary="User denied via review modal",
                    source_attribution=source_attribution,
                    excerpt=excerpt,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content="Tool call denied by user via review modal.",
                    success=False,
                    executed=False,
                )
            # Approved (allow_once / allow_conversation) — proceed to execution.
            if privileged:
                # No allow-for-conversation caching for the privileged tier
                # (closes the AI-14 cache landmine): each privileged action is a
                # FRESH human approval + a FRESH single-use token. An
                # allow_conversation choice is honored as allow_once and is
                # NEVER recorded to trust_state, so a prior conversation-allow
                # can never auto-satisfy a later privileged dispatch.
                user_outcome = "allowed_once"
                try:
                    dispatch_token_value = mint_token(
                        tool_name, arguments, os.getuid(),
                    )
                except DispatchTokenError as exc:
                    # The human approved, but without a verifiable token the root
                    # boundary cannot confirm that approval — fail CLOSED rather
                    # than dispatch an unbacked privileged action.
                    logger.error(
                        "dispatch-token mint failed for %s: %s", tool_name, exc
                    )
                    self._audit_log_decision(
                        call, decision, ingress_tracker,
                        user_outcome="denied",
                        exit_code=1,
                        result_summary=f"dispatch-token mint failed: {exc}",
                        source_attribution=source_attribution,
                        excerpt=excerpt,
                    )
                    return ToolResult(
                        call_id=call.call_id,
                        name=tool_name,
                        content=(
                            "Privileged action approved, but the approval token "
                            "could not be minted (signing key missing or "
                            "unreadable); refusing dispatch. Run 'intergen setup' "
                            "to (re)provision the dispatch key."
                        ),
                        success=False,
                        executed=False,
                    )
            elif user_choice == "allow_conversation":
                record_user_decision(
                    UserDecision.now_utc(user_choice),
                    call, trust_state, source_attribution,
                )
                user_outcome = "allowed_conversation"
            else:
                user_outcome = "allowed_once"

        # Execute path — dispatch to tool or external handler.
        # When decision.needs_pkexec is True AND the tool is a built-in
        # (external handlers cannot route through pkexec — they live in
        # a separate trust domain and have their own authentication
        # surface), route through the pkexec runner per the build-system
        # coordinator's 49a585ca integration contract. Otherwise direct
        # tool.execute() in the user context. Per RFC §6 line 161 the
        # provenance gate (intent) ran upstream of this point and
        # PolicyKit (authentication) runs at pkexec invocation; both
        # fire for privileged operations.
        if tool is not None:
            try:
                if decision.needs_pkexec:
                    result = self._dispatch_via_pkexec(
                        call, tool_name, arguments, dispatch_token_value,
                    )
                else:
                    # Read-through cache for AUTO read-only calls: a fresh hit
                    # skips the system call (and downstream re-synthesis). Privileged
                    # (pkexec) calls are never cached — they take the branch above.
                    _uid = os.getuid()
                    result = self._cache.get(tool_name, arguments, _uid)
                    if result is None:
                        result = tool.execute(arguments)
                        self._cache.put(tool_name, arguments, _uid, result)
                exit_code = 0 if result.success else 1
                # Invalidate cached reads after a successful state-changing call
                # (no-op for read-only tools). Covers both dispatch paths above.
                if result.success:
                    self._cache.invalidate_for_write(
                        tool_name, arguments, os.getuid())
            except Exception as e:  # noqa: BLE001 — wrap into ToolResult
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.error(
                    "Tool %s failed in %.0fms: %s", tool_name, elapsed_ms, e
                )
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Error executing {tool_name}: {e}",
                    success=False,
                )
                exit_code = 1
        else:
            # External handler dispatch (MCP / cloud / etc.)
            try:
                result_text = external_handler(arguments)  # type: ignore[misc]
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=result_text,
                    success=True,
                )
                exit_code = 0
            except Exception as e:  # noqa: BLE001
                logger.error("External tool %s failed: %s", tool_name, e)
                result = ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"Error: {e}",
                    success=False,
                )
                exit_code = 1

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug(
            "Tool %s completed in %.0fms (%d chars)",
            tool_name, elapsed_ms, len(result.content),
        )

        # INGRESS scan (design plan §6 step 10 + decision #2): before a result
        # from an external/MCP tool OR an INGRESS_TOOLS_V1 tool re-enters the LLM
        # context, scan it for injection content. ALWAYS scanned regardless of
        # provenance — content arriving from outside is never user-authorized.
        #   BLOCK -> withhold: the model gets a "result withheld" notice, never
        #            the poison (decision #2, high-confidence drop).
        #   FLAG  -> the human modal (decision #2, medium-confidence); a deny or
        #            a no-UI context fails closed to withhold (HG #10).
        # Runs BEFORE the audit write so the audit reflects the withhold outcome;
        # the verdict reason (not the raw poison) is what lands in the log.
        # External/MCP content is scanned whenever it will re-enter context —
        # success OR failure (defense-in-depth, peer-review hardening). The router feeds
        # result.content back to the LLM unconditionally, so a handler that returns
        # FAILURE carrying outside-influenced text must not bypass the scan: the
        # stated guarantee ("content from outside is ALWAYS scanned regardless of
        # provenance") has to hold on the failure path too. Built-in INGRESS_TOOLS_V1
        # results keep the success gate — their failure content is our own local
        # error string, not outside text. (The scanner ALLOWs empty content, so an
        # empty result still short-circuits cleanly.)
        result_summary = result.content
        content_withheld = False
        scan_ingress = self._scanner is not None and (
            external_handler is not None
            or (result.success and tool_name in INGRESS_TOOLS_V1)
        )
        if scan_ingress:
            # G3-22 structured returns / D-3: when a tool emits a model_summary,
            # the SUMMARY is the model-facing field — the router and web server
            # feed `model_summary or content` back to the model. So the summary
            # is a NEW model-facing trust boundary and MUST be scanned, not just
            # `content`. Scan BOTH (content = the full source, catches injection
            # anywhere incl. the middle a head/tail summary would drop; summary =
            # the derived model-facing text, catches injection in a non-subset
            # transform). Injection in EITHER blocks; a block withholds BOTH so a
            # poisoned summary can never reach the model behind a clean content
            # or vice-versa. (docs/architecture/intergen-structured-tool-returns-
            # design.md §7.)
            scan_text = result.content
            if result.model_summary is not None:
                scan_text = f"{result.content}\n{result.model_summary}"
            ingress_verdict = self._scanner.scan(
                scan_text,
                ScanContext(
                    surface=_scan_surface(
                        tool_name, arguments, external=external_handler is not None
                    ),
                    direction=ScanDirection.INGRESS,
                    tool_name=tool_name,
                ),
            )
            if ingress_verdict.disposition is ScanDisposition.BLOCK:
                content_withheld = True
                user_outcome = f"{user_outcome}+ingress_blocked"
                result_summary = (
                    f"Sentinel ingress-scan BLOCK: {ingress_verdict.reason}; "
                    "content withheld from context"
                )
                result.content = _withheld_notice(tool_name, ingress_verdict.reason)
                # Withhold the model-facing summary too — else the model would
                # fall through to a scanned-clean notice but a poisoned summary
                # could survive. None => router/web fall back to content (= the
                # withheld notice).
                result.model_summary = None
            elif ingress_verdict.disposition is ScanDisposition.FLAG:
                flag_decision = replace(
                    decision,
                    reason=(
                        "Sentinel ingress scan flagged the returned content: "
                        f"{ingress_verdict.reason}"
                    ),
                )
                choice = review_callback(call, flag_decision) if review_callback else "deny"
                if choice in ("deny", "deny_conversation"):
                    content_withheld = True
                    user_outcome = f"{user_outcome}+ingress_flag_denied"
                    result_summary = (
                        f"Sentinel ingress-scan FLAG denied: {ingress_verdict.reason}; "
                        "content withheld from context"
                    )
                    result.content = _withheld_notice(tool_name, ingress_verdict.reason)
                    result.model_summary = None

        # Audit-log the dispatch decision + execution outcome (+ scan verdict).
        self._audit_log_decision(
            call, decision, ingress_tracker,
            user_outcome=user_outcome,
            exit_code=exit_code,
            result_summary=result_summary,
            source_attribution=source_attribution,
            excerpt=excerpt,
        )

        # Record this tool fire in the per-turn tracker so subsequent
        # dispatches in the same conversation turn observe it in their
        # watermark history per RFC §5.1 — the tracker captures what
        # fired BEFORE the call under review, so the record happens
        # AFTER our dispatch decision on this call.
        ingress_tracker.record_tool_call(tool_name)

        # AI-2 (audit 2026-05-29): spotlight ingress output at this CENTRAL
        # dispatch chokepoint — wrap any successful result from an INGRESS_TOOLS_V1
        # tool OR an external/MCP tool in UNTRUSTED-INGRESS markers BEFORE it
        # re-enters the LLM context, so the model is structurally aware of the
        # trust boundary and labels any follow-on action ingress_derived (RFC §10
        # spotlighting). Sentinel build seq step 3 BROADENS this to external/MCP:
        # MCP is the canonical outside-party surface, even more than read_file.
        # Withheld content is NOT spotlighted (the notice is our own text, not
        # untrusted ingress). The same INGRESS_TOOLS_V1 set drives both this and
        # the §5.1 watermark, so the two cannot drift apart (the AI-13 root cause).
        # External/MCP results are spotlighted success-or-not (matching the
        # ingress-scan reach above — defense-in-depth); built-in
        # INGRESS_TOOLS_V1 keeps the success gate (failure content is our own text).
        if (
            not content_withheld
            and (
                external_handler is not None
                or (result.success and tool_name in INGRESS_TOOLS_V1)
            )
        ):
            source, source_type = _ingress_source_attribution(tool_name, arguments)
            if not is_wrapped(result.content):
                result.content = wrap_ingress_content(
                    result.content, source, source_type)
            # The model_summary is ALSO model-facing untrusted ingress (D-3), so
            # wrap it with the same markers — the model must see the trust
            # boundary on whichever field it synthesizes from. The is_wrapped
            # guards keep this idempotent if a cached result is re-served (the
            # ToolCache stores the result before this point, so a cache hit can
            # re-enter already-wrapped; do not double-wrap).
            if (result.model_summary is not None
                    and not is_wrapped(result.model_summary)):
                result.model_summary = wrap_ingress_content(
                    result.model_summary, source, source_type)

        return result

    @staticmethod
    def _dispatch_via_pkexec(
        call: ToolCall,
        tool_name: str,
        arguments: dict[str, Any],
        dispatch_token: str | None = None,
    ) -> ToolResult:
        """Route a privileged tool call through the pkexec runner.

        PolicyKit (authentication) wraps a thin sh shim at
        /usr/bin/intergen-privileged-runner that hands off to
        `python3 -m intergen.privileged_dispatch --request <path>` in root
        context. The privileged dispatcher re-validates against the same
        _PRIVILEGED_TOOLS allowlist + per-tool argument schema the gate
        consulted; defense-in-depth at the trust boundary.

        TWO THINGS CHANGED HERE, 2026-08-24, and they share this one call.

        1. WHO STARTS THE RUNNER. The daemon no longer starts pkexec as its own
           child. Its unit sets NoNewPrivileges=yes, which every child inherits
           and which cannot be cleared; under that flag the kernel ignores
           pkexec's setuid bit, so pkexec starts unprivileged, sees its own
           effective uid is not root, and refuses with exit 127 BEFORE PolicyKit
           is contacted. That is why no install of R001.1 could perform any
           privileged action. The daemon now asks its own systemd user manager
           to start a short-lived unit that runs the same pkexec invocation. The
           manager is not running under the flag, so the unit's process does not
           carry it. Measured on real hardware: a caller carrying the flag asked
           the manager to run a probe and the probe reported NoNewPrivs 0, with
           the caller's own context reporting 1 in the same capture.

           The daemon's hardening is not touched, and the PolicyKit action is
           the same narrow, purpose-built one whose exec.path names this runner.
           The unit is deliberately plain: imposing NoNewPrivileges on it would
           reproduce the defect exactly.

        2. WHAT IS ON THE COMMAND LINE. The tool arguments and the approval
           token used to be argv words 3, 4 and 5, and a command line is
           world-readable through /proc for the life of the process. They now
           travel in an owner-only file (intergen.privileged_request) whose PATH
           is the only thing passed. pkexec still scrubs the environment; this
           does not fight that, it changes what needs to survive.

        A None token means a privileged dispatch was reached without a minted
        approval — fail CLOSED rather than invoke the runner tokenless (the
        runner and dispatcher also refuse, but refusing here avoids the round
        trip and makes the invariant explicit). A request that cannot be written
        also fails closed: the only other way to carry those values is the
        command line, and that is the exposure being closed.

        Return shape: ToolResult constructed from the runner's stdout and exit
        code. systemd-run --wait propagates the unit's exit status as its own
        (measured), so 0 = success and non-zero = failure (validation, refusal,
        an authentication denial, or the manager itself failing to start it).
        """
        if dispatch_token is None:
            logger.error(
                "privileged dispatch of %s reached without an approval token; "
                "refusing", tool_name,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=(
                    f"Privileged tool {tool_name} cannot be dispatched without a "
                    "human-approval token; refusing (fail closed)."
                ),
                success=False,
            )

        try:
            request_path = privileged_request.write_request(
                tool_name, arguments, dispatch_token,
            )
        except privileged_request.RequestError as exc:
            logger.error(
                "cannot stage the privileged request for %s: %s", tool_name, exc,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=(
                    f"the privileged request for {tool_name} could not be "
                    f"written, so the action was not dispatched: {exc}"
                ),
                success=False,
            )

        # A recognisable unit name so the dispatch is findable in the journal
        # afterwards; random so two dispatches in flight cannot collide.
        unit_name = f"intergen-privileged-{secrets.token_hex(8)}"
        argv = [
            _SYSTEMD_RUN, "--user", "--quiet", "--collect", "--wait", "--pipe",
            f"--unit={unit_name}",
            # Everything after this is the command. A request path is generated
            # by us and cannot begin with a dash, but separating explicitly
            # means no future path shape can be read as an option.
            "--",
            "pkexec", _PKEXEC_RUNNER_PATH, request_path,
        ]

        try:
            try:
                completed = subprocess.run(
                    argv, capture_output=True, text=True, check=False,
                )
            except FileNotFoundError as exc:
                logger.error(
                    "cannot start the privileged runner for %s: %s",
                    tool_name, exc,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=(
                        f"{_SYSTEMD_RUN} is not available, so the privileged "
                        f"runner for {tool_name} could not be started. It is "
                        f"part of systemd; if it is missing the installation is "
                        f"broken upstream of this point."
                    ),
                    success=False,
                )
            except OSError as exc:
                logger.error(
                    "privileged dispatch invocation failed for %s: %s",
                    tool_name, exc,
                )
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=f"privileged dispatch invocation error: {exc}",
                    success=False,
                )

            if completed.returncode == 0:
                return ToolResult(
                    call_id=call.call_id,
                    name=tool_name,
                    content=completed.stdout.rstrip("\n"),
                    success=True,
                )

            return ToolRegistry._privileged_failure_result(
                call, tool_name, completed,
            )
        finally:
            # The runner removes the request as it reads it. This is the
            # caller's side of that contract: on any path where the runner
            # never got there — it was never started, it died early, the
            # manager refused — the request must not be left on disk, because
            # a request left behind is an approval token left behind.
            privileged_request.discard_request(request_path)

    #: What a path probe can establish. Each value is a MEASUREMENT, never a
    #: cause: "absent" means stat() said ENOENT, not that anyone is at fault.
    _PATH_ABSENT = "absent"
    _PATH_PRESENT = "present"
    _PATH_UNDETERMINED = "undetermined"

    @staticmethod
    def _probe_path(path: str) -> dict:
        """Report what the filesystem says about `path`. Infer nothing.

        `os.path.exists()` is a single boolean covering several different
        findings, and the diagnostic used to speak as though it covered one:

          * it answers False when the path is absent AND when a component of it
            cannot be traversed — absence and "permission denied" are different
            findings with different repairs;
          * it answers True for a directory, a socket, a device, and for a
            regular file that nothing on this system can execute.

        So this returns the states separately:

          absent        stat() reported ENOENT.
          undetermined  stat() failed some other way; `detail` says how, and
                        NOTHING about presence is claimed.
          present       stat() succeeded; `regular` and `executable` carry the
                        two further facts that decide whether it could run.

        Decided 2026-08-24: none of these is a cause, and no caller may turn
        one into a verdict about which component is at fault.
        """
        facts: dict[str, Any] = {
            "state": ToolRegistry._PATH_UNDETERMINED,
            "detail": "",
            "regular": None,
            "executable": None,
        }
        try:
            info = os.stat(path)
        except FileNotFoundError:
            facts["state"] = ToolRegistry._PATH_ABSENT
            return facts
        except NotADirectoryError as exc:
            facts["detail"] = (
                f"a component of the path is not a directory "
                f"({exc.strerror or exc})"
            )
            return facts
        except PermissionError as exc:
            facts["detail"] = (
                f"permission denied while resolving it ({exc.strerror or exc})"
            )
            return facts
        except OSError as exc:
            facts["detail"] = f"the check itself failed ({exc.strerror or exc})"
            return facts

        facts["state"] = ToolRegistry._PATH_PRESENT
        facts["regular"] = stat_module.S_ISREG(info.st_mode)
        try:
            facts["executable"] = os.access(path, os.X_OK)
        except OSError:
            facts["executable"] = None
        return facts

    @staticmethod
    def _describe_path(path: str, facts: dict) -> str:
        """One line of measured fact about `path`. No conclusion."""
        state = facts["state"]
        if state == ToolRegistry._PATH_ABSENT:
            return f"{path}: not present (checked)"
        if state == ToolRegistry._PATH_UNDETERMINED:
            return (
                f"{path}: presence could not be determined — "
                f"{facts['detail']} (checked)"
            )
        kind = "a regular file" if facts["regular"] else "NOT a regular file"
        if facts["executable"] is None:
            runnable = "executability could not be determined"
        elif facts["executable"]:
            runnable = "executable by this account"
        else:
            runnable = "NOT executable by this account"
        return f"{path}: present, {kind}, {runnable} (checked)"

    @staticmethod
    def _privileged_failure_result(
        call: ToolCall,
        tool_name: str,
        completed: Any,
    ) -> ToolResult:
        """Build the failure ToolResult, stating ONLY what was measured.

        WHY THIS IS NARROWER THAN IT LOOKS. The exit codes on this path do not
        identify a cause, and an earlier version of this function spoke as if
        they did. Two claims in particular were inferences wearing a
        measurement's clothes:

          * "exit 126 means the authentication prompt was dismissed". pkexec(1)
            propagates the exit status of a program it successfully executed,
            and this runner ends in `exec python3 ...` — so a 126 can equally
            come from the CHILD. Telling a person their prompt was dismissed
            when the tool actually failed sends them to repeat an action that
            already ran, or to hunt an authentication problem that never
            happened.
          * "exit 127 with the runner present means pkexec never reached it, so
            the installed package is not at fault". Presence was read from
            os.path.exists(), which proves neither a regular file nor
            executability nor correct contents; and 127 can likewise be the
            child's own status. An independent review drove three genuinely
            different states — a directory, a regular file nothing can execute,
            and a healthy executable whose program returned 127 — and every one
            of them produced that same innocence verdict.

        Decided 2026-08-24: this function reports the exit code, whatever the
        boundary said on stderr, and a per-component probe of each thing the
        dispatch depends on. Where a component is MEASURABLY absent, that is
        stated, because it was established. Where it is not, the message says
        the exit code does not identify a cause on its own — which is true, and
        is more useful to someone repairing a system than a confident wrong
        answer.

        The runner's own words still lead when it got far enough to speak: that
        is the one account in this whole path that was produced by something
        which knew what it was doing.
        """
        stdout_message = completed.stdout.rstrip("\n")
        stderr_message = completed.stderr.rstrip("\n")

        runner_facts = ToolRegistry._probe_path(_PKEXEC_RUNNER_PATH)
        systemd_run_path = shutil.which(_SYSTEMD_RUN)
        manager_socket = os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
            _USER_MANAGER_SOCKET_RELPATH,
        )
        manager_facts = ToolRegistry._probe_path(manager_socket)
        manager_running = manager_facts["state"] == ToolRegistry._PATH_PRESENT

        if stdout_message:
            # The runner reached its own logic and said why it stopped. That is
            # the most specific account anyone has; it leads.
            headline = stdout_message
        elif systemd_run_path is None:
            headline = (
                f"{_SYSTEMD_RUN} was not found on PATH (checked), so the "
                f"privileged runner for {tool_name} could not be started"
            )
        elif not manager_running:
            headline = (
                f"no systemd user manager was reachable for this account "
                f"(checked: {manager_socket}), so the privileged runner for "
                f"{tool_name} could not be started"
            )
        elif runner_facts["state"] == ToolRegistry._PATH_ABSENT:
            headline = (
                f"the privileged runner is not present at "
                f"{_PKEXEC_RUNNER_PATH} (checked), so {tool_name} could not be "
                f"dispatched"
            )
        else:
            headline = (
                f"the privileged dispatch of {tool_name} did not complete "
                f"(exit {completed.returncode}). This exit code does not "
                f"identify a cause on its own — it is returned both by the "
                f"privilege boundary and by the program it runs — so what was "
                f"measured is listed rather than guessed at"
            )

        measured = [
            f"exit code: {completed.returncode}",
            ToolRegistry._describe_path(_PKEXEC_RUNNER_PATH, runner_facts),
            f"{_SYSTEMD_RUN}: "
            + (f"found at {systemd_run_path} (checked)" if systemd_run_path
               else "not found on PATH (checked)"),
            "systemd user manager: "
            + ("reachable (checked)" if manager_running
               else f"not reachable (checked: {manager_socket})"),
        ]
        if stderr_message:
            measured.append(f"it said: {stderr_message}")
        content = f"{headline} [{'; '.join(measured)}]"
        logger.error(
            "privileged dispatch of %s failed: rc=%s runner=%s systemd_run=%s "
            "manager_reachable=%s stderr=%r",
            tool_name, completed.returncode, runner_facts,
            systemd_run_path, manager_running, stderr_message,
        )
        return ToolResult(
            call_id=call.call_id,
            name=tool_name,
            content=content,
            success=False,
        )

    @staticmethod
    def _audit_log_decision(
        call: ToolCall,
        decision: DispatchDecision,
        ingress_tracker: IngressTracker,
        *,
        user_outcome: str,
        exit_code: int,
        result_summary: str,
        source_attribution: str,
        excerpt: str,
    ) -> None:
        """Best-effort audit log write per RFC §9."""
        record = build_audit_record(
            call=call,
            decision=decision,
            ingress_tracker=ingress_tracker,
            user_outcome=user_outcome,
            exit_code=exit_code,
            result_summary=result_summary,
            source_attribution=source_attribution,
            excerpt=excerpt,
        )
        write_record(record)

    def get_tool(self, name: str) -> BaseTool | None:
        """Get a registered tool by name."""
        return self._tools.get(name)

    def get_schemas(self, names: set[str] | None = None) -> list[dict]:
        """Get OpenAI-compatible schemas for the given tools (or all)."""
        schemas = []
        for tool in self._tools.values():
            if names is None or tool.name in names:
                schemas.append(tool.schema.to_openai())
        return schemas

    def get_tool_schemas(self, names: set[str] | None = None) -> list[ToolSchema]:
        """Get ToolSchema objects for the given tools (or all).

        DISPATCH LOCKDOWN backstop: returns [] when tool-offering is locked, so
        the model is never offered tools on any surface and cannot emit a
        ToolCall. Code-owned dispatch does not call this method, so it is
        unaffected. (See set_tool_offering_locked.)
        """
        if self._tool_offering_locked:
            return []
        schemas = []
        for tool in self._tools.values():
            if names is None or tool.name in names:
                schemas.append(tool.schema)
        return schemas

    def get_all_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys()) + list(self._external_handlers.keys())

    def classify_safety(self, tool_name: str,
                        arguments: dict[str, Any]) -> SafetyTier:
        """Classify the safety tier for a specific tool invocation."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return SafetyTier.CONFIRM
        return tool.classify_safety(arguments)

    def build_prompt_rules(self, active_tools: set[str] | None = None) -> str:
        """Build numbered system prompt rules for active tools.

        Only includes rules for tools that are in the active set.
        """
        rules = [
            "When the user's request can be answered from your training data "
            "alone, answer directly without calling a tool.",
            "When the user asks about the current state of their system "
            "(files, packages, services, hardware), ALWAYS use a tool.",
            "Never fabricate system information. If unsure, use a tool to check.",
        ]

        for tool in self._tools.values():
            if active_tools is None or tool.name in active_tools:
                rules.append(
                    f"Tool '{tool.name}': {tool.description}"
                )

        for name, rule in self._external_rules.items():
            if active_tools is None or name in active_tools:
                rules.append(rule)

        numbered = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
        return "Tool usage guidelines:\n" + numbered

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def tool_count(self) -> int:
        return len(self._tools) + len(self._external_handlers)
