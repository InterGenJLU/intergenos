# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Every command the console sends reaches the server, or the pane says it did not.

``ConsoleClient.send`` raises ConnectionError when there is no connection, and a
send on a connection that is closing raises too. The console's slash commands
and its gate keys called it bare, inside the task the Enter key binding starts
and nobody awaits, so with no connection /new, /health, /governance, /metrics,
/status, /tier, /model with a tier and a gate key with a card pending were lost
without a word: the pane gained nothing, and the person could not tell a
command that went nowhere from one the server was still answering. A chat
message whose send failed on a closing connection was lost the same way, after
the pane had already shown it as sent. Escape had a second fault on the same
path: it emptied the pending card before the task that sends its deny ran, and
that task read the card back, so the deny was never sent, connected or not.

One table of every door that sends, and three situations for each:

* no connection: the pane names the command and says it was not sent, nothing
  reaches the wire, and nothing on the pane is cleared;
* a connection whose send fails: the pane says InterGen could not be reached
  and the command was not sent;
* a connection that works: the door's own frame reaches the wire once and the
  pane says nothing about a failure. This is the instrument's true positive,
  and for Escape it is the deny itself.

Hermetic by construction: the client is a recording stub that keeps
ConsoleClient's contract (``connected`` is the flag the shell reads; a send with
no connection raises) and talks to nothing; the model manifest the startup line
reads is stubbed to an empty machine. Typed input goes through
``_on_user_input``, the coroutine the Enter binding starts; keys go through the
handlers the console registers for them; server frames go through
``_process_server_message``, as the client's reader hands them over.
"""

from __future__ import annotations

import asyncio
import io
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

from prompt_toolkit.keys import Keys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# The conversation on the pane when the person acts: nothing below may clear it
# unless the server was reached.
QUESTION = "what is using my disk?"
ANSWER = "Your home directory holds most of it: 41 GB in ~/Videos."

GATE_PROMPT = {
    "type": "gate_prompt",
    "tool_call_id": "call-7",
    "tool_name": "run_command",
    "action": "list the largest files in the home directory",
    "blocked_by": "user_approval",
}

# A send on a connection that is closing, worded as the transport words it.
CLOSING = ConnectionResetError("Cannot write to closing transport")


class _RecordingClient:
    """Stands in for ConsoleClient. Records the frames it sends; talks to nothing.

    It keeps the real client's contract: ``connected`` is the flag the shell
    reads, a send without a connection raises as ConsoleClient.send does, and
    the convenience senders build the same frames ConsoleClient's do. ``fail``
    makes a connected send raise, as a send on a closing connection does.
    """

    def __init__(self, connected: bool = True,
                 fail: BaseException | None = None) -> None:
        self.frames: list[dict] = []
        self.connected = connected
        self._fail = fail

    async def send(self, data: dict) -> None:
        if not self.connected:
            raise ConnectionError("Not connected to InterGen web server")
        if self._fail is not None:
            raise self._fail
        self.frames.append(dict(data))

    async def send_message(self, content: str) -> None:
        await self.send({"type": "message", "content": content})

    async def send_slash_command(self, command: str) -> None:
        await self.send({"type": "slash_command", "command": command})

    async def send_gate_decision(self, tool_call_id: str,
                                 decision: str) -> None:
        await self.send({"type": "gate_decision",
                         "tool_call_id": tool_call_id,
                         "decision": decision})


def _shell(client: _RecordingClient):
    """A ConsoleShell wired to ``client``, holding one exchange on its pane.

    prompt_toolkit writes "Input is not a terminal" to stderr when an
    Application is built under pytest; it is captured so it cannot pollute a
    suite capture.
    """
    from intergen.console import shell as console_shell
    from intergen.model_manager import ModelManager

    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        sh = console_shell.ConsoleShell()
    sh._client = client
    sh._add_message("user", QUESTION)
    sh._add_message("assistant", ANSWER)
    return sh


def _rendered(sh) -> str:
    """The chat as the person would see it, colour codes stripped."""
    view = sh._render_chat()
    raw = getattr(view, "value", None) or str(view)
    return _ANSI_RE.sub("", raw)


async def _settle() -> list[BaseException]:
    """Let every task a key binding started run to its end; hand back what
    any of them raised (under the console, nobody would have seen it)."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    results = await asyncio.gather(*pending, return_exceptions=True)
    return [r for r in results if isinstance(r, BaseException)]


def _press(sh, key) -> None:
    """Press ``key`` through the binding the console registers for it."""
    bindings = sh._app.key_bindings.get_bindings_for_keys((key,))
    if not bindings:
        raise AssertionError(f"the console registers no binding for {key!r}")
    for binding in bindings:
        binding.handler(None)


def _typed(text: str):
    async def door(sh) -> None:
        await sh._on_user_input(text)
    return door


def _pressed(key):
    async def door(sh) -> None:
        _press(sh, key)
    return door


def _card_then(door):
    """A gate card arrives from the server, then the person acts on it."""
    async def carded(sh) -> None:
        await sh._process_server_message(dict(GATE_PROMPT))
        await door(sh)
    return carded


def _gate_frame(decision: str) -> dict:
    return {"type": "gate_decision", "tool_call_id": "call-7",
            "decision": decision}


