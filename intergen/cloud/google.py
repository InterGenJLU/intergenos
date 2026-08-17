# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Google adapter — native Gemini generateContent API (NOT OpenAI-shaped).

Gemini posts to /v1beta/models/<model>:generateContent, shapes the conversation
as ``contents`` of ``parts``, carries the system prompt as ``systemInstruction``,
and authenticates with the ``x-goog-api-key`` header (used in preference to the
?key= URL parameter so the key never lands in a logged URL). So this adapter
overrides the shaping hooks rather than using the OpenAI base shape.
"""
from __future__ import annotations

from intergen.cloud.http_adapter import HTTPCloudAdapter
from intergen.interfaces.types import LLMResponse, Message, MessageRole, ToolSchema


class GoogleAdapter(HTTPCloudAdapter):
    provider_label = "google"
    default_base_url = "https://generativelanguage.googleapis.com"
    api_version = "v1beta"

    def _endpoint(self) -> str:
        return (
            f"{self._base_url()}/{self.api_version}"
            f"/models/{self._config.model}:generateContent"
        )

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        # Header auth (not ?key=) so the key never lands in a logged URL.
        return {"x-goog-api-key": api_key}

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict:
        system_parts = [
            m.content for m in messages if m.role is MessageRole.SYSTEM
        ]
        contents = [
            {
                "role": "model" if m.role is MessageRole.ASSISTANT else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL)
        ]
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens or self._config.max_tokens,
                "temperature": (
                    temperature
                    if temperature is not None
                    else self._config.temperature
                ),
            },
        }
        if system_parts:
            body["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        # Gemini tool-calling (functionDeclarations) lands with the tool-calling
        # follow-up; the v1 substrate is text-in / text-out.
        return body

    def _parse_response(self, data: dict) -> LLMResponse:
        candidates = data.get("candidates") or []
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", []) or []
            text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {}) or {}
        return LLMResponse(
            text=text,
            model=self._config.model,
            tokens_prompt=usage.get("promptTokenCount", 0),
            tokens_completion=usage.get("candidatesTokenCount", 0),
            local=False,
        )
