# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Adapter factory — maps a ProviderConfig.adapter name to its adapter class.

There is deliberately NO default provider: local-only ships ready, and the user
opts into a cloud provider by configuring one. An unknown adapter name is a
hard error, never a silent fallback (default-deny posture).
"""
from __future__ import annotations

from intergen.cloud.anthropic import AnthropicAdapter
from intergen.cloud.custom import CustomAdapter
from intergen.cloud.deepseek import DeepSeekAdapter
from intergen.cloud.google import GoogleAdapter
from intergen.cloud.http_adapter import CloudAdapterError, HTTPCloudAdapter
from intergen.cloud.microsoft import MicrosoftAdapter
from intergen.cloud.openai import OpenAIAdapter
from intergen.cloud.xai import XAIAdapter
from intergen.interfaces.cloud import ProviderConfig

#: The ratified canonical set (design plan decision 7): Anthropic, Google,
#: Microsoft, OpenAI, xAI, DeepSeek + custom. Mistral is intentionally NOT here.
ADAPTERS: dict[str, type[HTTPCloudAdapter]] = {
    "anthropic": AnthropicAdapter,
    "google": GoogleAdapter,
    "microsoft": MicrosoftAdapter,
    "openai": OpenAIAdapter,
    "xai": XAIAdapter,
    "deepseek": DeepSeekAdapter,
    "custom": CustomAdapter,
}


def create_adapter(config: ProviderConfig) -> HTTPCloudAdapter:
    """Instantiate the adapter named by ``config.adapter``.

    Raises CloudAdapterError on an unknown adapter name (default-deny — never a
    silent fallback to some default provider).
    """
    key = (config.adapter or "").strip().lower()
    cls = ADAPTERS.get(key)
    if cls is None:
        raise CloudAdapterError(
            f"unknown cloud adapter '{config.adapter}'. "
            f"Known adapters: {', '.join(sorted(ADAPTERS))}"
        )
    return cls(config)
