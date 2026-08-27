# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phone-a-Friend — consent-first cloud assistance escalation (Sentinel build seq step 5).

The concrete EscalationManager behind the required phone-a-friend feature.
The distinction from llm.py's existing escalation: that path is a quality FALLBACK
(auto-escalate after local fails twice). The mandate's phone-a-friend is consent-first
ASSISTANCE — the System AI RECOGNIZES a task is multi-step / sensitive / "slightly
outside my scope" and OFFERS to reach the user's configured frontier model; on consent
it routes via the vendor-neutral cloud substrate (intergen/cloud/).

Modes (EscalationMode; config escalation.mode, default ask):
  * NEVER    — offline, never offer or send.
  * FALLBACK — auto-escalate only when the local quality gate fails.
  * ASK      — OFFER on recognition; the user consents before anything is sent (DEFAULT).
  * AUTO     — decide by confidence, no prompt.

Recognition = HYBRID (ratified decision #4): the heuristic offer (local confidence +
multi-step signal + query type + an explicit "ask Claude") PLUS a user-invoked
affordance (a GUI "Ask my frontier model" button with CLI parity) that the wiring
layer calls escalate() directly for. should_escalate() is the heuristic half; the
explicit affordance bypasses it (the user already asked).

Egress safety (ratified decision #6 — scan-on-derivation): phone-a-friend sends
conversation content to a third party. The INITIAL egress the user explicitly
authorized (the consented offer, shown via show-before-send) is trusted at source and
NOT auto-scanned. EVERY SUBSEQUENT egress in the flow (follow-on / agentic / not
individually consented) IS egress-scanned through the SAME ScannerPolicy as the tool
chokepoint — a BLOCK refuses the send so secrets are never shipped to the cloud,
matching the chokepoint posture. The substrate's own HG#8 guards (key from keyring
per-call, refused over non-TLS) apply underneath.

NO default provider: with none configured, escalation cannot run (offers degrade to a
"configure a provider" note); local-only ships ready.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable

from intergen.cloud.factory import create_adapter
from intergen.cloud.http_adapter import CloudAdapterError
from intergen.interfaces.cloud import (
    CloudProviderAdapter,
    EscalationDecision,
    EscalationManagerInterface,
    ProviderConfig,
    UsageRecord,
)
from intergen.interfaces.scanner import ScanContext, ScanDirection, ScanDisposition
from intergen.interfaces.types import (
    EscalationMode,
    LLMResponse,
    Message,
    ToolSchema,
)

logger = logging.getLogger(__name__)

# Confidence at or below which the heuristic offers help, ON THE 0-1 SCALE THE
# LIVE CALLER USES. The one producer is ConversationRouter._try_llm_freeform:
#     confidence = 1.0 if response.quality_passed else 0.5
# This constant read 3.0 with a comment claiming a 1-5 scale that no caller has
# ever passed (measured 2026-08-26). Both live values sat below it, so the
# low-confidence signal was TRUE on every freeform turn and the offer fired on
# all of them: a threshold that can never be false is a check that does not
# check. 0.5 is the boundary on this scale because it is the point at which the
# local answer is no better than even — below it there is a real reason to offer.
_LOW_CONFIDENCE = 0.5

# An explicit user ask for the frontier model — the heuristic always offers on these.
_EXPLICIT_ASK = re.compile(
    r"\b(ask|check with|phone|consult|escalate to)\s+"
    r"(claude|gpt|chatgpt|gemini|your frontier|the cloud|a frontier model)\b",
    re.IGNORECASE,
)
# The product's own offers tell the user to type 'ask my frontier model'
# (measured 2026-08-26: that exact sentence was not recognised above, which
# accepts the "your" form only). The possessive the assistant suggests is
# accepted alongside it.
_EXPLICIT_ASK_OWN = re.compile(
    r"\b(ask|check with|phone|consult|escalate to)\s+(?:my|the)\s+frontier\b",
    re.IGNORECASE,
)

# The multi-step signal is the DECOMPOSER's structured verdict, supplied by the
# caller via should_escalate(multistep=...). Decided 2026-07-23 (IG-S-12 sitting,
# piece 2): the structured signal is authoritative; the former in-module text
# regex is retired — a parallel regex drifts from the decomposer's own gate.


