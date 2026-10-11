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
registered once, when the server is built, however often it starts. A stop
ends every one-time sign-in and browser session the server holds, so a start
on the same server honours none of them, as a new server would not.

Each cell binds a free loopback port; nothing here touches the panel's own
address.
"""
from __future__ import annotations

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

from intergen import web_server
from intergen.tests.test_llama_launch import _free_port
from intergen.web_server import WebServer

# The shape `intergen setup` writes (secrets.token_hex(32)).
_TOKEN = "0123456789abcdef" * 4


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


def _a_token_of_its_own(test: unittest.TestCase) -> None:
    """Point the server at a token file in the case's own directory."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    token_path = Path(tmp.name) / "web-token"
    token_path.write_text(_TOKEN + "\n", encoding="utf-8")
    patcher = mock.patch.object(web_server, "TOKEN_PATH", token_path)
    patcher.start()
    test.addCleanup(patcher.stop)


async def _post(client, url: str, **kw) -> tuple[int, dict]:
    async with client.post(url, **kw) as r:
        return r.status, await r.json(content_type=None)


async def _status(client, base: str, credential: str) -> int:
    async with client.get(f"{base}/api/status",
                          headers={"Authorization": f"Bearer {credential}"}) as r:
        await r.read()
        return r.status


class AStopEndsEveryBrowserSession(unittest.TestCase):

    def test_a_session_and_a_sign_in_from_before_a_stop_open_nothing_after_a_start(self):
        _a_token_of_its_own(self)
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        server = WebServer(host="127.0.0.1", port=port)
        server.mark_ready()

        async def run():
            if not await server.start():
                raise AssertionError(server.bind_error)
            try:
                async with aiohttp.ClientSession(
                        cookie_jar=aiohttp.DummyCookieJar(),
                        timeout=aiohttp.ClientTimeout(total=10)) as client:
                    sign_in = {"Authorization": f"Bearer {_TOKEN}"}
                    _, unused = await _post(
                        client, f"{base}/api/panel-bootstrap", headers=sign_in)
                    _, used = await _post(
                        client, f"{base}/api/panel-bootstrap", headers=sign_in)
                    _, answer = await _post(
                        client, f"{base}/api/panel-session",
                        json={"bootstrap": used["bootstrap"]})
                    session = answer["session"]
                    before = await _status(client, base, session)
                    await server.stop()
                    if not await server.start():
                        raise AssertionError(server.bind_error)
                    after = await _status(client, base, session)
                    exchanged, _ = await _post(
                        client, f"{base}/api/panel-session",
                        json={"bootstrap": unused["bootstrap"]})
                    token = await _status(client, base, _TOKEN)
                    return before, after, exchanged, token
            finally:
                await server.stop()

        # Served before the stop; after the start on the same server the
        # session is refused and the unused sign-in exchanges for nothing,
        # while the token itself is served (the server does serve).
        self.assertEqual(asyncio.run(run()), (200, 401, 403, 200))


if __name__ == "__main__":
    unittest.main()
