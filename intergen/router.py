"""InterGen conversation router — routes user input to handlers.

Ported from JARVIS core/conversation_router.py (3,782 lines → ~250 lines).
Simplified from 18 priorities to 8. No voice, no conversation windows,
no multi-user, no task planner. Text-only, system-focused.

Priority chain:
  P1: Keyword/regex match → direct tool dispatch
  P2: Semantic embedding match → tool dispatch
  P3: LLM tool calling → tool dispatch + synthesis
  P4: LLM free response (fallback)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from intergen.interfaces.router import RouterInterface
from intergen.interfaces.types import (
    Message, MessageRole, RouteResult, ToolCall, ToolResult,
)
from intergen.llm import LLMRouter
from intergen.metrics import EventLogger, MetricsTracker
from intergen.safety import classify_command, sanitize_output
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ConversationRouter(RouterInterface):
    """Routes user input through a priority chain to produce a response."""

    def __init__(self, *,
                 tool_registry: ToolRegistry,
                 semantic_matcher: SemanticMatcher,
                 llm: LLMRouter,
                 event_logger: EventLogger | None = None,
                 metrics: MetricsTracker | None = None):
        self._tools = tool_registry
        self._semantic = semantic_matcher
        self._llm = llm
        self._events = event_logger
        self._metrics = metrics
        self._conversation_history: list[Message] = []
        self._max_history = 20

    def route(self, user_input: str, *,
              conversation_active: bool = False) -> RouteResult:
        """Route user input through the priority chain."""
        t0 = time.monotonic()
        user_input = user_input.strip()

        if not user_input:
            return RouteResult(text="", handled=False)

        if self._metrics:
            self._metrics.increment("requests")

        # P1: Keyword/regex match
        result = self._try_keyword_match(user_input)
        if result.handled:
            self._record(result, t0, "keyword")
            return result

        # P2: Semantic embedding match
        result = self._try_semantic_match(user_input)
        if result.handled:
            self._record(result, t0, "semantic")
            return result

        # P3: LLM with tool calling
        result = self._try_llm_tools(user_input)
        if result.handled:
            self._record(result, t0, "llm_tools")
            return result

        # P4: LLM free response (fallback)
        result = self._try_llm_freeform(user_input)
        self._record(result, t0, "llm_freeform")
        return result

    def _try_keyword_match(self, user_input: str) -> RouteResult:
        """P1: regex/keyword matching via semantic matcher Layer 1."""
        match = self._semantic._match_keywords(user_input)
        if match.intent_id is None:
            return RouteResult(handled=False)

        if match.tool_name:
            tool_result = self._execute_tool_for_intent(
                match.tool_name, user_input
            )
            if tool_result:
                return RouteResult(
                    text=tool_result.content,
                    source="keyword",
                    handled=True,
                    tool_results=[tool_result],
                )

        return RouteResult(handled=False)

    def _try_semantic_match(self, user_input: str) -> RouteResult:
        """P2: embedding similarity matching."""
        match = self._semantic._match_embeddings(user_input)
        if match.intent_id is None or match.score < 0.90:
            return RouteResult(handled=False)

        if match.tool_name:
            tool_result = self._execute_tool_for_intent(
                match.tool_name, user_input
            )
            if tool_result:
                synthesis = self._synthesize_tool_result(
                    user_input, match.tool_name, tool_result.content
                )
                return RouteResult(
                    text=synthesis,
                    source="semantic",
                    handled=True,
                    tool_results=[tool_result],
                    confidence=match.score,
                )

        return RouteResult(handled=False)

    def _try_llm_tools(self, user_input: str) -> RouteResult:
        """P3: LLM decides which tool to call."""
        messages = self._build_messages(user_input)
        schemas = self._tools.get_schemas()
        if not schemas:
            return RouteResult(handled=False)

        tool_schema_objs = [
            t.schema for t in self._tools._tools.values()
        ]

        collected_text = []
        tool_calls = []
        tool_results = []

        for chunk in self._llm.stream_with_tools(
            messages, tools=tool_schema_objs
        ):
            if isinstance(chunk, ToolCall):
                tool_calls.append(chunk)
                result = self._tools.execute(chunk.name, chunk.arguments)
                tool_results.append(result)
            else:
                collected_text.append(chunk)

        if tool_results:
            if collected_text:
                response_text = "".join(collected_text)
            else:
                response_text = self._synthesize_tool_result(
                    user_input,
                    tool_results[0].name,
                    tool_results[0].content,
                )
            return RouteResult(
                text=response_text,
                source="llm_tools",
                handled=True,
                tool_calls=tool_calls,
                tool_results=tool_results,
                used_llm=True,
            )

        if collected_text:
            return RouteResult(
                text="".join(collected_text),
                source="llm_tools",
                handled=True,
                used_llm=True,
            )

        return RouteResult(handled=False)

    def _try_llm_freeform(self, user_input: str) -> RouteResult:
        """P4: LLM free response (no tools)."""
        messages = self._build_messages(user_input)
        response = self._llm.chat(messages)

        self._append_history(user_input, response.text)

        return RouteResult(
            text=response.text,
            source="llm_freeform",
            handled=True,
            used_llm=True,
            escalated=not response.local,
            escalation_provider=(
                response.model if not response.local else None
            ),
            confidence=1.0 if response.quality_passed else 0.5,
        )

    # ── Tool execution helpers ──

    def _execute_tool_for_intent(self, tool_name: str,
                                 user_input: str) -> ToolResult | None:
        """Execute a tool based on matched intent, extracting args from input."""
        tool = self._tools.get_tool(tool_name)
        if tool is None:
            return None

        arguments = self._extract_arguments(tool_name, user_input)
        return self._tools.execute(tool_name, arguments)

    def _extract_arguments(self, tool_name: str,
                           user_input: str) -> dict[str, Any]:
        """Extract tool arguments from user input.

        For keyword/semantic matches, we build simple arguments.
        Complex argument extraction is deferred to LLM tool calling (P3).
        """
        if tool_name == "run_command":
            return {"command": user_input}
        if tool_name == "read_file":
            return {"path": user_input.split()[-1] if user_input.split() else ""}
        if tool_name == "web_search":
            return {"query": user_input}
        if tool_name == "manage_packages":
            parts = user_input.lower().split()
            if "install" in parts:
                idx = parts.index("install")
                pkg = parts[idx + 1] if idx + 1 < len(parts) else ""
                return {"action": "install", "package": pkg}
            if "remove" in parts or "uninstall" in parts:
                return {"action": "remove", "package": parts[-1]}
            return {"action": "search", "query": user_input}
        if tool_name == "manage_services":
            parts = user_input.lower().split()
            for action in ("start", "stop", "restart", "status", "enable", "disable"):
                if action in parts:
                    idx = parts.index(action)
                    svc = parts[idx + 1] if idx + 1 < len(parts) else ""
                    return {"action": action, "service": svc}
            return {"action": "status", "service": ""}
        if tool_name == "open_application":
            return {"name": user_input}

        return {"query": user_input}

    def _synthesize_tool_result(self, user_input: str, tool_name: str,
                                tool_output: str) -> str:
        """Use LLM to synthesize a natural response from tool output."""
        sanitized = sanitize_output(tool_output)
        synthesis_prompt = (
            f"The user asked: \"{user_input}\"\n\n"
            f"Tool '{tool_name}' returned:\n{sanitized}\n\n"
            "Synthesize a clear, concise response for the user based on this output. "
            "Include the relevant data. Be direct."
        )
        messages = self._llm.build_system_messages()
        messages.append(Message(role=MessageRole.USER, content=synthesis_prompt))
        response = self._llm.chat(messages)
        return response.text

    # ── Message building ──

    def _build_messages(self, user_input: str) -> list[Message]:
        """Build message list with system prompt and conversation history."""
        messages = self._llm.build_system_messages()

        for msg in self._conversation_history[-self._max_history:]:
            messages.append(msg)

        messages.append(Message(role=MessageRole.USER, content=user_input))
        return messages

    def _append_history(self, user_input: str, response: str) -> None:
        """Append exchange to conversation history."""
        self._conversation_history.append(
            Message(role=MessageRole.USER, content=user_input)
        )
        self._conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=response)
        )
        if len(self._conversation_history) > self._max_history * 2:
            self._conversation_history = self._conversation_history[
                -self._max_history:
            ]

    # ── Recording ──

    def _record(self, result: RouteResult, t0: float, source: str) -> None:
        """Record routing decision for metrics and logging."""
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info("Routed via %s in %.0fms (tools=%d, llm=%s)",
                     source, elapsed_ms,
                     len(result.tool_results),
                     result.used_llm)

        if self._metrics:
            self._metrics.record_latency("route", elapsed_ms)
            self._metrics.increment(f"route_{source}")
            if result.used_llm:
                self._metrics.increment("llm_calls")
            if result.escalated:
                self._metrics.increment("escalations")

        if self._events:
            self._events.emit(
                category="routing",
                event="route_completed",
                message=f"{source}: {result.text[:80]}",
                source="router",
                latency_ms=round(elapsed_ms, 1),
                metadata={
                    "source": source,
                    "tool_count": len(result.tool_results),
                    "used_llm": result.used_llm,
                    "escalated": result.escalated,
                    "confidence": result.confidence,
                },
            )

    # ── Status ──

    def get_status(self) -> dict:
        """Return router status."""
        status = {
            "tool_count": self._tools.tool_count,
            "intent_count": self._semantic.get_intent_count(),
            "history_length": len(self._conversation_history),
            "escalation_mode": self._llm.get_escalation_mode().value,
        }
        if self._metrics:
            status.update(self._metrics.get_status())
        return status
