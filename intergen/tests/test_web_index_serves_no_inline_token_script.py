# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The page is served without an inline token script.

The index handler wrote ``<script>window.__INTERGEN_TOKEN__ = "...";</script>``
into index.html whenever the request carried an Authorization header. The page
is served with its own Content-Security-Policy, whose ``script-src 'self'``
allows the scripts the server serves as files and no inline script, so a
browser that honours the policy never ran it. Confirmed in a real browser:
Firefox 140, sent the page's request with the header added, received the
script, refused it ("blocked an inline script (script-src-elem)") and left
``window.__INTERGEN_TOKEN__`` undefined. The token reaches the page by the
panel's document-start user script (intergen/panel/__init__.py), which the
page's policy does not govern; a browser opened on the page directly has no
token by either path.

The handler also wrote the header's value into the page unchecked, so a value
carrying markup closed the script element and opened another; the policy was
all that kept that from running.

The handler now writes no script into the page. The cases start the real
server on a loopback port the system assigns and fetch the page over HTTP, so
the route, the handler and the policy header are the ones a browser gets.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

from intergen import web_server
from intergen.session_manager import SessionManager

TOKEN = "index-case-token-5f0c2a9e"
MARKUP = "</script><script>window.__index_case_ran__ = 1;</script>"


async def _fetch(headers: dict) -> tuple[int, str, str]:
    """GET / from a started server: (status, page, Content-Security-Policy)."""
    with tempfile.TemporaryDirectory(prefix="index-case-") as tmp, \
         mock.patch.object(web_server, "SessionManager",
                           lambda: SessionManager(Path(tmp))), \
         mock.patch.object(web_server.attribution, "attribution_line",
                           return_value=None):
        srv = web_server.WebServer(host="127.0.0.1", port=0)
        if not await srv.start():
            raise AssertionError("the server did not bind a loopback port")
        try:
            srv.mark_ready()
            host, port = srv._runner.addresses[0][:2]
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{host}:{port}/",
                                       headers=headers) as resp:
                    return (resp.status, await resp.text(),
                            resp.headers.get("Content-Security-Policy", ""))
        finally:
            await srv.stop()


def _inline_scripts(page: str) -> list[str]:
    return [tag for tag in re.findall(r"<script\b[^>]*>", page, re.I)
            if not re.search(r"\bsrc\s*=", tag, re.I)]


class TheIndexServesNoTokenScript(unittest.TestCase):

    def test_an_authorized_request_gets_no_token_in_the_page(self):
        status, page, _ = asyncio.run(
            _fetch({"Authorization": f"Bearer {TOKEN}"}))
        self.assertEqual(status, 200)
        self.assertFalse(TOKEN in page, "the token was written into the page")
        self.assertFalse("__INTERGEN_TOKEN__" in page,
                         "a token script was written into the page")
        self.assertEqual(_inline_scripts(page), [],
                         "the page carries an inline script its own policy "
                         "refuses")

    def test_the_header_does_not_change_the_page(self):
        _, with_header, _ = asyncio.run(
            _fetch({"Authorization": f"Bearer {TOKEN}"}))
        _, without, _ = asyncio.run(_fetch({}))
        self.assertTrue(with_header == without,
                        "the page served with the header differs from the "
                        "page served without it")

    def test_a_header_value_is_not_written_into_the_page(self):
        _, page, _ = asyncio.run(_fetch({"Authorization": f"Bearer {MARKUP}"}))
        self.assertFalse("__index_case_ran__" in page,
                         "the header's markup was written into the page")

    def test_control_the_policy_served_refuses_inline_script(self):
        """The premise of the confirmation, pinned: the page is served with a
        script-src of 'self' alone — no 'unsafe-inline', nonce or hash — so no
        inline script in it could run."""
        _, _, csp = asyncio.run(_fetch({}))
        # The policy is built for the address the server binds (the socket's
        # connect-src names it); the server here is made for 127.0.0.1 and the
        # port the system assigns, asked for as 0.
        self.assertEqual(csp, web_server._content_security_policy("127.0.0.1", 0))
        directives = dict(d.strip().split(" ", 1)
                          for d in csp.split(";") if d.strip())
        self.assertEqual(directives["script-src"], "'self'")



class ThePanelGivesThePageItsTokenOneWay(unittest.TestCase):
    """The panel loaded the page with an Authorization header carrying the
    token, for the server's inline script. The server writes no script now,
    and nothing read that header. The panel loads the page plainly; its
    document-start user script is the one path the token takes."""

    def _panel(self):
        try:
            from intergen import panel
        except (ImportError, ValueError) as exc:  # no GTK 4 / WebKit 6 here
            self.skipTest(f"the panel cannot be imported here: {exc}")
        win = panel.PanelWindow.__new__(panel.PanelWindow)
        win._webview = mock.MagicMock()
        return panel, win

    def test_the_page_request_carries_no_token(self):
        panel, win = self._panel()
        with mock.patch.object(panel, "WebKit") as webkit, \
             mock.patch.object(panel.PanelWindow, "_load_token",
                               return_value=TOKEN):
            win._do_load_web_ui()
        headers = (webkit.URIRequest.new.return_value
                   .get_http_headers.return_value)
        self.assertFalse(headers.append.called,
                         "the page request still carries the token in a header")
        win._webview.load_uri.assert_called_once_with("http://localhost:8089/")

    def test_control_the_user_script_gives_the_page_its_token(self):
        panel, win = self._panel()
        with mock.patch.object(panel, "WebKit") as webkit, \
             mock.patch.object(panel.PanelWindow, "_load_token",
                               return_value=TOKEN):
            win._make_webview()
        args = webkit.UserScript.new.call_args.args
        self.assertEqual(args[0], f'window.__INTERGEN_TOKEN__ = "{TOKEN}";')
        self.assertIs(args[2], webkit.UserScriptInjectionTime.START)


if __name__ == "__main__":
    unittest.main()
