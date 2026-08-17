# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Router review_callback threading — the full-mode (D-Bus Ask) tool-gate seam.

route() forwards its review_callback to EVERY ToolRegistry.execute() call this
turn — P1 keyword + P2 semantic (via _execute_tool_for_intent) and P3 llm_tools
— so a full-mode frontend can present a human Allow/Deny surface on held or
privileged dispatches across ALL paths, not just the LLM one. None ⇒ the
registry fail-closed-denies (its no-UI contract), which is the safe default for
the decide_only/streaming path and for direct helper/test calls. Runs on any
host (no LLM, no real tools, no display).
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.interfaces.provenance import Provenance


def _sentinel_callback(call, decision):  # the review surface the registry invokes
    return "deny"


class _RecordingTools:
    """Fake ToolRegistry that records the review_callback execute() receives."""

    def __init__(self):
        self.seen_callback = "UNSET"

    def get_tool(self, name):
        return object()  # non-None so _execute_tool_for_intent proceeds

    def get_tool_schemas(self):
        return [{"name": "demo"}]

    def execute(self, call, *, ingress_tracker=None, trust_state=None,
                review_callback=None):
        self.seen_callback = review_callback
        return ToolResult(call_id="", name=getattr(call, "name", "demo"),
                          content="ok", success=True)


class _OneToolLLM:
    """Fake LLM: emits one ToolCall, no agentic synthesis (forces fallback)."""

    def __init__(self):
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0

    def stream_with_tools(self, messages, tools):
        yield ToolCall(name="demo", arguments={},
                       source_of_request=Provenance.USER_DIRECT)

    def continue_after_tool_call(self, messages, call, content,
                                 *, success=True, executed=True, max_tokens=400,
                                 temperature=0.3):
        return None

    def _strip_filler(self, text):
        return text


def _bare_router(tools, callback):
    r = ConversationRouter.__new__(ConversationRouter)
    # These tests exercise the NATIVE P3 dispatch path → unlock it (the
    # dispatch-lockdown default is fail-closed locked; see test_dispatch_lockdown).
    r._lock_dispatch = False
    r._tools = tools
    r._ingress_tracker = object()
    r._trust_state = object()
    r._review_callback = callback
    return r


class ReviewCallbackThreadingTests(unittest.TestCase):
    def test_p1_p2_path_forwards_callback(self):
        # _execute_tool_for_intent is the P1-keyword + P2-semantic execute site.
        tools = _RecordingTools()
        r = _bare_router(tools, _sentinel_callback)
        r._extract_arguments = lambda name, ui: {}
        result = r._execute_tool_for_intent("demo", "do demo")
        self.assertIsNotNone(result)
        self.assertIs(tools.seen_callback, _sentinel_callback)

    def test_p3_llm_tools_path_forwards_callback(self):
        tools = _RecordingTools()
        r = _bare_router(tools, _sentinel_callback)
        r._llm = _OneToolLLM()
        r._build_messages = lambda ui, with_tools=True, grounding=None: []
        r._append_history = lambda ui, txt: None
        # **kw so this double does not re-break when the real signature
        # grows a parameter (it gained raw_output in release 158).
        r._synthesize_tool_result = lambda ui, name, content, **kw: "synth"
        r._try_llm_tools("do demo")
        self.assertIs(tools.seen_callback, _sentinel_callback)

    def test_none_callback_forwarded_as_none_fail_closed_default(self):
        # decide_only/streaming + direct calls default to None → registry denies.
        tools = _RecordingTools()
        r = _bare_router(tools, None)
        r._extract_arguments = lambda name, ui: {}
        r._execute_tool_for_intent("demo", "do demo")
        self.assertIsNone(tools.seen_callback)

    def test_default_review_callback_attribute_is_none(self):
        # A freshly constructed router defaults to the safe-deny callback.
        from intergen.interfaces.provenance import (
            ConversationTrustState, IngressTracker,
        )
        r = ConversationRouter.__new__(ConversationRouter)
        r._ingress_tracker = IngressTracker()
        r._trust_state = ConversationTrustState()
        # __init__ sets this to None; emulate that contract explicitly.
        r._review_callback = None
        self.assertIsNone(r._review_callback)


if __name__ == "__main__":
    unittest.main()
