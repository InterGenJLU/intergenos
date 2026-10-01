# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool-enabled turn whose model request got no response must say so.

WHAT IS ALREADY PINNED, ONE METHOD AWAY. ``LLMRouter.stream()`` answers a
transport failure with three things (llm.py, the ``except Exception`` block
under the connect): a single log line per down-episode, a recorded transport
failure (``note_transport_failure``), and a ``("model", "no_response")`` row on
the decision trace. Its own comment names what the record and the row are for:

    Measured 2026-08-26: an engine died mid-run, every subsequent call was
    refused, and the scenario harness graded four scenarios PASS with nothing
    behind them.

and the record is what the serving floor reads to choose between
"my model server isn't running" and "could you rephrase" — advice that cannot
help, because rephrasing does not start a server.
``intergen/tests/test_a_refused_connection_is_not_retried.py`` holds that
contract for the conversational path.

WHAT IS MEASURED HERE. ``stream_with_tools()`` — the path every tool-enabled
turn takes, on the web interface the ONLY model call the turn makes — answers
the same failure with a log line and nothing else. The generator ends with no
tokens, so nothing downstream can tell a dead engine from a bad generation:

  * the transport failure is NOT recorded, so the router still reports the
    endpoint as healthy and the last-resort text tells the person to rephrase;
  * no ``("model", "no_response")`` row is written, so a refused tool turn is
    indistinguishable, on the trace, from one the model answered — the exact
    2026-08-26 shape the row was added to close;
  * the same is true of the non-400 status branch two lines above.

