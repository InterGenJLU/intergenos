# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
    image_data: str | None = None


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
    # Concise, structured, salient-first summary for the MODEL to synthesize
    # from (G3-22 real fix; see docs/architecture/intergen-structured-tool-
    # returns-design.md). When None, the model synthesizes from `content` (the
    # legacy path, capped to 4000 chars in continue_after_tool_call as the
    # safety-net floor). `content` ALWAYS remains the full payload for the
    # user-facing transcript + audit log; only the model-facing field shrinks.
    # A tool sets this only when it can produce large/unbounded output; every
    # existing tool keeps model_summary=None and behaves exactly as before.
    model_summary: str | None = None
    # HARD safety block: the command/action was refused by the safety classifier
    # and never ran. A structured signal (not a content-string match) so the
    # router can skip the synthesis hop on a blocked dispatch — otherwise the
    # model narrates a blocked destructive command as success (the dd-wipe
    # fabrication). Default False; only the safety-block paths set it.
    blocked: bool = False
    # Did the tool actually RUN? True for a real execution (even one that ran and
    # returned a non-zero/error result); False when the dispatch was refused /
    # denied / not-executed at the gate (unknown tool, validation error, gate
    # reject, egress block, no-review-UI, user-deny, token-mint fail). The synth
    # hop MUST distinguish these: a ran-but-errored result is explained from its
    # output, but a not-executed result must NOT be narrated as success (the
    # shutdown fabrication — the model was told "it executed" when it was denied).
    executed: bool = True


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
            # Provenance contract (the three enum-value semantics, the
            # rejection rule, and the no-ingress-instruction guidance) lives
            # ONCE in _PROVENANCE_DIRECTIVE (llm.py), which build_system_prompt
            # always includes when tools are offered — i.e. exactly when this
            # schema renders. The enum above constrains the values; this
            # description is a pointer, not a 9×-repeated copy of the contract.
            "description": "Provenance label per D-008 (see system instructions).",
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
    # Capability descriptor + license — sourced authoritatively from the SIGNED
    # models-manifest (overlaid by ModelManager._apply_manifest), not guessed.
    license_ref: str = ""        # SPDX/LicenseRef the acceptance gate keys on
    has_vision: bool = False     # paired vision projector → --mmproj
    cacheable: bool = False      # backbone supports prefix-cache → --cache-reuse
    mmproj_filename: str | None = None  # the paired projector's mirror filename
    mmproj_sha256: str = ""             # its pin (fail-closed when empty)
    mmproj_size_gb: float = 0.0
    mmproj_local_path: str | None = None  # set once the projector is installed
    # Vendor-side (Hugging Face) filenames, when they differ from the mirror
    # names above. The mirror disambiguates upstream's generic per-repo names
    # (unsloth ships every projector as mmproj-F16.gguf); the vendor fallback
    # URL must still use the name the vendor actually serves. None = identical
    # on both sources. The sha256 pin is filename-independent either way.
    vendor_filename: str | None = None
    mmproj_vendor_filename: str | None = None


@dataclass
class AnswerLinkage:
    """WHERE THE DELIVERED TEXT ACTUALLY CAME FROM.

    `source` on a RouteResult names the ROUTE that handled the turn; it does not
    say which artifact the reply was composed from. Those differ, and the gap is
    where a whole defect class lived: a package dispatch executed, its result was
    handed to the wrong renderer, and the reply was composed from disk-summary
    state instead — route, trace and dispatch all agreed, while the answer came
    from somewhere else entirely. Nothing recorded that, so the substitution was
    only ever found by reading the answer.

    This records the composition, so the linkage is a SIGNAL rather than an
    inference:

      kind="dispatch"  composed from a tool result — `tool`/`call_id` name which
      kind="cache"     composed from cached system state
      kind="code"      a deterministic code-owned string (a safety refusal, an
                       honest fallback, a staged-offer line) — no dispatch claim
      kind="model"     free model text with no dispatch behind it

    `renderer` names the composer (template / llm_synth / system_map / …) so a
    substitution is attributable, not just detectable.

    A reply carrying a successful dispatch whose linkage is NOT that dispatch is
    the substituted class. That check reads these fields ONLY — never text
    overlap, which is unsound here: two summarizers answer from an authoritative
    live source (/proc, shutil) by ratified design and legitimately share no
    token with the tool output beside them.
    """
    kind: str
    tool: str = ""
    call_id: str = ""
    renderer: str = ""

    def as_detail(self) -> dict:
        """Flat form for a glass row."""
        return {"kind": self.kind, "tool": self.tool,
                "call_id": self.call_id, "renderer": self.renderer}


