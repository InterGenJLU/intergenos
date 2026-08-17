# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""HTTPCloudAdapter — vendor-neutral raw-HTTP base for cloud provider adapters.

The shared substrate of the Sentinel + phone-a-friend mandate (consolidated
design plan 2026-05-30, section 1). ONE adapter layer used by BOTH
phone-a-friend (assistance) AND the cloud scanner (review).

Transport is the Python standard library ``urllib.request`` ONLY. No vendor SDK
(``anthropic`` / ``openai`` / ``google-generativeai``), no ``requests``. This is
the same stdlib-HTTP pattern ``llama_manager.py`` and ``llm.py`` already use, and
it is what satisfies BOTH the indefinite NO-PYPI ban AND the vendor-neutrality
mandate in one move: a small auditable adapter instead of an opaque SDK.

The API key is read from the GNOME Keyring at call time via libsecret
(``gi.repository.Secret`` — the same GObject-Introspection path the rest of the
code already uses for Gio/Gtk/GLib, so no PyPI dependency is added), keyed by
``ProviderConfig.api_key_keyring_id``. It is never held in config, logs, or
long-lived memory (security-only-alignment rule 8). Key access sits behind ``lookup_secret``
so the platform-independent request/response shaping can be unit-tested without a
real keyring.

The base implements the OpenAI chat-completions shape, shared by openai, xai,
deepseek, azure-openai, and any custom OpenAI-compatible endpoint. Providers
whose REST API differs (anthropic, google) override the small shaping hooks
``_endpoint`` / ``_auth_headers`` / ``_build_body`` / ``_parse_response``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

