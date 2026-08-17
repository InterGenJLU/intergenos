# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""LocalQwenScanner — the local-LLM deep scanner tier (Sentinel build seq step 4).

Fully-offline semantic scanner: a small Qwen classifier served by llama.cpp,
reached over stdlib `urllib` (the same transport `llm.py` / `llama_manager`
already use — no PyPI, no SDK). It is the deeper tier `ScannerPolicy` escalates
to when the always-on `LocalRulesScanner` floor returns FLAG or when a deep
scan is requested; the floor's BLOCK short-circuits before this ever runs.

Operating model (ratified decision #3 — on-demand spawn with keep-alive):
  * The Qwen instance is NOT started at boot. It loads on the first deep scan,
    stays warm through a burst of scans, and is unloaded once idle past
    `idle_timeout` (the caller ticks `unload_if_idle()`), so a user who never
    triggers a FLAG never pays the RAM. It runs on its OWN port via its OWN
    LlamaManager instance, alongside the chat (and embedding) servers.

Security posture:
  * The content handed here is adversarial by nature (it reached the deep tier
    because the floor flagged it). The classifier prompt wraps the content in a
    delimited block and instructs the model to treat everything inside as DATA
    to classify, never as instructions to follow — defense-in-depth over the
    floor's marker-spoof / injection rules.
  * Fail CLOSED to FLAG on EVERY error path (model not configured, server will
    not start, request error, unparseable or unknown verdict). A deep scanner
    that cannot return a trustworthy ALLOW must hand the call to a human, never
    silently allow (security-only-alignment rule #10) — identical to the floor + policy.

The model path is injected (resolved by the wiring layer from the operator-
signed models-manifest via `model_manager`); the LlamaManager and the HTTP
transport are injectable too, so this scanner unit-tests without a live server.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Callable

from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanVerdict,
)
from intergen.llama_manager import LlamaManager
from intergen.scanner._classifier import (
    SYSTEM_PROMPT,
    build_user_prompt,
    fail_closed,
    parse_verdict,
)

logger = logging.getLogger(__name__)

# Distinct from the chat server (8080) and the AI-12 embedding instance, so the
# Qwen scanner runs alongside them without a port collision.
_DEFAULT_PORT = 8091
_DEFAULT_IDLE_TIMEOUT = 300.0   # seconds warm-then-unload
_DEFAULT_REQUEST_TIMEOUT = 20.0

# The classifier prompt + verdict parser are shared with CloudScanner via
# intergen.scanner._classifier — one prompt, one parser for both deep tiers.
_NAME = "local-qwen"
_ERROR_CATEGORY = "scanner.qwen-error"
_UNAVAILABLE_CATEGORY = "scanner.qwen-unavailable"

# Transport signature: (url, payload_bytes, timeout) -> parsed JSON dict.
HttpPost = Callable[[str, bytes, float], dict]


def _default_http_post(url: str, payload: bytes, timeout: float) -> dict:
    """POST JSON to a local llama-server endpoint and return the parsed body."""
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class LocalQwenScanner(Scanner):
    """On-demand, keep-alive local Qwen classifier (deep scan tier)."""

    def __init__(
        self,
        model_path: str | None = None,
        *,
        port: int = _DEFAULT_PORT,
        manager: LlamaManager | None = None,
        http_post: HttpPost | None = None,
        clock: Callable[[], float] | None = None,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._model_path = model_path
        self._port = port
        self._manager = manager if manager is not None else LlamaManager()
        self._http_post = http_post if http_post is not None else _default_http_post
        self._clock = clock if clock is not None else time.monotonic
        self._idle_timeout = idle_timeout
        self._request_timeout = request_timeout
        self._last_used: float | None = None

    @property
    def name(self) -> str:
        return "local-qwen"

    @property
    def is_local(self) -> bool:
        return True

    # -- lifecycle (on-demand spawn + keep-alive + idle unload) --------------

    def _ensure_running(self) -> bool:
        """Start the Qwen instance on demand; return True if it is serving."""
        if self._model_path is None:
            return False
        if self._manager.is_running():
            return True
        ok = self._manager.start(self._model_path, port=self._port)
        if not ok:
            logger.error("LocalQwenScanner could not start the Qwen llama-server")
        return bool(ok)

    def unload_if_idle(self, now: float | None = None) -> bool:
        """Stop the instance if it has been idle past idle_timeout. The caller
        ticks this (e.g. chokepoint housekeeping); returns True if it unloaded.
        """
        if not self._manager.is_running() or self._last_used is None:
            return False
        now = self._clock() if now is None else now
        if (now - self._last_used) >= self._idle_timeout:
            self._manager.stop()
            self._last_used = None
            return True
        return False

    def stop(self) -> None:
        """Unload the Qwen instance now."""
        if self._manager.is_running():
            self._manager.stop()
        self._last_used = None

    # -- scan ----------------------------------------------------------------

    def scan(self, content: str, ctx: ScanContext) -> ScanVerdict:
        if not content:
            return ScanVerdict.allow(scanner=self.name)
        if self._model_path is None:
            return fail_closed("local-qwen model not configured", _UNAVAILABLE_CATEGORY, _NAME)
        if not self._ensure_running():
            return fail_closed("local-qwen server unavailable", _UNAVAILABLE_CATEGORY, _NAME)

        self._last_used = self._clock()
        payload = json.dumps({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(content, ctx)},
            ],
            "temperature": 0.0,
            "stream": False,
        }).encode()

        try:
            body = self._http_post(
                self._manager.get_endpoint(), payload, self._request_timeout
            )
        except Exception as exc:  # noqa: BLE001 — fail closed, never silently allow
            logger.warning("LocalQwenScanner request failed (%s); failing closed to FLAG",
                           type(exc).__name__)
            return fail_closed(f"local-qwen request error: {type(exc).__name__}",
                               _ERROR_CATEGORY, _NAME)

        return self._parse_verdict(body)

    def _parse_verdict(self, body: dict) -> ScanVerdict:
        """Extract the reply text from the chat-completion envelope, then hand
        the verdict mapping to the shared classifier parser (fail closed)."""
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError):
            return fail_closed("local-qwen malformed response envelope", _ERROR_CATEGORY, _NAME)
        return parse_verdict(text, _NAME, _ERROR_CATEGORY)
