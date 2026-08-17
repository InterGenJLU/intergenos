# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""CloudScanner — the opt-in cloud deep-scan tier (Sentinel build seq step 4).

The third `Scanner` implementation (after the always-on `LocalRulesScanner`
floor and the local `LocalQwenScanner`). It wraps the vendor-neutral cloud
substrate (`intergen/cloud/`, build seq step 1) so a user who wants a stronger
semantic read than the local model can route deep scans to a configured cloud
provider. Like the local tier it is the deeper scanner `ScannerPolicy`
escalates to on a floor FLAG or an explicit deep scan; the floor's BLOCK
short-circuits before this ever runs.

OPT-IN, NEVER DEFAULT — the security surface to keep in view
-----------------------------------------------------------
This tier sends the flagged content to a THIRD-PARTY provider to be judged.
That content is, by construction, exactly the material the deterministic floor
found suspicious — for EGRESS that is data already headed off the machine, for
INGRESS it is untrusted inbound content. Routing it to a cloud API is itself an
egress of potentially sensitive bytes, so this scanner is constructed only when
the operator has explicitly configured a cloud provider (a `ProviderConfig`
the user set; config is AI-immutable / human-set). With no config it is simply
not attached to the policy, and the local floor (+ optional local-Qwen tier)
stands alone. There is no implicit cloud fallback. The substrate's own HG#8
guards still apply underneath: the API key is read per-call from the keyring
and refused over any non-TLS transport before it is even fetched.

Security posture (shared with the local tier via `_classifier`): the content
is wrapped in a delimited DATA block and the model is told to treat it strictly
as data, never as instructions. Every error/doubt path fails CLOSED to FLAG —
provider unreachable, HTTP error, malformed or unparseable verdict, unknown
disposition — never a silent ALLOW (security-only-alignment rule #10), identical to the
floor and the policy. And because the policy merges most-severe-wins, a cloud
verdict can only ever HOLD or ESCALATE the floor's disposition, never downgrade
it: a model fooled into "allow" still leaves the floor's FLAG standing for the
human modal.

The cloud adapter is injectable so this scanner unit-tests with a mock adapter
and no network (the same seam the substrate's own tests use).
"""

from __future__ import annotations

import logging

from intergen.cloud.factory import create_adapter
from intergen.cloud.http_adapter import CloudAdapterError
from intergen.interfaces.cloud import CloudProviderAdapter, ProviderConfig
from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanDisposition,
    ScanVerdict,
)
from intergen.interfaces.types import LLMResponse, Message, MessageRole
from intergen.scanner._classifier import (
    SYSTEM_PROMPT,
    build_user_prompt,
    fail_closed,
    parse_verdict,
)

logger = logging.getLogger(__name__)

_ERROR_CATEGORY = "scanner.cloud-error"
_UNAVAILABLE_CATEGORY = "scanner.cloud-unavailable"

# Keep the verdict request small and deterministic: the classifier emits a short
# JSON object, and temperature 0 makes the security judgement reproducible.
_MAX_TOKENS = 256
_TEMPERATURE = 0.0


class CloudScanner(Scanner):
    """Opt-in cloud-backed deep scanner (wraps a vendor-neutral adapter)."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        adapter: CloudProviderAdapter | None = None,
    ) -> None:
        """Configure the cloud deep tier.

        Exactly one of `config` (a provider the operator set, from which the
        adapter is built via the substrate factory) or `adapter` (an already-
        built adapter, used by tests and by wiring that holds one) is needed.
        With neither, the scanner is unconfigured and every scan fails CLOSED
        to FLAG — it never silently allows.
        """
        if adapter is not None:
            self._adapter: CloudProviderAdapter | None = adapter
        elif config is not None:
            self._adapter = create_adapter(config)
        else:
            self._adapter = None

    @property
    def name(self) -> str:
        return "cloud"

    @property
    def is_local(self) -> bool:
        return False

    def scan(self, content: str, ctx: ScanContext) -> ScanVerdict:
        if not content:
            return ScanVerdict.allow(scanner=self.name)
        if self._adapter is None:
            return fail_closed(
                "cloud scanner not configured", _UNAVAILABLE_CATEGORY, self.name
            )

        messages = [
            Message(role=MessageRole.SYSTEM, content=SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=build_user_prompt(content, ctx)),
        ]

        try:
            response: LLMResponse = self._adapter.send(
                messages, max_tokens=_MAX_TOKENS, temperature=_TEMPERATURE
            )
        except CloudAdapterError as exc:
            logger.warning(
                "CloudScanner provider error (%s); failing closed to FLAG", exc
            )
            return fail_closed(
                f"cloud scanner provider error: {type(exc).__name__}",
                _ERROR_CATEGORY,
                self.name,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed, never silently allow
            logger.warning(
                "CloudScanner unexpected error (%s); failing closed to FLAG",
                type(exc).__name__,
            )
            return fail_closed(
                f"cloud scanner error: {type(exc).__name__}",
                _ERROR_CATEGORY,
                self.name,
            )

        text = response.text if response and response.text else ""
        if not text:
            return fail_closed(
                "cloud scanner empty response", _ERROR_CATEGORY, self.name
            )
        return parse_verdict(text, self.name, _ERROR_CATEGORY)
