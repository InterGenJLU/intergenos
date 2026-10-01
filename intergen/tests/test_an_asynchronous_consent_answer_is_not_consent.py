# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What the consent mechanism may be, and what its answer may be.

The send path that asks the mechanism is synchronous: it calls it and reads the
answer. A second read of the asking rule found that neither end of that was
constrained.

The setter accepted any object at all, and the send path converted whatever came
back with ordinary truth testing. Hand it a coroutine function — the obvious
shape for a front end whose own consent dialog is asynchronous — and calling it
returns a coroutine object whose body has NOT run. A coroutine object is truthy.
So a mechanism written to refuse authorised the send, the refusal it meant to
give was never evaluated, and the turn reached the destination while the dialog
that should have stopped it had not even started.

Two gates close that, and both are here:

  * the setter refuses what cannot answer — something that is not callable, a
    coroutine function, and anything that cannot be asked with the payload and
    the destination. A front end learns at setup, not at the first turn whose
    local attempts failed;
  * the send path refuses an awaitable ANSWER, which is the case the setter
    cannot see: an ordinary function may still return a coroutine it never
    awaited. The coroutine is closed rather than left to warn, and the refusal
    is recorded with the destination it was asked about;
  * the send path takes a Boolean true and nothing else. Ordinary truth testing
    is what made an unexecuted coroutine read as approval, so every other shape
    an answer might arrive in — a string, a number, a stand-in object — is a
    refusal, recorded with the shape that came back;
  * what is dispatched is what was approved. The payload is captured before the
    question is put and compared again before it is handed over, so a mechanism
    cannot approve one turn and have another leave.

Hermetic: urllib.request.urlopen always times out, which spends the reply
ladder, and the destination is a recorder in memory. Nothing opens a socket and
nothing leaves the machine.
"""

from __future__ import annotations

import socket
import unittest
import warnings
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.cloud import CloudProviderAdapter
from intergen.interfaces.types import Message, MessageRole
from intergen.llm import LLMRouter

TIMED_OUT = socket.timeout("timed out")
LOCAL_CONTENT = "a sentence that was typed on this machine and belongs to it"


class _AlwaysTimesOut:
    def __init__(self) -> None:
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        raise TIMED_OUT


class _Recording(CloudProviderAdapter):
    """It keeps the cloud-provider contract: the router registers no other
    kind."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self):
        return "the-destination"

    def send(self, messages, *, tools=None, max_tokens=None,
             temperature=None, should_stop=None):
        self.calls += 1
        return mock.Mock(text="an answer from off the machine",
                         tokens_prompt=1, tokens_completion=2)

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return True, "the-destination"


async def _an_asynchronous_refusal(_messages, _destination):
    """The shape a front end with an asynchronous dialog would write."""
    return False


def _a_synchronous_function_returning_a_coroutine(messages, destination):
    """Synchronous to the setter, asynchronous in fact.

    This is what the setter cannot see: nothing about this function says
    asynchronous until its return value is looked at.
    """
    return _an_asynchronous_refusal(messages, destination)