@dataclass
class RouteResult:
    text: str = ""
    source: str = ""
    handled: bool = False
    # Full, unsummarized output behind a terse summary (e.g. the raw
    # `df`/`lscpu`/`lsusb` table when `text` is the one-line summary). Carried on
    # BOTH synthesis paths — the deterministic template AND the LLM paraphrase —
    # so the summariser is never the only witness of the original: the raw is
    # always retrievable (web "show full output" expander, CLI `intergen last
    # --raw`). Empty only when the dispatch produced no richer raw than the
    # summary itself (e.g. a pure freeform LLM answer with no tool output).
    full_output: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    used_llm: bool = False
    escalated: bool = False
    escalation_provider: str | None = None
    confidence: float = 1.0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    # Phone-a-friend OFFER (decision #4 heuristic half): when set, the local answer
    # was produced but the router recognizes the task may benefit from the user's
    # configured frontier model. The frontend surfaces this as an OFFER the user can
    # accept or ignore ("Want me to reach {provider}?"); acceptance routes through the
    # consent modal + Escalate (user_consented=True) — the SAME path as the
    # user-invoked affordance. None = no offer. Setting this NEVER sends anything; an
    # explicit human acceptance is always required first.
    escalation_offer: str | None = None
    # WHY a keyword rung declined, when it did. Empty on every handled result
    # and on every rung that is not the keyword rung.
    #
    # The keyword rung used to return a bare handled=False in three different
    # situations — nothing matched the clause, an intent matched but carries no
    # tool, and a tool matched whose dispatch did not succeed — and emit nothing
    # to tell them apart. Downstream, a clause NOBODY claimed and a clause a
    # carrier WANTED and could not serve looked identical, so a failed dispatch
    # could only ever be read as "unrecognised". One of the three:
    #   "no_intent"                — no keyword pattern claimed the clause
    #   "intent_without_tool"      — an intent matched that dispatches nothing
    #   "arguments_indeterminate"  — a carrier claimed it and could build no
    #                                arguments, so nothing was dispatched
    #   "dispatch_failed"          — a tool ran and did not succeed
    # This is a WITNESS, not a route: `handled` is unchanged in every case and no
    # caller is required to read it.
    decline_reason: str = ""
    # Links this result to its decision trace (trace.py). Empty unless
    # INTERGEN_TRACE is enabled; populated in router.route from the active
    # root span so the harness can join a turn's result to its trace.
    trace_id: str = ""
    # M3(i) re-offer reminder: a prefixed "Yes, <tail>" over a LIVE action offer
    # does NOT execute — it keeps the offer armed and routes the tail. This carries
    # the one-line code-owned reminder ("that offer is still standing…") that must
    # ride AFTER the tail answer. route() appends it inline for non-streamed turns;
    # the web streamer appends it after the streamed tail. None = no reminder.
    reoffer_reminder: str | None = None
    # M3(i) effective (stripped) input: when a prefixed reply's TAIL was routed
    # ("Yes, <tail>" / "No, <tail>"), this is the stripped tail the GENERATION must
    # use. The non-streamed paths already generate from the swapped input inside
    # _route_impl; the web streamer regenerates separately, so it reads this to
    # prompt the model with the clean tail (not "Yes, …", which — with the offer
    # now in history — stalls the small model). Empty = use the raw input.
    effective_input: str = ""
    # WHERE THE TEXT CAME FROM (see AnswerLinkage). None means the composing site
    # declared nothing — which is NOT the same as "code-owned", and must never be
    # read as one. The delivery surfaces record the absence on the trace so an
    # uninstrumented path is visible rather than silently exempt from the
    # substituted-result check.
    answer_linkage: AnswerLinkage | None = None


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    local: bool = True
    quality_passed: bool = True
    # Runtime semantic-health flags (G10 interface contract): empty == clean, each
    # entry is a corruption check name from intergen.semantic_health
    # (foreign_script_flood / system_prompt_echo / repetition_blowup /
    # charset_sanity). The engine-side reaction ladder consumes these; the
    # router-side per-flag reaction is a separate consumer.
    semantic_flags: list[str] = field(default_factory=list)


@dataclass
class ServerHealth:
    running: bool
    model_loaded: bool
    uptime_seconds: float = 0.0
    requests_served: int = 0
    last_error: str | None = None


