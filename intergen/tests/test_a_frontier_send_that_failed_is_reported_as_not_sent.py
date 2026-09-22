# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A frontier send that failed is reported as not sent, on both surfaces.

The escalation manager answers every failure with an ordinary response rather
than an exception, so that a failed escalation can never crash the assistant.
Both of its consumers inferred a send from the absence of an exception. In a
second reading on 2026-09-22, with the provider unreachable and with anything
else raised during the send — the two failures a real machine meets — the
daemon answered "sent", the frontier question command exited 0 with the
failure sentence printed as its answer, and the web chat's frontier button
answered "sent", named the provider beside the failure, and stored the failure
sentence in the conversation as though the assistant had said it.

The manager now returns every failure it catches typed as not sent, and both
consumers read that type. These cases build the manager with its own
constructor, from the same two configuration sections a machine carries, and
replace only the provider adapter, the consent prompt and the transports. Each
failing case has a control beside it that answers, and stays "sent".
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli
from intergen.cloud.http_adapter import CloudAdapterError
from intergen.dbus_daemon import InterGenDaemon
from intergen.escalation import EscalationManager
from intergen.interfaces import types as interface_types
from intergen.interfaces.scanner import ScanDisposition
from intergen.interfaces.types import LLMResponse, Message, MessageRole
from intergen.web_server import WebServer

# Read by name so that a tree without the type fails each case below on its
# own, instead of failing to import this file at all.
_NOT_SENT = getattr(interface_types, "EscalationNotSent", None)

_ESCALATION_CFG = {"mode": "ask", "primary_provider": "example"}
_PROVIDERS_CFG = [{
    "name": "example", "adapter": "openai-compatible", "model": "example-large",
    "api_key_keyring_id": "intergen/example",
    "base_url": "https://frontier.example.invalid/v1",
}]
_ANSWER = "The root filesystem is encrypted."

# The two failures a real machine meets, as the manager's two catch sites
# see them: (label, the exception the adapter raises, text the reply carries).
_FAILURES = [
    ("the provider unreachable",
     lambda: CloudAdapterError("connection refused"),
     "Frontier model unreachable"),
    ("anything else raised during the send",
     lambda: RuntimeError("socket closed"),
     "Escalation failed: RuntimeError"),
]