from intergen.interfaces.cloud import CloudProviderAdapter, ProviderConfig
from intergen.interfaces.types import (
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolSchema,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0


class CloudAdapterError(Exception):
    """A cloud request failed (transport, auth, or malformed response)."""


def lookup_secret(keyring_id: str) -> str:
    """Read an API key from the GNOME Keyring via libsecret.

    Uses ``gi.repository.Secret`` — the same GObject-Introspection path the rest
    of the code already uses for Gio/Gtk/GLib, so no PyPI dependency is added.
    Linux-only; isolated in this one function so the request/response shaping can
    be unit-tested by monkeypatching it.
    """
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    schema = Secret.Schema.new(
        "org.intergenos.intergen",
        Secret.SchemaFlags.NONE,
        {"keyring_id": Secret.SchemaAttributeType.STRING},
    )
    key = Secret.password_lookup_sync(schema, {"keyring_id": keyring_id}, None)
    if not key:
        raise CloudAdapterError(
            f"no API key in the keyring for id '{keyring_id}' "
            "(configure the provider so its key is stored)"
        )
    return key


def _secret_schema():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    return Secret, Secret.Schema.new(
        "org.intergenos.intergen",
        Secret.SchemaFlags.NONE,
        {"keyring_id": Secret.SchemaAttributeType.STRING},
    )


def store_secret(keyring_id: str, api_key: str, label: str | None = None) -> None:
    """Write an API key to the system keyring (the write side of lookup_secret).

    The key is held ONLY in the keyring — never in config, logs, or process
    args. Used by the provider-config panel when the user enters a key.
    """
    if not keyring_id or not api_key:
        raise CloudAdapterError("store_secret requires a keyring_id and a key")
    Secret, schema = _secret_schema()
    ok = Secret.password_store_sync(
        schema, {"keyring_id": keyring_id},
        Secret.COLLECTION_DEFAULT,
        label or f"InterGen cloud-provider API key ({keyring_id})",
        api_key, None,
    )
    if not ok:
        raise CloudAdapterError(
            f"failed to store the API key in the keyring for id '{keyring_id}'"
        )


def delete_secret(keyring_id: str) -> bool:
    """Remove an API key from the keyring. Returns True if one was removed."""
    if not keyring_id:
        return False
    Secret, schema = _secret_schema()
    return bool(Secret.password_clear_sync(schema, {"keyring_id": keyring_id}, None))


class HTTPCloudAdapter(CloudProviderAdapter):
    """Raw-HTTP (stdlib urllib) adapter base; OpenAI chat-completions by default."""

    #: Human label used when ``config.adapter`` is empty. Subclasses set this.
    provider_label = "openai-compatible"
    #: Default API root when ``config.base_url`` is unset. Subclasses set this.
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return self._config.adapter or self.provider_label

    # -- key access seam (Linux libsecret; monkeypatched in tests) ------------
    def _api_key(self) -> str:
        return lookup_secret(self._config.api_key_keyring_id)

    # -- per-provider overridable shaping -------------------------------------
    def _base_url(self) -> str:
        return (self._config.base_url or self.default_base_url).rstrip("/")

    def _endpoint(self) -> str:
        return f"{self._base_url()}/chat/completions"

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    def _build_body(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict:
        body: dict = {
            "model": self._config.model,
            "messages": [self._message_to_wire(m) for m in messages],
            "max_tokens": max_tokens or self._config.max_tokens,
            "temperature": (
                temperature if temperature is not None else self._config.temperature
            ),
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
        return body

    @staticmethod
    def _message_to_wire(message: Message) -> dict:
        wire: dict = {"role": message.role.value, "content": message.content}
        if message.tool_call_id:
            wire["tool_call_id"] = message.tool_call_id
        if message.name:
            wire["name"] = message.name
        return wire

    def _parse_response(self, data: dict) -> LLMResponse:
        choices = data.get("choices") or [{}]
        message = choices[0].get("message", {}) if choices else {}
        usage = data.get("usage", {}) or {}
        return LLMResponse(
            text=message.get("content") or "",
            model=data.get("model") or self._config.model,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            local=False,
        )

    # -- transport (stdlib urllib, same pattern as llm.py) --------------------
    @staticmethod
    def _require_secure_transport(endpoint: str) -> None:
        """Refuse to attach the API key over a non-TLS transport (HG rule 8).

        The key rides an auth header; over plain http it would cross the wire in
        cleartext. https is required, with a loopback exception (a local
        OpenAI-compatible server has no wire to sniff). Checked BEFORE the key is
        even fetched, so a misconfigured http base_url never touches the keyring.
        """
        parsed = urllib.parse.urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        is_loopback = host == "localhost" or host == "::1" or host.startswith("127.")
        if parsed.scheme == "https" or (parsed.scheme == "http" and is_loopback):
            return
        raise CloudAdapterError(
            f"refusing to send the API key over a non-TLS endpoint "
            f"({endpoint!r}); use https (http is allowed only for loopback)."
        )

    def _request(self, body: dict, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
        endpoint = self._endpoint()
        self._require_secure_transport(endpoint)
        api_key = self._api_key()
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_headers(api_key))
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise CloudAdapterError(f"{self.name} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CloudAdapterError(f"{self.name} unreachable: {exc.reason}") from exc
        except (ValueError, KeyError) as exc:
            raise CloudAdapterError(
                f"{self.name} returned a malformed response: {exc}"
            ) from exc

    # -- CloudProviderAdapter ABC ---------------------------------------------
    def send(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        body = self._build_body(messages, tools, max_tokens, temperature)
        data = self._request(body)
        return self._parse_response(data)

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Iterator[str | ToolCall]:
        # v1 substrate: satisfy the ABC by delegating to send() and yielding the
        # text once. True token streaming (SSE parsing) is per-provider and lands
        # as an override in a follow-up; the substrate's job is correct,
        # auditable transport first.
        response = self.send(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature
        )
        if response.text:
            yield response.text

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._api_key()
        except CloudAdapterError as exc:
            return False, f"key lookup failed: {exc}"
        try:
            self.send([Message(role=MessageRole.USER, content="ping")], max_tokens=1)
        except CloudAdapterError as exc:
            return False, str(exc)
        return True, f"{self.name} reachable"
