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
carrying it in the fragment, which a browser sends to no server. The page's own
script takes the sign-in from its address, strips it from the address and the
history, and exchanges it once, by a POST to its own origin, for a browser
session it keeps in sessionStorage. The page presents that session on its
socket and on every request it makes, and the server checks it on each of
them; the server sets no cookie, and the page itself never carries the token.
A sign-in spent, lapsed or never issued exchanges for nothing, in plain words.
A request with no browser session, the panel window's own among them, is
served the page with no token, as it was before this change: the window's
token comes by its own document-start user script, and in a browser the page
itself says why it has none. A request carrying the token in an Authorization
header is served no token from it.

Each cell serves a real WebServer on a free loopback port with a token file
of its own; nothing here touches the panel's own address or the account's
token.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
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
# panel's own refusals say instead.
SETUP_WORDS = "intergen setup"
ITEM_WORDS = "Open in Browser"

# The cookie the panel once exchanged a sign-in for. The panel sets no cookie
# now; a request carrying one by this name is a request with no session.
COOKIE = "intergen_panel"

ATTRIBUTION = "Built with a model whose license asks for this line."

EXTENSION_JS = (Path(web_server.__file__).parent / "panel" / "extension"
                / "extension.js")
APP_JS = Path(web_server.__file__).parent / "web" / "app.js"