class _Adapter:
    """The provider adapter, replaced: it raises ``failure`` or answers."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self._failure = failure
        self.sent: list = []

    def send(self, messages, *, tools=None, max_tokens=0, temperature=0.0):
        self.sent.append(messages)
        if self._failure is not None:
            raise self._failure
        return LLMResponse(text=_ANSWER, model="example-large", local=False)


class _Scanner:
    """The egress scan, replaced: every derived send gets ``disposition``."""

    def __init__(self, disposition) -> None:
        self._disposition = disposition

    def scan(self, content, ctx):
        return mock.Mock(disposition=self._disposition, reason="test verdict")


def _manager(adapter: _Adapter, providers=_PROVIDERS_CFG, scanner=None):
    return EscalationManager.from_config(
        _ESCALATION_CFG, providers, scanner=scanner,
        adapter_factory=lambda cfg: adapter)


def _question() -> list:
    return [Message(role=MessageRole.USER, content="is my disk encrypted?")]


def _is_not_sent(response) -> bool:
    return _NOT_SENT is not None and isinstance(response, _NOT_SENT)


def _daemon_reply(adapter: _Adapter) -> str:
    """The daemon's own reply, from its own method, with the manager built by
    its own constructor. Only the consent prompt is answered for it."""
    daemon = InterGenDaemon.__new__(InterGenDaemon)   # its constructor starts the service
    daemon._escalation = _manager(adapter)
    with mock.patch("intergen.consent_modal.prompt_send_consent",
                    return_value=True):
        return daemon.escalate("is my disk encrypted?")


class TheManagerTypesEveryFailureItCatches(unittest.TestCase):

    def test_each_failure_it_catches_comes_back_typed_not_sent(self) -> None:
        cases = [
            ("no provider", _manager(_Adapter(), providers=[]), True),
            ("the outbound content refused by the egress scan",
             _manager(_Adapter(), scanner=_Scanner(ScanDisposition.BLOCK)), False),
            ("the outbound content held by the egress scan",
             _manager(_Adapter(), scanner=_Scanner(ScanDisposition.FLAG)), False),
        ] + [
            (label, _manager(_Adapter(failure())), True)
            for label, failure, _text in _FAILURES
        ]
        for label, manager, consented in cases:
            with self.subTest(label):
                response = manager.escalate(_question(),
                                            user_consented=consented)
                self.assertTrue(
                    _is_not_sent(response),
                    f"{label}: came back as {type(response).__name__}, which "
                    "no consumer can tell from an answer")

    def test_an_answer_is_not_typed_not_sent(self) -> None:
        response = _manager(_Adapter()).escalate(_question(),
                                                 user_consented=True)
        self.assertEqual(response.text, _ANSWER)
        self.assertFalse(_is_not_sent(response))


class TheDaemonSaysNotSent(unittest.TestCase):

    def test_a_failed_send_is_not_sent(self) -> None:
        for label, failure, text in _FAILURES:
            with self.subTest(label):
                reply = json.loads(_daemon_reply(_Adapter(failure())))
                self.assertIs(reply["sent"], False,
                              f"{label}: nothing reached the frontier model")
                self.assertNotEqual(reply["source"], "frontier:example",
                                    "a failure is not the provider's answer")
                self.assertIn(text, reply["response"])

    def test_an_answer_is_sent(self) -> None:
        reply = json.loads(_daemon_reply(_Adapter()))
        self.assertIs(reply["sent"], True)
        self.assertEqual(reply["source"], "frontier:example")
        self.assertEqual(reply["response"], _ANSWER)


class TheQuestionCommandExitsNonZero(unittest.TestCase):
    """The real command, handed the daemon's real reply over a replaced bus."""

    @staticmethod
    def _ask(reply: str):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
                    mock.patch.object(cli, "try_dbus", return_value=reply), \
                    mock.patch.object(cli, "_last_answer_path",
                                      return_value=Path(tmp) / "last.json"):
                with redirect_stdout(out), redirect_stderr(err):
                    try:
                        cli.cmd_ask_frontier("is my disk encrypted?")
                    except SystemExit as exc:
                        code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_a_failed_send_exits_non_zero(self) -> None:
        for label, failure, text in _FAILURES:
            with self.subTest(label):
                code, shown, said = self._ask(_daemon_reply(_Adapter(failure())))
                self.assertEqual(code, 2,
                                 f"{label}: a send that failed is not an answer")
                self.assertIn(text, shown,
                              "the reply's own sentence still reaches the person")
                self.assertIn("did not answer", said)

    def test_an_answer_exits_zero(self) -> None:
        code, shown, said = self._ask(_daemon_reply(_Adapter()))
        self.assertIsNone(code)
        self.assertIn(_ANSWER, shown)
        self.assertNotIn("did not answer", said)


class _Socket:
    def __init__(self) -> None:
        self.frames: list = []

    async def send_json(self, frame) -> None:
        self.frames.append(frame)


class _Connection:
    def __init__(self) -> None:
        self.ws = _Socket()
        self.session_history: list = []


class TheWebChatSaysNotSent(unittest.IsolatedAsyncioTestCase):
    """The web chat's frontier button, through the real handler."""

    @staticmethod
    async def _press(adapter: _Adapter) -> _Connection:
        server = WebServer.__new__(WebServer)   # its constructor opens the listener
        server._router = mock.Mock()
        server._router._escalation = _manager(adapter)
        connection = _Connection()
        with mock.patch("intergen.consent_modal.prompt_send_consent",
                        return_value=True):
            await server._handle_frontier_escalate(
                connection, {"content": "is my disk encrypted?"})
        return connection

    async def test_a_failed_send_is_not_sent_and_not_kept(self) -> None:
        for label, failure, text in _FAILURES:
            with self.subTest(label):
                connection = await self._press(_Adapter(failure()))
                frame = connection.ws.frames[-1]
                self.assertIs(frame["sent"], False,
                              f"{label}: nothing reached the frontier model")
                self.assertIsNone(frame["provider"],
                                  "no provider is named beside a failure")
                self.assertIn(text, frame["content"])
                self.assertEqual(connection.session_history, [],
                                 "a failure notice is not stored as the "
                                 "assistant's own words")

    async def test_an_answer_is_sent_and_kept(self) -> None:
        connection = await self._press(_Adapter())
        frame = connection.ws.frames[-1]
        self.assertIs(frame["sent"], True)
        self.assertEqual(frame["provider"], "example")
        self.assertEqual([m.content for m in connection.session_history],
                         [_ANSWER])


if __name__ == "__main__":
    unittest.main()
