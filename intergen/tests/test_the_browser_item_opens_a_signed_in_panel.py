# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The browser item opens the panel signed in, and the panel says so when it
cannot.

THE DEFECT THIS PINS. The shell extension's Open in Browser item launched the
panel's bare address. The page carries the panel's token only when the server
puts it there, and the server did that only for a request carrying the token
in an Authorization header, which a browser opening an address never sends.
The page therefore opened with no token, opened no socket, and told an account
that was set up to run setup.

THE RULE THESE CELLS HOLD. The item asks the server, with the token, for a
one-time sign-in that lapses within a minute, and opens the panel's address
carrying it. The server exchanges it once for a browser cookie (HttpOnly,
SameSite=Strict) and sends the browser on to the clean address, so the sign-in
leaves the address bar and the history. The index that cookie authorises
carries the token in a script the page's own content security policy lets run,
and the page opens its socket by its own path. A sign-in used twice, lapsed or
never issued is refused in plain words. A request with no browser session, the
panel window's own among them, is served the page with no token, as it was
before this change: the window's token comes by its own document-start user
script, and in a browser the page itself says why it has none. A request
carrying the token in an Authorization header is served no token from it.

Each cell serves a real WebServer on a free loopback port with a token file
of its own; nothing here touches the panel's own address or the account's
token.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp
from yarl import URL

from intergen import attribution, web_server
from intergen.tests.test_llama_launch import _free_port
from intergen.web_server import WebServer

# The shape `intergen setup` writes (secrets.token_hex(32)).
TOKEN = "0123456789abcdef" * 4
OTHER_TOKEN = "fedcba9876543210" * 4

# What the page must never say to an account that has a token, and what the
# panel's own pages say instead.
SETUP_WORDS = "intergen setup"
ITEM_WORDS = "Open in Browser"

# The browser session's cookie, by the name the server sets it under.
COOKIE = "intergen_panel"

ATTRIBUTION = "Built with a model whose license asks for this line."

EXTENSION_JS = (Path(web_server.__file__).parent / "panel" / "extension"
                / "extension.js")

_TOKEN_SCRIPT = re.compile(
    r"<script([^>]*)>\s*window\.__INTERGEN_TOKEN__\s*=\s*(\"[^\"]*\")\s*;"
    r"\s*</script>")


def _script_src(policy: str) -> list[str]:
    """The sources of a content security policy's script-src directive."""
    for directive in policy.split(";"):
        parts = directive.split()
        if parts and parts[0] == "script-src":
            return parts[1:]
    return []


def _page_token(html: str, policy: str) -> str:
    """The token a page carries, asserting the script that carries it is one
    the page's own policy lets run (its nonce is in script-src)."""
    found = _TOKEN_SCRIPT.search(html)
    if found is None:
        raise AssertionError("the page carries no token")
    nonce = re.search(r'\bnonce="([^"]+)"', found.group(1))
    if nonce is None or f"'nonce-{nonce.group(1)}'" not in _script_src(policy):
        raise AssertionError(
            "the page's token script is one its own content security policy "
            f"refuses to run (script attributes {found.group(1)!r}, "
            f"script-src {_script_src(policy)!r})")
    return json.loads(found.group(2))


class _Panel:
    """A real server on a free loopback port, with its own token file."""

    def __init__(self, test: unittest.TestCase, token: str | None = TOKEN):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.token_path = Path(tmp.name) / "web-token"
        if token is not None:
            self.token_path.write_text(token + "\n", encoding="utf-8")
        for patcher in (
                mock.patch.object(web_server, "TOKEN_PATH", self.token_path),
                mock.patch.object(attribution, "attribution_line",
                                  return_value=ATTRIBUTION)):
            patcher.start()
            test.addCleanup(patcher.stop)
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = WebServer(host="127.0.0.1", port=self.port)
        self.server.mark_ready()

    def run(self, scenario):
        """Serve, run scenario(session), stop; the session keeps cookies an
        IP-addressed server sets, as a browser does."""
        async def go():
            if not await self.server.start():
                raise AssertionError(self.server.bind_error)
            try:
                jar = aiohttp.CookieJar(unsafe=True)
                async with aiohttp.ClientSession(
                        cookie_jar=jar,
                        timeout=aiohttp.ClientTimeout(total=10)) as session:
                    return await scenario(session)
            finally:
                await self.server.stop()
        return asyncio.run(go())

    async def mint(self, session, token: str | None = TOKEN) -> str:
        """Ask for a one-time sign-in, as the browser item does."""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with session.post(f"{self.base}/api/panel-bootstrap",
                                headers=headers) as r:
            body = await r.text()
            if r.status != 200:
                raise AssertionError(
                    f"no sign-in was minted: {r.status} {body[:200]!r}")
            return json.loads(body)["bootstrap"]

    async def get(self, session, path: str, **kw):
        async with session.get(f"{self.base}{path}", **kw) as r:
            return r.status, r.headers, await r.text(), r


