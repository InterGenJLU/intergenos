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

import http.client
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterator

from intergen.interfaces.cloud import (
    CloudCallStopped,
    CloudProviderAdapter,
    ProviderConfig,
)
from intergen.interfaces.types import (
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolSchema,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0

#: The most of a reply one read takes. Each read returns what one receive
#: brings, up to this many bytes, and the caller's stop is checked between
#: reads and before every receive.
READ_CHUNK = 4096

#: How much of an error reply's body is read, as a prefix: the error's detail
#: is its first 500 characters, and the bound keeps an endpoint that answers
#: an error with a huge or unending body from holding the call while it keeps
#: sending. One byte past it is read too, to tell a body cut at the bound from
#: one that ends there.
ERROR_BODY_LIMIT = 64 * 1024


class CloudAdapterError(Exception):
    """A cloud request failed (transport, auth, or malformed response)."""


class _BoundReceives:
    """A reply's socket as its reader receives through it while the reply is
    read: every receive first checks the caller's stop and waits no longer
    than what is left of the whole read's timeout, and with nothing left it
    receives nothing and times out. One read1 can make several receives - a
    chunked reply's framing (the chunk-size line and its extension, a
    chunk's closing CRLF, the trailers) is read inside it - so a bound set
    once per read would hold only the first of them. Everything else is the
    socket's own."""

    def __init__(self, sock: socket.socket, read_by: float,
                 stop_if_asked: Callable[[], None]) -> None:
        self._sock = sock
        self._read_by = read_by
        self._stop_if_asked = stop_if_asked

    def recv_into(self, *args, **kwargs) -> int:
        self._stop_if_asked()
        left = self._read_by - time.monotonic()
        if left <= 0:
            raise TimeoutError("the whole read's timeout has passed")
        self._sock.settimeout(left)
        return self._sock.recv_into(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._sock, name)


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

    def _stop_if_asked(self, should_stop: Callable[[], bool] | None) -> None:
        if should_stop is not None and should_stop():
            raise CloudCallStopped(
                f"{self.name}: the turn this call belonged to has ended")

    def _reply_stream(self, resp, reading: str) -> socket.SocketIO | None:
        """The socket stream a reply's body is received from: its
        HTTPResponse's, for a reply and for an HTTPError (whose fp is the
        HTTPResponse). A reply that is no HTTPResponse, or one already
        closed, is not received from a socket and has none. An HTTPResponse
        whose socket cannot be reached is not read: its receives could not be
        bound."""
        reply = (resp if isinstance(resp, http.client.HTTPResponse)
                 else getattr(resp, "fp", None))
        if not isinstance(reply, http.client.HTTPResponse) or reply.fp is None:
            return None
        stream = getattr(reply.fp, "raw", None)
        if (isinstance(stream, socket.SocketIO)
                and isinstance(getattr(stream, "_sock", None), socket.socket)):
            return stream
        raise CloudAdapterError(
            f"{self.name}: {reading} was not read: its socket could not be "
            "reached to bound each receive to the request's timeout")

    def _read_reply(self, resp, should_stop: Callable[[], bool] | None,
                    timeout: float, limit: int | None = None,
                    reading: str = "its reply") -> bytes:
        """The reply's body, a success's or an error's, stop or none. Each
        read is one read1 (at most READ_CHUNK bytes), the stop is checked
        before each read and once more after the last, and for the read's
        duration every receive from the reply's socket checks the stop first
        (_BoundReceives), so a call whose turn ends is stopped within one
        receive however slowly the endpoint sends - its body or a chunked
        body's framing - and a turn that ends during the read that finds the
        end of the body stops the call all the same. A read that waited for
        a whole chunk held the stop until the chunk was complete: 4096
        receives of a byte, each just inside the 60-second default, is about
        68 hours. The whole read has the same timeout, and every receive
        waits only for what is left of it: a body still arriving once it has
        passed, or a receive still waiting when it passes, ends the call as a
        provider error naming it, the rest not read. With a limit, no more
        than ``limit`` bytes are read: the read ends there as it ends at the
        end of the body. The reply's socket is its own again after the read,
        its timeout left at what the last receive was given; the caller
        closes the reply (a success's with-block, an error's finally)."""
        read_by = time.monotonic() + timeout
        late = (f"{self.name}: {reading} was not read within {timeout:g}s, "
                "the request's timeout; the rest was not read")
        stream = self._reply_stream(resp, reading)
        if stream is not None:
            sock = stream._sock
            bound = _BoundReceives(
                sock, read_by, lambda: self._stop_if_asked(should_stop))
            stream._sock = bound
        parts: list[bytes] = []
        left = limit
        try:
            while left is None or left > 0:
                self._stop_if_asked(should_stop)
                if time.monotonic() >= read_by:
                    raise CloudAdapterError(late)
                size = READ_CHUNK if left is None else min(READ_CHUNK, left)
                try:
                    chunk = resp.read1(size)
                except TimeoutError as exc:
                    raise CloudAdapterError(late) from exc
                if not chunk:
                    break
                parts.append(chunk)
                if left is not None:
                    left -= len(chunk)
        finally:
            # A reply read to its end closed its stream, and with it the
            # socket; one that is still open is given its own socket back.
            if stream is not None and stream._sock is bound:
                stream._sock = sock
        self._stop_if_asked(should_stop)
        return b"".join(parts)

    def _request(self, body: dict, *, timeout: float = DEFAULT_TIMEOUT,
                 should_stop: Callable[[], bool] | None = None) -> dict:
        # An ended turn opens no request and never reads the key.
        self._stop_if_asked(should_stop)
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
                return json.loads(
                    self._read_reply(resp, should_stop, timeout).decode())
        except urllib.error.HTTPError as exc:
            # The error reply's body is read as a success's is, through the
            # stop-aware reader: a turn that ends while it is read, its last
            # read included, stops the call (CloudCallStopped) within the
            # receive in flight, before any provider error is made. It is read
            # only as a prefix, ERROR_BODY_LIMIT bytes and the one byte past
            # them that tells a body cut there from one that ends there, so
            # an endpoint that answers an error with a huge or unending body
            # cannot hold the call while it keeps sending. urlopen raised
            # before any with-block owned the reply, so it is closed here,
            # however the read ends.
            try:
                body = self._read_reply(
                    exc, should_stop, timeout, limit=ERROR_BODY_LIMIT + 1,
                    reading=f"its HTTP {exc.code} error reply")
            finally:
                exc.close()
            detail = body.decode(errors="replace")[:500]
            if len(body) > ERROR_BODY_LIMIT:
                detail += (f" (the error body is longer than {ERROR_BODY_LIMIT}"
                           " bytes; the rest was not read)")
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
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        body = self._build_body(messages, tools, max_tokens, temperature)
        data = self._request(body, should_stop=should_stop)
        response = self._parse_response(data)
        # A turn that ended as the last chunk arrived is not handed its answer.
        self._stop_if_asked(should_stop)
        return response

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Iterator[str | ToolCall]:
        # v1 substrate: satisfy the ABC by delegating to send() and yielding the
        # text once. True token streaming (SSE parsing) is per-provider and lands
        # as an override in a follow-up; the substrate's job is correct,
        # auditable transport first.
        response = self.send(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature,
            should_stop=should_stop,
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
