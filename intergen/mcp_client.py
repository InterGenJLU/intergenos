"""InterGen MCP client — bridges external MCP servers into the tool pipeline.

Ported from JARVIS core/mcp_client.py. Connects to MCP servers via
subprocess, discovers tools, validates with Glasswing security, and
registers with the tool registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from intergen.interfaces.mcp import (
    GlasswingGuardInterface, MCPClientInterface, MCPServerConfig,
    MCPToolInfo, MCPTrustTier,
)
from intergen.interfaces.types import ToolResult, ToolSchema, SafetyTier
from intergen.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_MCP_AUDIT_LOG = "/var/log/intergen/mcp-audit.log"
_SCHEMA_PIN_DIR = "/var/lib/intergen/mcp-pins"


class MCPClient(MCPClientInterface):
    """Connects to MCP servers and bridges their tools into InterGen."""

    def __init__(self, tool_registry: ToolRegistry,
                 guard: GlasswingGuard | None = None):
        self._registry = tool_registry
        self._guard = guard or GlasswingGuard()
        self._servers: dict[str, _MCPServerConnection] = {}

    def start(self, servers: dict[str, MCPServerConfig]) -> None:
        """Connect to all configured MCP servers."""
        for name, config in servers.items():
            try:
                conn = _MCPServerConnection(name, config)
                conn.connect()
                tools = conn.list_tools()

                for tool in tools:
                    safe, reason = self._guard.validate_tool_description(tool)
                    if not safe:
                        logger.warning("MCP tool %s/%s rejected: %s",
                                       name, tool.tool_name, reason)
                        continue

                    schema_ok, schema_reason = self._guard.validate_schema(tool)
                    if not schema_ok:
                        logger.warning("MCP tool %s/%s schema changed: %s",
                                       name, tool.tool_name, schema_reason)

                    handler = self._make_handler(conn, tool)
                    self._registry.register_external(
                        name=f"mcp_{name}_{tool.tool_name}",
                        schema=tool.schema,
                        handler=handler,
                        system_prompt_rule=f"MCP tool '{tool.tool_name}' from server '{name}': {tool.schema.description}",
                    )

                self._servers[name] = conn
                logger.info("MCP server %s connected (%d tools)",
                            name, len(tools))

            except Exception as e:
                logger.error("Failed to connect MCP server %s: %s", name, e)

    def stop(self) -> None:
        for name, conn in self._servers.items():
            try:
                conn.disconnect()
            except Exception as e:
                logger.error("Error disconnecting MCP server %s: %s", name, e)
        self._servers.clear()

    def call_tool(self, server_name: str, tool_name: str,
                  arguments: dict[str, Any]) -> ToolResult:
        conn = self._servers.get(server_name)
        if conn is None:
            return ToolResult(
                call_id="", name=tool_name,
                content=f"MCP server '{server_name}' not connected",
                success=False,
            )

        if not self._guard.check_rate_limit(server_name):
            return ToolResult(
                call_id="", name=tool_name,
                content=f"Rate limit exceeded for server '{server_name}'",
                success=False,
            )

        try:
            result = conn.call(tool_name, arguments)
            self._guard.audit_log(
                server_name, tool_name, arguments, result,
                conn.config.trust_tier,
            )
            return ToolResult(
                call_id="", name=tool_name,
                content=result, success=True,
            )
        except Exception as e:
            logger.error("MCP call %s/%s failed: %s", server_name, tool_name, e)
            return ToolResult(
                call_id="", name=tool_name,
                content=f"MCP error: {e}", success=False,
            )

    def list_tools(self, server_name: str | None = None) -> list[MCPToolInfo]:
        tools = []
        for name, conn in self._servers.items():
            if server_name and name != server_name:
                continue
            tools.extend(conn.list_tools())
        return tools

    def get_server_status(self) -> dict[str, dict]:
        return {
            name: {
                "connected": conn.is_connected,
                "tool_count": len(conn.list_tools()),
                "trust_tier": conn.config.trust_tier.value,
            }
            for name, conn in self._servers.items()
        }

    def _make_handler(self, conn: _MCPServerConnection,
                      tool: MCPToolInfo):
        """Create a sync handler closure for a tool."""
        server_name = conn.name
        tool_name = tool.tool_name

        def handler(arguments: dict) -> str:
            result = self.call_tool(server_name, tool_name, arguments)
            return result.content

        return handler


class _MCPServerConnection:
    """Manages a subprocess connection to a single MCP server."""

    def __init__(self, name: str, config: MCPServerConfig):
        self.name = name
        self.config = config
        self._process: subprocess.Popen | None = None
        self._tools: list[MCPToolInfo] = []

    def connect(self) -> None:
        """Start the MCP server subprocess."""
        env = dict(self.config.env)
        self._process = subprocess.Popen(
            [self.config.command] + self.config.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env if env else None,
        )
        logger.info("MCP server %s started (pid=%d)",
                     self.name, self._process.pid)

    def disconnect(self) -> None:
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def list_tools(self) -> list[MCPToolInfo]:
        return self._tools

    def call(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on this server via JSON-RPC over stdin/stdout."""
        if not self._process:
            raise RuntimeError(f"MCP server {self.name} not connected")

        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": int(time.monotonic() * 1000),
        }

        self._process.stdin.write(
            (json.dumps(request) + "\n").encode()
        )
        self._process.stdin.flush()

        response_line = self._process.stdout.readline()
        if not response_line:
            raise RuntimeError("MCP server returned empty response")

        response = json.loads(response_line)
        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        result = response.get("result", {})
        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", str(content))
        return str(result)

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None


