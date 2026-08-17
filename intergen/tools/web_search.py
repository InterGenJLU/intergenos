# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web search — DuckDuckGo (free, no API key) with Serper upgrade path.

Default: DuckDuckGo HTML scraping (no API key needed, works offline-first).
Optional: Serper.dev API for higher quality results (requires API key).

The user chooses whether to enable cloud search — consistent with
InterGen's "local first, cloud optional" philosophy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
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
            content, model_summary = render_search_results(query, results)
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

            content, model_summary = render_search_results(query, results)
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
