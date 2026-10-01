# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What ASK mode does once there is somebody to ask, and what it does without.

The pinned case beside this one (test_zz192_p3_01.py) holds the defect: under
the shipped default mode, whose name promises the person is asked, a turn whose
two local attempts failed the quality gate was handed whole to the first
registered cloud adapter with nothing asked. This module holds the rest of the
rule, which that case does not reach:

  * ASK with nobody registered to ask sends nothing, and SAYS so once, naming
    the mode and how many providers were available — a silent refusal would be
    as hard to act on as the silent send was;
  * ASK with a consent mechanism asks it, and asks it with the messages that
    would leave, not with a summary of them;
  * a mechanism that answers yes authorises THAT turn, and the decision is
    recorded;
  * a mechanism that answers no, or that raises, refuses: a consent hook that
    cannot answer has not consented;
  * FALLBACK and AUTO authorise an unprompted send, which is what the project
    defines them as, and NEVER still sends nothing;
  * a mode this router has not been taught to authorise authorises nothing.

Hermetic, as the pinned case is: urllib.request.urlopen is replaced by a
stand-in that always times out, which is what drives the reply ladder to its
end, and the cloud "provider" is a recorder that answers in memory. Nothing
opens a socket and nothing leaves the machine.
"""

from __future__ import annotations

import socket
import unittest
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.cloud import CloudProviderAdapter
from intergen.interfaces.types import EscalationMode, Message, MessageRole
from intergen.llm import LLMRouter

TIMED_OUT = socket.timeout("timed out")
LOCAL_CONTENT = "a sentence that was typed on this machine and belongs to it"


class _AlwaysTimesOut:
    """Every local request times out, which is the case the ladder retries."""

    def __init__(self) -> None:
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        raise TIMED_OUT


class _Recording(CloudProviderAdapter):
    """A cloud adapter stand-in that records everything handed to it. It
    keeps the cloud-provider contract: the router registers no other kind."""

    def __init__(self) -> None:
        self.calls = 0
        self.sent: list[list[Message]] = []

    @property
    def name(self):
        return "recording-provider"

    def send(self, messages, *, tools=None, max_tokens=None,
             temperature=None, should_stop=None):
        self.calls += 1
        self.sent.append(list(messages))
        return mock.Mock(text="an answer from off the machine",
                         tokens_prompt=1, tokens_completion=2)

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return True, "recording-provider"


class _Consent:
    """A consent mechanism that records what it was asked and answers as told."""

    def __init__(self, answer):
        self.answer = answer
        self.asked_with: list[list[Message]] = []
        # The destination each question was about. The mechanism is asked once
        # per destination, with the payload that destination will receive; the
        # module beside this one holds that rule and its cases.
        self.asked_about: list[str | None] = []

    def __call__(self, messages, destination=None):
        self.asked_with.append(list(messages))
        self.asked_about.append(destination)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class AskModeAsksBeforeATurnLeaves(unittest.TestCase):

    def setUp(self):
        # config=None is the shipped shape: the daemon builds the router with
        # no "escalation_mode" key, so the constructor's own default runs.
        self.llm = LLMRouter(config=None)
        self.provider = _Recording()
        registered, why = self.llm.register_cloud_provider(
            "recording-provider", self.provider)
        self.assertTrue(registered, why)
        self.transport = _AlwaysTimesOut()

    def _one_turn(self, text: str = LOCAL_CONTENT):
        with mock.patch("urllib.request.urlopen", self.transport), \
             mock.patch.object(llm_module.glass, "emit",
                               lambda *_a, **_k: None):
            return self.llm.chat(
                [Message(role=MessageRole.USER, content=text)])

    def _assert_the_ladder_was_spent(self):
        self.assertGreaterEqual(
            self.transport.attempts, 1,
            "the local server was never asked, so this turn never reached the "
            "escalation step and proves nothing")

    # ---- nobody to ask

    def test_with_no_consent_hook_the_refusal_is_recorded_not_silent(self):
        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn()
        self._assert_the_ladder_was_spent()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("no consent hook is registered", said)
        self.assertIn("was NOT sent to a cloud provider", said)
        self.assertIn("(1 registered)", said,
                      "the record must say a provider WAS available, or the "
                      "reader cannot tell a refusal from an absence")

    # ---- somebody to ask

    def test_the_consent_hook_is_asked_with_what_would_leave(self):
        consent = _Consent(True)
        self.llm.set_escalation_consent(consent)
        self._one_turn()
        self._assert_the_ladder_was_spent()
        self.assertEqual(len(consent.asked_with), 1,
                         "the hook is asked exactly once for one turn")
        asked = [m.content for m in consent.asked_with[0]
                 if m.role is MessageRole.USER]
        self.assertIn(LOCAL_CONTENT, asked,
                      "a consent prompt that is not shown what leaves cannot "
                      "be consent to it leaving")

    def test_consent_given_authorises_that_turn_and_is_recorded(self):
        self.llm.set_escalation_consent(_Consent(True))
        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn()
        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(reply.local)
        self.assertEqual(reply.model, "cloud:recording-provider")
        self.assertIn("escalation consent was GIVEN", " ".join(logs.output))

    def test_consent_refused_sends_nothing(self):
        self.llm.set_escalation_consent(_Consent(False))
        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn()
        self._assert_the_ladder_was_spent()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)
        self.assertIn("escalation consent was REFUSED", " ".join(logs.output))

    def test_a_consent_hook_that_raises_has_not_consented(self):
        self.llm.set_escalation_consent(_Consent(RuntimeError("no answer")))
        with self.assertLogs("intergen.llm", level="ERROR") as logs:
            reply = self._one_turn()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("consent hook raised (RuntimeError: no answer)", said)
        self.assertIn("was NOT sent", said)

    def test_clearing_the_hook_returns_to_sending_nothing(self):
        self.llm.set_escalation_consent(_Consent(True))
        self.llm.set_escalation_consent(None)
        reply = self._one_turn()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)

    # ---- the modes that do and do not authorise, by the project's own words

    def test_auto_authorises_an_unprompted_send(self):
        """AUTO is defined as "decide by confidence, no prompt"."""
        self.llm.set_escalation_mode(EscalationMode.AUTO)
        reply = self._one_turn()
        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(reply.local)

    def test_never_still_sends_nothing_and_adds_no_words(self):
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        reply = self._one_turn()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)

    def test_a_mode_this_router_was_not_taught_authorises_nothing(self):
        """The test is an allow list, so a mode added later is refused until
        this router is taught about it — and says which mode it refused."""
        class _NewMode:
            value = "a-mode-added-later"

        self.llm.set_escalation_mode(_NewMode())
        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn()
        self.assertEqual(self.provider.calls, 0)
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("a-mode-added-later", said)
        self.assertIn("not one this router has been taught to authorise", said)


if __name__ == "__main__":
    unittest.main()
