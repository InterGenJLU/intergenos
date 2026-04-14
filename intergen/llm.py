"""InterGen LLM router — local llama.cpp + cloud escalation.

Ported from JARVIS core/llm_router.py. Key differences:
- Uses llama-server HTTP API (not llama-cli binary)
- Cloud escalation is provider-agnostic (not Anthropic-only)
- Quality gate integrated into chat() flow
- Simplified system prompt (system-focused, not general assistant)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterator

import requests

from intergen.interfaces.llm import LLMInterface
from intergen.interfaces.types import (
    EscalationMode, LLMResponse, Message, MessageRole, ToolCall, ToolSchema,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are InterGen, the AI assistant built into InterGenOS. You help users understand, manage, and secure their system.

Key traits:
- You know the system inside and out — packages, services, hardware, logs, network
- You are honest about what you don't know
- You use tools to check real system state rather than guessing
- You explain what you're doing and why
- You ask for confirmation before making changes
- You never fabricate system information

You are running on InterGenOS, a Linux distribution built entirely from source (LFS-based, GNOME on Wayland)."""


class LLMRouter(LLMInterface):
    """Routes LLM requests to local llama-server or cloud providers."""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}

        self._endpoint = config.get(
            "endpoint", "http://127.0.0.1:8080/v1/chat/completions"
        )
        self._temperature = config.get("temperature", 0.6)
        self._top_p = config.get("top_p", 0.8)
        self._top_k = config.get("top_k", 20)
        self._max_tokens_default = config.get("max_tokens", 4096)
        self._tool_calling = config.get("tool_calling", True)
        self._presence_penalty = config.get("presence_penalty", 1.5)

        self._escalation_mode = EscalationMode(
            config.get("escalation_mode", "ask")
        )
        self._cloud_providers: dict[str, Any] = {}

        self._api_call_count = 0
        self._last_call_info: dict[str, Any] | None = None

    # ── Core streaming ──

    def stream(self, messages: list[Message], *,
               max_tokens: int | None = None,
               temperature: float | None = None) -> Iterator[str]:
        """Stream tokens from local LLM."""
        msg_dicts = self._to_openai_messages(messages)
        payload = {
            "messages": msg_dicts,
            "temperature": temperature or self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "max_tokens": max_tokens or self._max_tokens_default,
            "stream": True,
        }

        try:
            response = requests.post(
                self._endpoint, json=payload,
                timeout=60, stream=True,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error("Local LLM request failed: %s", e)
            return

        yield from self._parse_sse_stream(response)

    def stream_with_tools(self, messages: list[Message], *,
                          tools: list[ToolSchema],
                          max_tokens: int | None = None,
                          temperature: float | None = None) -> Iterator[str | ToolCall]:
        """Stream tokens with tool calling support.

        Tool calls come as fragmented JSON across SSE chunks.
        Arguments are accumulated and yielded as a single ToolCall.
        """
        if not self._tool_calling or not tools:
            yield from self.stream(messages, max_tokens=max_tokens,
                                   temperature=temperature)
            return

        msg_dicts = self._to_openai_messages(messages)
        tool_dicts = [t.to_openai() for t in tools]

        payload = {
            "messages": msg_dicts,
            "temperature": temperature or self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "max_tokens": max_tokens or self._max_tokens_default,
            "stream": True,
            "tools": tool_dicts,
            "tool_choice": "auto",
        }
        if self._presence_penalty is not None:
            payload["presence_penalty"] = self._presence_penalty

        try:
            response = requests.post(
                self._endpoint, json=payload,
                timeout=60, stream=True,
            )
        except requests.RequestException as e:
            logger.error("Local LLM tool request failed: %s", e)
            return

        if response.status_code == 400:
            self._handle_context_overflow(response, payload)
            try:
                response = requests.post(
                    self._endpoint, json=payload,
                    timeout=60, stream=True,
                )
            except requests.RequestException as e:
                logger.error("Retry after context overflow failed: %s", e)
                return

        if response.status_code != 200:
            logger.error("LLM returned status %d", response.status_code)
            return

        tool_call_id = ""
        tool_call_name = ""
        tool_call_args = ""
        is_tool_call = False
        input_tokens = 0
        output_tokens = 0

        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "):
                continue
            data = line_str[6:]
            if data.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(data)

                timings = chunk.get("timings")
                if timings:
                    input_tokens = timings.get("prompt_n", 0)
                    output_tokens = timings.get("predicted_n", 0)

                delta = chunk["choices"][0].get("delta", {})
                finish_reason = chunk["choices"][0].get("finish_reason")

                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    is_tool_call = True
                    tc = tool_calls[0]
                    if tc.get("id"):
                        tool_call_id = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        tool_call_name = func["name"]
                    if func.get("arguments"):
                        tool_call_args += func["arguments"]
                    continue

                # Qwen3.5 reasoning: skip reasoning_content tokens,
                # only yield final content to user
                token = delta.get("content", "")
                if token:
                    yield token

                if finish_reason == "tool_calls" and is_tool_call:
                    args = self._parse_tool_args(tool_call_args)
                    logger.info("Tool call: %s(%s)", tool_call_name, args)
                    yield ToolCall(
                        name=tool_call_name,
                        arguments=args,
                        call_id=tool_call_id,
                    )
                    return

            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.debug("Skipping malformed SSE chunk: %s", e)
                continue

        if is_tool_call and tool_call_name:
            args = self._parse_tool_args(tool_call_args)
            logger.info("Tool call (no finish_reason): %s(%s)",
                        tool_call_name, args)
            yield ToolCall(
                name=tool_call_name,
                arguments=args,
                call_id=tool_call_id,
            )

    # ── Non-streaming chat with quality gate ──

    def chat(self, messages: list[Message], *,
             max_tokens: int | None = None,
             temperature: float | None = None) -> LLMResponse:
        """Generate response: local → quality gate → retry → cloud fallback."""
        max_tok = max_tokens or self._max_tokens_default
        user_msg = self._extract_user_message(messages)

        # Attempt 1: local
        t0 = time.monotonic()
        tokens = list(self.stream(messages, max_tokens=max_tok,
                                  temperature=temperature))
        response_text = "".join(tokens)
        elapsed = (time.monotonic() - t0) * 1000

        quality_issue = self.check_quality(response_text, user_msg)
        if not quality_issue:
            return LLMResponse(
                text=self._strip_filler(response_text),
                model="local", local=True,
            )

        # Qwen3.5 reasoning: if content is empty, the model may have spent
        # all tokens on chain-of-thought. Retry with /no_think tag or
        # higher max_tokens.
        if quality_issue == "empty":
            logger.warning("Empty response — may be reasoning model token "
                           "exhaustion. Retrying with higher max_tokens.")
            max_tok = min(max_tok * 2, 8192)

        logger.warning("Local LLM quality issue (%s) — retrying", quality_issue)

        # Attempt 2: retry with nudge
        nudged = list(messages)
        nudged.append(Message(
            role=MessageRole.USER,
            content=(f"{user_msg}\n\nPlease provide a direct, helpful answer. "
                     "Do not use extended reasoning — answer concisely."),
        ))
        t0 = time.monotonic()
        tokens = list(self.stream(nudged, max_tokens=max_tok,
                                  temperature=temperature))
        response_text = "".join(tokens)

        quality_issue = self.check_quality(response_text, user_msg)
        if not quality_issue:
            return LLMResponse(
                text=self._strip_filler(response_text),
                model="local", local=True, quality_passed=True,
            )

        logger.warning("Local LLM failed twice (%s)", quality_issue)

        # Attempt 3: cloud escalation
        if self._escalation_mode == EscalationMode.NEVER:
            return LLMResponse(
                text=response_text or "",
                model="local", local=True, quality_passed=False,
            )

        cloud_response = self._escalate_to_cloud(messages, max_tokens=max_tok)
        if cloud_response:
            return cloud_response

        return LLMResponse(
            text=response_text or "",
            model="local", local=True, quality_passed=False,
        )

    # ── Quality gate ──

    def check_quality(self, response: str, user_message: str) -> str:
        """Check response quality. Returns empty string if OK, reason if not."""
        if not response or not response.strip():
            return "empty"

        text = response.strip()
        if len(text) < 3:
            return "too_short"

        words = text.lower().split()
        if len(words) >= 10:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.25:
                return "repetitive"

        if (user_message
                and text.lower().strip("?.! ") == user_message.lower().strip("?.! ")):
            return "echo"

        bad_markers = [
            "<|im_start|>", "<|im_end|>", "[INST]", "[/INST]",
            "<<SYS>>", "<think>", "</think>",
        ]
        for marker in bad_markers:
            if marker in text:
                return "artifacts"

        return ""

    # ── Escalation mode ──

    def get_escalation_mode(self) -> EscalationMode:
        return self._escalation_mode

    def set_escalation_mode(self, mode: EscalationMode) -> None:
        self._escalation_mode = mode
        logger.info("Escalation mode set to: %s", mode.value)

    # ── Cloud escalation ──

    def register_cloud_provider(self, name: str, adapter: Any) -> None:
        """Register a cloud provider adapter for escalation."""
        self._cloud_providers[name] = adapter
        logger.info("Registered cloud provider: %s", name)

    def _escalate_to_cloud(self, messages: list[Message], *,
                           max_tokens: int | None = None) -> LLMResponse | None:
        """Attempt cloud escalation with registered providers."""
        if not self._cloud_providers:
            logger.warning("No cloud providers configured for escalation")
            return None

        for name, adapter in self._cloud_providers.items():
            try:
                logger.info("Escalating to cloud provider: %s", name)
                result = adapter.send(messages, max_tokens=max_tokens)
                self._api_call_count += 1
                return LLMResponse(
                    text=result.text,
                    model=f"cloud:{name}",
                    tokens_prompt=result.tokens_prompt,
                    tokens_completion=result.tokens_completion,
                    local=False,
                    quality_passed=True,
                )
            except Exception as e:
                logger.error("Cloud provider %s failed: %s", name, e)
                continue

        return None

    # ── Internal helpers ──

    def _parse_sse_stream(self, response: requests.Response) -> Iterator[str]:
        """Parse SSE stream and yield text tokens.

        Qwen3.5 is a reasoning model: chain-of-thought goes into
        'reasoning_content' and the final answer into 'content'.
        We only yield 'content' tokens to the user. If the model
        finishes with content empty but reasoning_content populated,
        it likely ran out of tokens mid-thought.
        """
        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "):
                continue
            data = line_str[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    yield token
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

    def _handle_context_overflow(self, response: requests.Response,
                                 payload: dict) -> None:
        """Trim messages on context overflow (400 error)."""
        try:
            err = response.json().get("error", {})
        except Exception:
            return
        if err.get("type") == "exceed_context_size_error":
            msgs = payload["messages"]
            if len(msgs) > 3:
                payload["messages"] = [msgs[0]] + msgs[-2:]
                logger.warning("Context overflow — trimmed to 3 messages")

    @staticmethod
    def _parse_tool_args(raw: str) -> dict:
        """Parse accumulated tool call arguments JSON."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"query": raw}

    @staticmethod
    def _to_openai_messages(messages: list[Message]) -> list[dict]:
        """Convert Message list to OpenAI-compatible dicts."""
        result = []
        for msg in messages:
            d: dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content,
            }
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name:
                d["name"] = msg.name
            result.append(d)
        return result

    @staticmethod
    def _extract_user_message(messages: list[Message]) -> str:
        """Extract the last user message text."""
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                return msg.content
        return ""

    @staticmethod
    def _strip_filler(text: str) -> str:
        """Strip trailing 'feel free to ask' filler from responses."""
        filler = [
            r"\s*(?:Feel free|Don't hesitate|Let me know|If you (?:have|need)|"
            r"I(?:'m| am) here|Happy to help|Is there anything).*$"
        ]
        for pattern in filler:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
        return text.rstrip()

    def build_system_messages(self, extra_context: str = "") -> list[Message]:
        """Build the system prompt as a Message list."""
        prompt = _SYSTEM_PROMPT
        if extra_context:
            prompt += f"\n\n{extra_context}"
        return [Message(role=MessageRole.SYSTEM, content=prompt)]

    @property
    def api_call_count(self) -> int:
        return self._api_call_count
