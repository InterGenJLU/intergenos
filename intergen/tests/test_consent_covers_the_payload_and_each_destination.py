# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What the consent mechanism is shown, and how many destinations it covers.

The module beside this one holds the rule that the asking mode asks at all. This
one holds what it is asked ABOUT, which a second read of that work found was not
what actually leaves the machine:

  * an image supplied with the turn is attached to the message the adapter
    receives, and it was attached AFTER the consent mechanism had answered. The
    mechanism therefore approved a text-only payload while a picture left with
    it. What the mechanism is shown must be what leaves, image included;
  * the mechanism was asked once, before the first destination was tried, and
    that one answer was reused for every further destination the failover
    reached. Approving that a conversation leaves for one destination is not
    approving that it leaves for another, so each destination is asked about by
    name;
  * a refusal for one destination skips that destination and nothing else;
  * with every destination refused, the local answer is served and the refusal
    is recorded, naming the destination it was refused for.

The mechanism is asked with two positional arguments, the payload and the
destination's name, so a mechanism that cannot be told where the turn is going
cannot answer at all rather than answering about something else.

Hermetic, as the module beside it is: urllib.request.urlopen is replaced by a
stand-in that always times out, which is what spends the reply ladder, and every
destination is a recorder in memory. Nothing opens a socket and nothing leaves
the machine.

The image presence is snapshotted INSIDE the mechanism, at the moment it is
asked. The mechanism is handed the live message objects, so a snapshot taken
afterwards would show an image attached a moment later and would read as consent
to something the mechanism never saw.
"""

from __future__ import annotations

import socket
import threading
import unittest
from unittest import mock

from intergen import image_ingress
from intergen import llm as llm_module
from intergen.image_ingress import ImageIngressGate
from intergen.scanner.policy import ScannerPolicy
from intergen.interfaces.cloud import CloudProviderAdapter
from intergen.interfaces.types import EscalationMode, Message, MessageRole
from intergen.llm import LLMRouter

TIMED_OUT = socket.timeout("timed out")
LOCAL_CONTENT = "a sentence that was typed on this machine and belongs to it"
PRIVATE_IMAGE = "data:image/png;base64,dGhlLXBlcnNvbnMtb3duLXBpY3R1cmU="


class _AlwaysTimesOut:
    """Every local request times out, which is the case the ladder retries."""

    def __init__(self) -> None:
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        raise TIMED_OUT


class _Recording(CloudProviderAdapter):
    """A destination stand-in that records what it was handed, as it was. It
    keeps the cloud-provider contract: the router registers no other kind."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0
        self.handed: list[list[tuple[str, str, str | None]]] = []

    @property
    def name(self):
        return self._name

    def send(self, messages, *, tools=None, max_tokens=None,
             temperature=None, should_stop=None):
        self.calls += 1
        self.handed.append([(m.role.value, m.content, m.image_data)
                            for m in messages])
        return mock.Mock(text="an answer from off the machine",
                         tokens_prompt=1, tokens_completion=2)

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return True, self._name

    def images_handed(self) -> list[str | None]:
        return [image for turn in self.handed for _r, _c, image in turn
                if image is not None]


