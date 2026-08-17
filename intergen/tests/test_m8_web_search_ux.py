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

import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.router import (ConversationRouter, detect_file_lifecycle_intent,
                             _WEB_CAP_Q_RE)
from intergen.tools.web_search import render_search_results
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


if __name__ == "__main__":
    unittest.main()
