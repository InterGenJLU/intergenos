# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two frontier replies that said something other than what happened.

A SEND NOBODY WAS ASKED ABOUT. The consent step shows the person the exact
content and waits for Send. When it cannot show anything — no unlocked desktop
session, or no dialog could open — it notifies and refuses, which is right, but
it returned the same False a person's Cancel returns, and both callers turned
that into "Cancelled — nothing was sent to the frontier model." Measured in a
second reading on 2026-09-22 with the real consent step and no active session:
the command line and the web chat both told the person they had cancelled,
although nobody was asked. The consent step now records which of the two it
was, and both callers say it.

AN EMPTY ANSWER. A provider that answers with no text: the command line treats
that as no answer (exit 2, nothing kept for ``intergen last``), but the web
chat marked it sent, named the provider, and appended an empty assistant turn
to the conversation the next turn is built from. The web chat now does what the
command line does: the page is told the model returned nothing, and nothing is
kept.

Replaced in these cases, and nothing else: the session check, the two dialogs,
the desktop notification and the dialog program's process call (so nothing can
appear on the screen of the machine running them), the provider adapter, the
bus transport of the command, and the web socket. The escalation manager is
built by its own constructor. The outcome values are written as the strings
they are, so that a tree without them fails each case on its own instead of
failing to import this file.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli, consent_modal
from intergen.dbus_daemon import InterGenDaemon
from intergen.escalation import EscalationManager
from intergen.interfaces.types import LLMResponse
from intergen.web_server import WebServer

SEND, DECLINED, NOT_SHOWN = "send", "declined", "not-shown"
_NOBODY_ASKED = "no way to show you the content for review first"
_CANCELLED = "Cancelled — nothing was sent to the frontier model."

_ESCALATION_CFG = {"mode": "ask", "primary_provider": "example"}
_PROVIDERS_CFG = [{
    "name": "example", "adapter": "openai-compatible", "model": "example-large",
    "api_key_keyring_id": "intergen/example",
    "base_url": "https://frontier.example.invalid/v1",
}]
_QUESTION = "is my disk encrypted?"
_ANSWER = "The root filesystem is encrypted."

_APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"


class _Adapter:
    """The provider adapter, replaced: it answers with ``text``."""

    def __init__(self, text: str = _ANSWER) -> None:
        self._text = text
        self.sent: list = []

    def send(self, messages, *, tools=None, max_tokens=0, temperature=0.0):
        self.sent.append(messages)
        return LLMResponse(text=self._text, model="example-large", local=False)


def _manager(adapter: _Adapter):
    return EscalationManager.from_config(
        _ESCALATION_CFG, _PROVIDERS_CFG, scanner=None,
        adapter_factory=lambda cfg: adapter)


class _NoScreen:
    """The real consent step with no unlocked desktop session. The fallback's
    desktop notification is recorded instead of sent."""

    def __enter__(self):
        self._patches = [
            mock.patch.object(consent_modal, "_session_active", return_value=False),
            mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                              return_value=False),
        ]
        mocks = [p.start() for p in self._patches]
        self.notified = mocks[1]
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _dialogs(branded, zenity_path, zenity_run):
    """A session that is active, with the branded dialog answering ``branded``,
    zenity found at ``zenity_path`` (None: not installed), and zenity's process
    call replaced by ``zenity_run`` (a return value, or an exception to raise).
    The fallback's notification is recorded instead of sent."""
    run = (mock.Mock(side_effect=zenity_run) if isinstance(zenity_run, BaseException)
           else mock.Mock(return_value=zenity_run))
    return [
        mock.patch.object(consent_modal, "_session_active", return_value=True),
        mock.patch.object(consent_modal.consent_dialog, "run_consent_dialog",
                          return_value=branded),
        mock.patch.object(consent_modal.shutil, "which", return_value=zenity_path),
        mock.patch.object(consent_modal.subprocess, "run", run),
        mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                          return_value=False),
    ]


def _ask_with(patches) -> tuple[bool, list, mock.Mock]:
    outcome: list = []
    started = [p.start() for p in patches]
    try:
        allowed = consent_modal.prompt_send_consent(
            _QUESTION, "example", "you asked", outcome=outcome)
    finally:
        for p in reversed(patches):
            p.stop()
    return allowed, outcome, started[-1]


