# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Anthropic adapter — native Messages API (NOT OpenAI-shaped).

Anthropic's API is not OpenAI-compatible: it posts to /v1/messages, carries the
system prompt as a top-level field (not a system message), requires max_tokens,
authenticates with the ``x-api-key`` + ``anthropic-version`` headers, and returns
content as a list of typed blocks. So this adapter overrides the shaping hooks
rather than using the OpenAI base shape. (Forcing OpenAI format on Anthropic
would be wrong — provider-native shaping, confirmed in the substrate plan.)
"""
from __future__ import annotations

from intergen.cloud.http_adapter import HTTPCloudAdapter
from intergen.interfaces.types import LLMResponse, Message, MessageRole, ToolSchema


class AnthropicAdapter(HTTPCloudAdapter):
    provider_label = "anthropic"
    default_base_url = "https://api.anthropic.com"
    #: Anthropic API version pin (sent as the anthropic-version header).
    anthropic_version = "2023-06-01"

    def _endpoint(self) -> str:
        return f"{self._base_url()}/v1/messages"

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-api-key": api_key, "anthropic-version": self.anthropic_version}

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict:
        # System prompts are a top-level field in Anthropic, not a message.
        system_parts = [
            m.content for m in messages if m.role is MessageRole.SYSTEM
        ]
        # user / assistant turns. TOOL-role content is folded into a user turn
        # as plain text for the v1 text substrate (structured tool_result blocks
        # are a follow-up once tool-calling rides the cloud path). NOTE for that
        # follow-up: Anthropic requires turns to alternate user/assistant
        # starting with user; this folding can yield consecutive user turns in a
        # multi-turn/tool flow (HTTP 400), so coalesce same-role turns then.
        convo = [
            {
                "role": "assistant" if m.role is MessageRole.ASSISTANT else "user",
                "content": m.content,
            }
            for m in messages
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL)
        ]
        body: dict = {
            "model": self._config.model,
            "max_tokens": max_tokens or self._config.max_tokens,
            "messages": convo,
            "temperature": (
                temperature if temperature is not None else self._config.temperature
            ),
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters or {"type": "object"},
                }
                for t in tools
            ]
        return body

    def _parse_response(self, data: dict) -> LLMResponse:
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        )
        usage = data.get("usage", {}) or {}
        return LLMResponse(
            text=text,
            model=data.get("model") or self._config.model,
            tokens_prompt=usage.get("input_tokens", 0),
            tokens_completion=usage.get("output_tokens", 0),
            local=False,
        )
