# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every client and check of the panel follows the address its configuration
names.

The panel's server binds web_server.host and web_server.port, shipped as
127.0.0.1:8089. Its clients did not follow it: the page and the dashboard
opened ws://localhost:8089/ws, the page's content security policy allowed a
socket to ws://localhost:8089 only, the console client connected to
ws://127.0.0.1:8089/ws, the panel window loaded http://localhost:8089/ and
recognised its own start-up race only on that address, and the shell
extension's browser item opened http://localhost:8089. A different configured
port moved the server alone and left every client dead. The health report's
"Web server" line was green for any connection count, so a panel that was not
serving never showed there.

Now the page builds its socket address from its own origin; the policy allows
the configured address; the console client and the panel window read the
configuration through the same function the daemon binds by, so neither
follows an address the panel refuses (the session's token stays on this
machine) and each says why when it cannot connect; the extension asks the
daemon, whose status names the address it serves the panel on; and the health
check reads the daemon's own panel state and whether the configured address
answers. On the shipped address nothing changes for the person.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import time
import unittest
from pathlib import Path
from unittest import mock

from intergen import dbus_daemon as dd
from intergen.config import Config
from intergen.tests.test_llama_launch import _free_port

PKG = Path(__file__).resolve().parents[1]
# A configured port that is not the shipped one and does not contain it.
OTHER_PORT = 18123
DEADLINE = 15.0