class StartFailure(Enum):
    """Structured reason a llama-server ``start()`` failed.

    Lets the daemon classify a launch failure STRUCTURALLY — on the reason,
    not by string-matching ``_last_error`` — so a
    declared capability the running server failed to honor surfaces as ONE
    conspicuous "model-server integrity failure" distinct from the benign
    no-model-downloaded degrade.

    The INTEGRITY members are the declared-but-absent / declared-but-unserved
    cases: a capability the SIGNED models-manifest declares that the running
    server did not honor (projector missing, toolless template loaded,
    tools/vision not advertised at /props). Per the manifest's signing
    guarantee such a mismatch reads as tamper / corruption, not a routine miss
    — deny AND make it conspicuous. The operational members (model/binary
    absent, crash, spawn error) are the ordinary "the server didn't come up"
    class the daemon already degrades on.
    """
    NONE = auto()                   # no failure recorded (start succeeded / not yet run)
    # --- operational: the server didn't come up (benign degrade) ---
    MODEL_FILE_ABSENT = auto()      # the GGUF path does not exist
    BINARY_ABSENT = auto()          # llama-server binary not found
    SPAWN_ERROR = auto()            # OSError launching the subprocess
    UNHEALTHY = auto()              # never became healthy within the startup timeout
    PORT_IN_USE = auto()            # the target port is already held by another process (e.g. the GDM greeter session's own InterGen daemon) — our child cannot bind it; refuse rather than misread the foreign server's health as ours
    OFFLOAD_FAILED = auto()         # the discrete tier expected GPU acceleration but the model did not offload to the GPU (0 layers / unconfirmable) — the 9B/35B on CPU is unusably slow, so this is NOT a serve-anyway: the daemon falls to the 2B floor loudly. Distinct from the integrity class (not tamper — a hardware-capability shortfall) and from the benign no-model degrade (a model WAS selected; only the tier claim failed)
    # --- integrity: a declared capability the running server did not honor ---
    MMPROJ_MISSING = auto()         # has_vision declared but the projector is unset/absent
    CHAT_TEMPLATE_MISSING = auto()  # a tool-capable template was configured but is absent
    TOOLS_NOT_ADVERTISED = auto()   # /props lacks chat_template_caps.supports_tools
    VISION_NOT_ADVERTISED = auto()  # /props lacks modalities.vision for a vision model

    @property
    def is_integrity(self) -> bool:
        """True for a declared-but-unhonored capability failure — the daemon
        surfaces these as a single conspicuous integrity-failure state rather
        than the routine no-model degrade."""
        return self in _INTEGRITY_START_FAILURES

    @property
    def is_transient(self) -> bool:
        """True when a second attempt can plausibly succeed, so the daemon retries
        with a bounded back-off and keeps the manager (and therefore the watchdog)
        instead of going down for the life of the process. Disjoint from
        is_integrity by construction — see _TRANSIENT_START_FAILURES."""
        return self in _TRANSIENT_START_FAILURES


_INTEGRITY_START_FAILURES = frozenset({
    StartFailure.MMPROJ_MISSING,
    StartFailure.CHAT_TEMPLATE_MISSING,
    StartFailure.TOOLS_NOT_ADVERTISED,
    StartFailure.VISION_NOT_ADVERTISED,
})

# The failures a SECOND ATTEMPT can survive — the counterpart of the integrity
# set above, and for the same reason: the daemon decides structurally, on the
# reason, never by string-matching the error text.
#
# The daemon used to treat PORT_IN_USE as the only transient case and drop the
# manager for every other failure, which also removed the watchdog (it is built
# under `if self._llama:`), so a chat model that failed once was down for the
# life of the process. Measured on a dual-GPU workstation 2026-08-26: a start
# eleven seconds after a model re-drive released the same card recorded
# UNHEALTHY, was dropped, and the assistant answered nothing for two and a half
# hours — while the identical command run by hand later loaded and served
# normally and a plain restart brought it up first try. A momentarily-busy
# device is exactly what one attempt cannot survive.
#
#   UNHEALTHY   the child died or never answered /health — the measured case
#   PORT_IN_USE the holder releases the port (the cold-boot greeter collision)
#   SPAWN_ERROR an OSError launching the child (a transient resource limit)
#
# Deliberately NOT transient: MODEL_FILE_ABSENT and BINARY_ABSENT do not become
# true by waiting, and retrying them only delays an honest degrade;
# OFFLOAD_FAILED is a hardware-capability shortfall whose answer is the 2B floor,
# not another attempt; and every INTEGRITY member reads as tamper or corruption,
# where retrying is not recovery.
_TRANSIENT_START_FAILURES = frozenset({
    StartFailure.UNHEALTHY,
    StartFailure.PORT_IN_USE,
    StartFailure.SPAWN_ERROR,
})
