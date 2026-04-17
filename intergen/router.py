"""InterGen conversation router — routes user input to handlers.

Ported from JARVIS core/conversation_router.py (3,782 lines → ~250 lines).
Simplified from 18 priorities to 8. No voice, no conversation windows,
no multi-user, no task planner. Text-only, system-focused.

Priority chain:
  P0: Compound query detection → tier-aware decomposition
  P1: Keyword/regex match → direct tool dispatch
  P2: Semantic embedding match → tool dispatch
  P3: LLM tool calling → tool dispatch + synthesis
  P4: LLM free response (fallback)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from intergen.decomposer import analyze_query, DecomposedQuery
from intergen.memory import MemoryManager
from intergen.interfaces.router import RouterInterface
from intergen.state_cache import StateCache
from intergen.interfaces.types import (
    HardwareTierLevel, Message, MessageRole, RouteResult, ToolCall, ToolResult,
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
                 metrics: MetricsTracker | None = None,
                 hardware_tier: HardwareTierLevel = HardwareTierLevel.TIER_2,
                 memory: MemoryManager | None = None,
                 state_cache: StateCache | None = None):
        self._tools = tool_registry
        self._semantic = semantic_matcher
        self._llm = llm
        self._events = event_logger
        self._metrics = metrics
        self._hardware_tier = hardware_tier
        self._memory = memory
        self._state_cache = state_cache
        self._conversation_history: list[Message] = []
        self._max_history = 20
        self._first_interaction = True
        self._last_semantic_score = 0.0

        # Start a new session if memory is available
        if self._memory:
            self._memory.start_session()

    def route(self, user_input: str, *,
              conversation_active: bool = False) -> RouteResult:
        """Route user input through the priority chain."""
        t0 = time.monotonic()
        user_input = user_input.strip()
        self._current_query_type = self._classify_query_type(user_input)

        if not user_input:
            return RouteResult(
                text="What can I help with?",
                source="empty_input",
                handled=True,
            )

        if self._metrics:
            self._metrics.increment("requests")

        # Normalize input once — all downstream methods get clean text
        user_input = self._semantic._normalize_input(user_input)

        # Track first interaction (for session awareness on demand)
        if self._first_interaction:
            self._first_interaction = False

        # Safety pre-check — queries containing safety-trigger words
        # must NOT be intercepted by cache (e.g., "format my disk")
        _SAFETY_TRIGGERS = (
            "format", "delete", "remove", "wipe", "destroy", "erase",
            "ignore", "bypass", "override", "hack", "inject",
            "mkfs", "mkfs.ext4", "fdisk", "parted",
            "shutdown", "shut down", "reboot", "power off", "turn off",
            "rm -rf", "rm -f", "dd if=", "dd of=",
            "chmod 777", "chown", "shred", "wipefs", ":(){ :|:& };:",
        )
        lower_input_raw = user_input.lower()
        has_safety_trigger = any(t in lower_input_raw for t in _SAFETY_TRIGGERS)

        # P0: Compound query detection — multi-part queries bypass cache
        decomposition = analyze_query(user_input, self._hardware_tier)
        if decomposition.needs_decomposition:
            result = self._handle_compound(user_input, decomposition)
            if result.handled:
                self._record(result, t0, "decomposed")
                return result

        # Smart cache — instant response for single-value system state only.
        # Skip cache if: safety trigger detected, or cached value is multi-line
        # (multi-line output like df/free needs LLM formatting, not raw dumps).
        if self._state_cache and not has_safety_trigger:
            cached = self._state_cache.lookup_for_query(user_input)
            if cached and "\n" not in cached.strip():
                response = self._template_synthesis(user_input, cached)
                if response:
                    self._record(
                        RouteResult(text=response, source="cache", handled=True),
                        t0, "cache",
                    )
                    return RouteResult(
                        text=response, source="cache", handled=True,
                    )

        # Self-awareness — instant template responses, no LLM needed
        lower_input = user_input.lower().strip()
        identity_response = self._try_self_awareness(lower_input)
        if identity_response:
            return RouteResult(
                text=identity_response, source="identity", handled=True,
            )

        # Memory operations
        if self._memory:
            mem_result = self._try_memory(user_input)
            if mem_result.handled:
                self._record(mem_result, t0, "memory")
                return mem_result

        # P1: Keyword/regex match
        result = self._try_keyword_match(user_input)
        if result.handled:
            self._record(result, t0, "keyword")
            return result

        # P2: Semantic embedding match
        p2_match = self._semantic._match_embeddings(user_input)
        self._last_semantic_score = p2_match.score if p2_match.score is not None else 0.0
        if p2_match.intent_id is not None and p2_match.score >= 0.85:
            result = self._try_semantic_match(user_input)
            if result.handled:
                self._record(result, t0, "semantic")
                return result

        # P3: LLM with tool calling — eligibility threshold (not skip threshold).
        # Queries are eligible for tools if: semantic score suggests relevance,
        # OR the adaptive classifier tagged them as diagnostic or safety.
        # Diagnostic/safety queries MUST go through tools — freeform fabrication
        # is the #1 remaining quality gap (flagged by 4/4 code reviewers).
        eligible_for_tools = (
            p2_match.score >= 0.7
            or self._current_query_type == "diagnostic"
            or self._current_query_type == "safety"
        )
        if eligible_for_tools:
            result = self._try_llm_tools(user_input)
            if result.handled:
                self._record(result, t0, "llm_tools")
                return result

        # P4: LLM free response (fallback)
        result = self._try_llm_freeform(user_input)
        self._record(result, t0, "llm_freeform")
        return result

    @staticmethod
    def _try_self_awareness(lower_input: str) -> str | None:
        """Handle self-awareness queries with instant template responses."""
        _IDENTITY = {
            "what are you": (
                "I'm InterGen, your AI assistant. "
                "I help you manage your system — packages, services, "
                "files, hardware, network. I can run commands, diagnose "
                "problems, and answer questions."
            ),
            "who are you": None,  # falls through to "what are you"
            "tell me about yourself": None,
            "describe yourself": None,
            "what is your name": "I'm InterGen.",
            "what's your name": "I'm InterGen.",
            "who made you": "I was built by InterGenJLU as part of this operating system.",
            "who built you": None,  # same as who made you
            "who created you": None,
            "are you an ai": (
                "I'm InterGen — an AI assistant that runs locally "
                "on this machine."
            ),
            "are you a bot": None,
            "are you artificial intelligence": None,
            "what can you do": (
                "I can check system status (disk, memory, CPU, network), "
                "manage packages and services, read and write files, "
                "search the web, open applications, and answer questions."
            ),
            "what are your capabilities": None,
            "what are your limitations": (
                "I work best with system administration tasks. I can't "
                "browse the web in real-time, make phone calls, or access "
                "hardware I don't have drivers for. For complex reasoning, "
                "I can escalate to a cloud provider if you've configured one."
            ),
            "do you run locally": (
                "Everything runs locally on your machine. No data leaves "
                "this system unless you explicitly configure cloud escalation."
            ),
            "are you local": None,
            "where do you run": None,
            "what about privacy": (
                "Everything stays on your machine. I run entirely on your "
                "hardware — no queries, responses, or system data are sent "
                "anywhere. Your data never leaves this computer unless you "
                "explicitly configure cloud escalation."
            ),
            "is my data private": None,
            "where does my data go": None,
            "do you send my data": None,
            "is my data sent": None,
            "data stays local": None,
            "are you private": None,
            "how do you work": (
                "I route your queries through a priority chain: cached system "
                "data first (instant), then keyword matching, semantic matching, "
                "and finally an LLM for complex questions. Most system queries "
                "are answered in under 10 milliseconds without touching the LLM."
            ),
            "can you write code": (
                "I can help explain code, write simple scripts, and generate "
                "configuration files. For complex programming tasks, cloud "
                "escalation to a more capable model is recommended."
            ),
            "what operating system": (
                "This system runs InterGenOS — a Linux distribution built "
                "entirely from source. I'm InterGen, the AI assistant "
                "built into it."
            ),
            "what os is this": None,
            "what os are you": None,
            "what can you help me with": None,  # falls through to "what can you do"
            "what can you help with": None,
        }

        clean = lower_input.rstrip("?!.")
        # Exact match first
        if clean in _IDENTITY:
            response = _IDENTITY[clean]
            if response is not None:
                return response
            return _IDENTITY["what are you"]
        # Substring match — longest keys first to avoid false positives
        # ("can you write code" must match before "are you")
        for key in sorted(_IDENTITY.keys(), key=len, reverse=True):
            if key in clean:
                val = _IDENTITY[key]
                if val is not None:
                    return val
                return _IDENTITY["what are you"]
        return None

    def _try_memory(self, user_input: str) -> RouteResult:
        """Handle memory operations: remember, recall, forget, session recall."""
        # Session recall: "what were we working on?" / "what did we do last time?"
        lower = user_input.lower()
        if any(p in lower for p in [
            "what were we", "what did we do", "last time", "last session",
            "where did we leave off", "what was I working on",
            "pick up where we left off", "continue where we",
        ]):
            welcome = self._memory.format_welcome_back()
            if welcome:
                return RouteResult(text=welcome, source="memory", handled=True)
            return RouteResult(
                text="I don't have any record of a previous session.",
                source="memory", handled=True,
            )

        # Transparency: "what do you know about me?"
        if MemoryManager.is_transparency_request(user_input):
            response = self._memory.format_transparency_response()
            return RouteResult(text=response, source="memory", handled=True)

        # Forget: "forget about my backup drive"
        subject = MemoryManager.is_forget_request(user_input)
        if subject is not None:
            response = self._memory.format_forget_response(subject)
            return RouteResult(text=response, source="memory", handled=True)

        # Remember: "remember that my backup drive is /dev/sdb1"
        if MemoryManager.is_remember_request(user_input):
            facts = self._memory.extract_and_store(user_input)
            if facts:
                stored = ", ".join(f"**{f.key}** = {f.value}" for f in facts)
                return RouteResult(
                    text=f"Got it. I'll remember: {stored}",
                    source="memory", handled=True,
                )
            return RouteResult(
                text="I couldn't extract a fact from that. Try: 'Remember that [something] is [value]'",
                source="memory", handled=True,
            )

        # Not a memory operation — also try passive extraction
        # (extract facts from natural conversation without explicit "remember")
        # Skipped for now — only explicit storage per PRIME DIRECTIVE

        return RouteResult(handled=False)

    def _handle_compound(self, user_input: str,
                         decomposition: DecomposedQuery) -> RouteResult:
        """P0: Handle compound queries by executing sub-queries sequentially."""
        results_text = [decomposition.response_prefix, ""]
        all_tool_calls = []
        all_tool_results = []
        used_llm = False

        for i, sub_query in enumerate(decomposition.sub_queries, 1):
            sub_result = self._route_single(sub_query)
            results_text.append(f"**{i}.** {sub_result.text}")
            all_tool_calls.extend(sub_result.tool_calls)
            all_tool_results.extend(sub_result.tool_results)
            if sub_result.used_llm:
                used_llm = True

        return RouteResult(
            text="\n\n".join(results_text),
            source="decomposed",
            handled=True,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            used_llm=used_llm,
        )

    def _route_single(self, user_input: str) -> RouteResult:
        """Route a single (non-compound) query through P1→P4."""
        result = self._try_keyword_match(user_input)
        if result.handled:
            return result
        result = self._try_semantic_match(user_input)
        if result.handled:
            return result
        result = self._try_llm_tools(user_input)
        if result.handled:
            return result
        return self._try_llm_freeform(user_input)

    def _try_keyword_match(self, user_input: str) -> RouteResult:
        """P1: regex/keyword matching via semantic matcher Layer 1.

        Uses template synthesis for known query types (instant, no LLM).
        Falls back to LLM synthesis only for unexpected output.
        """
        match = self._semantic._match_keywords(user_input)
        if match.intent_id is None:
            return RouteResult(handled=False)

        if match.tool_name:
            tool_result = self._execute_tool_for_intent(
                match.tool_name, user_input
            )
            if tool_result and tool_result.success:
                # Try template synthesis first (instant, no LLM)
                response = self._template_synthesis(
                    user_input, tool_result.content
                )
                used_llm = False
                if response is None:
                    # Fall back to LLM synthesis for complex output
                    response = self._synthesize_tool_result(
                        user_input, match.tool_name, tool_result.content
                    )
                    used_llm = True
                return RouteResult(
                    text=response,
                    source="keyword",
                    handled=True,
                    tool_results=[tool_result],
                    used_llm=used_llm,
                )

        return RouteResult(handled=False)

    def _try_semantic_match(self, user_input: str) -> RouteResult:
        """P2: embedding similarity matching.

        Uses template synthesis first (instant), LLM fallback for complex output.
        Same pattern as P1 — no reason to call the LLM to format 'intergenos'
        into 'Your hostname is intergenos' when a template does it in 0ms.
        """
        match = self._semantic._match_embeddings(user_input)
        # Store score for P3 skip decision
        self._last_semantic_score = match.score if match.score is not None else 0.0
        if match.intent_id is None or match.score < 0.85:
            return RouteResult(handled=False)

        if match.tool_name:
            tool_result = self._execute_tool_for_intent(
                match.tool_name, user_input
            )
            if tool_result and tool_result.success:
                response = self._template_synthesis(
                    user_input, tool_result.content
                )
                used_llm = False
                if response is None:
                    response = self._synthesize_tool_result(
                        user_input, match.tool_name, tool_result.content
                    )
                    used_llm = True
                return RouteResult(
                    text=response,
                    source="semantic",
                    handled=True,
                    tool_results=[tool_result],
                    confidence=match.score,
                    used_llm=used_llm,
                )

        return RouteResult(handled=False)

    def _try_llm_tools(self, user_input: str) -> RouteResult:
        """P3: LLM decides which tool to call."""
        messages = self._build_messages(user_input)
        tool_schema_objs = self._tools.get_tool_schemas()
        if not tool_schema_objs:
            return RouteResult(handled=False)

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
            synthesis = self._llm.continue_after_tool_call(
                messages,
                tool_calls[0],
                tool_results[0].content,
            )
            if synthesis:
                response_text = synthesis.text
                tok_p = synthesis.tokens_prompt
                tok_c = synthesis.tokens_completion
            else:
                logger.info("Agentic synthesis failed — falling back to template")
                if collected_text:
                    response_text = self._llm._strip_filler("".join(collected_text))
                else:
                    response_text = self._synthesize_tool_result(
                        user_input,
                        tool_results[0].name,
                        tool_results[0].content,
                    )
                tok_p = getattr(self._llm, '_last_prompt_tokens', 0)
                tok_c = getattr(self._llm, '_last_completion_tokens', 0)

            self._append_history(user_input, response_text)

            return RouteResult(
                text=response_text,
                source="llm_tools",
                handled=True,
                tool_calls=tool_calls,
                tool_results=tool_results,
                used_llm=True,
                tokens_prompt=tok_p,
                tokens_completion=tok_c,
            )

        if collected_text:
            return RouteResult(
                text=self._llm._strip_filler("".join(collected_text)),
                source="llm_tools",
                handled=True,
                used_llm=True,
                tokens_prompt=getattr(self._llm, '_last_prompt_tokens', 0),
                tokens_completion=getattr(self._llm, '_last_completion_tokens', 0),
            )

        return RouteResult(handled=False)

    def _try_llm_freeform(self, user_input: str) -> RouteResult:
        """P4: LLM free response (no tools)."""
        messages = self._build_messages(user_input)

        if self._current_query_type == "diagnostic":
            messages.append(Message(
                role=MessageRole.USER,
                content=(
                    "IMPORTANT: If you cannot answer this from tool output "
                    "you have already seen, say 'I don't have current data "
                    "on that' — do NOT fabricate system data."
                ),
            ))

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
            tokens_prompt=response.tokens_prompt,
            tokens_completion=response.tokens_completion,
        )

    # ── Tool execution helpers ──

    def _execute_tool_for_intent(self, tool_name: str,
                                 user_input: str) -> ToolResult | None:
        """Execute a tool based on matched intent, extracting args from input."""
        tool = self._tools.get_tool(tool_name)
        if tool is None:
            return None

        arguments = self._extract_arguments(tool_name, user_input)
        if arguments is None:
            return None
        try:
            return self._tools.execute(tool_name, arguments)
        except Exception as e:
            logger.error("Tool %s execution failed: %s", tool_name, e)
            return None

    def _extract_arguments(self, tool_name: str,
                           user_input: str) -> dict[str, Any] | None:
        """Extract tool arguments from user input.

        For keyword/semantic matches, we build simple arguments.
        Complex argument extraction is deferred to LLM tool calling (P3).
        """
        if tool_name == "run_command":
            cmd = self._natural_language_to_command(user_input)
            if cmd:
                return {"command": cmd}
            raw_match = re.match(
                r"^(?:run|execute|shell)\s+(.+)", user_input, re.IGNORECASE
            )
            if raw_match:
                return {"command": raw_match.group(1).strip()}
            return None
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
            # "Is X running?" / "Is X active?" pattern
            running_match = re.search(
                r"is\s+(\S+)\s+(?:running|active|up|enabled)", user_input, re.IGNORECASE
            )
            if running_match:
                return {"action": "status", "service": running_match.group(1)}
            # "What services are running?" → list
            if "services" in user_input.lower() or "list" in user_input.lower():
                return {"action": "list", "service": ""}
            return {"action": "status", "service": ""}
        if tool_name == "open_application":
            return {"name": user_input}

        return {"query": user_input}

    @staticmethod
    def _template_synthesis(user_input: str, output: str) -> str | None:
        """Template-based synthesis for P1 matches — instant, no LLM.

        Maps known query patterns to natural language templates.
        Returns None if no template matches (triggers LLM fallback).
        """
        lower = user_input.lower().strip()
        out = output.strip()

        if not out:
            return None

        # Single-line output templates (most system info queries)
        if out.count("\n") == 0 and len(out) < 200:
            if "hostname" in lower:
                return f"Your hostname is {out}."
            if "kernel" in lower:
                return f"You're running kernel {out}."
            if "uptime" in lower:
                return f"System uptime: {out}"
            if "ip" in lower and "addr" not in out:
                return f"Your IP address is {out}."

        # Multi-line output — parse and summarize, then show raw data
        if "disk" in lower or "storage" in lower or "df" in lower or "full" in lower or "space" in lower:
            summary = ConversationRouter._summarize_disk(out)
            return f"{summary}\n\n```\n{out}\n```"
        if "memory" in lower or "ram" in lower or "free" in lower:
            summary = ConversationRouter._summarize_memory(out)
            return f"{summary}\n\n```\n{out}\n```"
        if "cpu" in lower:
            return f"Here's your CPU information:\n\n{out}"
        # Service status — single-line results
        if ("running" in lower or "active" in lower or "status" in lower) and \
                out.count("\n") == 0:
            if "active" in out.lower() or "running" in out.lower():
                return f"Yes, it's running. {out}"
            if "inactive" in out.lower() or "dead" in out.lower():
                return f"No, it's not running. {out}"
            return out
        if "services" in lower or "systemctl" in lower:
            return f"Here are the running services:\n\n{out}"
        if "packages" in lower:
            return f"Here are your packages:\n\n{out}"
        if "os" in lower or "operating system" in lower:
            return f"Here's your OS information:\n\n{out}"
        if "gpu" in lower or "vga" in lower:
            return f"Here's your GPU information:\n\n{out}"
        if "usb" in lower:
            return f"Here are your USB devices:\n\n{out}"
        if "block" in lower or "lsblk" in lower:
            return f"Here are your block devices:\n\n{out}"
        if "network" in lower:
            return f"Here are your network interfaces:\n\n{out}"

        # No template matched — LLM will handle it
        return None

    @staticmethod
    def _summarize_disk(output: str) -> str:
        """Parse df -h output into a human-readable summary."""
        lines = output.strip().split("\n")
        parts = []
        for line in lines[1:]:
            cols = line.split()
            if len(cols) >= 5 and cols[4].endswith("%"):
                fs = cols[0]
                if fs.startswith("/dev/"):
                    mount = cols[5] if len(cols) > 5 else "/"
                    pct = cols[4]
                    avail = cols[3]
                    parts.append(f"{mount} is at {pct} usage ({avail} free)")
        if parts:
            return "Disk usage: " + ", ".join(parts) + "."
        return "Here's your disk usage:"

    @staticmethod
    def _summarize_memory(output: str) -> str:
        """Parse free -h output into a human-readable summary."""
        lines = output.strip().split("\n")
        for line in lines:
            if line.startswith("Mem:"):
                cols = line.split()
                if len(cols) >= 4:
                    total = cols[1]
                    used = cols[2]
                    avail = cols[6] if len(cols) > 6 else cols[3]
                    return f"You have {total} total RAM, {used} in use, {avail} available."
        return "Here's your memory usage:"

    @staticmethod
    def _natural_language_to_command(user_input: str) -> str | None:
        """Map common natural language system queries to shell commands.

        Returns the command string, or None if the input doesn't map
        to a known query (falls through to LLM for complex cases).
        """
        lower = user_input.lower().strip()

        _QUERY_MAP = {
            "hostname": "hostname",
            "host name": "hostname",
            "my hostname": "hostname",
            "kernel": "uname -r",
            "kernel version": "uname -r",
            "what kernel": "uname -r",
            "ip address": "ip -brief addr show",
            "my ip": "ip -brief addr show",
            "network interfaces": "ip -brief addr show",
            "disk space": "df -h",
            "disk usage": "df -h",
            "storage": "df -h",
            "memory": "free -h",
            "ram": "free -h",
            "memory usage": "free -h",
            "cpu": "lscpu | head -20",
            "cpu info": "lscpu | head -20",
            "uptime": "uptime",
            "how long": "uptime",
            "been running": "uptime",
            "been up": "uptime",
            "os version": "cat /etc/os-release",
            "operating system": "cat /etc/os-release",
            "what os": "cat /etc/os-release",
            "gpu": "lspci | grep -i vga",
            "usb devices": "lsusb",
            "block devices": "lsblk",
            "system info": "uname -a && free -h && df -h",
            "system status": "uptime && free -h && df -h",
            "system health": "uptime && free -h && df -h",
        }

        for phrase, cmd in _QUERY_MAP.items():
            if phrase in lower:
                return cmd

        return None

    def _synthesize_tool_result(self, user_input: str, tool_name: str,
                                tool_output: str) -> str:
        """Use LLM to synthesize a natural response from tool output."""
        sanitized = sanitize_output(tool_output)
        synthesis_prompt = (
            f"The user asked: \"{user_input}\"\n\n"
            f"Tool '{tool_name}' returned:\n{sanitized}\n\n"
            "Synthesize a clear, concise response for the user.\n"
            "RULES:\n" + self._llm._SYNTHESIS_RULES
        )
        messages = self._llm.build_system_messages()
        messages.append(Message(role=MessageRole.USER, content=synthesis_prompt))
        response = self._llm.chat(messages)
        return response.text

    # ── Message building ──

    _IDENTITY_KEYWORDS = frozenset([
        "name", "who", "what are you", "hostname", "host", "box",
        "machine", "computer", "yourself", "your name",
    ])
    _DIAGNOSTIC_KEYWORDS = frozenset([
        "slow", "crash", "broke", "error", "fail", "down", "full",
        "running out", "can't reach", "not working", "check", "diagnose",
        "fix", "install", "remove", "restart", "status", "show me",
        "df ", "free ", "find ", "cat ", "top", "htop",
    ])

    _SAFETY_TRIGGER_WORDS = frozenset([
        "format", "delete", "remove", "wipe", "destroy", "erase",
        "ignore", "bypass", "override", "hack", "inject",
        "mkfs", "mkfs.ext4", "fdisk", "parted",
        "shutdown", "shut down", "reboot", "power off", "turn off",
        "rm -rf", "rm -f", "dd if=", "dd of=",
        "chmod 777", "chown", "shred", "wipefs", ":(){ :|:& };:",
    ])

    _GRATITUDE_MARKERS = frozenset([
        "thanks", "thank you", "appreciate", "great job", "well done",
    ])

    def _classify_query_type(self, user_input: str) -> str:
        """Classify query for adaptive prompt selection.

        Uses existing signals — no LLM call. Returns one of:
        identity, diagnostic, safety, general.
        """
        lower = user_input.lower()

        if any(t in lower for t in self._SAFETY_TRIGGER_WORDS):
            return "safety"

        # Gratitude bypass: "thanks, that fixed it" should not route to tools
        # just because "fix" substring-matches "fixed". Gratitude wins.
        if any(m in lower for m in self._GRATITUDE_MARKERS):
            return "general"

        words = lower.split()

        # Identity: always check keywords (not just short queries),
        # and ultra-short queries (≤2 words) always get identity context
        # to prevent "I am InterGenOS" on ambiguous inputs.
        for kw in self._IDENTITY_KEYWORDS:
            if kw in lower:
                return "identity"
        if len(words) <= 2:
            return "identity"

        for kw in self._DIAGNOSTIC_KEYWORDS:
            if kw in lower:
                return "diagnostic"

        return "general"

    def _build_messages(self, user_input: str) -> list[Message]:
        """Build message list with adaptive system prompt."""
        query_type = getattr(self, '_current_query_type', 'general')
        messages = self._llm.build_system_messages(query_type=query_type)

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

        # Track turn for session awareness
        if self._memory and result.handled:
            tool_names = [tr.name for tr in result.tool_results]
            self._memory.record_turn(result.text[:200], tool_names or None)
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
                    "query_type": getattr(self, '_current_query_type', 'general'),
                    "tool_count": len(result.tool_results),
                    "used_llm": result.used_llm,
                    "escalated": result.escalated,
                    "confidence": result.confidence,
                    "tokens_prompt": result.tokens_prompt,
                    "tokens_completion": result.tokens_completion,
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
