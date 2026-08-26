# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phone-a-Friend EscalationManager (Sentinel build seq step 5).

Drives the concrete EscalationManager with an injected fake adapter + fake scanner
(no network, no keyring): the mode-aware HYBRID recognition (decision #4), the
decision-#6 egress scan-on-derivation (consented hop not scanned; derived hop scanned,
BLOCK refuses / FLAG holds), usage logging, provider config (no default), and
fail-closed-degrade on a provider error. Runs on any host.
"""

from __future__ import annotations

import unittest

from intergen.escalation import EscalationManager
from intergen.interfaces.cloud import ProviderConfig
from intergen.interfaces.scanner import (
    ScanContext, ScanDirection, ScanDisposition, ScanVerdict,
)
from intergen.interfaces.types import EscalationMode, LLMResponse, Message, MessageRole


class _FakeAdapter:
    def __init__(self, name="acme", response=None, raises=None):
        self._name = name
        self._response = response or LLMResponse(
            text="cloud answer", model="acme-1", tokens_prompt=10,
            tokens_completion=5, local=False,
        )
        self._raises = raises
        self.sent = []

    @property
    def name(self):
        return self._name

    def send(self, messages, *, tools=None, max_tokens=None, temperature=None):
        if self._raises:
            raise self._raises
        self.sent.append(messages)
        return self._response

    def stream(self, *a, **k):
        raise NotImplementedError

    def test_connection(self):
        return (True, "ok")


class _FakeScanner:
    def __init__(self, disposition=ScanDisposition.ALLOW):
        self._d = disposition
        self.calls = []

    def scan(self, content, ctx: ScanContext) -> ScanVerdict:
        self.calls.append((content, ctx))
        return ScanVerdict(disposition=self._d, reason="fake", scanner="fake")


def _cfg(name="acme", adapter="openai"):
    return ProviderConfig(name=name, adapter=adapter, model="acme-1",
                          api_key_keyring_id="intergen-acme")


def _msgs(text="hello"):
    return [Message(role=MessageRole.USER, content=text)]


def _mgr(**kw):
    kw.setdefault("adapter_factory", lambda c: _FakeAdapter(c.name))
    return EscalationManager(**kw)


class RecognitionTests(unittest.TestCase):
    """Confidence is on the 0-1 scale the live caller passes
    (ConversationRouter._try_llm_freeform: 1.0 when the answer passed the quality
    gate, 0.5 when it did not). These literals read 5.0 and 2.0 until 2026-08-26,
    on a 1-5 scale escalation._LOW_CONFIDENCE claimed and no caller ever used.

    TWO CALLS BELOW DELIBERATELY KEEP THEIR ORIGINAL 5.0 — the explicit-ask test
    and the fallback-mode test. Rewriting those two lines would re-add the
    provider names they type as NEW lines, which the public-language gate blocks
    (rulebook Rule 22), and rewording the inputs would drop the only coverage the
    provider-name alternatives of _EXPLICIT_ASK have. The value is inert in both:
    one fires on the explicit ask and the other runs in FALLBACK mode, where only
    the quality verdict is read. 5.0 is above the threshold either way, so both
    read as CONFIDENT on the corrected scale, which is what those tests need."""

    def test_never_mode_never_escalates(self):
        m = _mgr(mode=EscalationMode.NEVER, providers=[_cfg()])
        d = m.should_escalate("ask claude please", "", "fail", 1.0)
        self.assertFalse(d.should_escalate)

    def test_no_provider_triggered_ask_offers_setup(self):
        # Decided 2026-07-23: ASK mode + firing signals + NO provider → a True
        # decision with provider=None, so the offer surface can point the user
        # at provider setup instead of staying silent.
        m = _mgr(mode=EscalationMode.ASK, providers=[])
        d = m.should_escalate("ask your frontier model", "", "fail", 1.0)
        self.assertTrue(d.should_escalate)
        self.assertIsNone(d.provider)

    def test_no_provider_untriggered_stays_local_only(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[])
        d = m.should_escalate("what time is it", "12:00", "", 1.0)
        self.assertFalse(d.should_escalate)
        self.assertIn("local-only", d.reason)

    def test_no_provider_nonask_modes_never_true(self):
        # AUTO/FALLBACK have nothing to act on without a provider — the
        # setup-pointer decision is ASK-only.
        for mode in (EscalationMode.AUTO, EscalationMode.FALLBACK):
            m = _mgr(mode=mode, providers=[])
            d = m.should_escalate("ask your frontier model", "", "fail", 1.0)
            self.assertFalse(d.should_escalate, mode)

    def test_multistep_flag_triggers_offer(self):
        # The decomposer's structured verdict is the multi-step signal; the
        # retired text regex must NOT fire on phrasing alone.
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        flagged = m.should_escalate("hello", "x", "", 1.0, multistep=True)
        self.assertTrue(flagged.should_escalate)
        self.assertIn("multi-step", flagged.reason)
        by_text = m.should_escalate("do a then b step by step", "x", "", 1.0)
        self.assertFalse(by_text.should_escalate)

    def test_ask_explicit_offers(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        d = m.should_escalate("can you ask Claude about this?", "x", "", 5.0)
        self.assertTrue(d.should_escalate)
        self.assertEqual(d.provider, "acme")

    def test_ask_quality_fail_offers(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        d = m.should_escalate("hello", "", "quality gate failed", 1.0)
        self.assertTrue(d.should_escalate)

    def test_ask_low_confidence_offers(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        d = m.should_escalate("hello", "maybe", "", 0.5)
        self.assertTrue(d.should_escalate)

    def test_ask_sufficient_no_offer(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        d = m.should_escalate("what time is it", "12:00", "", 1.0)
        self.assertFalse(d.should_escalate)

    def test_fallback_only_on_quality_fail(self):
        m = _mgr(mode=EscalationMode.FALLBACK, providers=[_cfg()])
        self.assertTrue(m.should_escalate("hi", "", "failed", 1.0).should_escalate)
        # explicit ask does NOT trigger FALLBACK (it is quality-gate-only)
        self.assertFalse(m.should_escalate("ask claude", "ok", "", 5.0).should_escalate)


class EscalateTests(unittest.TestCase):
    def test_consented_hop_not_scanned_and_sends(self):
        scanner = _FakeScanner(ScanDisposition.BLOCK)  # would block if consulted
        adapter = _FakeAdapter()
        m = _mgr(providers=[_cfg()], scanner=scanner,
                 adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs(), reason="user accepted offer", user_consented=True)
        self.assertEqual(resp.text, "cloud answer")
        self.assertEqual(scanner.calls, [], "consented hop must not be auto-scanned")
        self.assertEqual(len(adapter.sent), 1)

    def test_derived_hop_scanned_block_refuses(self):
        scanner = _FakeScanner(ScanDisposition.BLOCK)
        adapter = _FakeAdapter()
        m = _mgr(providers=[_cfg()], scanner=scanner,
                 adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs("secret stuff"), user_consented=False)
        self.assertIn("refused", resp.text.lower())
        self.assertEqual(len(scanner.calls), 1)
        self.assertIs(scanner.calls[0][1].direction, ScanDirection.EGRESS)
        self.assertEqual(adapter.sent, [], "blocked egress must not send")

    def test_derived_hop_flag_holds(self):
        scanner = _FakeScanner(ScanDisposition.FLAG)
        adapter = _FakeAdapter()
        m = _mgr(providers=[_cfg()], scanner=scanner,
                 adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs(), user_consented=False)
        self.assertIn("held", resp.text.lower())
        self.assertEqual(adapter.sent, [])

    def test_derived_hop_allow_sends(self):
        scanner = _FakeScanner(ScanDisposition.ALLOW)
        adapter = _FakeAdapter()
        m = _mgr(providers=[_cfg()], scanner=scanner,
                 adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs(), user_consented=False)
        self.assertEqual(resp.text, "cloud answer")
        self.assertEqual(len(adapter.sent), 1)

    def test_no_provider_error_response(self):
        m = _mgr(providers=[])
        resp = m.escalate(_msgs(), user_consented=True)
        self.assertFalse(resp.quality_passed)
        self.assertIn("no configured provider", resp.text.lower())

    def test_malformed_response_tokens_degrade_not_crash(self):
        # Don't trust the adapter's response shape: a response missing the token
        # fields must log usage with 0 counts, not crash the escalation.
        class _NoTokens:
            text = "answer"
        adapter = _FakeAdapter()
        adapter._response = _NoTokens()
        m = _mgr(providers=[_cfg()], adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs(), user_consented=True)
        self.assertEqual(resp.text, "answer")
        usage = m.get_usage(30)
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].tokens_prompt, 0)
        self.assertEqual(usage[0].tokens_completion, 0)

    def test_provider_error_degrades(self):
        from intergen.cloud.http_adapter import CloudAdapterError
        adapter = _FakeAdapter(raises=CloudAdapterError("503"))
        m = _mgr(providers=[_cfg()], adapter_factory=lambda c: adapter)
        resp = m.escalate(_msgs(), user_consented=True)
        self.assertFalse(resp.quality_passed)
        self.assertIn("unreachable", resp.text.lower())

    def test_usage_logged_and_filtered(self):
        adapter = _FakeAdapter()
        clock = [1_000_000.0]
        m = _mgr(providers=[_cfg()], adapter_factory=lambda c: adapter,
                 clock=lambda: clock[0])
        m.escalate(_msgs(), user_consented=True)
        self.assertEqual(len(m.get_usage(30)), 1)
        clock[0] += 40 * 86400  # advance 40 days
        self.assertEqual(len(m.get_usage(30)), 0)  # outside the window


class ConfigTests(unittest.TestCase):
    def test_configure_valid_provider(self):
        m = _mgr(providers=[])
        ok, msg = m.configure_provider(_cfg())
        self.assertTrue(ok, msg)
        self.assertEqual([p.name for p in m.list_providers()], ["acme"])

    def test_configure_unknown_adapter_rejected(self):
        def _boom(c):
            raise ValueError("unknown adapter")
        m = EscalationManager(adapter_factory=_boom)
        ok, msg = m.configure_provider(_cfg(adapter="bogus"))
        self.assertFalse(ok)
        self.assertIn("unknown", msg.lower())

    def test_mode_get_set(self):
        m = _mgr()
        self.assertIs(m.get_mode(), EscalationMode.ASK)
        m.set_mode(EscalationMode.AUTO)
        self.assertIs(m.get_mode(), EscalationMode.AUTO)

    def test_list_providers_carries_only_keyring_id(self):
        m = _mgr(providers=[_cfg()])
        p = m.list_providers()[0]
        self.assertEqual(p.api_key_keyring_id, "intergen-acme")
        # ProviderConfig has no api_key field at all — key lives in the keyring.
        self.assertFalse(hasattr(p, "api_key"))


class FromConfigTests(unittest.TestCase):
    _FF = staticmethod(lambda c: _FakeAdapter(c.name))

    def _prov(self, name="acme", adapter="openai"):
        return {"name": name, "adapter": adapter, "model": "m-1",
                "api_key_keyring_id": f"intergen-{name}"}

    def test_parses_mode_and_providers(self):
        m = EscalationManager.from_config(
            {"mode": "auto"}, [self._prov()], adapter_factory=self._FF)
        self.assertIs(m.get_mode(), EscalationMode.AUTO)
        self.assertEqual([p.name for p in m.list_providers()], ["acme"])

    def test_primary_provider_ordered_first(self):
        m = EscalationManager.from_config(
            {"mode": "ask", "primary_provider": "beta"},
            [self._prov("acme"), self._prov("beta"), self._prov("gamma")],
            adapter_factory=self._FF)
        # _primary_provider() picks providers[0]; primary must lead.
        self.assertEqual(m.list_providers()[0].name, "beta")

    def test_invalid_mode_defaults_to_ask(self):
        m = EscalationManager.from_config(
            {"mode": "garbage"}, [], adapter_factory=self._FF)
        self.assertIs(m.get_mode(), EscalationMode.ASK)

    def test_malformed_provider_entry_skipped_not_crashed(self):
        m = EscalationManager.from_config(
            {"mode": "ask"},
            [{"name": "ok", "adapter": "openai", "model": "m",
              "api_key_keyring_id": "k"},
             {"name": "broken"}],          # missing required fields
            adapter_factory=self._FF)
        self.assertEqual([p.name for p in m.list_providers()], ["ok"])

    def test_none_config_yields_ask_no_providers(self):
        m = EscalationManager.from_config(None, None, adapter_factory=self._FF)
        self.assertIs(m.get_mode(), EscalationMode.ASK)
        self.assertEqual(m.list_providers(), [])

    def test_injected_scanner_used_for_derived_egress(self):
        scanner = _FakeScanner(ScanDisposition.BLOCK)
        m = EscalationManager.from_config(
            {"mode": "ask", "primary_provider": "acme"}, [self._prov()],
            scanner=scanner, adapter_factory=self._FF)
        # A derived (non-consented) egress must hit the injected scanner -> BLOCK.
        resp = m.escalate(_msgs("secret"), user_consented=False)
        self.assertTrue(scanner.calls)
        self.assertFalse(resp.quality_passed)



class OfferedPhraseIsRecognisedTests(unittest.TestCase):
    """The offer text tells the user what to TYPE to reach the frontier model.
    Measured 2026-08-26: the router's offer and the conversational steer both said
    "type 'ask my frontier model'", and the explicit-ask matcher recognised only
    "ask your frontier …" — a user who did exactly what the assistant asked was not
    heard. Every phrase the product quotes in an offer must be one the matcher
    accepts, and the phrases are read from the product's own strings so the two
    cannot drift apart again."""

    def test_ask_my_frontier_model_is_an_explicit_ask(self):
        m = _mgr(mode=EscalationMode.ASK, providers=[_cfg()])
        d = m.should_escalate("ask my frontier model", "fine", "", 1.0)
        self.assertTrue(d.should_escalate)
        self.assertEqual(d.reason, "you asked me to reach your frontier model")

    def test_every_quoted_offer_phrase_matches_the_explicit_ask(self):
        import re
        from intergen import escalation, safety
        from intergen.router import ConversationRouter  # noqa: F401 — module import
        import intergen.router as router_mod
        import inspect
        quoted = set()
        for mod in (router_mod, safety):
            src = inspect.getsource(mod)
            # adjacent string literals split over lines are one string
            src = re.sub(r'"\s*\n\s*"', "", src)
            quoted.update(re.findall(r"type '([^']+)' in", src))
        self.assertTrue(quoted, "no offer phrase found in the product strings")
        for phrase in sorted(quoted):
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    escalation._EXPLICIT_ASK.search(phrase)
                    or escalation._EXPLICIT_ASK_OWN.search(phrase),
                    f"the product tells the user to type {phrase!r} but the "
                    f"explicit-ask matcher does not recognise it")


if __name__ == "__main__":
    unittest.main()
