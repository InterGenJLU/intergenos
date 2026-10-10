# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel's web server starts again after a stop.

THE DEFECT THIS PINS. WebServer documents its start as retriable, and its stop
cleaned the aiohttp runner up but kept it, while its start set a runner up
only when it had none. A start after a stop therefore made a site on the
cleaned-up runner and raised "Call runner.setup() before making a site". The
daemon's own path no longer reaches this, because the daemon builds a new
server for every start of the panel; the class still documented a retry that
raised, which the next caller would inherit.

THE RULE THESE CELLS HOLD. A stop drops the site and the runner it cleaned
up, so a later start sets them up afresh and serves; the page's middleware is
registered once, when the server is built, however often it starts.

Each cell binds a free loopback port; nothing here touches the panel's own
address.
"""
from __future__ import annotations

import asyncio
import socket
import unittest

import aiohttp

from intergen.tests.test_llama_launch import _free_port
from intergen.web_server import WebServer


async def _answers(port: int) -> bool:
    """Whether the server answers an HTTP request on the port, whatever
    the status, the request served to its end on the server's own loop."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/status",
                                   timeout=aiohttp.ClientTimeout(total=5)):
                return True
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


class AStoppedServerStartsAgain(unittest.TestCase):

    def test_a_refused_start_a_stop_and_a_start_serves(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        server = WebServer(host="127.0.0.1", port=port)

        async def run():
            refused = await server.start()
            await server.stop()
            holder.close()
            try:
                again = await server.start()
                serving = await _answers(port)
            finally:
                await server.stop()
            return refused, again, serving

        try:
            refused, again, serving = asyncio.run(run())
        finally:
            holder.close()
        self.assertFalse(refused, "the held port was not refused")
        self.assertTrue(again, server.bind_error)
        self.assertTrue(serving)

    def test_a_server_that_served_and_stopped_serves_again(self):
        port = _free_port()
        server = WebServer(host="127.0.0.1", port=port)

        async def run():
            first = await server.start()
            await server.stop()
            try:
                second = await server.start()
                serving = await _answers(port)
            finally:
                await server.stop()
            return first, second, serving

        first, second, serving = asyncio.run(run())
        self.assertTrue(first)
        self.assertTrue(second, server.bind_error)
        self.assertTrue(serving)

    def test_the_middleware_is_registered_once_however_often_it_starts(self):
        server = WebServer(host="127.0.0.1", port=_free_port())

        async def run():
            for _ in range(2):
                await server.start()
                await server.stop()

        asyncio.run(run())
        self.assertEqual(
            server._app.middlewares.count(server._csp_middleware), 1)


if __name__ == "__main__":
    unittest.main()