class TheConsentStepSaysWhyNothingWasSent(unittest.TestCase):

    def test_no_unlocked_session_is_recorded_as_not_shown(self) -> None:
        outcome: list = []
        with _NoScreen() as screen:
            allowed = consent_modal.prompt_send_consent(
                _QUESTION, "example", "you asked", outcome=outcome)
        self.assertIs(allowed, False, "nothing is sent without a Send")
        self.assertEqual(outcome, [NOT_SHOWN],
                         "nobody was asked, and the record must say so")
        screen.notified.assert_called_once_with("example")

    def test_neither_dialog_could_open_is_recorded_as_not_shown(self) -> None:
        allowed, outcome, notified = _ask_with(_dialogs(None, None, None))
        self.assertIs(allowed, False)
        self.assertEqual(outcome, [NOT_SHOWN])
        notified.assert_called_once_with("example")

    def test_a_dialog_program_that_cannot_start_is_recorded_as_not_shown(self) -> None:
        allowed, outcome, notified = _ask_with(
            _dialogs(None, "/usr/bin/zenity", OSError("exec failed")))
        self.assertIs(allowed, False)
        self.assertEqual(outcome, [NOT_SHOWN],
                         "a dialog that never started asked nobody")
        notified.assert_called_once_with("example")

    def test_the_persons_cancel_is_recorded_as_declined(self) -> None:
        allowed, outcome, notified = _ask_with(_dialogs(False, None, None))
        self.assertIs(allowed, False)
        self.assertEqual(outcome, [DECLINED])
        notified.assert_not_called()

    def test_the_persons_cancel_in_zenity_is_recorded_as_declined(self) -> None:
        allowed, outcome, notified = _ask_with(
            _dialogs(None, "/usr/bin/zenity", mock.Mock(returncode=1)))
        self.assertIs(allowed, False)
        self.assertEqual(outcome, [DECLINED])
        notified.assert_not_called()

    def test_the_persons_send_is_recorded_as_send(self) -> None:
        allowed, outcome, notified = _ask_with(_dialogs(True, None, None))
        self.assertIs(allowed, True)
        self.assertEqual(outcome, [SEND])
        notified.assert_not_called()

    def test_the_unattended_evaluation_refusal_is_recorded_as_declined(self) -> None:
        outcome: list = []
        with mock.patch.object(consent_modal.eval_consent, "is_armed",
                               return_value=True), \
                mock.patch.object(consent_modal.eval_consent, "send_verdict",
                                  return_value=False):
            allowed = consent_modal.prompt_send_consent(
                _QUESTION, "example", "you asked", outcome=outcome)
        self.assertIs(allowed, False)
        self.assertEqual(outcome, [DECLINED])

    def test_a_caller_that_asks_for_no_reason_still_gets_its_answer(self) -> None:
        with _NoScreen():
            self.assertIs(consent_modal.prompt_send_consent(_QUESTION, "example"),
                          False)


def _daemon_reply(adapter: _Adapter) -> dict:
    """The daemon's own reply from its own method, the real consent step run
    with no unlocked session."""
    daemon = InterGenDaemon.__new__(InterGenDaemon)   # its constructor starts the service
    daemon._escalation = _manager(adapter)
    with _NoScreen():
        return json.loads(daemon.escalate(_QUESTION))


def _ask_command(reply: dict):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with TemporaryDirectory() as tmp:
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
                mock.patch.object(cli, "try_dbus", return_value=json.dumps(reply)), \
                mock.patch.object(cli, "_last_answer_path",
                                  return_value=Path(tmp) / "last.json"):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_ask_frontier(_QUESTION)
                except SystemExit as exc:
                    code = exc.code
    return code, out.getvalue(), err.getvalue()


class TheCommandLineSaysNobodyWasAsked(unittest.TestCase):

    def test_the_daemon_reply_says_nobody_was_asked(self) -> None:
        adapter = _Adapter()
        reply = _daemon_reply(adapter)
        self.assertIs(reply["sent"], False)
        self.assertIn(_NOBODY_ASKED, reply["response"])
        self.assertNotIn("Cancelled", reply["response"],
                         "the person did not cancel: nobody asked them")
        self.assertEqual(adapter.sent, [], "nothing reached the provider")

    def test_the_command_prints_it_and_exits_non_zero(self) -> None:
        code, shown, said = _ask_command(_daemon_reply(_Adapter()))
        self.assertEqual(code, 2)
        self.assertIn(_NOBODY_ASKED, shown)
        self.assertNotIn("Cancelled", shown)
        self.assertIn("did not answer", said)

    def test_the_persons_cancel_still_says_cancelled(self) -> None:
        daemon = InterGenDaemon.__new__(InterGenDaemon)
        daemon._escalation = _manager(_Adapter())
        patches = _dialogs(False, None, None)
        for p in patches:
            p.start()
        try:
            reply = json.loads(daemon.escalate(_QUESTION))
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIs(reply["sent"], False)
        self.assertEqual(reply["response"], _CANCELLED)


class _Socket:
    def __init__(self) -> None:
        self.frames: list = []

    async def send_json(self, frame) -> None:
        self.frames.append(frame)


class _Connection:
    def __init__(self) -> None:
        self.ws = _Socket()
        self.session_history: list = []


async def _press(adapter: _Adapter, consent) -> _Connection:
    """The web chat's frontier button, through the real handler. ``consent`` is
    a list of patches that stand for the consent step's surroundings."""
    server = WebServer.__new__(WebServer)   # its constructor opens the listener
    server._router = mock.Mock()
    server._router._escalation = _manager(adapter)
    connection = _Connection()
    for p in consent:
        p.start()
    try:
        await server._handle_frontier_escalate(connection, {"content": _QUESTION})
    finally:
        for p in reversed(consent):
            p.stop()
    return connection


