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


class WebSearchTool(BaseTool):
    """Search the web using DuckDuckGo or Serper."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information. Returns a list of results "
            "with titles, URLs, and snippets. Uses DuckDuckGo by default; "
            "optionally uses Serper.dev API for higher quality results."
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
        """Search using Serper.dev API."""
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

            lines = [f"Search results for: {query}\n"]
            for i, item in enumerate(organic[:num], 1):
                title = item.get("title", "")
                url = item.get("link", "")
                snippet = item.get("snippet", "")
                lines.append(f"{i}. {title}")
                lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet}")
                lines.append("")

            return ToolResult(
                call_id="", name=self.name,
                content="\n".join(lines),
                success=True,
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

            lines = [f"Search results for: {query}\n"]
            for i, (title, url, snippet) in enumerate(results, 1):
                lines.append(f"{i}. {title}")
                lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet}")
                lines.append("")

            return ToolResult(
                call_id="", name=self.name,
                content="\n".join(lines),
                success=True,
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