class AnAsynchronousConsentAnswerIsNotConsent(unittest.TestCase):

    def setUp(self):
        self.llm = LLMRouter(config=None)
        self.destination = _Recording()
        registered, why = self.llm.register_cloud_provider(
            "the-destination", self.destination)
        self.assertTrue(registered, why)
        self.transport = _AlwaysTimesOut()

    def _one_turn(self):
        with mock.patch("urllib.request.urlopen", self.transport), \
             mock.patch.object(llm_module.glass, "emit",
                               lambda *_a, **_k: None):
            return self.llm.chat(
                [Message(role=MessageRole.USER, content=LOCAL_CONTENT)])

    # ---- what the setter refuses

    def test_an_asynchronous_mechanism_is_refused_at_registration(self):
        with self.assertRaises(TypeError) as raised:
            self.llm.set_escalation_consent(_an_asynchronous_refusal)
        self.assertIn("asynchronous", str(raised.exception).lower())
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0,
                         "a refused registration must leave nobody to ask, "
                         "not a half-registered mechanism")
        self.assertTrue(reply.local)

    def test_a_mechanism_that_cannot_be_asked_about_a_destination_is_refused(self):
        with self.assertRaises(TypeError):
            self.llm.set_escalation_consent(lambda _messages: True)
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)

    def test_something_that_is_not_callable_is_refused_at_registration(self):
        with self.assertRaises(TypeError):
            self.llm.set_escalation_consent("yes")
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)

    # ---- what the send path refuses

    def test_an_awaitable_answer_is_not_consent(self):
        self.llm.set_escalation_consent(
            _a_synchronous_function_returning_a_coroutine)
        with self.assertLogs("intergen.llm", level="ERROR") as logs:
            reply = self._one_turn()
        self.assertGreaterEqual(self.transport.attempts, 1)
        self.assertEqual(self.destination.calls, 0,
                         "a coroutine is truthy, so truth-testing it sent the "
                         "turn on an answer that was never evaluated")
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("awaitable", said)
        self.assertIn("the-destination", said)

    def test_the_refused_coroutine_is_closed_rather_than_left_to_warn(self):
        self.llm.set_escalation_consent(
            _a_synchronous_function_returning_a_coroutine)
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            with self.assertLogs("intergen.llm", level="ERROR"):
                self._one_turn()
        never_awaited = [w for w in seen
                         if "never awaited" in str(w.message)]
        self.assertEqual(
            never_awaited, [],
            "a coroutine the router refused must be closed by the router: an "
            "unclosed one warns from wherever the garbage collector gets to it")

    # ---- only a Boolean true is consent

    def test_an_answer_that_is_merely_truthy_is_not_consent(self):
        self.llm.set_escalation_consent(lambda _m, _d: "yes")
        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("only a Boolean true is consent", said)
        self.assertIn("str", said, "the record must name what came back")

    def test_a_stand_in_object_does_not_authorise_a_send(self):
        """A test double left wired by accident is truthy; it is not consent."""
        self.llm.set_escalation_consent(lambda _m, _d: mock.Mock())
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)

    # ---- what leaves is what was approved

    def test_a_turn_changed_after_it_was_approved_is_not_sent(self):
        """The approval is bound to content, not to a list object."""

        def consent(messages, _destination):
            messages[-1].content = "something the mechanism was never shown"
            return True

        self.llm.set_escalation_consent(consent)
        with self.assertLogs("intergen.llm", level="ERROR") as logs:
            reply = self._one_turn()
        self.assertEqual(
            self.destination.calls, 0,
            "what left was not what was approved, so it must not leave")
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("changed between the approval and the send", said)
        self.assertIn("the-destination", said)

    def test_an_image_attached_after_the_approval_is_not_sent(self):
        """The same rule for the payload half of the finding beside this one."""

        def consent(messages, _destination):
            messages[-1].image_data = "data:image/png;base64,YWZ0ZXJ3YXJkcw=="
            return True

        self.llm.set_escalation_consent(consent)
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)

    # ---- the legs that must not change

    def test_a_synchronous_mechanism_is_still_registered_and_still_asked(self):
        asked = []

        def consent(messages, destination):
            asked.append(destination)
            return True

        self.llm.set_escalation_consent(consent)
        reply = self._one_turn()
        self.assertEqual(asked, ["the-destination"])
        self.assertEqual(self.destination.calls, 1)
        self.assertFalse(reply.local)

    def test_a_synchronous_refusal_still_refuses(self):
        self.llm.set_escalation_consent(lambda _m, _d: False)
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)

    def test_clearing_the_mechanism_is_still_allowed(self):
        self.llm.set_escalation_consent(lambda _m, _d: True)
        self.llm.set_escalation_consent(None)
        reply = self._one_turn()
        self.assertEqual(self.destination.calls, 0)
        self.assertTrue(reply.local)


if __name__ == "__main__":
    unittest.main()