WHAT IS PINNED HERE: a refused tool request, and a tool request answered with
a non-400 status, are recorded and traced exactly as the conversational path
records and traces them. The last test is the CONTROL: the identical failure
on ``stream()``, proving these assertions detect a true positive.
"""

from __future__ import annotations

import unittest
import urllib.error
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.types import Message, MessageRole, ToolSchema
from intergen.llm import LLMRouter

# The measured 2026-08-26 shape: nothing is listening on the endpoint, so the
# connect is refused before a single byte is written.
REFUSED = urllib.error.URLError(
    ConnectionRefusedError(111, "Connection refused"))
# The other branch the same block owns: the server answered, with a status that
# is not the 400 the context-overflow retry exists for.
STATUS_500 = urllib.error.HTTPError(
    "http://127.0.0.1:8080/v1/chat/completions", 500,
    "Internal Server Error", {}, None)

_TOOLS = [ToolSchema(name="run_command",
                     description="Run a shell command",
                     parameters={"type": "object",
                                 "properties": {"command": {"type": "string"}},
                                 "required": ["command"]})]
_MSGS = [Message(role=MessageRole.SYSTEM, content="You are InterGen."),
         Message(role=MessageRole.USER, content="is sshd enabled?")]


class _Failing:
    """A urlopen stand-in that always fails the same way, and counts attempts."""

    def __init__(self, exc):
        self.exc = exc
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        raise self.exc


class ARefusedToolRequestTests(unittest.TestCase):

    def setUp(self):
        # tool_calling is the default (llm.py: config.get("tool_calling", True));
        # named here so the turn cannot fall through to the plain stream() path.
        self.llm = LLMRouter({"tool_calling": True})

    def _one_tool_turn(self, exc):
        """Drive ONE tool-enabled turn whose transport always fails.

        Returns what the generator yielded and the trace rows it wrote. The
        assertLogs wrapper is not decoration: it proves the run actually
        reached the failure branch under test, and keeps its line out of the
        captured output.
        """
        transport = _Failing(exc)
        rows: list[tuple] = []
        with self.assertLogs("intergen.llm", level="ERROR"):
            with mock.patch("urllib.request.urlopen", transport), \
                 mock.patch.object(
                     llm_module.glass, "emit",
                     side_effect=lambda *a, **k: rows.append((a, k))):
                out = list(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        return out, rows

    @staticmethod
    def _no_response_rows(rows):
        return [k for a, k in rows if a[:2] == ("model", "no_response")]

    # ---- the precondition ---------------------------------------------------

    def test_the_refused_tool_request_yields_no_tokens(self):
        """Not the defect — the state the defect is invisible in."""
        out, _rows = self._one_tool_turn(REFUSED)
        self.assertEqual(out, [], "a refused connect produced output")

    # ---- the defect ---------------------------------------------------------

    def test_a_refused_tool_request_is_recorded_and_not_reported_as_a_bad_generation(self):
        """The whole finding in one case: recorded, traced, and named honestly.

        Nothing was listening, so there was no generation to judge. All three
        assertions are one fact — that the tool path, like the conversational
        path, wrote down WHY there were no tokens.
        """
        _out, rows = self._one_tool_turn(REFUSED)

        self.assertIsNotNone(
            self.llm.transport_error,
            "a refused tool request left the endpoint recorded as healthy, so "
            "nothing downstream can tell a dead engine from a bad generation")

        self.assertEqual(
            len(self._no_response_rows(rows)), 1,
            "no ('model', 'no_response') row for a model call that never "
            "happened — on the trace this refused turn reads exactly like an "
            "answered one (rows written: %r)" % [a[:2] for a, _k in rows])

        self.assertEqual(
            self.llm._servable_text("", "empty"),
            self.llm._MODEL_SERVER_DOWN_FALLBACK,
            "the serving floor picked the 'could you rephrase' text for a "
            "server that is not running; rephrasing does not start a server")

    def test_the_last_resort_text_names_the_server_not_the_phrasing(self):
        """The clause above, on its own line, so the red is unambiguous.

        This reads the ROUTER's state after the refused tool request, which is
        what the serving floor reads. It is the state that is wrong, not only
        the sentence: a caller that consults the floor without first making a
        conversational call gets the rephrase advice for a dead server.
        """
        self._one_tool_turn(REFUSED)
        self.assertEqual(
            self.llm._servable_text("", "empty"),
            self.llm._MODEL_SERVER_DOWN_FALLBACK,
            "after a refused tool request the floor still offers the text for "
            "a bad generation")

    def test_the_recorded_failure_names_which_kind_it_was(self):
        """The ladder reads the kind, not the message text: a refused connect
        must not be retried, and it can only be known as refused if it was
        recorded as refused."""
        self._one_tool_turn(REFUSED)
        self.assertEqual(self.llm._transport_error_kind, "refused")

    def test_the_trace_row_names_the_endpoint_and_the_kind(self):
        _out, rows = self._one_tool_turn(REFUSED)
        row = self._no_response_rows(rows)
        self.assertTrue(row, "no no_response row to read")
        detail = row[0]["detail"]
        self.assertEqual(detail.get("transport"), "refused")
        self.assertEqual(detail.get("endpoint"), self.llm._endpoint)

    def test_a_non_400_status_is_recorded_and_traced_too(self):
        """The branch two lines above the one this finding quotes: the server
        answered with a status, the turn yields nothing, and the same record
        and row are owed."""
        _out, rows = self._one_tool_turn(STATUS_500)
        self.assertIsNotNone(
            self.llm.transport_error,
            "a non-400 status on the tool path left no record of why the turn "
            "produced nothing")
        self.assertEqual(
            len(self._no_response_rows(rows)), 1,
            "no no_response row for a tool request answered with status 500")

    # ---- the control --------------------------------------------------------

    def test_the_conversational_path_does_all_three(self):
        """THE CONTROL. The identical failure through stream(), which already
        holds this contract. If this one fails, the assertions above are
        measuring the harness rather than the code."""
        transport = _Failing(REFUSED)
        rows: list[tuple] = []
        with self.assertLogs("intergen.llm", level="ERROR"):
            with mock.patch("urllib.request.urlopen", transport), \
                 mock.patch.object(
                     llm_module.glass, "emit",
                     side_effect=lambda *a, **k: rows.append((a, k))):
                out = list(self.llm.stream(_MSGS))

        self.assertEqual(out, [])
        self.assertIsNotNone(self.llm.transport_error)
        self.assertEqual(len(self._no_response_rows(rows)), 1)
        self.assertEqual(self.llm._servable_text("", "empty"),
                         self.llm._MODEL_SERVER_DOWN_FALLBACK)


if __name__ == "__main__":
    unittest.main()
