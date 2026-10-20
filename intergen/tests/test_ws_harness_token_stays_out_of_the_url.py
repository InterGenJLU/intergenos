# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The harness never puts the panel token in a URL.

The panel authenticates a WebSocket only from its Sec-WebSocket-Protocol
header ("intergen, bearer.<token>"), which is how the shipped page sends it;
the server's access log records every request line, query string included.
The harness used to send the token BOTH ways, so every connection it opened
wrote the credential into the daemon's log (measured 2026-10-20: one log line
per battery run) while the copy in the URL authenticated nothing. These cases
pin the header-only form for every client the harness has, on the address it
builds and on the call it actually makes, with no daemon and no socket.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from intergen.tests import ws_harness as wh

TOKEN = "tok-3f9a1c7e-not-for-a-url"


class _Message:
    def __init__(self, data):
        self.type = wh.aiohttp.WSMsgType.TEXT
        self.data = json.dumps(data)


class _Socket:
    closed = False

    async def receive(self):
        return _Message({"type": "connected", "client_id": "case"})

    async def close(self):
        self.closed = True


class _Session:
    """Records what ws_connect was asked for, then hands back a socket that
    completes the connected handshake."""

    seen: list[tuple[str, list[str]]] = []

    async def ws_connect(self, url, protocols=()):
        _Session.seen.append((url, list(protocols)))
        return _Socket()

    async def close(self):
        pass


class TheTokenStaysOutOfTheUrl(unittest.TestCase):

    def test_no_client_builds_an_address_that_carries_the_token(self):
        for client in (wh.WSConversation(token=TOKEN),
                       wh.WSGateClient(token=TOKEN)):
            with self.subTest(client=type(client).__name__):
                self.assertNotIn(TOKEN, client._url)
                self.assertNotIn("token=", client._url)
                self.assertTrue(client._url.endswith(
                    "/ws?source_interface=web"), client._url)

    def test_the_connection_sends_it_in_the_protocol_header_only(self):
        _Session.seen = []

        async def connect():
            async with wh.WSConversation(token=TOKEN) as conv:
                return conv.all_messages

        with mock.patch.object(wh.aiohttp, "ClientSession", _Session):
            messages = asyncio.run(connect())
        self.assertEqual(messages[0]["type"], "connected")
        (url, protocols), = _Session.seen
        self.assertNotIn(TOKEN, url)
        self.assertEqual(protocols, ["intergen", f"bearer.{TOKEN}"])

    def test_the_address_is_the_one_the_shipped_page_opens(self):
        """app.js opens `${WS_URL}?source_interface=web` with the subprotocols
        ['intergen', 'bearer.' + token]; the harness asks the same of the
        server, so it exercises the path a person uses."""
        page = (wh.Path(wh.__file__).resolve().parents[1] / "web" /
                "app.js").read_text(encoding="utf-8")
        self.assertIn("`${WS_URL}?source_interface=web`", page)
        self.assertIn("['intergen', 'bearer.' + token]", page)
        self.assertEqual(wh.WSConversation(token=TOKEN, host="h",
                                           port=1)._url,
                         "ws://h:1/ws?source_interface=web")


if __name__ == "__main__":
    unittest.main()
