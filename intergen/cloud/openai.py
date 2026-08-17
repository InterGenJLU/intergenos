# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""OpenAI adapter — the reference OpenAI chat-completions endpoint.

The base HTTPCloudAdapter already speaks the OpenAI chat-completions shape, so
this adapter only pins the provider label and the default API root.
"""
from __future__ import annotations

from intergen.cloud.http_adapter import HTTPCloudAdapter


class OpenAIAdapter(HTTPCloudAdapter):
    provider_label = "openai"
    default_base_url = "https://api.openai.com/v1"
