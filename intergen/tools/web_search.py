# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web search — DuckDuckGo (free, no API key) with Serper upgrade path.

Default: DuckDuckGo HTML scraping (no API key needed, works offline-first).
Optional: Serper.dev API for higher quality results (requires API key).

The user chooses whether to enable cloud search — consistent with
InterGen's "local first, cloud optional" philosophy.
"""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
SERPER_URL = "https://google.serper.dev/search"
USER_AGENT = "InterGen/0.1 (InterGenOS AI Assistant)"

# G3-22 structured returns: web_search is self-bounded (<=10 results) so it
# rarely overflows, but it is the canonical UNTRUSTED-external surface. When the
# full results would overflow the local 2B, hand the MODEL a trimmed summary
# (title + url + a capped snippet — less bulk AND a smaller injection surface)
# and keep the full snippets for the USER. Below the threshold the model gets
# the full content unchanged (model_summary stays None).
_MODEL_OVERFLOW_CHARS = 4000   # matches continue_after_tool_call's floor
_SNIPPET_CAP = 160
_PAGE_MAX_BYTES = 128 * 1024
_PAGE_TEXT_CHARS = 8000
_PAGE_MODEL_CHARS = 2400
_PAGE_TIMEOUT = 8
_PAGE_REDIRECTS = 3


class _PageText(HTMLParser):
    """Collect visible body text without scripts, styling or navigation."""

    _IGNORED = frozenset({
        "head", "script", "style", "nav", "footer", "header", "svg", "template",
    })
    _BLOCKS = frozenset({
        "p", "div", "article", "section", "li", "br", "h1", "h2", "h3", "h4",
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._IGNORED:
            self.ignored.append(tag)
        if not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.ignored.remove(tag)
        if not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    def text(self):
        return "\n".join(
            line for part in "".join(self.parts).splitlines()
            if (line := " ".join(part.split()))
        )


class _DeadlineReader(io.RawIOBase):
    """Apply the remaining budget to every receive, including HTTP headers."""

    def __init__(self, sock, remaining):
        self._sock = sock
        self._remaining = remaining
        self._raw = sock.makefile("rb", buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self._sock.settimeout(self._remaining())
        return self._raw.readinto(buffer)

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


def _fetch_page_text(url: str) -> tuple[str, str, bool]:
    """Fetch public page text with byte and socket-I/O bounds.

    DNS uses the host resolver; its latency is not a socket timeout.
    """
    deadline = time.monotonic() + _PAGE_TIMEOUT

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("page fetch timed out")
        return budget

    for hop in range(_PAGE_REDIRECTS + 1):
        if len(url) > 2048 or any(ord(char) < 32 for char in url):
            raise ValueError("invalid page URL")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("page URL must name an HTTP or HTTPS host")
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in {80, 443}:
            raise ValueError("page URL uses an unsupported port")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(
                not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("page URL does not resolve exclusively to public addresses")
        # Connect to the checked numeric address, keeping the original hostname
        # for TLS and Host. A second hostname lookup cannot change the destination.
        address = addresses[0][4][0]
        connection = http.client.HTTPConnection(host, port, timeout=remaining())
        sock = socket.create_connection((address, port), timeout=remaining())
        response = None
        try:
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                sock.settimeout(remaining())
                sock = context.wrap_socket(sock, server_hostname=host)
            connection.sock = sock
            sock.settimeout(remaining())
            path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request("GET", path, headers={
                "Host": parsed.netloc, "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain", "Accept-Encoding": "identity",
                "Connection": "close",
            })

            def bounded_response(*args, **kwargs):
                result = http.client.HTTPResponse(*args, **kwargs)
                result.fp.close()
                result.fp = io.BufferedReader(_DeadlineReader(sock, remaining))
                return result

            connection.response_class = bounded_response
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == _PAGE_REDIRECTS:
                    raise ValueError("page redirect limit reached")
                url = urllib.parse.urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"page returned HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            if content_type.split(";", 1)[0].strip() not in {"text/html", "text/plain"}:
                raise ValueError("page did not return HTML or plain text")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("page returned an unsupported content encoding")
            body = bytearray()
            while len(body) <= _PAGE_MAX_BYTES and not response.isclosed():
                sock.settimeout(remaining())
                chunk = response.read1(min(8192, _PAGE_MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            truncated = len(body) > _PAGE_MAX_BYTES
            charset = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type)
            encoding = charset.group(1) if charset else "utf-8"
            text = bytes(body[:_PAGE_MAX_BYTES]).decode(encoding, errors="replace")
            if content_type.startswith("text/html"):
                parser = _PageText()
                parser.feed(text)
                text = parser.text()
            truncated = truncated or len(text) > _PAGE_TEXT_CHARS
            return url, text[:_PAGE_TEXT_CHARS].strip(), truncated
        finally:
            if response is not None:
                response.close()
            connection.close()
            sock.close()
    raise ValueError("page redirect limit reached")


def render_search_results(query: str, results: list[tuple[str, str, str]]):
    """Render (content, model_summary) from a list of (title, url, snippet).

    content = the full, user-facing listing (unchanged shape). model_summary =
    a concise per-result title/url/trimmed-snippet line, ONLY when the full
    content would overflow the 2B; None otherwise. Purely factual — no
    model-steering (that lives in LLMRouter._SYNTHESIS_RULES). Both fields are
    untrusted ingress: the dispatcher scans + spotlights each at the trust
    boundary (intergen-structured-tool-returns-design.md §7).
    """
    lines = [f"Search results for: {query}\n"]
    for i, (title, url, snippet) in enumerate(results, 1):
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    content = "\n".join(lines)

    if len(content) <= _MODEL_OVERFLOW_CHARS:
        return content, None

    sum_lines = [f"{len(results)} results for: {query}"]
    for i, (title, url, snippet) in enumerate(results, 1):
        trimmed = (snippet[:_SNIPPET_CAP] + "…"
                   if len(snippet) > _SNIPPET_CAP else snippet)
        line = f"{i}. {title} — {url}"
        if trimmed:
            line += f" — {trimmed}"
        sum_lines.append(line)
    return content, "\n".join(sum_lines)


class WebSearchTool(BaseTool):
    """Search the web using DuckDuckGo or Serper."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the INTERNET for general knowledge, documentation, or "
            "current information from the web. Returns titles, URLs, and "
            "snippets (DuckDuckGo by default; Serper.dev if configured). Use "
            "this only for information from the web — NOT for questions about "
            "THIS machine's own state (printers, disk, services, files): use "
            "run_command or the file tools for those."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 10).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the web search."""
        query = arguments.get("query", "").strip()
        num_results = min(arguments.get("num_results", 5), 10)
        log.info("Web search: %s (max %d results)", query, num_results)

        if not query:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: empty search query", success=False,
            )

        # Try Serper first if API key is available
        serper_key = os.environ.get("SERPER_API_KEY")
        if serper_key:
            result = self._search_serper(query, num_results, serper_key)
            if result is not None:
                return result
            log.warning("Serper search failed, falling back to DuckDuckGo")

        # DuckDuckGo fallback
        return self._search_ddg(query, num_results)

    def _search_serper(self, query: str, num: int, api_key: str) -> ToolResult | None:
        """Search using Serper.dev API.

        Defense-in-depth on the api_key argument: the caller in `execute()`
        already gates this on `if serper_key:`, but the function should also
        defend against being called with None/empty so a future refactor
        that bypasses the env-var check produces a clear configuration
        error rather than a confusing "X-API-KEY: None" HTTP response from
        Serper itself.
        """
        if not api_key:
            log.warning("Serper search invoked without API key configured")
            return ToolResult(
                call_id="", name=self.name,
                content=("Error: Serper API key not configured. "
                         "Set the SERPER_API_KEY environment variable, or "
                         "InterGen will fall back to DuckDuckGo search."),
                success=False,
            )
        try:
            data = json.dumps({"q": query, "num": num}).encode()
            req = urllib.request.Request(
                SERPER_URL,
                data=data,
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())

            organic = result.get("organic", [])
            if not organic:
                return None

            results = [
                (item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
                for item in organic[:num]
            ]
            content, model_summary = self._render_with_page(query, results)
            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=True,
                model_summary=model_summary,
            )
        except Exception as e:
            log.warning("Serper API error: %s", e)
            return None

    def _search_ddg(self, query: str, num: int) -> ToolResult:
        """Search using DuckDuckGo HTML interface."""
        try:
            params = urllib.parse.urlencode({"q": query}).encode()
            req = urllib.request.Request(
                DDG_URL,
                data=params,
                headers={"User-Agent": USER_AGENT},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            results = self._parse_ddg_html(html, num)
            if not results:
                return ToolResult(
                    call_id="", name=self.name,
                    content=f"No results found for: {query}",
                    success=True,
                )

            content, model_summary = self._render_with_page(query, results)
            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=True,
                model_summary=model_summary,
            )
        except Exception as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Search failed: {e}",
                success=False,
            )

    def _render_with_page(self, query, results):
        """Carry page evidence through the same ingress boundary as snippets."""
        content, summary = render_search_results(query, results)
        if not results:
            return content, summary
        source = results[0][1]
        try:
            source, text, truncated = _fetch_page_text(source)
            if not text:
                raise ValueError("page contains no readable body text")
            label = "Page excerpt (truncated)" if truncated else "Page excerpt"
            page = f"Source: {source}\n{label}:\n{text}"
            model_page = f"Source: {source}\nPage excerpt:\n{text[:_PAGE_MODEL_CHARS]}"
        except Exception as exc:
            log.warning("Search page fetch unavailable: %s", exc)
            source = " ".join(source.split())
            page = f"Source: {source}\nThe linked page did not provide readable text."
            model_page = page
        # The source and retrieval outcome precede the external text, so the
        # answer renderer reads this header before any page or snippet content.
        content = page + "\n\n" + content
        if len(content) > _MODEL_OVERFLOW_CHARS:
            compact, _ = render_search_results(
                query, [(title, url, snippet[:_SNIPPET_CAP])
                        for title, url, snippet in results[:3]])
            summary = (model_page + "\n\n" + compact)[:_MODEL_OVERFLOW_CHARS]
        return content, summary

    def _parse_ddg_html(self, html: str, num: int) -> list[tuple[str, str, str]]:
        """Parse DuckDuckGo HTML results page.

        Returns list of (title, url, snippet) tuples.
        """
        results = []

        # DuckDuckGo wraps results in <a class="result__a"> tags
        # and snippets in <a class="result__snippet"> tags
        result_blocks = re.findall(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
            html, re.DOTALL,
        )

        for url, title, snippet in result_blocks[:num]:
            # Clean HTML tags from title and snippet
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()

            # DuckDuckGo wraps URLs in a redirect — extract the real URL
            if "uddg=" in url:
                match = re.search(r"uddg=([^&]+)", url)
                if match:
                    url = urllib.parse.unquote(match.group(1))

            if title and url:
                results.append((title, url, snippet))

        return results
