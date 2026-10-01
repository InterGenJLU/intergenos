# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A new conversation at the console must not carry the previous one's document buffer.

`/new` exists to END a conversation. The server's own `_handle_new_session`
says so in as many words: "The old conversation ENDS. Emptying the pane is not
enough", and it resets the model buffer, the consent decisions, the ingress
watermark and the pending offers on the daemon side.

The terminal console keeps one piece of conversation content that `/new` does
not reach: `_doc_buffer`, loaded by `/paste`, `/done` or `/clipboard` and
prepended to the content of EVERY subsequent outbound message
(intergen/console/shell.py, `_on_user_input`). `/new` clears `_messages` and
sends the new_session frame; it leaves `_doc_buffer` loaded. The console has no
handler for the `session_created` frame the server answers with, so nothing on
the return path clears it either, and the visible "You" panel renders only the
typed text — so the carried content is not on screen anywhere.

These two cases pin the boundary:

* the first outbound message of a new conversation carries exactly what the
  person typed, and
* `/new` leaves the document buffer empty.

Hermetic by construction: the client is a recording stub that talks to nothing,
no socket is opened, no daemon is asked, and the model manifest lookup the
console's startup line performs is stubbed to an empty machine.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

# A stand-in for a private document loaded into the buffer. Distinctive so the
# assertion can look for it anywhere in the outbound frames.
PRIVATE_LINE = "PRIVATE-DOCUMENT-LINE-ALPHA"


class _RecordingClient:
    """Stands in for ConsoleClient. Records frames; talks to nothing.

    The two methods the shell calls: `send` for the slash-command frames and
    `send_message` for chat content. `connected` is the attribute
    `_on_user_input` gates on before it sends.
    """

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.connected = True

    async def send(self, data: dict) -> None:
        self.frames.append(dict(data))

    async def send_message(self, content: str) -> None:
        await self.send({"type": "message", "content": content})

    # -- readers -----------------------------------------------------------

    def messages_after_new_session(self) -> list[str]:
        """The content of every chat message sent after the last new_session."""
        start = 0
        for i, frame in enumerate(self.frames):
            if frame.get("type") == "new_session":
                start = i + 1
        return [f.get("content", "") for f in self.frames[start:]
                if f.get("type") == "message"]


def _build_shell():
    """A ConsoleShell wired to a recording stub client.

    `list_downloaded` is stubbed to an empty machine so the startup attribution
    line reads no manifest off the host. prompt_toolkit writes "Input is not a
    terminal" to stderr when an Application is built under pytest; it is
    captured so it cannot pollute a suite capture, and it says nothing about
    this behaviour either way.
    """
    from intergen.console import shell as console_shell
    from intergen.model_manager import ModelManager

    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        sh = console_shell.ConsoleShell()
    sh._client = _RecordingClient()
    return sh


async def _load_buffer_then_start_a_new_conversation(sh) -> None:
    """Drive the real input path: /paste, a line, /done, /new.

    Every step goes through `_on_user_input`, which is exactly what the console's
    `enter` key binding calls, so nothing here reaches the code by a private
    door the person at the terminal cannot open.
    """
    await sh._on_user_input("/paste")
    await sh._on_user_input(PRIVATE_LINE)
    await sh._on_user_input("/done")


class ANewConversationDropsTheDocumentBufferTests(unittest.TestCase):
    """intergen/console/shell.py — the /new conversation boundary."""

    def test_a_new_conversation_does_not_carry_the_document_buffer(self):
        sh = _build_shell()

        async def _run() -> None:
            await _load_buffer_then_start_a_new_conversation(sh)
            # Setup check, not the behaviour under test: the buffer really did
            # load, so a green result below cannot come from a no-op setup.
            self.assertIn(PRIVATE_LINE, sh._doc_buffer,
                          "setup failed: /paste + /done did not load the buffer")

            await sh._on_user_input("/new")
            await sh._on_user_input("hello")

        asyncio.run(_run())

        sent = sh._client.messages_after_new_session()
        self.assertEqual(
            sent, ["hello"],
            "the first message of the new conversation is not what the person "
            "typed: the previous conversation's document buffer is prepended "
            f"to it and sent to the assistant. Sent: {sent!r}")
        self.assertNotIn(
            PRIVATE_LINE, "\n".join(sent),
            "content loaded before /new crossed the conversation boundary")

    def test_new_leaves_the_document_buffer_empty(self):
        sh = _build_shell()

        async def _run() -> None:
            await _load_buffer_then_start_a_new_conversation(sh)
            await sh._on_user_input("/new")

        asyncio.run(_run())

        self.assertEqual(
            sh._doc_buffer, "",
            "/new left the previous conversation's document buffer loaded, so "
            "it is prefixed to every message of the new conversation with "
            "nothing on screen to say so")

    def test_the_new_session_frame_was_actually_sent(self):
        """Guards the two cases above: they would be vacuous if /new never
        reached the client at all."""
        sh = _build_shell()

        async def _run() -> None:
            await _load_buffer_then_start_a_new_conversation(sh)
            await sh._on_user_input("/new")

        asyncio.run(_run())

        self.assertTrue(
            any(f.get("type") == "new_session" for f in sh._client.frames),
            f"/new sent no new_session frame: {sh._client.frames!r}")


if __name__ == "__main__":
    unittest.main()
