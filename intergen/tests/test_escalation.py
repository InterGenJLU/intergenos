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
from intergen.interfaces.types import (
    EscalationMode, EscalationNotSent, LLMResponse, Message, MessageRole,
)


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

    def test_invalid_mode_reads_as_never_and_names_the_value(self):
        m = EscalationManager.from_config(
            {"mode": "garbage"}, [], adapter_factory=self._FF)
        self.assertIs(m.get_mode(), EscalationMode.NEVER)
        self.assertEqual(m.unreadable_mode(), "'garbage'")
        self.assertIn("'garbage'", m.refusal())

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


class NeverRefusesEverySendTests(unittest.TestCase):
    """escalation.mode = never refuses the send itself, not only the offer.

    The mode was read by should_escalate alone, so a send the person invoked
    (the panel button, `intergen ask-frontier`, the bus's Escalate) went to the
    provider under never. Now every send under never comes back not sent, with
    a sentence naming the setting, before any adapter is built or any scan
    runs; the other three modes send a consented hop as before."""

    def _recording(self):
        built = []

        def factory(config):
            adapter = _FakeAdapter(config.name)
            built.append(adapter)
            return adapter
        return built, factory

    def assert_refused_by_the_setting(self, resp):
        self.assertIsInstance(resp, EscalationNotSent)
        self.assertTrue(resp.text.startswith("Nothing was sent"), resp.text)
        self.assertIn("escalation.mode", resp.text)
        self.assertIn("never", resp.text)

    def test_never_refuses_the_hop_the_person_consented_to(self):
        built, factory = self._recording()
        m = _mgr(mode=EscalationMode.NEVER, providers=[_cfg()],
                 adapter_factory=factory)
        resp = m.escalate(_msgs("please send this"), user_consented=True)
        self.assert_refused_by_the_setting(resp)
        self.assertEqual(built, [])  # no adapter was even built
        self.assertEqual(m.get_usage(), [])

    def test_never_refuses_a_derived_hop_before_any_scan(self):
        built, factory = self._recording()
        scanner = _FakeScanner(ScanDisposition.ALLOW)
        m = _mgr(mode=EscalationMode.NEVER, providers=[_cfg()],
                 adapter_factory=factory, scanner=scanner)
        resp = m.escalate(_msgs("follow-on"), user_consented=False)
        self.assert_refused_by_the_setting(resp)
        self.assertEqual(scanner.calls, [])
        self.assertEqual(built, [])

    def test_never_read_from_config_refuses(self):
        built, factory = self._recording()
        m = EscalationManager.from_config(
            {"mode": "never", "primary_provider": "acme"},
            [{"name": "acme", "adapter": "openai", "model": "acme-1",
              "api_key_keyring_id": "intergen-acme"}],
            adapter_factory=factory)
        self.assertIs(m.get_mode(), EscalationMode.NEVER)
        self.assert_refused_by_the_setting(
            m.escalate(_msgs("hello"), user_consented=True))
        self.assertEqual(built, [])

    def test_the_refusal_the_callers_ask_before_the_dialog(self):
        # The callers put this question before the show-before-send dialog:
        # the sentence under never, nothing under every other mode.
        never = _mgr(mode=EscalationMode.NEVER, providers=[_cfg()])
        self.assertEqual(never.refusal(),
                         never.escalate(_msgs("x"), user_consented=True).text)
        self.assertIn("escalation.mode", never.refusal())
        for mode in (EscalationMode.ASK, EscalationMode.AUTO,
                     EscalationMode.FALLBACK):
            with self.subTest(mode=mode.value):
                self.assertIsNone(_mgr(mode=mode, providers=[_cfg()]).refusal())

    def test_the_other_modes_send_the_consented_hop_unchanged(self):
        for mode in (EscalationMode.ASK, EscalationMode.AUTO,
                     EscalationMode.FALLBACK):
            with self.subTest(mode=mode.value):
                built, factory = self._recording()
                m = _mgr(mode=mode, providers=[_cfg()], adapter_factory=factory)
                resp = m.escalate(_msgs("hello"), user_consented=True)
                self.assertNotIsInstance(resp, EscalationNotSent)
                self.assertEqual(resp.text, "cloud answer")
                self.assertEqual(len(built), 1)
                self.assertEqual(len(built[0].sent), 1)


