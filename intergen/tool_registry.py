"""InterGen tool registry — discovers, registers, and executes tools.

Ported from JARVIS core/tool_registry.py. Simplified: tools are class-based
(BaseTool subclasses) rather than module-based, and safety classification
is built into each tool.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from importlib import import_module
from typing import Any, Callable

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

logger = logging.getLogger(__name__)


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

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name.

        Handles both internal BaseTool instances and external handlers.
        """
        t0 = time.monotonic()

        tool = self._tools.get(tool_name)
        if tool is not None:
            validation_error = tool.validate_arguments(arguments)
            if validation_error:
                return ToolResult(
                    call_id="",
                    name=tool_name,
                    content=f"Validation error: {validation_error}",
                    success=False,
                )

            safety = tool.classify_safety(arguments)
            if safety == SafetyTier.BLOCKED:
                from intergen.safety import get_blocked_response
                cmd = arguments.get("command", str(arguments))
                return ToolResult(
                    call_id="",
                    name=tool_name,
                    content=get_blocked_response(cmd),
                    success=False,
                )

            try:
                result = tool.execute(arguments)
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.debug("Tool %s completed in %.0fms (%d chars)",
                             tool_name, elapsed_ms, len(result.content))
                return result
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.error("Tool %s failed in %.0fms: %s",
                             tool_name, elapsed_ms, e)
                return ToolResult(
                    call_id="",
                    name=tool_name,
                    content=f"Error executing {tool_name}: {e}",
                    success=False,
                )

        ext_handler = self._external_handlers.get(tool_name)
        if ext_handler is not None:
            try:
                result_text = ext_handler(arguments)
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.debug("External tool %s completed in %.0fms",
                             tool_name, elapsed_ms)
                return ToolResult(
                    call_id="", name=tool_name,
                    content=result_text, success=True,
                )
            except Exception as e:
                logger.error("External tool %s failed: %s", tool_name, e)
                return ToolResult(
                    call_id="", name=tool_name,
                    content=f"Error: {e}", success=False,
                )

        return ToolResult(
            call_id="", name=tool_name,
            content=f"Unknown tool: {tool_name}", success=False,
        )

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