def _inline_scripts(html: str) -> list[str]:
    """Every script element of the page that is not a file the server serves."""
    return [tag for tag in re.findall(r"<script\b[^>]*>", html, re.I)
            if not re.search(r"\bsrc\s*=", tag, re.I)]


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
        """Serve, run scenario(session), stop; the session keeps any cookie an
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

    async def exchange(self, session, bootstrap: str):
        """Exchange a sign-in for a browser session, as the page's own script
        does: a POST to its own origin, the sign-in in the body."""
        async with session.post(f"{self.base}/api/panel-session",
                                json={"bootstrap": bootstrap}) as r:
            return r.status, r.headers, await r.text()

    async def sign_in(self, session, token: str = TOKEN) -> str:
        """The item's sign-in and the page's exchange: the browser session."""
        bootstrap = await self.mint(session, token)
        status, _, body = await self.exchange(session, bootstrap)
        if status != 200:
            raise AssertionError(
                f"the sign-in exchanged for nothing: {status} {body[:200]!r}")
        return json.loads(body)["session"]

    async def get(self, session, path: str, **kw):
        async with session.get(f"{self.base}{path}", **kw) as r:
            return r.status, r.headers, await r.text(), r

    async def socket(self, session, *protocols: str):
        """Open the page's socket as app.js does; the first frame, or the
        status of the refused handshake."""
        try:
            async with session.ws_connect(
                    f"ws://127.0.0.1:{self.port}/ws?source_interface=web",
                    protocols=protocols) as ws:
                return await ws.receive_json(timeout=5)
        except aiohttp.WSServerHandshakeError as e:
            return e.status

    async def api_status(self, session, credential: str) -> int:
        async with session.get(
                f"{self.base}/api/status",
                headers={"Authorization": f"Bearer {credential}"}) as r:
            return r.status

    async def socket_fate(self, session, credential: str, change,
                          wait: float = 5.0):
        """Open the page's socket on a credential, run change(session), and
        read what becomes of the socket: ("closed", code, reason) when the
        server closes it within `wait` seconds, or ("served", reply) when it
        is still open then, with the server's answer to a message sent on it
        (an unknown type, answered only by a socket still being served)."""
        async with session.ws_connect(
                f"ws://127.0.0.1:{self.port}/ws?source_interface=web",
                protocols=("intergen", f"bearer.{credential}")) as ws:
            connected = await ws.receive_json(timeout=5)
            if connected.get("type") != "connected":
                raise AssertionError(f"the socket did not connect: {connected}")
            await change(session)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + wait
            while (left := deadline - loop.time()) > 0:
                try:
                    msg = await ws.receive(timeout=left)
                except asyncio.TimeoutError:
                    break
                if msg.type in (aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED):
                    return "closed", msg.data, msg.extra
            await ws.send_json({"type": "a_message_after_the_change"})
            while True:
                reply = await ws.receive_json(timeout=5)
                if reply.get("code") == "unknown_type":
                    return "served", reply


class TheBrowserItemsSignIn(unittest.TestCase):

    def test_the_sign_in_launch_lands_a_connected_page(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            # The address the item opens carries the sign-in in its fragment,
            # which the browser keeps: the request is for the bare address.
            status, _, html, _ = await panel.get(session, "/")
            self.assertEqual(status, 200)
            self.assertNotIn(TOKEN, html)
            self.assertEqual(_inline_scripts(html), [])
            status, _, body = await panel.exchange(session, bootstrap)
            self.assertEqual(status, 200, body[:200])
            browser_session = json.loads(body)["session"]
            # The socket, opened by the page's own path: app.js's URL and its
            # two subprotocols, the session taken from sessionStorage.
            return await panel.socket(session, "intergen",
                                      f"bearer.{browser_session}")

        connected = panel.run(scenario)
        self.assertIsInstance(connected, dict, f"the socket was refused: "
                              f"{connected}")
        self.assertEqual(connected.get("type"), "connected")
        self.assertEqual(connected.get("source_interface"), "web")

    def test_the_exchange_answers_a_session_and_sets_no_cookie(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            status, headers, body = await panel.exchange(session, bootstrap)
            return (bootstrap, status, headers, body,
                    len(session.cookie_jar))

        bootstrap, status, headers, body, kept = panel.run(scenario)
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(kept, 0, "the browser kept a cookie from the panel")
        answer = json.loads(body)
        self.assertNotIn(answer["session"], (bootstrap, TOKEN))
        self.assertNotIn(TOKEN, body)
        self.assertGreater(answer["expires_in"], 0)

    def test_a_spent_sign_in_exchanges_for_nothing(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            first = await panel.exchange(session, bootstrap)
            again = await panel.exchange(session, bootstrap)
            return first[0], again

        first, (status, headers, body) = panel.run(scenario)
        self.assertEqual(first, 200)
        self.assertEqual(status, 403)
        self.assertNotIn("session", json.loads(body))
        self.assertNotIn("Set-Cookie", headers)
        self.assertIn(ITEM_WORDS, body)
        self.assertNotIn(SETUP_WORDS, body)

    def test_an_expired_sign_in_exchanges_for_nothing(self):
        panel = _Panel(self)
        lapse = mock.patch.object(web_server, "PANEL_BOOTSTRAP_TTL_S", 0.05,
                                  create=True)
        lapse.start()
        self.addCleanup(lapse.stop)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            await asyncio.sleep(0.2)
            return await panel.exchange(session, bootstrap)

        status, _, body = panel.run(scenario)
        self.assertEqual(status, 403)
        self.assertNotIn("session", json.loads(body))
        self.assertIn(ITEM_WORDS, body)
        self.assertNotIn(SETUP_WORDS, body)

    def test_a_sign_in_never_issued_exchanges_for_nothing(self):
        panel = _Panel(self)

        async def scenario(session):
            answers = []
            for body in ({"bootstrap": "never-issued"}, {}, "not json"):
                kw = {"json": body} if isinstance(body, dict) else {
                    "data": body}
                async with session.post(f"{panel.base}/api/panel-session",
                                        **kw) as r:
                    answers.append((r.status, await r.text()))
            return answers

        for status, body in panel.run(scenario):
            self.assertEqual(status, 403, body[:200])
            self.assertNotIn("session", json.loads(body))
            self.assertIn(ITEM_WORDS, body)
            self.assertNotIn(SETUP_WORDS, body)

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
            browser_session = await panel.sign_in(session)
            before = await panel.api_status(session, browser_session)
            panel.token_path.write_text(OTHER_TOKEN + "\n", encoding="utf-8")
            return (before, await panel.api_status(session, browser_session),
                    await panel.socket(session, "intergen",
                                       f"bearer.{browser_session}"))

        before, api, socket = panel.run(scenario)
        self.assertEqual(before, 200)
        self.assertEqual(api, 401)
        self.assertEqual(socket, 401)

    def test_a_restart_ends_the_browser_session(self):
        panel = _Panel(self)

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            await panel.server.stop()
            panel.server = WebServer(host="127.0.0.1", port=panel.port)
            panel.server.mark_ready()
            if not await panel.server.start():
                raise AssertionError(panel.server.bind_error)
            return (await panel.api_status(session, browser_session),
                    await panel.socket(session, "intergen",
                                       f"bearer.{browser_session}"),
                    await panel.api_status(session, TOKEN))

        self.assertEqual(panel.run(scenario), (401, 401, 200))

    def test_the_sign_in_alone_opens_nothing(self):
        panel = _Panel(self)

        async def scenario(session):
            bootstrap = await panel.mint(session)
            return (await panel.api_status(session, bootstrap),
                    await panel.socket(session, "intergen",
                                       f"bearer.{bootstrap}"))

        self.assertEqual(panel.run(scenario), (401, 401))

    def test_a_request_without_the_session_opens_nothing(self):
        panel = _Panel(self)

        async def scenario(session):
            await panel.sign_in(session)
            async with session.get(f"{panel.base}/api/status") as r:
                api = r.status
            return api, await panel.socket(session, "intergen")

        self.assertEqual(panel.run(scenario), (401, 401))


class AnOpenSocketEndsWithItsSession(unittest.TestCase):
    """A socket's credential is checked once, at its handshake, so the server
    closes the sockets a browser session opened when that session ends: when
    the token changes, when the session lapses, and when newer sessions drop
    it. An open socket was otherwise served after its session had ended,
    while a new request and a new socket on that session were refused. The
    page then asks whether its session is still honoured and, told it is
    not, says how to sign in again."""

    def assertClosedAsEnded(self, fate):
        self.assertEqual(fate[0], "closed",
                         f"the socket was still served: {fate}")
        self.assertEqual(fate[1], aiohttp.WSCloseCode.POLICY_VIOLATION, fate)

    def test_a_changed_token_closes_the_socket_its_session_opened(self):
        panel = _Panel(self)

        async def rotate(session):
            panel.token_path.write_text(OTHER_TOKEN + "\n", encoding="utf-8")

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            return await panel.socket_fate(session, browser_session, rotate)

        self.assertClosedAsEnded(panel.run(scenario))

    def test_a_token_file_that_is_not_text_closes_the_socket_and_refuses(self):
        panel = _Panel(self)

        async def garble(session):
            panel.token_path.write_bytes(b"\xff\xfe not text\n")

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            fate = await panel.socket_fate(session, browser_session, garble)
            return fate, await panel.api_status(session, browser_session)

        fate, status = panel.run(scenario)
        self.assertClosedAsEnded(fate)
        self.assertEqual(status, 401)

    def test_a_lapsed_session_closes_the_socket_it_opened(self):
        panel = _Panel(self)
        lapse = mock.patch.object(web_server, "PANEL_SESSION_TTL_S", 1.0)
        lapse.start()
        self.addCleanup(lapse.stop)

        async def nothing(session):
            return None

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            return await panel.socket_fate(session, browser_session, nothing)

        self.assertClosedAsEnded(panel.run(scenario))

    def test_a_session_newer_ones_dropped_closes_the_socket_it_opened(self):
        panel = _Panel(self)
        kept = mock.patch.object(web_server, "_PANEL_SESSIONS_KEPT", 2)
        kept.start()
        self.addCleanup(kept.stop)

        async def two_newer(session):
            await panel.sign_in(session)
            await panel.sign_in(session)

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            return await panel.socket_fate(session, browser_session, two_newer)

        self.assertClosedAsEnded(panel.run(scenario))

    def test_a_kept_session_keeps_its_socket(self):
        panel = _Panel(self)

        async def nothing(session):
            return None

        async def scenario(session):
            browser_session = await panel.sign_in(session)
            return await panel.socket_fate(session, browser_session, nothing,
                                           wait=2.5)

        fate = panel.run(scenario)
        self.assertEqual(fate[0], "served", fate)

    def test_the_tokens_own_socket_is_kept_while_a_session_is_watched(self):
        panel = _Panel(self)

        async def a_browser_session(session):
            await panel.sign_in(session)

        async def scenario(session):
            return await panel.socket_fate(session, TOKEN, a_browser_session,
                                           wait=2.5)

        fate = panel.run(scenario)
        self.assertEqual(fate[0], "served", fate)


class ACredentialOutsideAsciiIsRefused(unittest.TestCase):
    """A credential carrying bytes outside ASCII is refused like any other
    wrong one. Compared as strings it raised, and the panel answered 500."""

    @staticmethod
    async def status_of(port: int, request: bytes) -> int:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request)
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 5)
        writer.close()
        return int(line.split()[1])

    def test_every_door_refuses_it(self):
        panel = _Panel(self)
        body = b'{"bootstrap": "\\ud800"}'
        requests = {
            "the API": b"GET /api/status HTTP/1.1\r\nHost: x\r\n"
                       b"Authorization: Bearer \xc3\xa9" + TOKEN.encode()
                       + b"\r\nConnection: close\r\n\r\n",
            "the socket": b"GET /ws HTTP/1.1\r\nHost: x\r\n"
                          b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          b"Sec-WebSocket-Version: 13\r\n"
                          b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                          b"Sec-WebSocket-Protocol: intergen, bearer.\xc3\xa9"
                          + TOKEN.encode() + b"\r\n\r\n",
            "the sign-in": b"POST /api/panel-bootstrap HTTP/1.1\r\nHost: x\r\n"
                           b"Authorization: Bearer \xc3\xa9" + TOKEN.encode()
                           + b"\r\nContent-Length: 0\r\n"
                           b"Connection: close\r\n\r\n",
            "the exchange": b"POST /api/panel-session HTTP/1.1\r\nHost: x\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: " + str(len(body)).encode()
                            + b"\r\nConnection: close\r\n\r\n" + body,
        }

        async def scenario(session):
            return {door: await self.status_of(panel.port, request)
                    for door, request in requests.items()}

        self.assertEqual(panel.run(scenario),
                         {"the API": 401, "the socket": 401,
                          "the sign-in": 401, "the exchange": 403})


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

    def test_a_cookie_by_the_name_the_panel_once_set_is_served_the_same(self):
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

    def test_a_signed_in_browser_is_served_the_same_page(self):
        panel = _Panel(self)

        async def scenario(session):
            await panel.sign_in(session)
            return await panel.get(session, "/")

        status, headers, html, _ = panel.run(scenario)
        self.assert_the_page_with_no_token(status, headers, html)
        self.assertEqual(_inline_scripts(html), [])


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
        self.assertEqual(panel.run(lambda session: panel.socket(
            session, "intergen", f"bearer.{OTHER_TOKEN}")), 401)


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

    def test_the_item_opens_the_address_carrying_the_sign_in_in_its_fragment(self):
        self.assertIn('"#bootstrap="', self.item)
        self.assertNotIn("?bootstrap=", self.item,
                         "the item carries the sign-in where the browser "
                         "sends it")
        self.assertNotIn("launch_default_for_uri(status.panel_url", self.item,
                         "the item still opens the bare address")

    def test_the_item_sends_the_token_through_no_proxy(self):
        self.assertIn("set_proxy_resolver(null)", self.item)


def _page_code(*names: str) -> str:
    """The shipped app.js's declarations of these names, each taken whole (a
    function by brace matching, a const or let to its semicolon), so the code
    that ships is what runs."""
    text = APP_JS.read_text(encoding="utf-8")
    parts = []
    for name in names:
        found = re.search(r"^[ \t]*(?:async[ \t]+)?function[ \t]+"
                          + re.escape(name) + r"[ \t]*\(", text, re.MULTILINE)
        if found:
            depth = 0
            for at in range(text.index("{", found.end()), len(text)):
                depth += {"{": 1, "}": -1}.get(text[at], 0)
                if depth == 0:
                    parts.append(text[found.start():at + 1])
                    break
            continue
        found = re.search(r"^[ \t]*(?:const|let)[ \t]+" + re.escape(name)
                          + r"\b[^;]*;", text, re.MULTILINE)
        if found is None:
            raise AssertionError(f"app.js declares no {name}")
        parts.append(found.group(0))
    return "\n".join(parts)


# Runs the page's sign-in, taken from app.js, under node: the address, the
# history, sessionStorage and fetch are stand-ins that record what the page
# does with them, in order; fetch answers as the case says.
_DRIVER = r"""'use strict';
const plan = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const events = [];
const location = { hash: plan.hash, pathname: '/', search: '' };
const history = {
  replaceState(state, title, url) {
    events.push(['replaceState', String(url)]);
    location.hash = '';
  },
};
const items = {};
const sessionStorage = {
  getItem: (k) => (k in items ? items[k] : null),
  setItem: (k, v) => { items[k] = String(v); events.push(['setItem', String(v)]); },
  removeItem: (k) => { delete items[k]; events.push(['removeItem']); },
};
const answers = plan.answers.slice();
async function fetch(url, init) {
  events.push(['fetch', String(url), init || {}]);
  const answer = answers.shift();
  if (!answer) throw new Error('the page asked the server more than the case answers');
  if (answer.throws) throw new TypeError(answer.throws);
  return { ok: answer.status >= 200 && answer.status < 300,
           status: answer.status, json: async () => answer.body };
}
//PAGE//
if (plan.kept !== null) items[CREDENTIAL_KEY] = plan.kept;
(async () => {
  const pending = browserSession();
  const synchronous = !(pending && typeof pending.then === 'function');
  const session = synchronous ? pending : await pending;
  process.stdout.write(JSON.stringify({
    session, synchronous, events, signInWords,
    kept: CREDENTIAL_KEY in items ? items[CREDENTIAL_KEY] : null,
    address: location.pathname + location.search + location.hash }));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(2); });
