"""Shared types and dataclasses used across InterGen modules."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator


class SafetyTier(Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


class HardwareTierLevel(Enum):
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3


class EscalationMode(Enum):
    NEVER = "never"
    FALLBACK = "fallback"
    ASK = "ask"
    AUTO = "auto"


class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: MessageRole
    content: str
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    success: bool = True


@dataclass
class ToolSchema:
    """OpenAI-compatible function calling schema."""
    name: str
    description: str
    parameters: dict[str, Any]
    safety_tier: SafetyTier = SafetyTier.AUTO

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


@dataclass
class HardwareTier:
    ram_gb: float
    gpu_vendor: str | None
    gpu_model: str | None
    gpu_vram_mb: int | None
    tier: HardwareTierLevel
    recommended_model: str
    recommended_quant: str
    estimated_model_size_gb: float


@dataclass
class ModelInfo:
    name: str
    filename: str
    repo_id: str
    quant: str
    size_gb: float
    sha256: str
    tier: HardwareTierLevel
    local_path: str | None = None
    downloaded: bool = False


@dataclass
class RouteResult:
    text: str = ""
    source: str = ""
    handled: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    used_llm: bool = False
    escalated: bool = False
    escalation_provider: str | None = None
    confidence: float = 1.0


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    local: bool = True
    quality_passed: bool = True


@dataclass
class ServerHealth:
    running: bool
    model_loaded: bool
    uptime_seconds: float = 0.0
    requests_served: int = 0
    last_error: str | None = None