def _no_screen_patches():
    return [
        mock.patch.object(consent_modal, "_session_active", return_value=False),
        mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                          return_value=False),
    ]


def _consented():
    return [mock.patch("intergen.consent_modal.prompt_send_consent",
                       return_value=True)]


class TheWebChatSaysNobodyWasAsked(unittest.IsolatedAsyncioTestCase):

    async def test_the_page_is_told_nobody_was_asked_and_nothing_is_kept(self) -> None:
        adapter = _Adapter()
        connection = await _press(adapter, _no_screen_patches())
        frame = connection.ws.frames[-1]
        self.assertIs(frame["sent"], False)
        self.assertIn(_NOBODY_ASKED, frame["content"])
        self.assertNotIn("Cancelled", frame["content"])
        self.assertIsNone(frame["provider"])
        self.assertEqual(connection.session_history, [])
        self.assertEqual(adapter.sent, [])

    async def test_the_persons_cancel_still_says_cancelled(self) -> None:
        connection = await _press(_Adapter(), _dialogs(False, None, None))
        frame = connection.ws.frames[-1]
        self.assertIs(frame["sent"], False)
        self.assertEqual(frame["content"], _CANCELLED)


class TheWebChatKeepsNoEmptyAnswer(unittest.IsolatedAsyncioTestCase):

    async def test_an_empty_answer_is_not_kept_and_the_page_is_told(self) -> None:
        for label, text in (("empty", ""), ("only white space", "  \n ")):
            with self.subTest(label):
                connection = await _press(_Adapter(text), _consented())
                frame = connection.ws.frames[-1]
                self.assertIs(frame["sent"], True,
                              "the content did reach the provider")
                self.assertIs(frame.get("answered"), False,
                              "an empty answer is not an answer")
                self.assertIn("returned no answer", frame["content"])
                self.assertEqual(frame["provider"], "example")
                self.assertEqual(connection.session_history, [],
                                 "no empty assistant turn goes into the "
                                 "conversation the next turn is built from")

    async def test_an_answer_is_sent_answered_and_kept(self) -> None:
        connection = await _press(_Adapter(), _consented())
        frame = connection.ws.frames[-1]
        self.assertIs(frame["sent"], True)
        self.assertIs(frame.get("answered"), True)
        self.assertEqual(frame["content"], _ANSWER)
        self.assertEqual([m.content for m in connection.session_history],
                         [_ANSWER])


def _page_function(name: str) -> str | None:
    """The full source of the page's ``function name(...) { ... }``, taken from
    the shipped app.js by brace matching, so the code that ships is what runs."""
    import re
    text = _APP_JS.read_text(encoding="utf-8")
    m = re.search(r"^\s*function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{",
                  text, re.MULTILINE)
    if not m:
        return None
    i = text.index("{", m.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
    return None


@unittest.skipUnless(shutil.which("node"), "node is required to run the page's "
                                           "own handler")
class ThePageShowsAnEmptyAnswerAsANotice(unittest.TestCase):
    """The page's frontier-reply handler, taken from the shipped app.js and run
    under node with the two things it calls recorded."""

    def _render(self, frames: list) -> list:
        source = _page_function("handleFrontierResponse")
        self.assertIsNotNone(source, "the page no longer declares the handler "
                                     "this case covers")
        script = (
            "const calls = [];\n"
            "function addMessage(role, content, src) { calls.push(['message', role, content, src]); }\n"
            "function showBanner(text, kind) { calls.push(['banner', text, kind]); }\n"
            + source + "\n"
            + "const frames = " + json.dumps(frames) + ";\n"
            "for (const f of frames) { handleFrontierResponse(f); }\n"
            "console.log(JSON.stringify(calls));\n")
        with tempfile.TemporaryDirectory(prefix="frontier-page-") as tmp:
            probe = Path(tmp) / "probe.js"
            probe.write_text(script, encoding="utf-8")
            run = subprocess.run(["node", str(probe)], capture_output=True,
                                 text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_an_empty_answer_is_a_notice_not_a_turn(self) -> None:
        calls = self._render([{"type": "frontier_response", "sent": True,
                               "answered": False, "provider": "example",
                               "content": "Your frontier model (example) "
                                          "returned no answer."}])
        self.assertEqual([c[0] for c in calls], ["banner"],
                         "nothing the model said is shown as its turn")
        self.assertIn("returned no answer", calls[0][1])

    def test_an_answer_is_a_turn(self) -> None:
        calls = self._render([{"type": "frontier_response", "sent": True,
                               "answered": True, "provider": "example",
                               "content": _ANSWER}])
        self.assertEqual(calls, [["message", "assistant", _ANSWER,
                                  "frontier:example"]])

    def test_a_send_that_did_not_happen_is_a_notice(self) -> None:
        calls = self._render([{"type": "frontier_response", "sent": False,
                               "answered": False, "provider": None,
                               "content": _CANCELLED}])
        self.assertEqual(calls, [["banner", _CANCELLED, "info"]])


if __name__ == "__main__":
    unittest.main()