# escalation.mode as a person may write it in config.yml, and the words the
# refusal names it by, as the config file's YAML reads it.
UNREADABLE = [
    ("off", "false (YAML reads off, no and false as false)"),
    ("no", "false (YAML reads off, no and false as false)"),
    ("false", "false (YAML reads off, no and false as false)"),
    ("on", "true (YAML reads on, yes and true as true)"),
    ("yes", "true (YAML reads on, yes and true as true)"),
    ("true", "true (YAML reads on, yes and true as true)"),
    ("none", "'none'"),
    ("disabled", "'disabled'"),
    ("0", "0"),
    ("'never '", "'never '"),
    ("", "empty"),
]


def _section_as_written(directory, written):
    """The escalation section as the daemon reads it (Config over a file
    holding this mode, read by the same YAML reader as config.yml)."""
    from intergen.config import Config
    path = f"{directory}/config.yml"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"escalation:\n  mode: {written}\n  primary_provider: acme\n")
    return Config(config_path=path).get("escalation")


class AnUnreadableModeReadsAsNever(unittest.TestCase):
    """An escalation.mode the parser cannot read was read as ask: a person who
    turned phone-a-friend off in any spelling but never kept the offer and
    every send behind the dialog. It reads as never now, the refusal naming the
    value written and the four accepted, and nothing reaches a provider."""

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _manager(self, written, built):
        def factory(config):
            adapter = _FakeAdapter(config.name)
            built.append(adapter)
            return adapter
        return EscalationManager.from_config(
            _section_as_written(self._dir.name, written),
            [{"name": "acme", "adapter": "openai", "model": "acme-1",
              "api_key_keyring_id": "intergen-acme"}],
            adapter_factory=factory)

    def test_each_unreadable_value_refuses_every_send_and_names_itself(self):
        for written, words in UNREADABLE:
            with self.subTest(written=written):
                built = []
                m = self._manager(written, built)
                self.assertIs(m.get_mode(), EscalationMode.NEVER)
                self.assertEqual(m.unreadable_mode(), words)
                refused = m.refusal()
                self.assertTrue(refused.startswith("Nothing was sent"), refused)
                self.assertIn(f"escalation.mode setting is {words}, ", refused)
                self.assertIn("not one of never, fallback, ask or auto", refused)
                self.assertIn("read as never", refused)
                resp = m.escalate(_msgs("please send this"), user_consented=True)
                self.assertIsInstance(resp, EscalationNotSent)
                self.assertEqual(resp.text, refused)
                self.assertEqual(built, [])
                self.assertFalse(m.should_escalate(
                    "ask my frontier model about this", "", "failed",
                    0.0).should_escalate)

    def test_the_four_modes_read_as_themselves_in_any_case(self):
        for written in ("never", "Never", "fallback", "ask", "AUTO"):
            with self.subTest(written=written):
                m = self._manager(written, [])
                self.assertIs(m.get_mode(), EscalationMode(written.lower()))
                self.assertIsNone(m.unreadable_mode())

    def test_a_mode_set_by_the_wiring_clears_the_unreadable_value(self):
        m = self._manager("off", [])
        m.set_mode(EscalationMode.ASK)
        self.assertIsNone(m.unreadable_mode())
        self.assertIsNone(m.refusal())


