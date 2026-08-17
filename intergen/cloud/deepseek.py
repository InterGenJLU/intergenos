# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""DeepSeek adapter — OpenAI-compatible chat-completions endpoint."""
from __future__ import annotations

from intergen.cloud.http_adapter import HTTPCloudAdapter


class DeepSeekAdapter(HTTPCloudAdapter):
    provider_label = "deepseek"
    default_base_url = "https://api.deepseek.com/v1"
