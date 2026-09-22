# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A phone-a-friend that could not be set up is not reported as one with no provider.

The assistant builds its escalation step once, at start, from the escalation
and providers sections of its configuration; when building it raises (a
section of the wrong shape does), the daemon logs the failure and runs on with
no step at all. Its own frontier method then answers "Phone-a-friend is not
available (no escalation manager).", and ``intergen ask-frontier`` prints that.
The web chat's frontier button tested "no step, or no provider" together and
answered both with "No frontier model is configured. Add a provider to
~/.config/intergen/ ...", a cause nobody had established: a provider may be
configured, and a person told to add one is sent to fix the wrong thing.
Measured in a second reading on 2026-09-22 through the real web handler with
no step. The web chat now says what the command line says; the
configure-a-provider sentence stays for the case it describes, a step with no
provider in it.

Replaced in these cases, and nothing else: the router the handler reads the
step from, and the web socket. The daemon's sentence is taken from the
daemon's own method, so the two surfaces are compared with each other rather
than with a copy of either. Neither path reaches the consent step, and nothing
is sent anywhere.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon
from intergen.escalation import EscalationManager
from intergen.web_server import WebServer

_QUESTION = "is my disk encrypted?"
_NOT_CONFIGURED = "No frontier model is configured"


class _Socket:
    def __init__(self) -> None:
        self.frames: list = []

    async def send_json(self, frame) -> None:
        self.frames.append(frame)


class _Connection:
    def __init__(self) -> None:
        self.ws = _Socket()
        self.session_history: list = []


async def _press(step) -> _Connection:
    """The web chat's frontier button, through the real handler, with the
    router holding ``step`` as its escalation step (None: none was built)."""
    server = WebServer.__new__(WebServer)   # its constructor opens the listener
    server._router = mock.Mock()
    server._router._escalation = step
    connection = _Connection()
    await server._handle_frontier_escalate(connection, {"content": _QUESTION})
    return connection


def _daemon_says(step) -> dict:
    """The daemon's own frontier method, with ``step`` as its escalation step."""
    daemon = InterGenDaemon.__new__(InterGenDaemon)   # its constructor starts the service
    daemon._escalation = step
    return json.loads(daemon.escalate(_QUESTION))


class TheWebChatSaysWhatTheCommandLineSays(unittest.IsolatedAsyncioTestCase):

    async def test_no_step_is_not_reported_as_no_provider(self) -> None:
        connection = await _press(None)
        frame = connection.ws.frames[-1]
        daemon = _daemon_says(None)
        self.assertIs(frame["sent"], False)
        self.assertIs(daemon["sent"], False)
        self.assertNotIn(_NOT_CONFIGURED, frame["content"],
                         "nobody established that no provider is configured")
        self.assertEqual(frame["content"], daemon["response"],
                         "the page is told what the command line is told")
        self.assertEqual(connection.session_history, [])

    async def test_a_step_with_no_provider_still_says_so(self) -> None:
        step = EscalationManager.from_config({"mode": "ask"}, [], scanner=None)
        connection = await _press(step)
        frame = connection.ws.frames[-1]
        self.assertIs(frame["sent"], False)
        self.assertIn(_NOT_CONFIGURED, frame["content"])
        self.assertIn(_NOT_CONFIGURED, _daemon_says(step)["response"])
        self.assertEqual(connection.session_history, [])


if __name__ == "__main__":
    unittest.main()