# Every door that sends: (label, how the person uses it, the frame the server
# should receive, the words the pane must use when it was not sent).
DOORS = (
    ("/new", _typed("/new"),
     {"type": "new_session", "session_id": ""}, "/new was not sent"),
    ("/health", _typed("/health"),
     {"type": "request_health"}, "/health was not sent"),
    ("/governance", _typed("/governance"),
     {"type": "request_governance"}, "/governance was not sent"),
    ("/metrics", _typed("/metrics"),
     {"type": "request_metrics"}, "/metrics was not sent"),
    ("/status", _typed("/status"),
     {"type": "slash_command", "command": "/status"}, "/status was not sent"),
    ("/tier", _typed("/tier"),
     {"type": "slash_command", "command": "/tier"}, "/tier was not sent"),
    ("/model small", _typed("/model small"),
     {"type": "switch_model", "tier": "small"}, "/model small was not sent"),
    ("/clear", _typed("/clear"),
     {"type": "slash_command", "command": "/clear"}, "nothing was cleared"),
    ("Ctrl+L", _pressed(Keys.ControlL),
     {"type": "slash_command", "command": "/clear"}, "nothing was cleared"),
    ("gate key a", _card_then(_typed("a")),
     _gate_frame("allow"), "the gate decision (allow) was not sent"),
    ("gate key y", _card_then(_typed("y")),
     _gate_frame("allow_conversation"),
     "the gate decision (allow conversation) was not sent"),
    ("gate key n", _card_then(_typed("n")),
     _gate_frame("deny"), "the gate decision (deny) was not sent"),
    ("Escape", _card_then(_pressed(Keys.Escape)),
     _gate_frame("deny"), "the gate decision (deny) was not sent"),
    ("a chat message", _typed("hello"),
     {"type": "message", "content": "hello"}, "your message was not sent"),
)

# A chat message typed with no connection already said so before this change,
# in its own words, which other cases pin; it keeps them.
CHAT_NOT_CONNECTED = "Not connected to InterGen. Is the daemon running?"

# What the pane says on a send that did reach the server: none of it may be
# said when the send did not.
CLAIMS_OF_SENDING = ("Model tier requested", "Gate decision sent",
                     "New conversation", "Chat cleared")


def _use(door, client: _RecordingClient):
    """Use ``door`` at a fresh console wired to ``client``.

    Returns the shell, the pane lines it gained, and what escaped: the door's
    own coroutine (the task the Enter binding starts) or any task a key
    binding started.
    """
    sh = _shell(client)
    before = len(sh._messages)

    async def run() -> list[BaseException]:
        escaped: list[BaseException] = []
        try:
            await door(sh)
        except Exception as exc:  # noqa: BLE001 -- recorded, then asserted on
            escaped.append(exc)
        escaped.extend(await _settle())
        return escaped

    escaped = asyncio.run(run())
    gained = [m.get("content", "") for m in sh._messages[before:]
              if m.get("role") == "system"]
    return sh, gained, escaped


def _escaped_text(escaped: list[BaseException]) -> str:
    return "; ".join(f"{type(e).__name__}: {e}" for e in escaped)


class EveryDoorSaysWhatItDidNotSend(unittest.TestCase):
    """intergen/console/shell.py — every slash command that sends, both gate
    paths (a typed key and Escape), Ctrl+L and a chat message."""

    def test_with_no_connection_the_pane_says_each_command_was_not_sent(self):
        for label, door, _frame, unsent in DOORS:
            with self.subTest(door=label):
                sh, gained, escaped = _use(door, _RecordingClient(connected=False))
                said = "\n".join(gained)
                self.assertEqual(
                    escaped, [],
                    f"{label}: with no connection the command raised "
                    f"({_escaped_text(escaped)}) inside a task nobody awaits, "
                    f"and the pane gained {gained!r}")
                self.assertEqual(sh._client.frames, [])
                expected = (CHAT_NOT_CONNECTED if label == "a chat message"
                            else f"Not connected to InterGen: {unsent}.")
                self.assertIn(
                    expected, gained,
                    f"{label}: with no connection the pane did not say the "
                    f"command was not sent; it gained {gained!r}. "
                    f"Pane:\n{_rendered(sh)}")
                for claim in CLAIMS_OF_SENDING:
                    self.assertNotIn(claim, said)
                self.assertIn(QUESTION, _rendered(sh),
                              f"{label}: the pane was cleared by a command "
                              "that never reached the server")

    def test_a_send_that_fails_says_it_did_not_reach_the_server(self):
        for label, door, _frame, unsent in DOORS:
            with self.subTest(door=label):
                sh, gained, escaped = _use(
                    door, _RecordingClient(connected=True, fail=CLOSING))
                said = "\n".join(gained)
                self.assertEqual(
                    escaped, [],
                    f"{label}: a send on a closing connection raised "
                    f"({_escaped_text(escaped)}) inside a task nobody awaits, "
                    f"and the pane gained {gained!r}")
                self.assertEqual(sh._client.frames, [])
                self.assertIn(
                    f"Could not reach InterGen: {unsent} ({CLOSING}).", gained,
                    f"{label}: a send that failed was not reported; the pane "
                    f"gained {gained!r}. Pane:\n{_rendered(sh)}")
                for claim in CLAIMS_OF_SENDING:
                    self.assertNotIn(claim, said)
                self.assertIn(QUESTION, _rendered(sh))

    def test_control_with_a_connection_each_door_sends_its_frame(self):
        """The instrument's true positive: each door's own frame reaches the
        wire exactly once, and the pane reports no failure."""
        for label, door, frame, _unsent in DOORS:
            with self.subTest(door=label):
                sh, gained, escaped = _use(door, _RecordingClient(connected=True))
                self.assertEqual(escaped, [], _escaped_text(escaped))
                self.assertEqual(
                    sh._client.frames, [frame],
                    f"{label}: with a working connection the server received "
                    f"{sh._client.frames!r}, not its frame {frame!r}")
                for line in gained:
                    self.assertNotIn("not sent", line)
                    self.assertNotIn("Not connected", line)
                    self.assertNotIn("Could not reach", line)


if __name__ == "__main__":
    unittest.main()
