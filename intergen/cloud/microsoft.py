# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Microsoft Azure OpenAI adapter.

Wire body is OpenAI chat-completions (inherited), but Azure differs in three
ways: the user supplies their own resource ``base_url``; the model is the
*deployment* name embedded in the path; auth is the ``api-key`` header (not a
Bearer token); and an ``api-version`` query parameter is required.
"""
from __future__ import annotations

from intergen.cloud.http_adapter import CloudAdapterError, HTTPCloudAdapter


class MicrosoftAdapter(HTTPCloudAdapter):
    provider_label = "microsoft"
    #: Azure OpenAI stable data-plane API version. Overridable via subclass.
    api_version = "2024-06-01"

    def _base_url(self) -> str:
        if not self._config.base_url:
            raise CloudAdapterError(
                "the 'microsoft' (Azure OpenAI) adapter requires base_url "
                "(your Azure resource endpoint, e.g. "
                "https://<resource>.openai.azure.com)"
            )
        return self._config.base_url.rstrip("/")

    def _endpoint(self) -> str:
        # model == the Azure deployment name.
        return (
            f"{self._base_url()}/openai/deployments/{self._config.model}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"api-key": api_key}