# The escalation.mode values as written, each with the mode it reads as and the
# words it is named by when it is none of the four (None: read as itself).
MODE_ROWS = [
    ("off", "never", "false (YAML reads off, no and false as false)"),
    ("no", "never", "false (YAML reads off, no and false as false)"),
    ("false", "never", "false (YAML reads off, no and false as false)"),
    ("on", "never", "true (YAML reads on, yes and true as true)"),
    ("yes", "never", "true (YAML reads on, yes and true as true)"),
    ("true", "never", "true (YAML reads on, yes and true as true)"),
    ("none", "never", "'none'"),
    ("disabled", "never", "'disabled'"),
    ("0", "never", "0"),
    ("'never '", "never", "'never '"),
    ("", "never", "empty"),
    (None, "ask", None),
    ("never", "never", None),
    ("Never", "never", None),
    ("NEVER", "never", None),
    ("fallback", "fallback", None),
    ("ask", "ask", None),
    ("Ask", "ask", None),
    ("auto", "auto", None),
    ("AUTO", "auto", None),
]

# The escalation section itself written as a value, as config.yml holds it:
# the mode it reads as and its words, as a mode written the same way reads.
SECTION_ROWS = [
    ("off", "never", "false (YAML reads off, no and false as false)"),
    ("false", "never", "false (YAML reads off, no and false as false)"),
    ("no", "never", "false (YAML reads off, no and false as false)"),
    ("0", "never", "0"),
    ("never", "never", None),
    ("disabled", "never", "'disabled'"),
    ("on", "never", "true (YAML reads on, yes and true as true)"),
    ("", "ask", None),
    ("[]", "never", "[]"),
]


def _config_holding(directory, text):
    """The escalation section as the daemon reads it from a config.yml holding
    this text (Config, the same YAML reader)."""
    from intergen.config import Config
    path = f"{directory}/config.yml"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return Config(config_path=path).get("escalation")


class EveryModeRowReadsAsItsMode(unittest.TestCase):
    """The twenty mode rows: each value of escalation.mode as written reads as
    its mode, or as never with its words when it is none of the four; a section
    with no mode key is the default."""

    def test_each_mode_row(self):
        import tempfile
        for written, mode, words in MODE_ROWS:
            with self.subTest(written=written):
                with tempfile.TemporaryDirectory() as d:
                    text = ("escalation:\n  primary_provider: acme\n"
                            if written is None else
                            f"escalation:\n  mode: {written}\n")
                    m = EscalationManager.from_config(
                        _config_holding(d, text), [])
                self.assertIs(m.get_mode(), EscalationMode(mode))
                self.assertEqual(m.unreadable_mode(), words)


class TheSectionWrittenAsAValueReadsAsItsMode(unittest.TestCase):
    """The escalation section written as a value ("escalation: off") was read
    as no section: off, false, no, 0 and [] kept the offer and every send open
    behind the dialog, and never, disabled and on raised in from_config, so the
    daemon built no manager. It is read as its mode now: every value that is
    none of the four reads as never, named as a mode written the same way is
    named; never reads as never; only a section written empty is the default."""

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _manager(self, written, built):
        def factory(config):
            adapter = _FakeAdapter(config.name)
            built.append(adapter)
            return adapter
        return EscalationManager.from_config(
            _config_holding(self._dir.name, f"escalation: {written}\n"),
            [{"name": "acme", "adapter": "openai", "model": "acme-1",
              "api_key_keyring_id": "intergen-acme"}],
            adapter_factory=factory)

    def test_each_section_row(self):
        for written, mode, words in SECTION_ROWS:
            with self.subTest(written=written):
                built = []
                m = self._manager(written, built)
                self.assertIs(m.get_mode(), EscalationMode(mode))
                self.assertEqual(m.unreadable_mode(), words)
                if mode == "ask":
                    self.assertIsNone(m.refusal())
                    continue
                refused = m.refusal()
                self.assertTrue(refused.startswith("Nothing was sent"), refused)
                if words is not None:
                    self.assertIn(f"escalation.mode setting is {words}, ",
                                  refused)
                    self.assertIn("not one of never, fallback, ask or auto",
                                  refused)
                resp = m.escalate(_msgs("please send this"), user_consented=True)
                self.assertIsInstance(resp, EscalationNotSent)
                self.assertEqual(resp.text, refused)
                self.assertEqual(built, [])
                self.assertFalse(m.should_escalate(
                    "ask my frontier model about this", "", "failed",
                    0.0).should_escalate)


if __name__ == "__main__":
    unittest.main()
