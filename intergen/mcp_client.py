# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen MCP client — bridges external MCP servers into the tool pipeline.

Ported from a prior internal AI assistant project. Connects to MCP servers via
subprocess, discovers tools, validates with Sentinel security, and
registers with the tool registry.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import select
import subprocess
import time
from pathlib import Path
from typing import Any

from intergen.interfaces.mcp import (
    SentinelGuardInterface, MCPClientInterface, MCPServerConfig,
    MCPToolInfo, MCPTrustTier,
)
from intergen.interfaces.types import ToolResult, ToolSchema, SafetyTier
from intergen.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_MCP_AUDIT_LOG = "/var/log/intergen/mcp-audit.log"
# Schema-pin store. The InterGen daemon runs in the USER context (web-token in
# ~/.config, etc.), so the pin dir MUST be user-writable. The old root path
# /var/lib/intergen/mcp-pins was unwritable by the user daemon -> the first-use
# pin write silently failed -> rug-pull (TOFU) protection was silently OFF on
# every run (AI-7 review). Use a per-user location, matching the
# audit_log ~/.local/share/intergen fallback + setup.py's SESSIONS_DIR.
_SCHEMA_PIN_DIR = Path.home() / ".local" / "share" / "intergen" / "mcp-pins"

# MCP stdio JSON-RPC protocol constants.
# 2024-11-05 is the stable MCP protocol revision. We send raw JSON-RPC over
# the server's stdin/stdout (no `mcp` SDK dependency — keeps intergen's
# offline-built footprint minimal and matches the existing call() path).
_MCP_PROTOCOL_VERSION = "2024-11-05"
# Per-request read timeout (seconds). Bounds connect()/call() so a broken or
# non-responsive server cannot hang daemon startup or a tool call forever —
# connect() now reads during the handshake, which the prior stub did not, so
# the timeout closes the hang-on-startup window that adding reads would open.
_MCP_IO_TIMEOUT = 30.0