"""

NODE = shutil.which("node")
SIGN_IN = "Zm9vYmFyLXNpZ24taW4tdmFsdWUtZm9yLWEtY2FzZQ"
SESSION = "c2Vzc2lvbi12YWx1ZS1mb3ItYS1jYXNlLW9mLXRoZS1wYWdl"


def _run_page_sign_in(hash: str = "", kept: str | None = None,
                      answers: tuple = ()) -> dict:
    """What the page's sign-in did, run under node from the shipped app.js."""
    code = _page_code("CREDENTIAL_KEY", "signInWords", "takeSignIn",
                      "exchangeSignIn", "sessionStillHonoured",
                      "browserSession")
    with tempfile.TemporaryDirectory(prefix="page-sign-in-") as tmp:
        driver = Path(tmp) / "driver.js"
        plan = Path(tmp) / "plan.json"
        driver.write_text(_DRIVER.replace("//PAGE//", code), encoding="utf-8")
        plan.write_text(json.dumps({"hash": hash, "kept": kept,
                                    "answers": list(answers)}),
                        encoding="utf-8")
        run = subprocess.run([NODE, str(driver), str(plan)],
                             capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        raise AssertionError(f"the page's sign-in could not be run (exit "
                             f"{run.returncode}): {run.stderr}")
    return json.loads(run.stdout)


@unittest.skipUnless(NODE, "node is required to run the page's own sign-in")
class ThePagesSignIn(unittest.TestCase):
    """The page's sign-in runs in a browser; these run it, taken whole from
    the shipped app.js, under node."""

    def signed_in(self) -> dict:
        return _run_page_sign_in(
            hash=f"#bootstrap={SIGN_IN}",
            answers=({"status": 200, "body": {"session": SESSION,
                                              "expires_in": 43200}},))

    def test_the_page_strips_the_sign_in_before_it_asks_the_server_anything(self):
        events = self.signed_in()["events"]
        kinds = [event[0] for event in events]
        self.assertIn("replaceState", kinds, "the page leaves the sign-in in "
                      "its address and history")
        self.assertLess(kinds.index("replaceState"), kinds.index("fetch"),
                        "the page asks the server before it strips the "
                        "sign-in from its address")

    def test_the_address_carries_no_credential_once_the_page_has_read_it(self):
        record = self.signed_in()
        stripped = [e[1] for e in record["events"] if e[0] == "replaceState"]
        for address in stripped + [record["address"]]:
            self.assertEqual(address, "/")
            self.assertNotIn(SIGN_IN, address)
            self.assertNotIn(SESSION, address)

    def test_the_page_exchanges_the_sign_in_by_a_post_to_its_own_origin(self):
        asks = [e for e in self.signed_in()["events"] if e[0] == "fetch"]
        self.assertEqual(len(asks), 1, asks)
        _, url, init = asks[0]
        self.assertEqual(url, "/api/panel-session")
        self.assertEqual(init.get("method"), "POST")
        self.assertEqual(json.loads(init.get("body", "null")),
                         {"bootstrap": SIGN_IN})
        self.assertEqual(init.get("credentials"), "omit")
        self.assertNotIn("Authorization", init.get("headers", {}))

    def test_the_page_keeps_the_session_in_session_storage(self):
        record = self.signed_in()
        self.assertEqual(record["session"], SESSION)
        self.assertEqual(record["kept"], SESSION)

    def test_a_refused_sign_in_leaves_no_session_and_says_why(self):
        words = "This sign-in was already used. Choose Open in Browser again."
        record = _run_page_sign_in(
            hash=f"#bootstrap={SIGN_IN}",
            answers=({"status": 403, "body": {"type": "error",
                                              "code": "sign_in_refused",
                                              "message": words}},))
        self.assertIsNone(record["session"])
        self.assertIsNone(record["kept"])
        self.assertEqual(record["signInWords"], words)
        self.assertEqual(record["address"], "/")

    def test_a_kept_session_is_presented_once_the_server_still_honours_it(self):
        record = _run_page_sign_in(kept=SESSION,
                                   answers=({"status": 200, "body": {}},))
        self.assertEqual(record["session"], SESSION)
        asks = [e for e in record["events"] if e[0] == "fetch"]
        self.assertEqual([a[1] for a in asks], ["/api/status"])
        self.assertEqual(asks[0][2]["headers"]["Authorization"],
                         f"Bearer {SESSION}")
        self.assertEqual(asks[0][2].get("credentials"), "omit")

    def test_an_ended_session_is_dropped_and_the_page_says_so(self):
        record = _run_page_sign_in(kept=SESSION,
                                   answers=({"status": 401, "body": {}},))
        self.assertIsNone(record["session"])
        self.assertIsNone(record["kept"])
        self.assertIn(ITEM_WORDS, record["signInWords"])
        self.assertNotIn(SETUP_WORDS, record["signInWords"])

    def test_a_server_that_does_not_answer_leaves_the_session_to_the_socket(self):
        record = _run_page_sign_in(
            kept=SESSION, answers=({"throws": "connection refused"},))
        self.assertEqual(record["session"], SESSION)
        self.assertEqual(record["kept"], SESSION)

    def test_with_nothing_to_ask_the_page_asks_nothing(self):
        record = _run_page_sign_in()
        self.assertTrue(record["synchronous"])
        self.assertIsNone(record["session"])
        self.assertEqual(record["events"], [])

    def test_a_malformed_sign_in_is_stripped_and_exchanges_for_nothing(self):
        record = _run_page_sign_in(hash="#bootstrap=%E0%A4%A")
        self.assertIsNone(record["session"])
        self.assertEqual([e[0] for e in record["events"]], ["replaceState"])
        self.assertEqual(record["address"], "/")


if __name__ == "__main__":
    unittest.main()
