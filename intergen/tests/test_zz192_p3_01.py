# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A mode named "ask" must ask before the conversation leaves the machine.

WHAT IS CLAIMED. The router's escalation mode is built with

    self._escalation_mode = EscalationMode(config.get("escalation_mode", "ask"))

and the reply ladder's third attempt is gated on one test only:

    if self._escalation_mode == EscalationMode.NEVER:  # llm.py, chat()
        ...serve the local text...
    cloud_response = self._escalate_to_cloud(messages, ...)

so every mode that is not NEVER authorises the send. ASK is one of them, and
ASK is the default the daemon gets: intergen/dbus_daemon.py builds llm_config
with no "escalation_mode" key at all.

The four modes are defined by the project in intergen/escalation.py:

    NEVER    — offline, never offer or send.
    FALLBACK — auto-escalate only when the local quality gate fails.
    ASK      — OFFER on recognition; the user consents before anything is sent.
    AUTO     — decide by confidence, no prompt.

FALLBACK is, word for word, the mode this code path implements. ASK promises
the opposite: that the person consents BEFORE anything is sent. Nothing on the
chat() path asks — the only consent flow in the tree is the separate
EscalationManager.escalate(user_consented=...) behind the D-Bus escalate()
method, which is a different entry point and is not reached from here.

WHAT THIS PINS:

  * a router built with no "escalation_mode" is in ASK mode — the shipped
    default, since the daemon passes no such key;
  * an ASK-mode turn whose local attempts both fail the quality gate does NOT
    hand the conversation to a registered cloud adapter: under a mode whose
    name is a promise that the person is asked, an unasked send is the defect;
  * FALLBACK is the mode that authorises exactly this unprompted send, and it
    still does — the pin is about consent, not about disabling the fallback.

Hermetic: no socket, no model server, no daemon. urllib.request.urlopen is
replaced by a stand-in that always times out, which is what drives the reply
ladder to its end; the cloud "provider" is a recorder that answers in memory.
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

# What a person typed at their own machine, and what must not leave it unasked.
LOCAL_CONTENT = "the contents of my private notes, which I typed here"


class _AlwaysTimesOut:
    """A urlopen stand-in: every local request times out.

    A timed-out attempt is the case the ladder DOES retry, so both local
    attempts are spent and the turn reaches the escalation step — the same
    route a degenerate reply twice would take, with no engine involved.
    """

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

    def user_text(self) -> list[str]:
        return [m.content for turn in self.sent for m in turn
                if m.role is MessageRole.USER]


class TheDefaultModeAsksBeforeATurnLeavesTheMachine(unittest.TestCase):

    def setUp(self):
        # config=None is the shipped shape: the daemon's llm_config carries no
        # "escalation_mode" key, so the constructor's own default is what runs.
        self.llm = LLMRouter(config=None)
        self.provider = _Recording()
        registered, why = self.llm.register_cloud_provider(
            "recording-provider", self.provider)
        self.assertTrue(registered, why)
        self.transport = _AlwaysTimesOut()

    def _one_turn(self, text: str = LOCAL_CONTENT):
        """Drive one chat() turn whose local attempts all fail."""
        with mock.patch("urllib.request.urlopen", self.transport), \
             mock.patch.object(llm_module.glass, "emit",
                               lambda *_a, **_k: None):
            return self.llm.chat(
                [Message(role=MessageRole.USER, content=text)])

    # ---- the premise -------------------------------------------------------

    def test_the_shipped_default_escalation_mode_is_ask(self):
        self.assertIs(self.llm.get_escalation_mode(), EscalationMode.ASK)

    # ---- the pin -----------------------------------------------------------

    def test_the_default_ask_mode_does_not_send_a_turn_to_the_cloud_unasked(self):
        reply = self._one_turn()

        self.assertGreaterEqual(
            self.transport.attempts, 1,
            "the local server was never asked, so this turn never reached the "
            "escalation step and proves nothing")
        self.assertEqual(
            self.provider.calls, 0,
            "a mode named 'ask' sent the conversation to a cloud provider with "
            "nothing asked: no prompt, no consent record, no egress scan. "
            f"What left the machine: {self.provider.user_text()!r}")
        self.assertTrue(
            reply.local,
            "the turn was answered off the machine "
            f"(model={reply.model!r}) under the mode whose name promises the "
            "person is asked first")

    # ---- the control: the mode that DOES authorise the send ----------------

    def test_the_fallback_mode_is_the_one_that_authorises_an_unprompted_send(self):
        """FALLBACK means exactly "auto-escalate when the local gate fails", so
        the quality fallback must keep working when the mode says so."""
        self.llm.set_escalation_mode(EscalationMode.FALLBACK)
        reply = self._one_turn("a question the local model could not answer")

        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(reply.local)
        self.assertEqual(reply.model, "cloud:recording-provider")


if __name__ == "__main__":
    unittest.main()
