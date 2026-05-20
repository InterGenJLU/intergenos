"""Shared types and dataclasses used across InterGen modules."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterator

from intergen.interfaces.provenance import Provenance


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
    """A tool invocation request.

    source_of_request is REQUIRED per D-008 RFC §5.3 no-fallback policy
    (docs/architecture/intergen-provenance-gate-design.md). Constructed as
    Optional in the dataclass signature so existing call sites continue to
    type-check during the staged migration, but __post_init__ raises if it
    is left None. The dispatcher gate refuses to execute any ToolCall whose
    declared source_of_request is missing.
    """
    name: str
    arguments: dict[str, Any]
    call_id: str = ""
    source_of_request: "Provenance | None" = None  # required; validated in __post_init__

    def __post_init__(self) -> None:
        if self.source_of_request is None:
            raise ValueError(
                f"ToolCall.source_of_request is REQUIRED per D-008 RFC §5.3 "
                f"no-fallback policy. Tool: {self.name!r}. "
                f"The LLM system-prompt (§8) instructs the model to declare a "
                f"provenance label on every tool call; missing label means the "
                f"call MUST be rejected at the dispatcher."
            )


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
        # D-008 RFC §8: every tool call must declare a source_of_request
        # provenance label. Inject as a required enum on every tool's
        # argument schema so the LLM emits it alongside the user-defined
        # arguments. The dispatcher strips it from arguments before
        # passing them to the tool implementation.
        params = dict(self.parameters) if self.parameters else {"type": "object"}
        properties = dict(params.get("properties", {}))
        properties["source_of_request"] = {
            "type": "string",
            "enum": ["user_direct", "user_implied", "ingress_derived"],
            "description": (
                "Provenance label per D-008. user_direct = the user "
                "explicitly asked for this in their current message; "
                "user_implied = a reasonable follow-on the user would "
                "expect; ingress_derived = the action emerged from "
                "content you fetched or read, not user-authored."
            ),
        }
        required = list(params.get("required", []))
        if "source_of_request" not in required:
            required.append("source_of_request")
        params["properties"] = properties
        params["required"] = required
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
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
    tokens_prompt: int = 0
    tokens_completion: int = 0


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
