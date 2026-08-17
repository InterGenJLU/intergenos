# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Test suite for CloudScanner — the opt-in cloud deep-scan tier (step 4).

Covers verdict mapping (allow/flag/block, json-fence tolerance, score clamp),
every fail-closed path (unconfigured, provider error, unexpected error, empty
or malformed or unparseable or unknown-disposition reply), the opt-in/never-
default contract, the content framing handed to the provider, and the
ScannerPolicy composition (floor BLOCK short-circuits so the cloud provider is
never called; floor FLAG escalates and most-severe-wins can only hold/escalate
the floor).

The cloud adapter is injected as a fake, so these run with no network.
"""

from __future__ import annotations

import unittest

from intergen.cloud.http_adapter import CloudAdapterError
from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanDirection,
    ScanDisposition,
    ScanVerdict,
)
from intergen.interfaces.types import LLMResponse
from intergen.scanner.cloud_scanner import CloudScanner
from intergen.scanner.policy import ScannerPolicy, ScanDepth


# -- fakes -------------------------------------------------------------------

class FakeAdapter:
    """Records the messages it is sent and returns a canned reply (or raises)."""

    def __init__(self, reply: str = "", *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.calls: list[dict] = []

    def send(self, messages, *, tools=None, max_tokens=None, temperature=None):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self._raises is not None:
            raise self._raises
        return LLMResponse(text=self._reply, model="fake-model")


class StubScanner(Scanner):
    """A floor stand-in that returns a fixed verdict (for policy tests)."""

    def __init__(self, disposition: ScanDisposition) -> None:
        self._disposition = disposition
        self.calls = 0

    @property
    def name(self) -> str:
        return "stub-floor"

    @property
    def is_local(self) -> bool:
        return True

    def scan(self, content: str, ctx: ScanContext) -> ScanVerdict:
        self.calls += 1
        return ScanVerdict(disposition=self._disposition, scanner=self.name, score=0.5)


def _ctx() -> ScanContext:
    return ScanContext(surface="mcp:srv/tool", direction=ScanDirection.EGRESS)


def _scan(reply: str) -> ScanVerdict:
    return CloudScanner(adapter=FakeAdapter(reply)).scan("payload", _ctx())


# -- identity ----------------------------------------------------------------

class TestIdentity(unittest.TestCase):
    def test_name(self):
        self.assertEqual(CloudScanner().name, "cloud")

    def test_is_not_local(self):
        # It crosses the network to a third party — never report as on-device.
        self.assertFalse(CloudScanner().is_local)


# -- verdict mapping ---------------------------------------------------------

class TestConstruction(unittest.TestCase):
    def test_config_builds_adapter_via_factory(self):
        # The opt-in path: an operator-set ProviderConfig is turned into a real
        # substrate adapter (no network at construction time).
        from intergen.cloud.http_adapter import HTTPCloudAdapter
        from intergen.interfaces.cloud import ProviderConfig

        cfg = ProviderConfig(
            name="my-openai", adapter="openai", model="gpt-x", api_key_keyring_id="kid"
        )
        scanner = CloudScanner(config=cfg)
        self.assertIsInstance(scanner._adapter, HTTPCloudAdapter)
        self.assertFalse(scanner.is_local)

    def test_unknown_provider_is_a_hard_error(self):
        from intergen.interfaces.cloud import ProviderConfig

        cfg = ProviderConfig(
            name="bad", adapter="not-a-provider", model="x", api_key_keyring_id="kid"
        )
        with self.assertRaises(Exception):
            CloudScanner(config=cfg)


class TestVerdictMapping(unittest.TestCase):
    def test_allow(self):
        v = _scan('{"disposition": "allow", "reason": "clean", "score": 0.1}')
        self.assertIs(v.disposition, ScanDisposition.ALLOW)
        self.assertEqual(v.scanner, "cloud")

    def test_flag(self):
        v = _scan('{"disposition": "flag", "reason": "suspicious", "score": 0.7}')
        self.assertIs(v.disposition, ScanDisposition.FLAG)
        self.assertEqual(v.reason, "suspicious")

    def test_block(self):
        v = _scan('{"disposition": "block", "reason": "exfil", "score": 0.95, '
                  '"categories": ["secret.api-key"]}')
        self.assertIs(v.disposition, ScanDisposition.BLOCK)
        self.assertIn("secret.api-key", v.categories)

    def test_tolerates_json_fence(self):
        v = _scan('```json\n{"disposition": "block", "score": 0.9}\n```')
        self.assertIs(v.disposition, ScanDisposition.BLOCK)

    def test_score_is_clamped(self):
        v = _scan('{"disposition": "flag", "score": 7.5}')
        self.assertLessEqual(v.score, 1.0)

    def test_score_fallback_when_missing(self):
        v = _scan('{"disposition": "block"}')
        self.assertIs(v.disposition, ScanDisposition.BLOCK)
        self.assertGreater(v.score, 0.0)


# -- fail-closed paths (HG #10: never a silent ALLOW) ------------------------

class TestFailClosed(unittest.TestCase):
    def _assert_fail_closed(self, v: ScanVerdict):
        self.assertIs(v.disposition, ScanDisposition.FLAG)
        self.assertEqual(v.scanner, "cloud")

    def test_unconfigured_no_adapter_no_config(self):
        v = CloudScanner().scan("payload", _ctx())
        self._assert_fail_closed(v)
        self.assertIn("scanner.cloud-unavailable", v.categories)

    def test_provider_error_fails_closed(self):
        adapter = FakeAdapter(raises=CloudAdapterError("anthropic HTTP 503: down"))
        v = CloudScanner(adapter=adapter).scan("payload", _ctx())
        self._assert_fail_closed(v)
        self.assertIn("scanner.cloud-error", v.categories)

    def test_unexpected_error_fails_closed(self):
        adapter = FakeAdapter(raises=RuntimeError("boom"))
        v = CloudScanner(adapter=adapter).scan("payload", _ctx())
        self._assert_fail_closed(v)

    def test_empty_response_fails_closed(self):
        self._assert_fail_closed(_scan(""))

    def test_malformed_envelope_fails_closed(self):
        self._assert_fail_closed(_scan("not json at all"))

    def test_unparseable_verdict_fails_closed(self):
        self._assert_fail_closed(_scan('{"disposition": '))

    def test_unknown_disposition_fails_closed(self):
        self._assert_fail_closed(_scan('{"disposition": "quarantine"}'))

    def test_missing_disposition_key_fails_closed(self):
        self._assert_fail_closed(_scan('{"reason": "no disposition field"}'))

    def test_non_text_response_fails_closed(self):
        # A transport may hand back a truthy non-string `text` that survives the
        # `response.text` truthiness guard — e.g. an OpenAI content-parts list or
        # a number from a variant/proxy/error envelope. It must degrade to FLAG,
        # never crash the deep-scan path (the fail-closed invariant, HG #10).
        for reply in ([{"type": "text", "text": "hi"}], 7):
            v = CloudScanner(adapter=FakeAdapter(reply)).scan("payload", _ctx())
            self._assert_fail_closed(v)
            self.assertIn("scanner.cloud-error", v.categories)


# -- opt-in contract + request shaping ---------------------------------------

class TestOptInAndShaping(unittest.TestCase):
    def test_empty_content_allows_without_calling_provider(self):
        adapter = FakeAdapter('{"disposition": "block"}')
        v = CloudScanner(adapter=adapter).scan("", _ctx())
        self.assertIs(v.disposition, ScanDisposition.ALLOW)
        self.assertEqual(adapter.calls, [], "provider must not be called on empty content")

    def test_unconfigured_never_calls_a_provider(self):
        # The opt-in contract: with no config/adapter there is no network call,
        # the scan fails closed locally.
        v = CloudScanner().scan("payload", _ctx())
        self.assertIs(v.disposition, ScanDisposition.FLAG)

    def test_content_framed_as_delimited_data(self):
        adapter = FakeAdapter('{"disposition": "allow"}')
        CloudScanner(adapter=adapter).scan("rm -rf /etc", _ctx())
        sent = adapter.calls[0]["messages"]
        roles = [m.role.value for m in sent]
        self.assertEqual(roles, ["system", "user"])
        user = sent[1].content
        self.assertIn("BEGIN CONTENT TO CLASSIFY", user)
        self.assertIn("rm -rf /etc", user)
        self.assertIn("DIRECTION: egress", user)
        # deterministic security judgement
        self.assertEqual(adapter.calls[0]["temperature"], 0.0)

    def test_long_content_is_capped(self):
        adapter = FakeAdapter('{"disposition": "allow"}')
        CloudScanner(adapter=adapter).scan("A" * 20000, _ctx())
        user = adapter.calls[0]["messages"][1].content
        self.assertLess(len(user), 12000, "content window must be capped before send")


# -- ScannerPolicy composition -----------------------------------------------

class TestPolicyComposition(unittest.TestCase):
    def test_floor_block_short_circuits_cloud(self):
        # A floor BLOCK is final — the cloud provider must never be spent.
        adapter = FakeAdapter('{"disposition": "allow"}')
        cloud = CloudScanner(adapter=adapter)
        policy = ScannerPolicy(
            local_rules=StubScanner(ScanDisposition.BLOCK), deep_scanner=cloud
        )
        v = policy.scan("payload", _ctx())
        self.assertIs(v.disposition, ScanDisposition.BLOCK)
        self.assertEqual(adapter.calls, [], "cloud must not run after a floor BLOCK")

    def test_floor_flag_escalates_to_cloud(self):
        # The floor FLAGs -> cloud runs; cloud BLOCK escalates (most-severe-wins).
        adapter = FakeAdapter('{"disposition": "block", "score": 0.95}')
        cloud = CloudScanner(adapter=adapter)
        policy = ScannerPolicy(
            local_rules=StubScanner(ScanDisposition.FLAG), deep_scanner=cloud
        )
        v = policy.scan("payload", _ctx())
        self.assertIs(v.disposition, ScanDisposition.BLOCK)
        self.assertEqual(len(adapter.calls), 1)

    def test_cloud_cannot_downgrade_the_floor(self):
        # Even a cloud ALLOW cannot pull a floor FLAG below FLAG — the human
        # modal still fires. This is the key defense-in-depth property.
        adapter = FakeAdapter('{"disposition": "allow", "score": 0.0}')
        cloud = CloudScanner(adapter=adapter)
        policy = ScannerPolicy(
            local_rules=StubScanner(ScanDisposition.FLAG), deep_scanner=cloud
        )
        v = policy.scan("payload", _ctx())
        self.assertIs(v.disposition, ScanDisposition.FLAG)


if __name__ == "__main__":
    unittest.main()