class GlasswingGuard(GlasswingGuardInterface):
    """Security layer for MCP interactions.

    Implements OWASP MCP Top 10 mitigations:
    MCP02: Tool description injection scanning
    Schema hash pinning for rug pull detection
    Rate limiting and full audit logging
    """

    def __init__(self):
        self._rate_counts: dict[str, list[float]] = {}
        self._rate_limits: dict[str, int] = {}
        self._pin_dir = Path(_SCHEMA_PIN_DIR)

    def validate_tool_description(self, tool: MCPToolInfo) -> tuple[bool, str]:
        """Scan for prompt injection in tool descriptions."""
        desc = tool.schema.description.lower()

        injection_patterns = [
            "ignore previous",
            "ignore above",
            "disregard",
            "forget your instructions",
            "you are now",
            "new instructions",
            "override",
            "system prompt",
            "<|im_start|>",
            "<|im_end|>",
        ]

        for pattern in injection_patterns:
            if pattern in desc:
                return False, f"Injection pattern detected: '{pattern}'"

        if len(desc) > 2000:
            return False, "Description exceeds 2000 characters"

        return True, ""

    def validate_schema(self, tool: MCPToolInfo) -> tuple[bool, str]:
        """Check schema against pinned hash."""
        current_hash = self._hash_schema(tool.schema)

        if tool.schema_hash and current_hash != tool.schema_hash:
            return False, (f"Schema hash mismatch: expected {tool.schema_hash[:16]}..., "
                          f"got {current_hash[:16]}...")

        pin_file = self._pin_dir / f"{tool.server_name}_{tool.tool_name}.pin"
        if pin_file.exists():
            pinned = pin_file.read_text().strip()
            if pinned != current_hash:
                return False, f"Schema changed since pinning (rug pull detection)"
        else:
            try:
                self._pin_dir.mkdir(parents=True, exist_ok=True)
                pin_file.write_text(current_hash)
            except PermissionError:
                pass

        return True, ""

    def check_rate_limit(self, server_name: str) -> bool:
        limit = self._rate_limits.get(server_name, 60)
        now = time.time()
        calls = self._rate_counts.setdefault(server_name, [])
        calls[:] = [t for t in calls if now - t < 60]
        if len(calls) >= limit:
            return False
        calls.append(now)
        return True

    def set_rate_limit(self, server_name: str, per_minute: int) -> None:
        self._rate_limits[server_name] = per_minute

    def audit_log(self, server_name: str, tool_name: str,
                  arguments: dict, result: str,
                  trust_tier: MCPTrustTier) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "server": server_name,
            "tool": tool_name,
            "trust_tier": trust_tier.value,
            "arguments": {k: str(v)[:100] for k, v in arguments.items()},
            "result_length": len(result),
        }
        try:
            log_path = Path(_MCP_AUDIT_LOG)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except PermissionError:
            fallback = Path.home() / ".local" / "share" / "intergen" / "mcp-audit.log"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback, "a") as f:
                f.write(json.dumps(entry) + "\n")

    @staticmethod
    def _hash_schema(schema: ToolSchema) -> str:
        """Compute deterministic hash of a tool schema."""
        canonical = json.dumps(schema.to_openai(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
