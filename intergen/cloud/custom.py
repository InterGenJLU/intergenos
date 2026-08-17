# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Custom adapter — any OpenAI-compatible endpoint the user supplies.

Identical wire shape to the OpenAI adapter, but there is NO default endpoint:
the user MUST provide ``base_url`` in the provider config (a self-hosted or
third-party OpenAI-compatible server). This is the escape hatch that keeps the
substrate genuinely vendor-neutral.
"""
from __future__ import annotations

from intergen.cloud.http_adapter import CloudAdapterError, HTTPCloudAdapter


class CustomAdapter(HTTPCloudAdapter):
    provider_label = "custom"
    default_base_url = ""  # no default — the user must supply base_url

    def _base_url(self) -> str:
        if not self._config.base_url:
            raise CloudAdapterError(
                "the 'custom' adapter requires base_url in the provider config "
                "(the OpenAI-compatible endpoint to use)"
            )
        return self._config.base_url.rstrip("/")
