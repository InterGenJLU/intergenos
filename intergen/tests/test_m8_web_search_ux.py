# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8-3 web search UX (tweak wave 3) — trace-grounded from the discovery ledger.

web_search:128 is the dominant tool_starvation subcategory (m8-wave1-ledger): a
search-worthy ask (dd-mt-0120 "how much does the new steam deck cost", dd-mt-0129
"find me a recipe for banana bread") answered from stale training or refused ("I
can't pull a recipe from my system tools") instead of dispatching web_search and
rendering the results. Two halves:

* RENDER UX: web_search's result IS the top-N listing (title / url / snippet,
  render_search_results); the dispatch now carries it as full_output so the sources
  sit VERIFIABLE below the model's synthesis (normalized-first + verifiable-original),
  with a per-turn glass render event.
* CAPABILITY-QUESTION HONESTY: a QUESTION about the capability ("can you search the
  internet?") is answered from the live capability surface — the real presence of
  web_search in the tool registry — and is NEVER passed into web_search as a query
  (the capability-question-as-command incoherence, M8 doc §3.4). Grounded, not a
  hardcoded claim.

Execution gating byte-identical: web_search is a read-only (AUTO) tool that
dispatches under its existing gating; mutating tools stay CONFIRM.
"""

from __future__ import annotations

import io
import contextlib
import http.client
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.router import (ConversationRouter, detect_file_lifecycle_intent,
                             _WEB_CAP_Q_RE)
from intergen.tools.web_search import WebSearchTool, render_search_results
from intergen.tool_registry import ToolRegistry


def _cap_router(*, with_web: bool):
    r = ConversationRouter.__new__(ConversationRouter)
    r._conversation_history = []
    r._append_history = lambda *a, **k: None
    r._record = lambda *a, **k: None
    reg = mock.Mock()
    reg.get_all_names.return_value = (
        ["web_search", "read_file", "write_file"] if with_web
        else ["read_file", "write_file"])
    r._tools = reg
    return r


class M8CapabilityQuestionTests(unittest.TestCase):
    def test_grounded_yes_when_web_search_registered(self):
        r = _cap_router(with_web=True)
        res = r._try_capability_question("can you search the internet?", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "capability_question")
        self.assertIn("can search the web", res.text.lower())
        # Answered from the surface — NOT passed into web_search as a query.
        self.assertEqual(res.tool_calls, [])

    def test_grounded_no_when_web_search_absent(self):
        r = _cap_router(with_web=False)
        res = r._try_capability_question("can you go online?", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("isn't available", res.text.lower())

    def test_real_tool_ask_falls_through(self):
        # "search my files" is a real tool ask, not a web capability question —
        # the handler declines so it routes normally (and can dispatch a tool).
        r = _cap_router(with_web=True)
        self.assertIsNone(
            r._try_capability_question("can you search my files", 0.0))
        self.assertIsNone(
            r._try_capability_question("search the web for banana bread", 0.0))

    def test_regex_high_precision(self):
        for pos in ("can you search the internet?",
                    "are you able to access the internet",
                    "do you have internet access", "can you go online"):
            self.assertTrue(_WEB_CAP_Q_RE.search(pos), pos)
        for neg in ("can you search my files", "search the web for x",
                    "how do I search the internet", "can you write code"):
            self.assertFalse(_WEB_CAP_Q_RE.search(neg), neg)


class M8WebSearchRenderTests(unittest.TestCase):
    def test_render_top_n_title_url_snippet(self):
        content, _summary = render_search_results("steam deck price", [
            ("Steam Deck – Valve", "https://store.steampowered.com/steamdeck",
             "Steam Deck starts at ..."),
            ("Steam Deck review", "https://example.com/review", "A handheld ..."),
        ])
        # Numbered top-N with title, url, and snippet — the verifiable listing.
        self.assertIn("1. Steam Deck – Valve", content)
        self.assertIn("https://store.steampowered.com/steamdeck", content)
        self.assertIn("2. Steam Deck review", content)


class M8WebSearchGatingRegressionTests(unittest.TestCase):
    """web_search dispatches under its EXISTING gating (read-only AUTO); mutating
    tools stay CONFIRM — byte-identical, unchanged by the render/capability layer."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_web_search_is_read_only_auto(self):
        self.assertEqual(
            self.registry.classify_safety("web_search", {"query": "x"}),
            SafetyTier.AUTO)

    def test_mutating_write_file_still_confirm(self):
        self.assertEqual(
            self.registry.classify_safety(
                "write_file", {"path": "/home/t/a.txt", "content": "x"}),
            SafetyTier.CONFIRM)


class SearchPageEvidenceTests(unittest.TestCase):
    @contextlib.contextmanager
    def _network(self, page, *, source="https://example.com/news", headers=None):
        search = (
            f'<a class="result__a" href="{source}">News</a>'
            '<a class="result__snippet">Visit for the latest headlines.</a>'
        ).encode()
        body = io.BytesIO(page)
        response = mock.Mock(status=200)
        response.read1 = body.read1
        response.isclosed.return_value = False
        fields = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: fields.get(key, default)
        connection = mock.Mock()
        connection.getresponse.return_value = response
        sock = mock.Mock()
        context = mock.Mock()
        context.wrap_socket.return_value = sock
        # The network is entirely simulated. TEST-NET represents the public peer;
        # the classifier stand-in admits only that documentation address.
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("urllib.request.urlopen", return_value=io.BytesIO(search)) as search_call, \
                mock.patch("socket.getaddrinfo", return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))
                ]) as lookup, \
                mock.patch("ipaddress.ip_address", side_effect=lambda value:
                           SimpleNamespace(is_global=value == "192.0.2.10")), \
                mock.patch("socket.create_connection", return_value=sock) as connect, \
                mock.patch("ssl.create_default_context", return_value=context), \
                mock.patch("http.client.HTTPConnection", return_value=connection):
            yield search_call, lookup, connect, connection, response

    def test_a_navigation_snippet_is_supplemented_with_the_page_text(self):
        page = (
            "<html><head><title>News</title></head><body>"
            "<nav>Home Subscribe</nav><article>"
            "<h1>New observatory opens its first telescope</h1>"
            "<p>The observatory opened to visitors on Tuesday.</p>"
            "</article><script>ignored script text</script></body></html>"
        ).encode()
        with self._network(page) as (search_call, lookup, connect, connection, _response):
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertEqual(search_call.call_count, 1)
        self.assertEqual(connection.request.call_count, 1)
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(connect.call_args.args[0], ("192.0.2.10", 443))
        self.assertGreater(connect.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(connect.call_args.kwargs["timeout"], 8)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"],
                         "example.com")
        self.assertTrue(result.success)
        self.assertIn("New observatory opens its first telescope", result.content)
        self.assertIn("https://example.com/news", result.content)
        self.assertNotIn("ignored script text", result.content)

    def test_large_pages_are_capped_and_keep_page_evidence_in_the_model_summary(self):
        page = b"<p>New observatory opens</p>" + b"<p>More details.</p>" * 20000
        with self._network(page) as (_search, _lookup, _connect, _connection, response):
            read = mock.Mock(wraps=response.read1)
            response.read1 = read
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertIn("Page excerpt (truncated)", result.content)
        self.assertIn("New observatory opens", result.model_summary)
        self.assertIn("Source: https://example.com/news", result.model_summary)
        self.assertLessEqual(len(result.model_summary), 4000)
        self.assertEqual(sum(call.args[0] for call in read.call_args_list), 128 * 1024 + 1)

    def test_a_private_destination_is_not_contacted(self):
        with self._network(b"<p>not fetched</p>") as (_search, lookup, connect, _conn, _resp):
            lookup.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
            ]
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        connect.assert_not_called()
        self.assertTrue(result.success)
        self.assertIn("did not provide readable text", result.content)

    def test_a_redirect_cannot_switch_to_a_private_destination(self):
        with self._network(b"") as (_search, lookup, connect, _conn, response):
            lookup.side_effect = [
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))],
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
            ]
            response.status = 302
            response.getheader.side_effect = lambda key, default=None: (
                "https://internal.invalid/" if key == "Location" else default)
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertEqual(connect.call_count, 1)
        self.assertIn("did not provide readable text", result.content)

    def test_a_redirect_loop_stops_after_three_redirects(self):
        with self._network(b"") as (_search, _lookup, connect, _conn, response):
            response.status = 302
            response.getheader.side_effect = lambda key, default=None: (
                "/news" if key == "Location" else default)
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertEqual(connect.call_count, 4)
        self.assertEqual(response.close.call_count, 4)
        self.assertIn("did not provide readable text", result.content)

    def test_non_http_sources_and_credentials_are_not_fetched(self):
        for source in ("file:///etc/hosts", "https://user:password@example.com/news"):
            with self.subTest(source=source), self._network(b"", source=source) as network:
                result = WebSearchTool().execute({"query": "latest headline"})
                network[1].assert_not_called()
                network[2].assert_not_called()
                self.assertIn("did not provide readable text", result.content)

    def test_a_dynamic_page_does_not_turn_its_title_or_script_into_an_answer(self):
        with self._network(b"<html><head><title>News</title></head>"
                           b"<body><script>loadHeadlines()</script></body></html>"):
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertIn("did not provide readable text", result.content)
        self.assertNotIn("loadHeadlines", result.content)

    def test_a_timeout_keeps_the_search_results_and_reports_missing_page_text(self):
        with self._network(b"") as (_search, _lookup, connect, _conn, _response):
            connect.side_effect = TimeoutError("timed out")
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertTrue(result.success)
        self.assertIn("1. News", result.content)
        self.assertIn("did not provide readable text", result.content)

    def test_the_http_response_closing_at_eof_does_not_discard_the_page(self):
        page = b"<h1>New observatory opens its first telescope</h1>"
        wire = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(page)).encode() + b"\r\n\r\n" + page)
        source = mock.Mock()
        source.makefile.return_value = io.BytesIO(wire)
        response = http.client.HTTPResponse(source)
        response.begin()

        def timeout_on_open_socket(_timeout):
            if response.isclosed():
                raise OSError("closed socket")

        with self._network(page) as (_search, _lookup, connect, connection, _response):
            connection.getresponse.return_value = response
            connect.return_value.settimeout.side_effect = timeout_on_open_socket
            result = WebSearchTool().execute({"query": "latest headline example.com"})
        self.assertIn("New observatory opens its first telescope", result.content)
        self.assertNotIn("did not provide readable text", result.content)

    def test_a_link_only_model_answer_is_replaced_with_the_fetched_fact(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        router, registry, _executed = _build_router()
        registry.execute = lambda call, **_kwargs: WebSearchTool().execute(call.arguments)
        router._llm.stream = lambda *_args, **_kwargs: iter([
            "The most current headline is available at https://example.com/news."])
        with self._network(b"<article><h1>New observatory opens its first telescope</h1>"
                           b"<p>The observatory opened on Tuesday.</p></article>"):
            result = router.route("search the web for the latest headline")
        self.assertIn("New observatory opens its first telescope", result.text)
        self.assertIn("\nSource: https://example.com/news", result.text)
        self.assertNotIn("is available at", result.text)

    def test_unavailable_page_and_navigation_snippets_do_not_supply_a_headline(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        router, registry, _executed = _build_router()
        registry.execute = lambda call, **_kwargs: WebSearchTool().execute(call.arguments)
        answer = "The retrieved snippets do not state the latest headline."
        router._llm.stream = lambda *_args, **_kwargs: iter([answer])
        with self._network(b"<html><head><title>News</title></head>"
                           b"<body><script>loadHeadlines()</script></body></html>"):
            result = router.route("search the web for the latest headline")
        self.assertIn(answer, result.text)
        self.assertIn("https://example.com/news", result.text)

    def test_a_short_factual_answer_keeps_its_words_and_adds_the_source_line(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        router, registry, _executed = _build_router()
        registry.execute = lambda call, **_kwargs: WebSearchTool().execute(call.arguments)
        router._llm.stream = lambda *_args, **_kwargs: iter(["Closed today."])
        with self._network(b"<article><p>Closed today.</p></article>"):
            result = router.route("search the web for observatory opening hours")
        self.assertEqual(result.text, "Closed today.\n\nSource: https://example.com/news")


class LocalPageBoundaryTests(unittest.TestCase):
    @contextlib.contextmanager
    def _server(self, *, slow_part=None, status=200, body=b"Opening hours."):
        stopped = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                try:
                    if slow_part:
                        prefix = (b"HTTP/1.1 200 " if slow_part == "status"
                                  else b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        self.connection.sendall(prefix)
                        for _ in range(12):
                            if stopped.wait(0.1):
                                return
                            self.connection.sendall(b"x")
                        suffix = (b"\r\nContent-Type: text/plain\r\n"
                                  b"Content-Length: 2\r\n\r\nok")
                        self.connection.sendall(suffix)
                        return
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01})
        thread.start()
        real_connect = socket.create_connection

        def connect(address, *args, **kwargs):
            self.assertEqual(address, ("192.0.2.10", 80))
            return real_connect(server.server_address, *args, **kwargs)

        real_lookup = socket.getaddrinfo

        def lookup(host, port, *args, **kwargs):
            if host == "fixture.invalid":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                         ("192.0.2.10", port))]
            self.assertEqual(host, "127.0.0.1")
            return real_lookup(host, port, *args, **kwargs)

        try:
            with mock.patch("socket.getaddrinfo", side_effect=lookup), \
                    mock.patch("socket.create_connection", side_effect=connect), \
                    mock.patch("ipaddress.ip_address", side_effect=lambda value:
                               SimpleNamespace(is_global=value == "192.0.2.10")):
                yield "http://fixture.invalid/opening"
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_the_total_budget_covers_status_and_header_reads(self):
        from intergen.tools import web_search

        for part in ("status", "header"):
            with self.subTest(part=part), self._server(slow_part=part) as url, \
                    mock.patch.object(web_search, "_PAGE_TIMEOUT", 0.35):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    web_search._fetch_page_text(url)
                elapsed = time.monotonic() - started
                print(f"slow_{part}_elapsed={elapsed:.6f}", flush=True)
                self.assertLess(elapsed, 0.8)

    def test_an_unavailable_page_keeps_an_answering_snippet(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        fact = "The observatory opens at 09:00."
        with self._server(status=404) as url:
            for snippet, answer in (
                (fact, fact),
                ("The observatory has a gift shop.",
                 "The retrieved snippets do not state the opening time."),
            ):
                with self.subTest(snippet=snippet):
                    router, registry, _ = _build_router()
                    del registry.execute
                    prompts = []

                    def synthesize(messages, **_kwargs):
                        prompts.append(messages[-1].content)
                        yield answer

                    router._llm.stream = synthesize
                    search = (f'<a class="result__a" href="{url}">Opening hours</a>'
                              f'<a class="result__snippet">{snippet}</a>').encode()
                    with mock.patch.dict("os.environ", {"SERPER_API_KEY": ""}), \
                            mock.patch("urllib.request.urlopen", return_value=io.BytesIO(search)):
                        result = router.route("search the web for observatory opening hours")
                    self.assertEqual(len(prompts), 1)
                    self.assertIn(snippet, prompts[0])
                    self.assertIn("If they do not contain the fact, say so.", prompts[0])
                    self.assertEqual(result.text, answer + "\n\nSource: " + url)
                    self.assertIn(snippet, result.tool_results[0].content)

    def test_empty_snippets_keep_the_unavailable_outcome(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        with self._server(status=404) as url:
            router, registry, _ = _build_router()
            del registry.execute
            search = (f'<a class="result__a" href="{url}">Opening hours</a>'
                      '<a class="result__snippet"></a>').encode()
            with mock.patch.dict("os.environ", {"SERPER_API_KEY": ""}), \
                    mock.patch("urllib.request.urlopen", return_value=io.BytesIO(search)):
                result = router.route("search the web for observatory opening hours")
            self.assertIn("no readable text", result.text)
            self.assertEqual(router._llm.synthesis_calls, 0)

    def test_a_link_only_summary_falls_back_to_the_retrieved_snippet(self):
        from intergen.tests.test_router_executes_recognised_search import _build_router

        fact = "The observatory opens at 09:00."
        with self._server(status=404) as url:
            router, registry, _ = _build_router()
            del registry.execute
            router._llm.stream = lambda *_args, **_kwargs: iter([
                "The opening time is available at " + url + "."])
            search = (f'<a class="result__a" href="{url}">Opening hours</a>'
                      f'<a class="result__snippet">{fact}</a>').encode()
            with mock.patch.dict("os.environ", {"SERPER_API_KEY": ""}), \
                    mock.patch("urllib.request.urlopen", return_value=io.BytesIO(search)):
                result = router.route("search the web for observatory opening hours")
            self.assertEqual(result.text, "From the search snippets:\n" + fact
                             + "\n\nSource: " + url)


if __name__ == "__main__":
    unittest.main()