class _Config:
    """A configuration holding only the keys a case names; every other key
    falls to the default its reader passes, as an unset key does."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _configure(case: unittest.TestCase, **values) -> None:
    """Every Config the code under test builds answers the named keys with
    these values and every other key as it would."""
    real_get = Config.get

    def get(self, key, default=None):
        if key in values:
            return values[key]
        return real_get(self, key, default)

    patcher = mock.patch.object(Config, "get", get)
    patcher.start()
    case.addCleanup(patcher.stop)


def _held_port(case: unittest.TestCase) -> int:
    """A loopback port this case holds with a listening socket."""
    holder = socket.socket()
    case.addCleanup(holder.close)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    return holder.getsockname()[1]


class ThePageTakesItsSocketAddressFromItsOwnOrigin(unittest.TestCase):

    def assert_own_origin(self, name: str) -> None:
        source = (PKG / "web" / name).read_text(encoding="utf-8")
        found = re.search(r"const WS_URL = (.+?);\n", source, re.S)
        self.assertIsNotNone(found, f"{name} defines no WS_URL")
        expression = found.group(1)
        self.assertIn("location.host", expression,
                      f"{name} does not take the socket's host from the "
                      f"page's own origin")
        self.assertNotIn("8089", expression)
        self.assertNotIn("localhost", expression)
        self.assertNotIn("ws://localhost:8089", source)

    def test_the_page_builds_its_socket_address_from_its_own_origin(self):
        self.assert_own_origin("app.js")

    def test_the_dashboard_builds_its_socket_address_from_its_own_origin(self):
        self.assert_own_origin("dashboard.js")


class TheContentSecurityPolicyAllowsTheConfiguredAddress(unittest.TestCase):

    def connect_sources(self, host: str, port: int) -> str:
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request
        from intergen.web_server import WebServer

        server = WebServer(host=host, port=port)

        async def handler(request):
            return web.Response(text="ok")

        async def run():
            response = await server._csp_middleware(
                make_mocked_request("GET", "/"), handler)
            return response.headers["Content-Security-Policy"]

        policy = asyncio.run(run())
        return next(d.strip() for d in policy.split(";")
                    if d.strip().startswith("connect-src"))

    def test_a_configured_port_is_the_one_the_page_may_reach(self):
        sources = self.connect_sources("127.0.0.1", OTHER_PORT)
        self.assertIn(f"ws://127.0.0.1:{OTHER_PORT}", sources.split())
        self.assertNotIn("8089", sources)
        self.assertIn("'self'", sources.split())

    def test_an_ipv6_loopback_is_allowed_through_the_pages_own_origin(self):
        # A source expression cannot carry an IPv6 address, so the page's own
        # origin ('self') is what allows its socket there.
        sources = self.connect_sources("::1", OTHER_PORT)
        self.assertEqual(sources.split(), ["connect-src", "'self'"])

    def test_on_the_shipped_address_the_page_may_reach_the_shipped_port(self):
        sources = self.connect_sources("127.0.0.1", 8089)
        self.assertIn("'self'", sources.split())
        self.assertIn("ws://127.0.0.1:8089", sources.split())


class TheConsoleClientConnectsToTheConfiguredAddress(unittest.TestCase):

    def client_address(self) -> str:
        from intergen.console.client import ConsoleClient
        return ConsoleClient()._ws_url

    def test_the_client_connects_where_the_configuration_says(self):
        _configure(self, **{"web_server.port": OTHER_PORT})
        self.assertEqual(self.client_address(),
                         f"ws://127.0.0.1:{OTHER_PORT}/ws")

    def test_an_ipv6_loopback_is_written_in_brackets(self):
        _configure(self, **{"web_server.host": "::1",
                            "web_server.port": OTHER_PORT})
        self.assertEqual(self.client_address(), f"ws://[::1]:{OTHER_PORT}/ws")

    def test_on_the_shipped_address_nothing_changes(self):
        _configure(self, **{"web_server.host": "127.0.0.1",
                            "web_server.port": 8089})
        self.assertEqual(self.client_address(), "ws://127.0.0.1:8089/ws")

    def test_an_address_given_by_the_caller_is_kept(self):
        from intergen.console.client import ConsoleClient
        given = f"ws://127.0.0.1:{OTHER_PORT}/ws"
        self.assertEqual(ConsoleClient(ws_url=given)._ws_url, given)

    def test_an_address_the_panel_refuses_is_never_connected(self):
        # The panel serves a loopback address only; a client that followed any
        # other would hand the session's token to whatever listens there.
        _configure(self, **{"web_server.host": "0.0.0.0"})
        from intergen.console import client as console_client
        attempts = []

        class _Session:
            async def ws_connect(self, url, **kwargs):
                attempts.append(url)
                raise OSError("the stand-in session connects nowhere")

        raised = None
        with mock.patch.object(console_client.ConsoleClient, "_load_token",
                               return_value="0" * 64), \
                mock.patch.object(console_client.aiohttp, "ClientSession",
                                  _Session):
            try:
                asyncio.run(console_client.ConsoleClient().connect())
            except Exception as e:  # noqa: BLE001 — the case reads what came
                raised = e
        self.assertEqual(attempts, [], "the client offered its token to an "
                                       "address the panel refuses")
        self.assertIsInstance(raised, ConnectionError)
        self.assertIn("'0.0.0.0' is not a loopback IP address", str(raised))

    def test_the_console_says_why_it_did_not_connect(self):
        import io
        from contextlib import redirect_stderr
        from intergen.console import shell
        words = "the panel's configured address cannot be used: a reason"

        class _Client:
            async def connect(self):
                raise ConnectionError(words)

        # prompt_toolkit prints "Input is not a terminal" when built under a
        # test runner; it says nothing about this behaviour.
        with redirect_stderr(io.StringIO()):
            sh = shell.ConsoleShell()
        sh._client = _Client()
        asyncio.run(sh._connect_and_read())
        self.assertIn(words, sh._messages[-1]["content"])


class ThePanelWindowLoadsTheConfiguredAddress(unittest.TestCase):

    def test_the_window_loads_the_configured_address(self):
        _configure(self, **{"web_server.port": OTHER_PORT})
        from intergen import panel
        self.assertEqual(panel.panel_url(), f"http://127.0.0.1:{OTHER_PORT}/")

    def test_the_start_up_race_is_recognised_on_the_configured_address(self):
        _configure(self, **{"web_server.port": OTHER_PORT})
        from intergen import panel

        class _View:
            def __init__(self):
                self.pages = []

            def load_html(self, html, base):
                self.pages.append(html)

        # The window carries the retry the handler schedules, and nothing else.
        window = mock.Mock(spec=["_retry_load_web_ui"])
        view = _View()
        with mock.patch.object(panel.GLib, "timeout_add") as later:
            handled = panel.PanelWindow._on_load_failed(
                window, view, None, f"http://127.0.0.1:{OTHER_PORT}/", None)
            self.assertTrue(handled, "a failed load of the configured address "
                                     "is not recognised as the start-up race")
            self.assertEqual(len(view.pages), 1)
            later.assert_called_once()
        other = mock.Mock(spec=[])
        self.assertFalse(panel.PanelWindow._on_load_failed(
            other, _View(), None, "http://127.0.0.1:8089/", None),
            "a failed load of another address is taken for the panel's own")

    def test_on_the_shipped_address_nothing_changes(self):
        _configure(self, **{"web_server.host": "127.0.0.1",
                            "web_server.port": 8089})
        from intergen import panel
        self.assertEqual(panel.panel_url(), "http://127.0.0.1:8089/")

    def test_an_address_the_panel_refuses_is_never_loaded(self):
        # The window hands any page it loads the session's token, by its
        # document-start user script, so it must never load an address the
        # panel does not serve, by a request or by a plain load.
        _configure(self, **{"web_server.host": "0.0.0.0"})
        from intergen import panel

        class _View:
            def __init__(self):
                self.requests, self.pages = [], []

            def load_request(self, request):
                self.requests.append(request.get_uri())

            def load_uri(self, uri):
                self.requests.append(uri)

            def load_html(self, html, base):
                self.pages.append(html)

        window = mock.Mock(spec=["_webview", "_load_token"])
        window._webview = _View()
        window._load_token.return_value = "0" * 64
        panel.PanelWindow._do_load_web_ui(window)
        self.assertEqual(window._webview.requests, [],
                         "the window sent its token to an address the panel "
                         "refuses")
        self.assertEqual(len(window._webview.pages), 1)
        self.assertIn("0.0.0.0", window._webview.pages[0])
        self.assertIn("is not a loopback IP address", window._webview.pages[0])


class TheExtensionOpensThePanelWhereTheDaemonServesIt(unittest.TestCase):

    def daemon(self, **config) -> dd.InterGenDaemon:
        d = dd.InterGenDaemon()
        self.addCleanup(d.stop_service)
        d._config = _Config(**config)
        return d

    def test_the_status_names_the_configured_address(self):
        status = json.loads(self.daemon(**{"web_server.port": OTHER_PORT})
                            .status())
        self.assertEqual(status.get("panel_url"),
                         f"http://127.0.0.1:{OTHER_PORT}/")

    def test_once_built_the_status_names_the_address_the_server_binds(self):
        from intergen.web_server import WebServer
        d = self.daemon(**{"web_server.port": OTHER_PORT})
        d._web_server = WebServer(host="::1", port=OTHER_PORT + 1)
        self.assertEqual(json.loads(d.status()).get("panel_url"),
                         f"http://[::1]:{OTHER_PORT + 1}/")

    def test_an_address_the_panel_refuses_has_no_url(self):
        status = json.loads(self.daemon(**{"web_server.host": "0.0.0.0"})
                            .status())
        self.assertIn("panel_url", status)
        self.assertIsNone(status["panel_url"])

    def test_the_browser_item_names_no_address_of_its_own(self):
        source = (PKG / "panel" / "extension" / "extension.js").read_text(
            encoding="utf-8")
        self.assertNotIn("8089", source)
        self.assertNotIn("http://localhost", source)
        self.assertIn("panel_url", source,
                      "the browser item does not take the address from the "
                      "daemon's status")


class TheHealthCheckReadsThePanel(unittest.TestCase):

    def panel_check(self, **inputs) -> dict:
        from intergen.health import HealthAggregator
        report = HealthAggregator(**inputs).collect()
        for layer in report["layers"]:
            for check in layer["checks"]:
                if check["name"] == "Web server":
                    return check
        self.fail("the health report has no line for the panel")

    def test_a_panel_that_is_not_serving_is_down_in_words(self):
        words = f"the panel's port {OTHER_PORT} is held by another process"
        check = self.panel_check(panel_state=lambda: words,
                                 panel_address=("127.0.0.1", OTHER_PORT))
        self.assertEqual(check["status"], "red")
        self.assertIn(words, check["summary"])

    def test_a_serving_panel_whose_address_answers_is_up(self):
        port = _held_port(self)
        check = self.panel_check(panel_state=lambda: None,
                                 panel_address=("127.0.0.1", port))
        self.assertEqual(check["status"], "green")
        self.assertIn(f"127.0.0.1:{port}", check["summary"])

    def test_a_serving_panel_whose_address_does_not_answer_is_down(self):
        port = _free_port()
        check = self.panel_check(panel_state=lambda: None,
                                 panel_address=("127.0.0.1", port))
        self.assertEqual(check["status"], "red")
        self.assertIn(f"127.0.0.1:{port}", check["summary"])

    def test_a_panel_state_nobody_supplied_is_not_reported_green(self):
        self.assertEqual(self.panel_check()["status"], "yellow")


class TheDaemonsHealthCheckIsTheDaemonsOwnPanel(unittest.TestCase):

    def test_the_daemons_health_line_follows_its_panel(self):
        port = _free_port()
        d = dd.InterGenDaemon()
        self.addCleanup(d.stop_service)
        d._config = _Config(**{"web_server.port": port})
        d._start_panel()
        deadline = time.monotonic() + DEADLINE
        while (time.monotonic() < deadline
               and not (d._web_server and d._web_server.running)):
            time.sleep(0.05)
        self.assertTrue(d._web_server and d._web_server.running,
                        f"the panel did not bind within {DEADLINE} s")
        report = d._health_agg.collect()
        line = next(c for layer in report["layers"] for c in layer["checks"]
                    if c["name"] == "Web server")
        self.assertEqual(line["status"], "green")
        self.assertIn(f"127.0.0.1:{port}", line["summary"])

    def test_the_daemons_health_line_says_why_its_panel_is_down(self):
        port = _held_port(self)
        d = dd.InterGenDaemon()
        self.addCleanup(d.stop_service)
        d._config = _Config(**{"web_server.port": port})
        d._start_panel()
        # Until the refused bind is recorded the words say the bind is under
        # way; from then on every retry records the same words.
        deadline = time.monotonic() + DEADLINE
        while (time.monotonic() < deadline
               and "held by another process" not in (d._panel_down_now() or "")):
            time.sleep(0.05)
        words = d._panel_down_now()
        self.assertIn("held by another process", words or "",
                      f"the refused bind was not recorded within {DEADLINE} s")
        report = d._health_agg.collect()
        line = next(c for layer in report["layers"] for c in layer["checks"]
                    if c["name"] == "Web server")
        self.assertEqual(line["status"], "red")
        self.assertIn(words, line["summary"])


if __name__ == "__main__":
    unittest.main()