# Adapter factory signature (injectable for tests): ProviderConfig -> adapter.
AdapterFactory = Callable[[ProviderConfig], CloudProviderAdapter]


class EscalationManager(EscalationManagerInterface):
    """Consent-first phone-a-friend manager over the vendor-neutral substrate."""

    def __init__(
        self,
        *,
        mode: EscalationMode = EscalationMode.ASK,
        providers: list[ProviderConfig] | None = None,
        adapter_factory: AdapterFactory | None = None,
        scanner=None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._mode = mode
        self._providers: list[ProviderConfig] = list(providers or [])
        self._adapter_factory: AdapterFactory = adapter_factory or create_adapter
        # ScannerPolicy (or any .scan(content, ctx)) for decision-#6 egress scanning
        # of derived (non-consented) escalation payloads. None = no scan layer
        # (the wiring injects the always-on policy, same instance as the chokepoint).
        self._scanner = scanner
        self._clock: Callable[[], float] = clock or time.time
        self._usage: list[UsageRecord] = []
        self._adapters: dict[str, CloudProviderAdapter] = {}

    @classmethod
    def from_config(
        cls,
        escalation_cfg: dict | None,
        providers_cfg: list | None,
        *,
        scanner=None,
        adapter_factory: AdapterFactory | None = None,
        clock: Callable[[], float] | None = None,
    ) -> "EscalationManager":
        """Build a manager from the AI-immutable (decision #5) config sections.

        `escalation_cfg` = config["escalation"] ({mode, primary_provider});
        `providers_cfg`  = config["providers"] (list of provider dicts — the API key
        lives in the keyring, only its id is here). The primary_provider name is
        ordered first so _primary_provider() selects it. The SAME always-on
        ScannerPolicy the dispatch chokepoint uses is injected as `scanner`
        (decision #6 — derived egress is scanned through the shared floor).

        Degrade-don't-crash: an unparseable mode falls back to ASK (the safe
        consent-first default) and a malformed provider entry is skipped rather than
        crashing daemon startup.
        """
        escalation_cfg = escalation_cfg or {}
        try:
            mode = EscalationMode(str(escalation_cfg.get("mode", "ask")).lower())
        except ValueError:
            logger.warning("escalation.mode %r invalid; defaulting to ask",
                           escalation_cfg.get("mode"))
            mode = EscalationMode.ASK

        providers: list[ProviderConfig] = []
        for entry in (providers_cfg or []):
            try:
                providers.append(ProviderConfig(
                    name=entry["name"],
                    adapter=entry["adapter"],
                    model=entry["model"],
                    api_key_keyring_id=entry["api_key_keyring_id"],
                    base_url=entry.get("base_url"),
                    max_tokens=int(entry.get("max_tokens", 4096)),
                    temperature=float(entry.get("temperature", 0.7)),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("skipping malformed provider entry %r (%s)", entry, exc)

        primary = escalation_cfg.get("primary_provider")
        if primary:
            # Stable sort: entries whose name == primary (False sorts before True)
            # move to the front, the rest keep their config order.
            providers.sort(key=lambda p: p.name != primary)

        return cls(mode=mode, providers=providers, scanner=scanner,
                   adapter_factory=adapter_factory, clock=clock)

    # -- recognition (the heuristic half of decision #4) ---------------------

    def should_escalate(
        self,
        user_message: str,
        local_response: str,
        quality_check: str,
        confidence: float,
        *,
        multistep: bool = False,
        exceeds_scope: bool = False,
    ) -> EscalationDecision:
        """`multistep` is the decomposer's structured multi-part verdict for this
        turn (DecomposedQuery.needs_decomposition), wired by the caller — the
        heuristic carries no text fallback of its own (decided 2026-07-23).

        `exceeds_scope` is the one trigger that reads the REQUEST rather than the
        answer: the person asked for a whole professional artifact this tier is not
        equipped to produce (router._request_exceeds_local_scope), wired by the
        caller the same way multistep is. Every other member of this trigger set
        is a property of what came back — and on the conversational path
        `low_confidence` is arithmetically the same condition as `quality_failed`,
        because the caller derives confidence from quality_passed — so before this
        existed, whether a person learned a larger model could be asked depended on
        whether the local model's second draft happened to come back clean.
        Measured 2026-08-26: the same question graded FAIL then PASS seventeen
        minutes apart on that difference alone. Defaults False, so a caller that
        has not been taught the signal behaves exactly as before."""
        provider = self._primary_provider_name()
        if self._mode is EscalationMode.NEVER:
            return EscalationDecision(False, "escalation disabled (mode=never)", 0.0, None)

        quality_failed = bool(quality_check.strip())
        # No truthiness guard: `bool(confidence)` made 0.0 — the FLOOR of the
        # 0-1 scale, the least confident value there is — read as NOT low
        # confidence, silencing the one case that most needs the offer.
        low_confidence = confidence <= _LOW_CONFIDENCE
        explicit = bool(_EXPLICIT_ASK.search(user_message or "")
                        or _EXPLICIT_ASK_OWN.search(user_message or ""))

        if provider is None:
            # Decided 2026-07-23 (IG-S-12 sitting, piece 2): in ASK mode, firing
            # offer signals with NO designated provider still yield a True
            # decision (provider=None) so the offer surface can point the user at
            # the provider-setup path instead of staying silent. AUTO/FALLBACK
            # have nothing to act on without a provider — unchanged early no.
            triggered = (explicit or quality_failed or low_confidence
                         or multistep or exceeds_scope)
            if self._mode is EscalationMode.ASK and triggered:
                reason = self._trigger_reason(
                    explicit, quality_failed, low_confidence, multistep,
                    exceeds_scope)
                return EscalationDecision(True, reason, 0.95 if explicit else 0.7, None)
            return EscalationDecision(
                False, "no cloud provider configured (local-only)", 0.0, None
            )

        if self._mode is EscalationMode.FALLBACK:
            should = quality_failed
            reason = ("local quality gate failed — escalating"
                      if should else "local response passed; no escalation")
            return EscalationDecision(should, reason, 0.9 if should else 0.0,
                                      provider if should else None)

        # ASK (default) + AUTO share the same trigger set; ASK surfaces it as an
        # OFFER (the wiring prompts for consent), AUTO acts on it directly.
        triggered = (explicit or quality_failed or low_confidence or multistep
                     or exceeds_scope)
        if not triggered:
            return EscalationDecision(False, "local response is sufficient", 0.0, None)
        reason = self._trigger_reason(explicit, quality_failed, low_confidence,
                                      multistep, exceeds_scope)
        confidence_score = 0.95 if explicit else 0.7
        return EscalationDecision(True, reason, confidence_score, provider)

    @staticmethod
    def _trigger_reason(explicit, quality_failed, low_confidence, multistep,
                        exceeds_scope=False) -> str:
        if explicit:
            return "you asked me to reach your frontier model"
        # BEFORE the answer-shaped reasons: when the REQUEST is what exceeds this
        # tier, saying "my answer did not pass the quality gate" describes the
        # wrong thing, and it is the reason the person reads in the offer.
        if exceeds_scope:
            return ("this is a bigger piece of work than I can do well here")
        if quality_failed:
            return "my local answer did not pass the quality gate"
        if low_confidence:
            return "I am not confident in my local answer"
        if multistep:
            return "this looks multi-step / outside my local scope"
        return "escalation recommended"

    # -- execution -----------------------------------------------------------

    def escalate(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        reason: str = "",
        user_consented: bool = False,
    ) -> LLMResponse:
        """Route an escalation to the configured primary provider.

        user_consented=True marks the INITIAL human-authorized hop (the offer the
        user accepted, or the explicit GUI/CLI affordance) — NOT auto-scanned per
        decision #6. Any other (derived / agentic / follow-on) egress is scanned
        through the shared ScannerPolicy first; a BLOCK refuses the send so secrets
        are never shipped to the cloud. Default is the safe one: scan.
        """
        provider = self._primary_provider()
        if provider is None:
            return self._error_response(
                "Phone-a-friend has no configured provider; cannot escalate."
            )

        if self._scanner is not None and not user_consented:
            verdict = self._scanner.scan(
                self._egress_text(messages),
                ScanContext(
                    surface=f"escalation:{provider.name}",
                    direction=ScanDirection.EGRESS,
                    tool_name="phone_a_friend",
                ),
            )
            if verdict.disposition is ScanDisposition.BLOCK:
                logger.warning("phone-a-friend egress BLOCKED by Sentinel: %s", verdict.reason)
                return self._error_response(
                    "Escalation refused: Sentinel blocked the outbound content before "
                    f"it left the machine ({verdict.reason or 'exfil risk'})."
                )
            if verdict.disposition is ScanDisposition.FLAG:
                # A derived egress the floor finds suspicious must get explicit
                # human sign-off (the wiring's consent modal), not auto-send.
                logger.info("phone-a-friend egress FLAGGED; consent required: %s", verdict.reason)
                return self._error_response(
                    "Escalation held: the outbound content was flagged for your review "
                    f"({verdict.reason or 'review required'}); confirm to send."
                )

        try:
            adapter = self._adapter_for(provider)
            response = adapter.send(
                messages, tools=tools,
                max_tokens=provider.max_tokens, temperature=provider.temperature,
            )
        except CloudAdapterError as exc:
            logger.error("phone-a-friend provider error: %s", exc)
            return self._error_response(f"Frontier model unreachable: {exc}")
        except Exception as exc:  # noqa: BLE001 — never crash the assistant on escalation
            logger.error("phone-a-friend unexpected error: %s", type(exc).__name__)
            return self._error_response(f"Escalation failed: {type(exc).__name__}")

        # Usage accounting must not crash the escalation if an adapter returns a
        # response that does not honor the LLMResponse token fields — getattr-degrade
        # the counts to 0 (don't trust the response shape), consistent with the
        # degrade-don't-crash posture elsewhere.
        self._usage.append(UsageRecord(
            provider=provider.name,
            model=provider.model,
            tokens_prompt=getattr(response, "tokens_prompt", 0) or 0,
            tokens_completion=getattr(response, "tokens_completion", 0) or 0,
            reason=reason or "escalation",
            timestamp=self._clock(),
        ))
        return response

    # -- config / state ------------------------------------------------------

    def get_usage(self, last_n_days: int = 30) -> list[UsageRecord]:
        cutoff = self._clock() - last_n_days * 86400
        return [u for u in self._usage if u.timestamp >= cutoff]

    def get_mode(self) -> EscalationMode:
        return self._mode

    def set_mode(self, mode: EscalationMode) -> None:
        # NOTE: escalation.mode lives in the AI-immutable config (decision #5); a
        # programmatic set_mode here is for the wiring layer acting on a human's
        # authenticated choice, never an AI-self-driven flip.
        self._mode = mode

    def configure_provider(self, config: ProviderConfig) -> tuple[bool, str]:
        if not config.name or not config.adapter:
            return (False, "provider config requires a name and an adapter")
        # Validate the adapter is one the substrate factory knows (no default,
        # unknown = hard error) without sending anything.
        try:
            self._adapter_factory(config)
        except Exception as exc:  # noqa: BLE001 — surface a clean message
            return (False, f"unknown or invalid adapter {config.adapter!r}: {exc}")
        self._providers = [p for p in self._providers if p.name != config.name]
        self._providers.append(config)
        self._adapters.pop(config.name, None)
        return (True, f"provider {config.name} configured (key in keyring "
                      f"id={config.api_key_keyring_id})")

    def list_providers(self) -> list[ProviderConfig]:
        # The API key is never in ProviderConfig (only its keyring id), so these are
        # already redacted by construction.
        return list(self._providers)

    # -- helpers -------------------------------------------------------------

    def _primary_provider(self) -> ProviderConfig | None:
        return self._providers[0] if self._providers else None

    def _primary_provider_name(self) -> str | None:
        p = self._primary_provider()
        return p.name if p else None

    def _adapter_for(self, provider: ProviderConfig) -> CloudProviderAdapter:
        if provider.name not in self._adapters:
            self._adapters[provider.name] = self._adapter_factory(provider)
        return self._adapters[provider.name]

    @staticmethod
    def _egress_text(messages: list[Message]) -> str:
        return "\n".join(m.content for m in messages if getattr(m, "content", None))

    @staticmethod
    def _error_response(text: str) -> LLMResponse:
        return LLMResponse(text=text, model="phone-a-friend", local=False,
                           quality_passed=False)