class _Failing(CloudProviderAdapter):
    """A destination that cannot be reached, which is what spends a failover.
    It keeps the cloud-provider contract, as _Recording does."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls = 0

    @property
    def name(self):
        return self._name

    def send(self, messages, *, tools=None, max_tokens=None,
             temperature=None, should_stop=None):
        self.calls += 1
        raise ConnectionError("this destination did not answer")

    def stream(self, messages, **kwargs):
        yield self.send(messages, **kwargs).text

    def test_connection(self):
        return False, self._name


class _Consent:
    """Records WHAT IT WAS SHOWN at the moment it was asked, and by whom.

    answers may be a single verdict for every destination, or a mapping from a
    destination's name to its verdict.
    """

    def __init__(self, answers) -> None:
        self.answers = answers
        self.asked: list[tuple[list[tuple[str, str, str | None]], str]] = []

    def __call__(self, messages, destination=None):
        """Both shapes on purpose.

        The destination is optional so that this one instrument can be asked by
        a router that tells it where the turn is going and by one that does not,
        and the difference is then READ from what it recorded rather than from a
        signature error.
        """
        self.asked.append(
            ([(m.role.value, m.content, m.image_data) for m in messages],
             destination))
        if isinstance(self.answers, dict):
            return self.answers.get(destination, False)
        return self.answers

    def destinations_asked_about(self) -> list[str]:
        return [destination for _payload, destination in self.asked]

    def images_shown(self) -> list[str | None]:
        return [image for payload, _d in self.asked
                for _r, _c, image in payload if image is not None]


class ConsentCoversThePayloadAndEachDestination(unittest.TestCase):

    def setUp(self):
        # config=None is the shipped shape for everything but the vision turn:
        # the daemon builds the router with no "escalation_mode" key, so the
        # constructor's own default — the asking mode — runs.
        self.llm = LLMRouter(config=None)
        self.first = _Recording("first-destination")
        registered, why = self.llm.register_cloud_provider(
            "first-destination", self.first)
        self.assertTrue(registered, why)
        self.transport = _AlwaysTimesOut()

    def _router_with(self, *adapters):
        """A router of the shipped shape with these destinations, in order."""
        router = LLMRouter(config=None)
        for adapter in adapters:
            registered, why = router.register_cloud_provider(adapter.name,
                                                             adapter)
            self.assertTrue(registered, why)
        return router

    def _vision_router(self):
        """The same router as the daemon builds when a vision model is loaded.

        has_vision is false by default and an image turn then gets the honest
        code-owned reply instead of reaching the escalation step at all, so the
        payload question can only be asked of a vision-capable router.
        """
        router = LLMRouter(config={"has_vision": True})
        return router

    def _one_turn(self, router=None, image_data=None, text=LOCAL_CONTENT):
        router = router or self.llm
        # The person's own picture is admitted by the image gate before the
        # turn (the gate itself is test_image_ingress_gate); the model layer
        # refuses a picture with no admission, so the case admits its own.
        gate = ImageIngressGate(transcriber=lambda uri: "NO TEXT",
                                scanner=ScannerPolicy())
        if image_data:
            gate.admit(image_data, source="the case's own picture")
        with mock.patch.object(image_ingress, "GATE", gate), \
             mock.patch("urllib.request.urlopen", self.transport), \
             mock.patch.object(llm_module.glass, "emit",
                               lambda *_a, **_k: None):
            return router.chat(
                [Message(role=MessageRole.USER, content=text)],
                image_data=image_data)

    def _assert_the_ladder_was_spent(self):
        self.assertGreaterEqual(
            self.transport.attempts, 1,
            "the local server was never asked, so this turn never reached the "
            "escalation step and proves nothing")

    # ---- what the mechanism is shown

    def test_the_mechanism_is_shown_the_image_that_leaves(self):
        router = self._vision_router()
        adapter = _Recording("first-destination")
        registered, why = router.register_cloud_provider("first-destination",
                                                         adapter)
        self.assertTrue(registered, why)
        consent = _Consent(True)
        router.set_escalation_consent(consent)

        self._one_turn(router=router, image_data=PRIVATE_IMAGE)

        self._assert_the_ladder_was_spent()
        self.assertEqual(len(consent.asked), 1,
                         "one destination is one question")
        self.assertEqual(
            consent.images_shown(), [PRIVATE_IMAGE],
            "the mechanism approved a payload with no image in it while the "
            "image left with the turn: it consented to something else")
        self.assertEqual(
            adapter.images_handed(), [PRIVATE_IMAGE],
            "the image must still reach the destination that was approved")

    def test_the_mechanism_that_refuses_keeps_the_image_on_this_machine(self):
        router = self._vision_router()
        adapter = _Recording("first-destination")
        registered, why = router.register_cloud_provider("first-destination",
                                                         adapter)
        self.assertTrue(registered, why)
        router.set_escalation_consent(_Consent(False))

        reply = self._one_turn(router=router, image_data=PRIVATE_IMAGE)

        self._assert_the_ladder_was_spent()
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(adapter.images_handed(), [])
        self.assertTrue(reply.local)

    # ---- how many destinations one answer covers

    def test_each_destination_is_asked_about_by_name(self):
        """A failover reaches a second destination; it is its own question."""
        failing = _Failing("first-destination")
        second = _Recording("second-destination")
        router = self._router_with(failing, second)
        consent = _Consent(True)
        router.set_escalation_consent(consent)

        self._one_turn(router=router)

        self._assert_the_ladder_was_spent()
        self.assertEqual(failing.calls, 1)
        self.assertEqual(second.calls, 1)
        self.assertEqual(
            consent.destinations_asked_about(),
            ["first-destination", "second-destination"],
            "one approval covered a destination nobody was asked about")

    def test_a_refusal_skips_that_destination_and_asks_about_the_next(self):
        first = _Recording("first-destination")
        second = _Recording("second-destination")
        router = self._router_with(first, second)
        consent = _Consent({"second-destination": True})
        router.set_escalation_consent(consent)

        reply = self._one_turn(router=router)

        self._assert_the_ladder_was_spent()
        self.assertEqual(first.calls, 0, "a refused destination is not tried")
        self.assertEqual(second.calls, 1)
        self.assertFalse(reply.local)
        self.assertEqual(reply.model, "cloud:second-destination")
        self.assertEqual(consent.destinations_asked_about(),
                         ["first-destination", "second-destination"])

    def test_every_destination_refused_serves_the_local_answer_and_says_so(self):
        first = _Recording("first-destination")
        second = _Recording("second-destination")
        router = self._router_with(first, second)
        router.set_escalation_consent(_Consent(False))

        with self.assertLogs("intergen.llm", level="WARNING") as logs:
            reply = self._one_turn(router=router)

        self._assert_the_ladder_was_spent()
        self.assertEqual((first.calls, second.calls), (0, 0))
        self.assertTrue(reply.local)
        said = " ".join(logs.output)
        self.assertIn("REFUSED", said)
        self.assertIn("first-destination", said)
        self.assertIn("second-destination", said)

    # ---- a turn that ends while the mechanism is asked

    def test_a_yes_that_arrives_after_the_turn_ended_sends_nothing(self):
        """The question can wait on a person, and the turn can end while it
        does (/clear, a new conversation, a page that goes away). The turn's
        stop is read before the question is put, so a yes that arrives after
        the turn ended must not be taken as leave to send: nothing reaches the
        destination, the stop is recorded before the send, and the turn gets
        an ended turn's answer."""
        stop = threading.Event()

        def a_yes_once_the_turn_has_ended(_messages, _destination):
            stop.set()
            return True

        self.llm.set_escalation_consent(a_yes_once_the_turn_has_ended)
        emitted: list[tuple] = []
        token = llm_module.bind_turn_stop(stop)
        try:
            with mock.patch("urllib.request.urlopen", self.transport), \
                 mock.patch.object(llm_module.glass, "emit",
                                   lambda *a, **k: emitted.append((a, k))):
                reply = self.llm.chat(
                    [Message(role=MessageRole.USER, content=LOCAL_CONTENT)])
        finally:
            llm_module.unbind_turn_stop(token)

        self._assert_the_ladder_was_spent()
        self.assertEqual(self.first.calls, 0,
                         "a yes given after the turn ended sent the turn")
        self.assertEqual(reply.text, "")
        stopped = [k.get("detail") for a, k in emitted
                   if a[:2] == ("model", "stopped")]
        self.assertEqual(stopped, [{"endpoint": "cloud:first-destination",
                                    "stage": "before_send",
                                    "reason": "turn_ended"}])

    # ---- the legs that must not change

    def test_consent_given_still_sends_to_the_first_destination(self):
        self.llm.set_escalation_consent(_Consent(True))
        reply = self._one_turn()
        self.assertEqual(self.first.calls, 1)
        self.assertFalse(reply.local)
        self.assertEqual(reply.model, "cloud:first-destination")

    def test_with_nobody_to_ask_nothing_is_sent_and_no_destination_is_asked(self):
        reply = self._one_turn()
        self._assert_the_ladder_was_spent()
        self.assertEqual(self.first.calls, 0)
        self.assertTrue(reply.local)

    def test_the_unprompted_mode_still_sends_with_no_mechanism_registered(self):
        self.llm.set_escalation_mode(EscalationMode.FALLBACK)
        reply = self._one_turn()
        self.assertEqual(self.first.calls, 1)
        self.assertFalse(reply.local)

    def test_the_mode_that_never_sends_still_sends_nothing(self):
        self.llm.set_escalation_mode(EscalationMode.NEVER)
        self.llm.set_escalation_consent(_Consent(True))
        reply = self._one_turn()
        self.assertEqual(self.first.calls, 0)
        self.assertTrue(reply.local)


if __name__ == "__main__":
    unittest.main()