class TheBrowserItemsSignIn(unittest.TestCase):

    def test_the_sign_in_launch_lands_a_connected_page(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            status, headers, _, _ = await panel.get(
                session, f"/?bootstrap={bootstrap}", allow_redirects=False)
            self.assertEqual(status, 303)
            status, headers, html, _ = await panel.get(
                session, headers["Location"])
            self.assertEqual(status, 200)
            token = _page_token(html, headers["Content-Security-Policy"])
            self.assertEqual(token, TOKEN)
            # The socket, opened by the page's own path: app.js's URL and its
            # two subprotocols, the token taken from the page.
            async with session.ws_connect(
                    f"ws://127.0.0.1:{panel.port}/ws?source_interface=web",
                    protocols=("intergen", f"bearer.{token}")) as ws:
                return await ws.receive_json(timeout=5)

        connected = panel.run(scenario)
        self.assertEqual(connected.get("type"), "connected")
        self.assertEqual(connected.get("source_interface"), "web")

    def test_the_redirect_strips_the_sign_in(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            status, headers, _, r = await panel.get(
                session, f"/?bootstrap={bootstrap}", allow_redirects=False)
            first = (status, headers.get("Location"),
                     headers.get("Referrer-Policy"),
                     headers.get("Cache-Control"), r.cookies.get(COOKIE),
                     bootstrap)
            # A second sign-in, followed as a browser follows it.
            session.cookie_jar.clear()
            again = await panel.mint(session)
            status, _, _, r = await panel.get(session, f"/?bootstrap={again}")
            followed = (status, str(r.url), [h.status for h in r.history])
            return first, followed

        first, followed = panel.run(scenario)
        status, location, referrer, cache, cookie, bootstrap = first
        self.assertEqual(status, 303)
        self.assertEqual(location, "/", "the redirect does not lead to the "
                         "clean address")
        self.assertEqual(referrer, "no-referrer")
        self.assertEqual(cache, "no-store")
        self.assertIsNotNone(cookie, "the exchange set no cookie")
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/")
        self.assertNotIn(cookie.value, (bootstrap, TOKEN))
        status, url, history = followed
        self.assertEqual(status, 200)
        self.assertEqual(url, f"{panel.base}/")
        self.assertEqual(history, [303])

    def test_a_replayed_sign_in_is_refused(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            first = await panel.get(session, f"/?bootstrap={bootstrap}",
                                    allow_redirects=False)
            session.cookie_jar.clear()
            again = await panel.get(session, f"/?bootstrap={bootstrap}",
                                    allow_redirects=False)
            return first[0], again

        first, (status, headers, html, r) = panel.run(scenario)
        self.assertEqual(first, 303)
        self.assertEqual(status, 403)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertIsNone(r.cookies.get(COOKIE))
        self.assertIn(ITEM_WORDS, html)
        self.assertNotIn(SETUP_WORDS, html)

    def test_a_lapsed_sign_in_is_refused(self):
        panel = _Panel(self)
        lapse = mock.patch.object(web_server, "PANEL_BOOTSTRAP_TTL_S", 0.05,
                                  create=True)
        lapse.start()
        self.addCleanup(lapse.stop)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            await asyncio.sleep(0.2)
            return await panel.get(session, f"/?bootstrap={bootstrap}",
                                   allow_redirects=False)

        status, _, html, r = panel.run(scenario)
        self.assertEqual(status, 403)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertIsNone(r.cookies.get(COOKIE))
        self.assertIn(ITEM_WORDS, html)
        self.assertNotIn(SETUP_WORDS, html)

    def test_a_sign_in_never_issued_is_refused(self):
        panel = _Panel(self)

        async def scenario(session):
            return await panel.get(session, "/?bootstrap=never-issued",
                                   allow_redirects=False)

        status, _, html, _ = panel.run(scenario)
        self.assertEqual(status, 403)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertIn(ITEM_WORDS, html)
        self.assertNotIn(SETUP_WORDS, html)

    def test_the_sign_in_is_minted_only_for_the_token(self):
        panel = _Panel(self)

        async def scenario(session):
            answers = []
            for headers in ({}, {"Authorization": f"Bearer {OTHER_TOKEN}"}):
                async with session.post(f"{panel.base}/api/panel-bootstrap",
                                        headers=headers) as r:
                    answers.append((r.status, await r.text()))
            return answers

        for status, body in panel.run(scenario):
            self.assertEqual(status, 401, body[:200])
            self.assertNotIn("bootstrap", body)

    def test_a_rotated_token_ends_the_browser_session(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            await panel.get(session, f"/?bootstrap={bootstrap}")
            panel.token_path.write_text(OTHER_TOKEN + "\n", encoding="utf-8")
            return await panel.get(session, "/")

        status, _, html, _ = panel.run(scenario)
        self.assertEqual(status, 200)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertNotIn(TOKEN, html)
        self.assertNotIn(OTHER_TOKEN, html)

    def test_the_cookie_alone_opens_no_socket(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            await panel.get(session, f"/?bootstrap={bootstrap}")
            try:
                async with session.ws_connect(
                        f"ws://127.0.0.1:{panel.port}/ws?source_interface=web",
                        protocols=("intergen",)):
                    return "opened"
            except aiohttp.WSServerHandshakeError as e:
                return e.status

        self.assertEqual(panel.run(scenario), 401)


class TheUnsignedIndex(unittest.TestCase):
    """A request with no browser session is served the page with no token, as
    the panel window's own request is; the page's client says why it has none."""

    def assert_the_page_with_no_token(self, status, headers, html):
        self.assertEqual(status, 200)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertNotIn(TOKEN, html)
        self.assertIn('<script src="app.js', html,
                      "the page's own client, which says why it has no token, "
                      "is not served")
        self.assertIn(ATTRIBUTION, html,
                      "the attribution depends on how the page was opened")
        self.assertNotIn("Set-Cookie", headers)

    def test_the_bare_address_is_served_the_page_with_no_token(self):
        panel = _Panel(self)
        status, headers, html, _ = panel.run(
            lambda session: panel.get(session, "/"))
        self.assert_the_page_with_no_token(status, headers, html)

    def test_a_cookie_the_server_never_issued_is_served_the_same(self):
        panel = _Panel(self)

        async def scenario(session):
            session.cookie_jar.update_cookies(
                {COOKIE: "never-issued"}, response_url=URL(panel.base))
            return await panel.get(session, "/")

        status, headers, html, _ = panel.run(scenario)
        self.assert_the_page_with_no_token(status, headers, html)

    def test_an_account_with_no_token_is_served_the_page_with_no_token(self):
        panel = _Panel(self, token=None)
        status, headers, html, _ = panel.run(
            lambda session: panel.get(session, "/"))
        self.assert_the_page_with_no_token(status, headers, html)


class TheHeaderPathCarriesNoToken(unittest.TestCase):
    """A request carrying the token in an Authorization header, the path the
    panel window used before it loaded the page plainly, is served no token."""

    def test_a_request_carrying_the_token_in_a_header_is_served_no_token(self):
        panel = _Panel(self)
        status, _, html, _ = panel.run(lambda session: panel.get(
            session, "/", headers={"Authorization": f"Bearer {TOKEN}"}))
        self.assertEqual(status, 200)
        self.assertNotIn("__INTERGEN_TOKEN__", html)
        self.assertNotIn(TOKEN, html)
        self.assertIn('<script src="app.js', html)

    def test_the_socket_refuses_a_wrong_token(self):
        panel = _Panel(self)

        async def scenario(session):
            try:
                async with session.ws_connect(
                        f"ws://127.0.0.1:{panel.port}/ws?source_interface=web",
                        protocols=("intergen", f"bearer.{OTHER_TOKEN}")):
                    return "opened"
            except aiohttp.WSServerHandshakeError as e:
                return e.status

        self.assertEqual(panel.run(scenario), 401)


class TheBrowserItemsSource(unittest.TestCase):
    """The item runs inside the compositor and cannot be executed here; these
    read what it asks for and what it opens."""

    @classmethod
    def setUpClass(cls):
        text = EXTENSION_JS.read_text(encoding="utf-8")
        start = text.find("function _openPanelInBrowser(")
        end = text.find("// ── Panel indicator")
        if start < 0 or end < start:
            raise AssertionError("could not delimit the browser item's code")
        cls.item = text[start:end]
        cls.text = text

    def test_the_item_asks_for_a_sign_in_with_the_token(self):
        self.assertIn("api/panel-bootstrap", self.item)
        self.assertIn('"Authorization"', self.item)
        self.assertIn('"Bearer "', self.item)
        self.assertIn('"web-token"', self.text)

    def test_the_item_opens_the_address_carrying_the_sign_in(self):
        self.assertIn("?bootstrap=", self.item)
        self.assertNotIn("launch_default_for_uri(status.panel_url", self.item,
                         "the item still opens the bare address")

    def test_the_item_sends_the_token_through_no_proxy(self):
        self.assertIn("set_proxy_resolver(null)", self.item)


if __name__ == "__main__":
    unittest.main()
