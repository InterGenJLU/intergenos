# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel's browser credential never reaches another server on the machine.

THE DEFECT THIS PINS. The browser item's sign-in was exchanged for a cookie. A
cookie is scoped to a host, not to a port, so the browser sent it to every
server on 127.0.0.1, whatever port it listened on, and any of them could hand
it back to the panel and be served a page carrying the account's token.

THE RULE THESE CELLS HOLD. The panel sets no cookie on any door a browser can
reach its sign-in by: the address the item opens (the sign-in in its fragment,
which a browser sends to no server), an address with a sign-in in its query
(the form the item opened before), and the page's own exchange. The session the
page is given lives in its sessionStorage, which belongs to the panel's origin
alone (scheme, host and port), and the page presents it itself. A second
server on another loopback port, visited by the same browser, therefore
receives no credential, and whatever it did receive opens nothing on the panel:
neither a page carrying the token nor the socket.

Each cell serves a real WebServer on a free loopback port with a token file of
its own, beside a second server on another free loopback port that records
every request reaching it; nothing here touches the panel's own address or the
account's token.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import unittest
import urllib.request
from email.message import Message

import aiohttp
from aiohttp import web

from intergen.tests import test_the_browser_item_opens_a_signed_in_panel as item
from intergen.tests.test_llama_launch import _free_port

TOKEN = item.TOKEN

# Request headers that carry a credential to a server.
CREDENTIAL_HEADERS = ("Cookie", "Authorization", "Sec-WebSocket-Protocol")


class _OtherPort:
    """A second server on another loopback port of the same machine. It
    records every request that reaches it (its address and its headers)."""

    def __init__(self):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.seen: list[tuple[str, dict[str, str]]] = []
        self._runner: web.AppRunner | None = None

    async def _record(self, request: web.Request) -> web.Response:
        self.seen.append((request.path_qs, dict(request.headers)))
        return web.Response(text="another server")

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._record)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


async def _walk_every_sign_in_door(panel, browser) -> dict:
    """Sign in by every door a browser can reach the panel's sign-in by,
    following redirects and keeping cookies as a browser does, and record
    every response's headers and every value that is a credential."""
    responses = []
    values = [TOKEN]
    # An address with a sign-in in its query: the item's form before this
    # change, where the index exchanged it for a cookie.
    by_query = await panel.mint(browser)
    values.append(by_query)
    async with browser.get(f"{panel.base}/?bootstrap={by_query}") as r:
        await r.read()
        responses += [h.headers for h in r.history] + [r.headers]
    # The address the item opens now: the sign-in in its fragment, which the
    # browser keeps, then the page's own exchange of it.
    by_fragment = await panel.mint(browser)
    values.append(by_fragment)
    async with browser.get(f"{panel.base}/") as r:
        await r.read()
        responses += [h.headers for h in r.history] + [r.headers]
    async with browser.post(f"{panel.base}/api/panel-session",
                            json={"bootstrap": by_fragment}) as r:
        body = await r.text()
        responses.append(r.headers)
        exchanged = r.status
    if exchanged == 200:
        values.append(json.loads(body)["session"])
    return {"responses": responses, "values": values, "exchanged": exchanged}


# A token a page carries, in the form the panel once wrote it.
_TOKEN_IN_PAGE = re.compile(r'window\.__INTERGEN_TOKEN__\s*=\s*("[^"]*")')


async def _replayed_socket(panel, replayer, received: dict[str, str],
                           lifted: str | None):
    """The panel's socket, asked for with what the other server received and
    with any token the page it was served carried."""
    headers = {k: v for k, v in received.items()
               if k != "Sec-WebSocket-Protocol"}
    protocols = ["intergen"] + [
        part.strip() for part in
        received.get("Sec-WebSocket-Protocol", "").split(",")
        if part.strip().startswith("bearer.")]
    if lifted:
        protocols.append(f"bearer.{lifted}")
    try:
        async with replayer.ws_connect(
                f"ws://127.0.0.1:{panel.port}/ws?source_interface=web",
                headers=headers, protocols=protocols) as ws:
            await ws.receive_json(timeout=5)
            return "opened"
    except aiohttp.WSServerHandshakeError as e:
        return e.status


def _cookie_jar_match(set_cookies: list[str], from_url: str,
                      to_url: str) -> str | None:
    """The Cookie header the standard library's jar sends to to_url after
    taking the Set-Cookie headers from_url answered, as the reader's control
    did."""
    jar = http.cookiejar.CookieJar()

    class _Response:
        def __init__(self, headers: list[str]):
            self._message = Message()
            for value in headers:
                self._message["Set-Cookie"] = value

        def info(self):
            return self._message

    jar.extract_cookies(_Response(set_cookies),
                        urllib.request.Request(from_url))
    request = urllib.request.Request(to_url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie")


class TheCrossPortControl(unittest.TestCase):

    def walk(self):
        """The browser signs in by every door, then visits the other server;
        the other server then hands everything it received back to the panel
        from a client of its own, and uses any token the page it is served
        carries."""
        panel = item._Panel(self)
        other = _OtherPort()

        async def scenario(browser):
            await other.start()
            try:
                walked = await _walk_every_sign_in_door(panel, browser)
                async with browser.get(f"{other.base}/a-page") as r:
                    await r.read()
                received = {k: v for _, headers in other.seen
                            for k, v in headers.items()
                            if k in CREDENTIAL_HEADERS}
                async with aiohttp.ClientSession(
                        cookie_jar=aiohttp.DummyCookieJar(),
                        timeout=aiohttp.ClientTimeout(total=10)) as replayer:
                    async with replayer.get(f"{panel.base}/",
                                            headers=received) as r:
                        html = await r.text()
                    lifted = _TOKEN_IN_PAGE.search(html)
                    socket = await _replayed_socket(
                        panel, replayer, received,
                        json.loads(lifted.group(1)) if lifted else None)
                return walked, received, html, socket
            finally:
                await other.stop()

        walked, received, html, socket = panel.run(scenario)
        set_cookies = [v for h in walked["responses"]
                       for v in h.getall("Set-Cookie", [])]
        return {"walked": walked, "received": received, "html": html,
                "socket": socket, "set_cookies": set_cookies,
                "panel": panel, "other": other}

    def test_another_loopback_port_receives_no_credential(self):
        seen = self.walk()
        self.assertEqual(seen["received"], {},
                         "another server on this machine received "
                         f"{seen['received']!r}")
        for path, headers in seen["other"].seen:
            for value in seen["walked"]["values"]:
                self.assertNotIn(value, path)
                self.assertNotIn(value, json.dumps(headers))

    def test_the_standard_librarys_jar_sends_another_port_nothing(self):
        seen = self.walk()
        for port in (seen["panel"].port, seen["other"].port):
            self.assertIsNone(
                _cookie_jar_match(seen["set_cookies"],
                                  f"{seen['panel'].base}/",
                                  f"http://127.0.0.1:{port}/"),
                f"a jar holding the panel's cookies sends port {port} one")

    def test_what_another_port_received_opens_nothing_on_the_panel(self):
        seen = self.walk()
        self.assertNotIn(TOKEN, seen["html"])
        self.assertNotIn("__INTERGEN_TOKEN__", seen["html"])
        self.assertEqual(item._inline_scripts(seen["html"]), [])
        self.assertEqual(seen["socket"], 401)

    def test_no_door_of_the_sign_in_sets_a_cookie(self):
        seen = self.walk()
        self.assertEqual(seen["set_cookies"], [])
        self.assertEqual(seen["walked"]["exchanged"], 200,
                         "the page's exchange answered no session")


if __name__ == "__main__":
    unittest.main()
