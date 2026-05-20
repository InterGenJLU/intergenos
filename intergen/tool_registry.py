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
import subprocess
import time
from pathlib import Path
from importlib import import_module
from typing import Any, Callable

# Canonical pkexec runner path — installed by packages/ai/intergen/build.sh
# from intergen/data/intergen-privileged-runner per the build-system
# coordinator's 49a585ca T0-4-E integration pkexec gate artifacts. The
# constant lives here so the runtime import contract is colocated with
# the dispatcher that invokes it.
_PKEXEC_RUNNER_PATH = "/usr/bin/intergen-privileged-runner"

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolCall, ToolResult, ToolSchema
from intergen.interfaces.provenance import (
    ConversationTrustState,
    DispatchDecision,
    IngressTracker,
    ToolRiskTier,
    UserDecision,
)
from intergen.provenance import (
    build_audit_record,
    record_user_decision,
    verify_tool_call,
)
from intergen.audit_log import write_record

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
    """
    if tool_name in _PRIVILEGED_TOOLS:
        return ToolRiskTier.PRIVILEGED_STATE_CHANGING
    if tool is None:
        # External handler or unknown — safest assumption is user-scope.
        return ToolRiskTier.USER_SCOPE_STATE_CHANGING
    safety = tool.classify_safety(arguments)
    if safety == SafetyTier.CONFIRM:
        return ToolRiskTier.USER_SCOPE_STATE_CHANGING
    return ToolRiskTier.READ_ONLY


class ToolRegistry:
    """Discovers, registers, and dispatches tool calls."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._external_handlers: dict[str, Callable] = {}
        self._external_rules: dict[str, str] = {}
        self._ready = False

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
            )

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
                )

        # Classify tool risk tier + fire the dispatcher gate.
        risk_tier = _classify_risk_tier(tool, arguments, tool_name)
        decision = verify_tool_call(
            call,
            ingress_tracker,
            trust_state,
            risk_tier,
            source_attribution,
        )

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
            )

        # Hold-for-review path — route to review_callback (the GTK4 modal
        # in production; None during headless test contexts or boot phases
        # where the modal is not yet wired).
        user_outcome = "executed"
        if decision.action == "hold_for_review":
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
                )

            user_choice = review_callback(call, decision)
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
                )
            # allow_once or allow_conversation — proceed to execution.
            if user_choice == "allow_conversation":
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
                        call, tool_name, arguments,
                    )
                else:
                    result = tool.execute(arguments)
                exit_code = 0 if result.success else 1
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

        # Audit-log the dispatch decision + execution outcome.
        self._audit_log_decision(
            call, decision, ingress_tracker,
            user_outcome=user_outcome,
            exit_code=exit_code,
            result_summary=result.content,
            source_attribution=source_attribution,
            excerpt=excerpt,
        )

        # Record this tool fire in the per-turn tracker so subsequent
        # dispatches in the same conversation turn observe it in their
        # watermark history per RFC §5.1 — the tracker captures what
        # fired BEFORE the call under review, so the record happens
        # AFTER our dispatch decision on this call.
        ingress_tracker.record_tool_call(tool_name)

        return result

    @staticmethod
    def _dispatch_via_pkexec(
        call: ToolCall,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Route a privileged tool call through the pkexec runner.

        Per the build-system coordinator's 49a585ca T0-4-E integration
        contract: PolicyKit (authentication) wraps a thin sh shim at
        /usr/bin/intergen-privileged-runner that hands off to
        `python3 -m intergen.privileged_dispatch <tool_name> <args_json>`
        in root context. The privileged dispatcher re-validates against
        the same _PRIVILEGED_TOOLS allowlist + per-tool argument schema
        the gate consulted; defense-in-depth at the trust boundary.

        Return shape: ToolResult constructed from the runner's stdout
        and exit code. subprocess returncode 0 = success;
        non-zero = failure (validation, refusal, or pkexec auth-deny).
        """
        try:
            completed = subprocess.run(
                [
                    "pkexec",
                    _PKEXEC_RUNNER_PATH,
                    tool_name,
                    json.dumps(arguments),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            # pkexec binary itself missing — shipped by polkit; if absent
            # the install is broken upstream of this point.
            logger.error(
                "pkexec binary not found for %s dispatch: %s",
                tool_name, exc,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=(
                    "pkexec binary not available; privileged tool "
                    f"{tool_name} cannot be dispatched. Install polkit."
                ),
                success=False,
            )
        except OSError as exc:
            logger.error(
                "pkexec invocation failed for %s: %s", tool_name, exc,
            )
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=f"pkexec invocation error: {exc}",
                success=False,
            )

        if completed.returncode == 0:
            return ToolResult(
                call_id=call.call_id,
                name=tool_name,
                content=completed.stdout.rstrip("\n"),
                success=True,
            )

        # Non-zero from pkexec covers two distinct cases:
        #   126 — user dismissed/denied the PolicyKit auth prompt
        #   127 — runner not found at _PKEXEC_RUNNER_PATH
        #   1   — runner-side dispatch failure (validation / refusal /
        #         tool exception); runner emits human-readable message
        #         on stdout per its contract
        #   2   — runner argv shape wrong (caller bug)
        # We surface stdout (the runner's human-readable message) as the
        # ToolResult content so the LLM sees what happened; the audit
        # log preserves the exit code in result_summary downstream.
        stdout_message = completed.stdout.rstrip("\n")
        stderr_message = completed.stderr.rstrip("\n")
        if completed.returncode == 126:
            content = (
                f"pkexec authentication denied or cancelled by user for "
                f"{tool_name}"
            )
        elif completed.returncode == 127:
            content = (
                f"pkexec runner not found at {_PKEXEC_RUNNER_PATH}; "
                f"intergen package may be misinstalled"
            )
        elif stdout_message:
            content = stdout_message
        elif stderr_message:
            content = stderr_message
        else:
            content = (
                f"pkexec runner exited {completed.returncode} with no output"
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
        """Get ToolSchema objects for the given tools (or all)."""
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
