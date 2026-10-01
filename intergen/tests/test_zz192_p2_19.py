# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A missing web token is reported as a missing web token, not as a dead daemon.

`intergen console` opens a WebSocket to the local web server with the token
written by `intergen setup`. When that token is absent, empty or unreadable,
the client raises a ConnectionError that names both the cause and the remedy
(intergen/console/client.py:68-76):

    No web auth token found. Run 'intergen setup' first. Expected at <path>
    Web auth token is empty. Run 'intergen setup' first.

The console's connect path wraps the whole of that call in a bare
``except Exception`` and appends ONE sentence for every failure it can see
(intergen/console/shell.py:774-777):

    Failed to connect to InterGen. Is the daemon running?

So on a machine whose daemon is running normally and whose token is missing,
the console blames the one subsystem that is healthy, the diagnosis the client
took the trouble to write is discarded without even a log line, and the person
troubleshoots the wrong thing. Naming the wrong cause is worse than naming no
cause: it sends the reader somewhere else.

These cases pin the properties the quoted sentence promises:

* The diagnosis the client raises reaches the view. A handler may summarise a
  cause, but it may not replace one cause with a different one.
* A token failure does not assert something false about the daemon.
* The daemon question survives for the failure it was written for — a
  connection actually refused. A fix that deleted the sentence would trade one
  misdirection for another, so its correct case is pinned alongside.

Hermetic: no service, no socket, no daemon, no model server, no network, no
screen. The client's token check refuses before any aiohttp session is built,
and the console is driven with a stub client that never opens anything.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

# The two halves of the diagnosis: what went wrong, and what to do about it.
CAUSE = "token"
REMEDY = "intergen setup"

# The sentence the console appends today for every failure alike.
DAEMON_QUESTION = "Is the daemon running?"


def _quiet_shell():
    """Build a ConsoleShell with nothing of the machine in it.

    `list_downloaded` is stubbed so the seeded attribution line cannot vary
    with what this box happens to hold, and prompt_toolkit's "Input is not a
    terminal" notice is captured so it cannot pollute a suite capture — it
    says nothing about this behaviour either way (the same handling as
    intergen/tests/test_qwen_attribution_is_rendered.py).
    """
    from intergen.console import shell
    from intergen.model_manager import ModelManager
    with mock.patch.object(ModelManager, "list_downloaded", return_value=[]), \
         redirect_stderr(io.StringIO()):
        return shell.ConsoleShell()


def _last_system_message(sh) -> str:
    systems = [m for m in sh._messages if m.get("role") == "system"]
    if not systems:
        raise AssertionError(
            f"the console appended no system message at all: {sh._messages!r}")
    return systems[-1].get("content", "")


class _FailingClient:
    """Stands in for ConsoleClient, raising one chosen failure from connect().

    Nothing else happens: no ClientSession, no ws_connect, no socket.
    """

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.connected = False

    async def connect(self):
        raise self._error

    async def close(self) -> None:
        return None


class TheClientReallyWritesTheDiagnosisTests(unittest.TestCase):
    """The upstream half: the cause and the remedy exist before the console
    throws them away. A case built only on a stub could pass over a client
    that said nothing."""

    def _connect_error(self, token_file: Path) -> BaseException:
        from intergen.console import client as client_mod
        c = client_mod.ConsoleClient(source_interface="console")
        with mock.patch.object(client_mod, "TOKEN_PATH", token_file):
            with self.assertRaises(ConnectionError) as caught:
                asyncio.run(c.connect())
        return caught.exception

    def test_a_missing_token_file_raises_the_cause_and_the_remedy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            err = self._connect_error(Path(d) / "absent" / "web-token")
        self.assertIn(CAUSE, str(err).lower())
        self.assertIn(REMEDY, str(err).lower())

    def test_an_empty_token_file_raises_the_cause_and_the_remedy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "web-token"
            p.write_text("")
            err = self._connect_error(p)
        self.assertIn(CAUSE, str(err).lower())
        self.assertIn(REMEDY, str(err).lower())


class TheConsoleReportsTheCauseItWasGivenTests(unittest.TestCase):
    """The defect: intergen/console/shell.py:775."""

    def test_a_missing_web_token_is_reported_as_a_missing_web_token(self):
        sh = _quiet_shell()
        sh._client = _FailingClient(ConnectionError(
            "No web auth token found. Run 'intergen setup' first. "
            "Expected at /nonexistent/.config/intergen/web-token"))
        asyncio.run(sh._connect_and_read())
        shown = _last_system_message(sh)
        self.assertIn(
            CAUSE, shown.lower(),
            "the console never told the reader the token was the problem; "
            f"it said: {shown!r}")
        self.assertIn(
            REMEDY, shown.lower(),
            "the console never told the reader to run setup; "
            f"it said: {shown!r}")

    def test_a_token_failure_does_not_blame_a_running_daemon(self):
        sh = _quiet_shell()
        sh._client = _FailingClient(ConnectionError(
            "Web auth token is empty. Run 'intergen setup' first."))
        asyncio.run(sh._connect_and_read())
        shown = _last_system_message(sh)
        self.assertNotIn(
            DAEMON_QUESTION, shown,
            "a token failure was reported as a question about the daemon, "
            f"which is running: {shown!r}")

    def test_a_real_connection_refusal_still_names_the_daemon(self):
        """The sentence is right for the failure it was written for, and a fix
        must keep it there."""
        sh = _quiet_shell()
        sh._client = _FailingClient(
            ConnectionRefusedError(111, "Connection refused"))
        asyncio.run(sh._connect_and_read())
        shown = _last_system_message(sh)
        self.assertIn(
            "daemon", shown.lower(),
            "a refused connection stopped naming the daemon: "
            f"{shown!r}")


if __name__ == "__main__":
    unittest.main()
