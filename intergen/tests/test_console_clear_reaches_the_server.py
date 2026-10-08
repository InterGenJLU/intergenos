# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The console's /clear reaches the server, and the pane follows the server.

The model's buffer and the offers staged in it live on the server. The server's
/clear handler empties the buffer, withdraws the staged offers and answers
``buffer_cleared`` (intergen/web_server.py, ``_handle_slash_command``). The
console's /clear branch never sent the command: it emptied its own pane and
printed "Chat cleared.", so the offer the person had just been shown ("…Want me
to look it up?") was gone from the screen and still live on the server, where a
bare "yes" typed next took it up.

These cases pin the console's half of the boundary:

* /clear sends the /clear command to the server;
* the pane is emptied when the server says the buffer is (``buffer_cleared``),
  not before, so it never shows a clear the server did not make;
* with no connection, the console says so and clears nothing.

A control runs beside them so the instrument is shown to read a true positive:
a slash command the console already forwards (/status) is seen on the wire.

Hermetic by construction: the client is a recording stub that talks to nothing,
no socket is opened, and the model manifest lookup the console's startup line
performs is stubbed to an empty machine. The input path is the one the
console's Enter key binding calls (``_on_user_input``); server frames go through
``_process_server_message``, as the client's reader hands them over.
"""

from __future__ import annotations

import asyncio
import io
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# The turn the person saw before typing /clear: their question and the
# offer the server staged with its answer.
QUESTION = "what is the bitcoin price right now?"
OFFER = ("That's live data I can't know from memory — but I can search the "
         "web for it right now. Want me to look it up?")


class _RecordingClient:
    """Stands in for ConsoleClient. Records frames; talks to nothing.

    It keeps the real client's contract: ``connected`` is the flag the shell
    reads, and a send without a connection raises, as ConsoleClient.send does.
    """

    def __init__(self, connected: bool = True) -> None:
        self.frames: list[dict] = []
        self.connected = connected

    async def send(self, data: dict) -> None:
        if not self.connected:
            raise ConnectionError("Not connected to InterGen web server")
        self.frames.append(dict(data))

    async def send_message(self, content: str) -> None:
        await self.send({"type": "message", "content": content})

    async def send_slash_command(self, command: str) -> None:
        await self.send({"type": "slash_command", "command": command})

    def slash_commands(self) -> list[str]:
        return [f.get("command", "") for f in self.frames
                if f.get("type") == "slash_command"]


def _shell(connected: bool = True):
    """A ConsoleShell wired to a recording stub client, holding one turn.

    ``list_downloaded`` is stubbed to an empty machine so the startup
    attribution line reads no manifest off the host. prompt_toolkit writes
    "Input is not a terminal" to stderr when an Application is built under
    pytest; it is captured so it cannot pollute a suite capture.
    """
    from intergen.console import shell as console_shell
    from intergen.model_manager import ModelManager

    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
            redirect_stderr(io.StringIO()):
        sh = console_shell.ConsoleShell()
    sh._client = _RecordingClient(connected=connected)
    sh._add_message("user", QUESTION)
    sh._add_message("assistant", OFFER, "current_data_offer")
    return sh


def _rendered(sh) -> str:
    """The chat as the person would see it: the ANSI bridge unwrapped and the
    colour codes stripped, so a failure prints the pane a person would read."""
    view = sh._render_chat()
    raw = getattr(view, "value", None) or str(view)
    return _ANSI_RE.sub("", raw)


def _conversation(sh) -> list[str]:
    return [m.get("content", "") for m in sh._messages
            if m.get("role") in ("user", "assistant")]


def _system(sh) -> str:
    return "\n".join(m.get("content", "") for m in sh._messages
                     if m.get("role") == "system")


class TheConsolesClearReachesTheServer(unittest.TestCase):
    """intergen/console/shell.py — the /clear branch."""

    def test_clear_sends_the_command_to_the_server(self):
        sh = _shell()
        asyncio.run(sh._on_user_input("/clear"))
        self.assertIn(
            "/clear", sh._client.slash_commands(),
            "/clear emptied the console's pane and sent nothing: the server's "
            "buffer and the offer staged in it are untouched, so a bare 'yes' "
            f"typed next still takes the offer up. Sent: {sh._client.frames!r}")

    def test_the_pane_is_emptied_when_the_server_says_the_buffer_is(self):
        sh = _shell()

        async def _run() -> None:
            await sh._on_user_input("/clear")
            # Before the server answers, the pane still shows what the server
            # still holds: the offer is live there until buffer_cleared.
            self.assertIn(
                OFFER, _conversation(sh),
                "the pane was emptied before the server said the buffer was: "
                "the person is shown a clear the server has not made, while "
                f"the offer is still live there. Pane:\n{_rendered(sh)}")
            await sh._process_server_message({"type": "buffer_cleared"})

        asyncio.run(_run())

        self.assertEqual(
            _conversation(sh), [],
            "the server said the buffer was cleared and the pane still shows "
            f"the conversation. Pane:\n{_rendered(sh)}")
        self.assertNotIn(OFFER, _rendered(sh))
        self.assertIn("Chat cleared", _system(sh),
                      "the pane emptied without saying why")

    def test_clear_without_a_connection_says_so_and_clears_nothing(self):
        sh = _shell(connected=False)
        asyncio.run(sh._on_user_input("/clear"))

        self.assertEqual(sh._client.frames, [])
        self.assertIn(
            OFFER, _conversation(sh),
            "with no connection the pane was emptied anyway: it shows a clear "
            f"nothing made. Pane:\n{_rendered(sh)}")
        self.assertIn(
            "Not connected", _system(sh),
            "with no connection /clear said nothing about it. "
            f"Pane:\n{_rendered(sh)}")
        self.assertNotIn("Chat cleared", _system(sh))

    def test_control_a_slash_command_the_console_forwards_is_seen(self):
        """The instrument's true positive: /status, which the console already
        forwards, is recorded on the stub's wire."""
        sh = _shell()
        asyncio.run(sh._on_user_input("/status"))
        self.assertIn("/status", sh._client.slash_commands())


if __name__ == "__main__":
    unittest.main()
