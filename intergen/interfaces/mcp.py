"""MCP (Model Context Protocol) client interface + Glasswing security."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

from intergen.interfaces.types import ToolSchema, ToolResult


class MCPTrustTier(Enum):
    SYSTEM = "system"
    VERIFIED = "verified"
    COMMUNITY = "community"
    UNTRUSTED = "untrusted"


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    trust_tier: MCPTrustTier = MCPTrustTier.UNTRUSTED
    intent_examples: list[str] = field(default_factory=list)
    rate_limit_per_minute: int = 60


@dataclass
class MCPToolInfo:
    server_name: str
    tool_name: str
    schema: ToolSchema
    schema_hash: str
    trust_tier: MCPTrustTier


class MCPClientInterface(ABC):
    """Abstract interface for MCP server management."""

    @abstractmethod
    def start(self, servers: dict[str, MCPServerConfig]) -> None:
        """Connect to all configured MCP servers.

        Discovers tools, validates schemas, registers with tool registry.
        """

    @abstractmethod
    def stop(self) -> None:
        """Disconnect from all MCP servers."""

    @abstractmethod
    def call_tool(self, server_name: str, tool_name: str,
                  arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool on an MCP server.

        Args:
            server_name: Which MCP server to call.
            tool_name: Tool name on that server.
            arguments: Tool arguments.

        Returns:
            ToolResult with content and success flag.
        """

    @abstractmethod
    def list_tools(self, server_name: str | None = None) -> list[MCPToolInfo]:
        """List discovered tools, optionally filtered by server."""

    @abstractmethod
    def get_server_status(self) -> dict[str, dict]:
        """Return status of all MCP servers (connected, tool count, etc.)."""


class GlasswingGuardInterface(ABC):
    """Security layer for MCP interactions.

    Validates tool descriptions (OWASP MCP02 injection scanning),
    pins schema hashes (rug pull detection), enforces trust tiers,
    rate limits, and logs all MCP activity.
    """

    @abstractmethod
    def validate_tool_description(self, tool: MCPToolInfo) -> tuple[bool, str]:
        """Scan tool description for prompt injection attempts.

        Returns:
            (safe: bool, reason: str). reason is empty if safe.
        """

    @abstractmethod
    def validate_schema(self, tool: MCPToolInfo) -> tuple[bool, str]:
        """Check tool schema against pinned hash.

        Returns:
            (valid: bool, reason: str). Fails if schema changed since pinning.
        """

    @abstractmethod
    def check_rate_limit(self, server_name: str) -> bool:
        """Return True if the server is within its rate limit."""

    @abstractmethod
    def audit_log(self, server_name: str, tool_name: str,
                  arguments: dict, result: str, trust_tier: MCPTrustTier) -> None:
        """Log MCP tool call to audit log at /var/log/intergen/mcp-audit.log."""
