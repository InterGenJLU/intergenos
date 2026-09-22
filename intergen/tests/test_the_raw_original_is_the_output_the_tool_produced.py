# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen last --raw` shows what the tool printed, not a summary of it.

The command's promise is that the normalised prose a person read is one step
from the ground truth it was made from. On the model-driven tool route that
promise was not kept: the route declared its raw original only when the tool
was a web search, so a turn that ran a command left the field empty while the
command's own output sat unused in the turn's dispatch results. `--raw` then
printed the summary again and said the turn "was a direct answer, not
summarised tool output" — a statement about a turn that had in fact dispatched
a command, and it was wrong.

What is pinned here:

  * the model-driven tool route carries the output of the dispatch it answered
    from, for any tool, with a web search's rendered listing unchanged;
  * the reader used by the command line takes the route's declaration first and
    the dispatch results second, ignoring a dispatch that did not run or did
    not succeed;
  * `last --raw` on a turn that ran a command prints the command's output;
  * when nothing behind an answer was recorded, the line says only that.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.router import ConversationRouter

FREE_OUTPUT = ("               total        used        free\n"
               "Mem:            62Gi       8.7Gi        33Gi\n")
SUMMARY = "RAM: 67 GB total, 9.3 GB used, 58 GB available."


class _Synthesis:
    def __init__(self, text):
        self.text = text
        self.tokens_prompt = 0
        self.tokens_completion = 0


class _LLM:
    """Emits one tool call, then answers from its result."""

    def __init__(self, call):
        self._call = call

    def stream_with_tools(self, messages, tools=None):
        yield self._call

    def continue_after_tool_call(self, messages, call, output, success=True,
                                 executed=True):
        return _Synthesis(SUMMARY)


def _router_for(call, result):
    r = ConversationRouter.__new__(ConversationRouter)
    r._lock_dispatch = False
    r._turn_forbids_tools = False
    r._llm = _LLM(call)
    # A router built this way gets a conversation of its own (see _conv).
    r._review_callback = None
    r._append_history = lambda *a, **k: None
    r._grounding_context = lambda *a, **k: None
    r._build_messages = lambda *a, **k: []
    r._synth_renderer = lambda used_llm: "llm_synth"
    tools = mock.Mock()
    tools.get_tool_schemas.return_value = [{"name": "run_command"}]
    tools.execute.return_value = result
    r._tools = tools
    return r


class TheToolRouteCarriesItsRawTests(unittest.TestCase):

    def _route(self, result):
        call = ToolCall(call_id="c1", name="run_command",
                        arguments={"command": "free -h"},
                        source_of_request="model")
        r = _router_for(call, result)
        return r._try_llm_tools("how much memory does this machine have?")

    def test_a_command_turn_carries_the_command_output(self):
        res = self._route(ToolResult(call_id="c1", name="run_command",
                                     content=FREE_OUTPUT, success=True,
                                     executed=True))
        self.assertEqual(res.text, SUMMARY)
        self.assertEqual(res.full_output, FREE_OUTPUT)

    def test_a_web_search_still_carries_its_rendered_listing(self):
        listing = "1. A page – https://example.com — a snippet"
        res = self._route(ToolResult(call_id="c1", name="web_search",
                                     content=listing, success=True,
                                     executed=True))
        self.assertEqual(res.full_output, listing)

    def test_a_dispatch_that_did_not_run_carries_nothing(self):
        res = self._route(ToolResult(call_id="c1", name="run_command",
                                     content="denied at the gate",
                                     success=False, executed=False))
        self.assertEqual(res.full_output, "")


class TheReaderTakesTheOutputThatExistsTests(unittest.TestCase):

    def test_the_route_declaration_comes_first(self):
        self.assertEqual(
            cli._raw_original({"full_output": FREE_OUTPUT,
                               "tool_results": [{"name": "run_command",
                                                 "content": "something else",
                                                 "success": True,
                                                 "executed": True}]}),
            FREE_OUTPUT)

    def test_the_dispatch_results_are_the_second_witness(self):
        self.assertEqual(
            cli._raw_original({"full_output": "",
                               "tool_results": [{"name": "run_command",
                                                 "content": FREE_OUTPUT,
                                                 "success": True,
                                                 "executed": True}]}),
            FREE_OUTPUT)

    def test_a_dispatch_that_failed_or_never_ran_is_not_an_original(self):
        for tr in ({"name": "run_command", "content": "denied",
                    "success": False, "executed": False},
                   {"name": "run_command", "content": "error text",
                    "success": False, "executed": True}):
            self.assertEqual(
                cli._raw_original({"full_output": "", "tool_results": [tr]}), "")

    def test_a_turn_with_no_dispatch_has_no_original(self):
        self.assertEqual(
            cli._raw_original({"full_output": "", "tool_results": []}), "")


class TheCommandLineShowsItTests(unittest.TestCase):

    def _deliver_then_last(self, payload, args):
        with TemporaryDirectory() as d:
            cache = Path(d) / "last-answer.json"
            with mock.patch.object(cli, "_last_answer_path",
                                   return_value=cache):
                with redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()):
                    cli._deliver_answer(payload)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    cli.cmd_last(args)
                return out.getvalue(), err.getvalue(), json.loads(
                    cache.read_text())

    def test_raw_prints_what_the_command_printed(self):
        payload = {"response": SUMMARY, "handled": True, "full_output": "",
                   "tool_results": [{"call_id": "c1", "name": "run_command",
                                     "success": True, "executed": True,
                                     "blocked": False, "content": FREE_OUTPUT}]}
        out, _err, cached = self._deliver_then_last(payload, ["--raw"])
        self.assertIn("Mem:            62Gi", out)
        self.assertNotIn("RAM: 67 GB total", out)
        self.assertEqual(cached["full_output"], FREE_OUTPUT)

    def test_without_raw_the_summary_is_still_the_default(self):
        payload = {"response": SUMMARY, "handled": True, "full_output": "",
                   "tool_results": [{"call_id": "c1", "name": "run_command",
                                     "success": True, "executed": True,
                                     "blocked": False, "content": FREE_OUTPUT}]}
        out, _err, _cached = self._deliver_then_last(payload, [])
        self.assertIn(SUMMARY, out)
        self.assertNotIn("Mem:            62Gi", out)

    def test_a_turn_with_nothing_behind_it_says_only_that(self):
        payload = {"response": "Paris is the capital of France.",
                   "handled": True, "full_output": "", "tool_results": []}
        out, err, _cached = self._deliver_then_last(payload, ["--raw"])
        self.assertIn("Paris is the capital of France.", out)
        self.assertNotIn("direct answer", err)


if __name__ == "__main__":
    unittest.main()