class MCPClient(MCPClientInterface):
    """Connects to MCP servers and bridges their tools into InterGen."""

    def __init__(self, tool_registry: ToolRegistry,
                 guard: SentinelGuard | None = None):
        self._registry = tool_registry
        self._guard = guard or SentinelGuard()
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
                        # Fail CLOSED on a schema-pin violation (rug-pull / TOFU
                        # change). A tool whose schema changed since first-use
                        # pinning is a primary MCP supply-chain threat; detecting
                        # it but registering anyway is a fail-open. Skip
                        # registration — matching the description-scan path
                        # above. A legitimate schema UPDATE (server upgrade)
                        # must go through an explicit re-pin/consent path, never
                        # a silent re-expose.
                        logger.warning(
                            "MCP tool %s/%s REJECTED (schema-pin violation): %s",
                            name, tool.tool_name, schema_reason,
                        )
                        continue

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
        self._next_id = 0
        # Raw read buffer. We read the server's stdout at the fd level (under
        # select() for timeout) rather than via the BufferedReader, so the
        # buffered object must NOT be readline()'d elsewhere — every read goes
        # through _read_response, which owns this buffer.
        self._read_buf = b""

    def connect(self) -> None:
        """Start the MCP server subprocess and complete the MCP handshake.

        Per the MCP lifecycle (stdio transport):
          1. spawn the server subprocess
          2. initialize request -> initialize response (capability exchange)
          3. notifications/initialized (client signals readiness)
          4. tools/list -> populate self._tools (each as an MCPToolInfo with a
             pinned schema_hash)

        Raises on handshake/discovery failure after tearing the subprocess
        down, so MCPClient.start() can log + skip a bad server without
        leaking a half-initialized process.
        """
        env = dict(self.config.env)
        # stderr -> DEVNULL: we read stdout only (_read_response), so a PIPE'd
        # stderr is never drained and a server that writes >~64KB to it would
        # fill the pipe buffer and block. We surface failures via our own
        # handshake/timeout messages (and Popen raises FileNotFoundError for a
        # missing command), so discarding server stderr costs no diagnostics we
        # rely on. (AI-7 review hardening.)
        self._process = subprocess.Popen(
            [self.config.command] + self.config.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env if env else None,
        )
        logger.info("MCP server %s started (pid=%d)",
                     self.name, self._process.pid)

        try:
            self._handshake()
            self._tools = self._discover_tools()
        except Exception:
            # Don't leak a half-initialized server subprocess on failure.
            self.disconnect()
            raise

        logger.info("MCP server %s initialized (%d tools discovered)",
                    self.name, len(self._tools))

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _send(self, message: dict) -> None:
        """Write one JSON-RPC message (request or notification) + newline."""
        if not self._process or not self._process.stdin:
            raise RuntimeError(f"MCP server {self.name} not connected")
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        self._process.stdin.flush()

    def _read_response(self, expected_id: int,
                       timeout: float = _MCP_IO_TIMEOUT) -> dict:
        """Read JSON-RPC messages until the response with expected_id arrives.

        Reads the server's stdout at the fd level under select() so a
        non-responsive server times out (RuntimeError) instead of blocking
        the daemon. Notifications (no matching id) and non-JSON lines (some
        servers log to stdout) are skipped. Owns self._read_buf — the only
        reader of the process stdout.
        """
        if not self._process or not self._process.stdout:
            raise RuntimeError(f"MCP server {self.name} not connected")
        fd = self._process.stdout.fileno()
        deadline = time.monotonic() + timeout
        while True:
            # Drain any complete buffered lines first (a single read may carry
            # several messages; the next read may carry a partial line).
            while b"\n" in self._read_buf:
                line, self._read_buf = self._read_buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # non-JSON stdout noise — skip
                if isinstance(msg, dict) and msg.get("id") == expected_id:
                    return msg
                # notification / unrelated response — keep scanning
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"MCP server {self.name} timed out awaiting response "
                    f"id={expected_id}"
                )
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError(
                    f"MCP server {self.name} closed the connection "
                    f"(EOF awaiting id={expected_id})"
                )
            self._read_buf += chunk

    def _handshake(self) -> None:
        """initialize request/response + initialized notification."""
        init_id = self._new_id()
        self._send({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "intergen", "version": "0.1.0"},
            },
        })
        resp = self._read_response(init_id)
        if "error" in resp:
            raise RuntimeError(f"MCP initialize failed: {resp['error']}")
        result = resp.get("result", {})
        info = result.get("serverInfo", {})
        logger.info(
            "MCP server %s handshake: %s v%s (protocol %s)",
            self.name, info.get("name", "?"), info.get("version", "?"),
            result.get("protocolVersion", "?"),
        )
        # The client signals readiness with an 'initialized' notification
        # (no id, no response) before issuing any further requests.
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _discover_tools(self) -> list[MCPToolInfo]:
        """tools/list -> [MCPToolInfo] with discovery-time pinned schema hash."""
        list_id = self._new_id()
        self._send({
            "jsonrpc": "2.0", "id": list_id, "method": "tools/list",
            "params": {},
        })
        resp = self._read_response(list_id)
        if "error" in resp:
            raise RuntimeError(f"MCP tools/list failed: {resp['error']}")
        raw_tools = resp.get("result", {}).get("tools", [])
        discovered: list[MCPToolInfo] = []
        for t in raw_tools:
            tool_name = t.get("name")
            if not tool_name:
                logger.warning(
                    "MCP server %s returned a tool with no name; skipping",
                    self.name,
                )
                continue
            schema = ToolSchema(
                name=tool_name,
                description=t.get("description", ""),
                parameters=t.get("inputSchema") or {"type": "object"},
            )
            discovered.append(MCPToolInfo(
                server_name=self.name,
                tool_name=tool_name,
                schema=schema,
                schema_hash=SentinelGuard._hash_schema(schema),
                trust_tier=self.config.trust_tier,
            ))
        return discovered

    def disconnect(self) -> None:
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()  # reap after kill -> no zombie
            self._process = None

    def list_tools(self) -> list[MCPToolInfo]:
        return self._tools

    def call(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on this server via JSON-RPC over stdin/stdout."""
        if not self._process:
            raise RuntimeError(f"MCP server {self.name} not connected")

        call_id = self._new_id()
        self._send({
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })

        response = self._read_response(call_id)
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


class SentinelGuard(SentinelGuardInterface):
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
            except OSError as exc:
                # Do NOT swallow silently. A tool whose schema cannot be pinned
                # has NO cross-session rug-pull (TOFU) protection on subsequent
                # runs — silently returning "OK" claims protection that isn't
                # there (AI-7 finding). With the pin dir now in a
                # user-writable location, a failure here should be rare
                # (read-only home / disk full); make the degraded state
                # OBSERVABLE. (Fail-closed — refusing the tool when it cannot be
                # pinned — is the stricter alternative if the operator wants it;
                # we register + warn so a transient write failure does not block
                # all MCP tools.)
                logger.warning(
                    "MCP schema pin-write failed for %s/%s (%s: %s); "
                    "rug-pull protection will NOT persist for this tool",
                    tool.server_name, tool.tool_name,
                    type(exc).__name__, exc,
                )

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
