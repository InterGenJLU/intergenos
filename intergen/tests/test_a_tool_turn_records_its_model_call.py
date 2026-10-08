# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool-enabled turn's model call is on the decision trace.

The conversational path, ``LLMRouter.stream()``, writes two rows for every model
call it makes: ``("model", "first_token")`` when the first token arrives and
``("model", "complete")`` when the call has finished, with the endpoint, the
token counts, the finish reason and the text. ``intergen glass`` reads them to
show what the model was asked and what it answered.

``stream_with_tools()`` makes its own model call for every tool-enabled turn,
and on the web interface it is the only model call such a turn makes. It wrote
neither row, so a turn the model answered with a tool call, or with text, left
nothing on the trace to say the model was asked at all.

Pinned here: a structured tool call, a text answer and a tool the model was not
offered each leave exactly one ``first_token`` and one ``complete`` row, and
``complete`` names the tool the model asked for and is written before the tool
call is handed to the caller. The last three cases are controls: the fallback
to ``stream()`` when no tools are offered writes its own single ``complete``, a
request that got no response writes ``no_response`` and no ``complete``, and
``stream()`` is unchanged.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.types import Message, MessageRole, ToolCall, ToolSchema
from intergen.llm import LLMRouter

_TOOLS = [ToolSchema(name="run_command",
                     description="Run a shell command",
                     parameters={"type": "object",
                                 "properties": {"command": {"type": "string"},
                                                "source_of_request": {"type": "string"}},
                                 "required": ["command"]})]
_MSGS = [Message(role=MessageRole.SYSTEM, content="You are InterGen."),
         Message(role=MessageRole.USER, content="is sshd enabled?")]
_TIMINGS = {"prompt_n": 41, "predicted_n": 9, "prompt_ms": 12.0,
            "predicted_ms": 30.0}


class _Response:
    """A urllib response stand-in: SSE byte lines, then close."""

    def __init__(self, chunks):
        self._lines = [b"data: " + json.dumps(c).encode() for c in chunks]
        self._lines.append(b"data: [DONE]")
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def _tool_call_chunks(name="run_command"):
    args = json.dumps({"command": "systemctl is-enabled sshd",
                       "source_of_request": "user_direct"})
    return [
        {"choices": [{"delta": {"tool_calls": [
            {"id": "call-1", "function": {"name": name, "arguments": ""}}]},
            "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [
            {"function": {"arguments": args}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "timings": _TIMINGS},
    ]


def _text_chunks():
    return [
        {"choices": [{"delta": {"content": "sshd is "}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "enabled."}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "timings": _TIMINGS},
    ]


class AToolTurnIsOnTheDecisionTrace(unittest.TestCase):

    def setUp(self):
        # tool_calling named so the turn cannot fall through to stream().
        self.llm = LLMRouter({"tool_calling": True})
        self.rows: list[tuple] = []

    def _record(self):
        return mock.patch.object(
            llm_module.glass, "emit",
            side_effect=lambda *a, **k: self.rows.append((a, k)))

    def _turn(self, chunks, *, tools=_TOOLS, stop_at_tool_call=False):
        response = _Response(chunks)
        out = []
        with mock.patch("urllib.request.urlopen", lambda *_a, **_k: response), \
             self._record():
            gen = self.llm.stream_with_tools(_MSGS, tools=tools)
            for item in gen:
                out.append(item)
                if stop_at_tool_call and isinstance(item, ToolCall):
                    gen.close()
                    break
        return out

    def _model_rows(self, event):
        return [k for a, k in self.rows if a[:2] == ("model", event)]

    # ---- the defect ---------------------------------------------------------

    def test_a_structured_tool_call_writes_first_token_and_complete(self):
        out = self._turn(_tool_call_chunks())
        self.assertEqual([type(i) for i in out], [ToolCall])
        self.assertEqual(len(self._model_rows("first_token")), 1,
                         "the tool turn's model call wrote no first_token row")
        complete = self._model_rows("complete")
        self.assertEqual(len(complete), 1,
                         "the tool turn's model call wrote no complete row")
        detail = complete[0]["detail"]
        self.assertEqual(detail["tool_call"], "run_command")
        self.assertEqual(detail["finish_reason"], "tool_calls")
        self.assertEqual(detail["endpoint"], self.llm._endpoint)
        self.assertEqual(detail["prompt_tok_count"], 41)
        self.assertEqual(detail["completion_tok_count"], 9)
        self.assertIsNotNone(complete[0]["dur_ms"])

    def test_a_text_answer_on_the_tool_path_writes_first_token_and_complete(self):
        out = self._turn(_text_chunks())
        self.assertEqual("".join(out), "sshd is enabled.")
        self.assertEqual(len(self._model_rows("first_token")), 1)
        complete = self._model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["detail"]["text"], "sshd is enabled.")
        self.assertIsNone(complete[0]["detail"]["tool_call"])
        self.assertEqual(complete[0]["detail"]["finish_reason"], "stop")

    def test_a_caller_that_stops_at_the_tool_call_still_leaves_complete(self):
        out = self._turn(_tool_call_chunks(), stop_at_tool_call=True)
        self.assertEqual([type(i) for i in out], [ToolCall])
        self.assertEqual(len(self._model_rows("complete")), 1,
                         "complete must be written before the tool call is "
                         "handed over, not after the caller reads on")

    def test_a_tool_the_model_was_not_offered_is_still_recorded_as_asked_for(self):
        with self.assertLogs("intergen.llm", level="WARNING"):
            out = self._turn(_tool_call_chunks(name="format_disk"))
        self.assertEqual(out, [], "a tool that was not offered was handed over")
        complete = self._model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["detail"]["tool_call"], "format_disk")

    # ---- the controls -------------------------------------------------------

    def test_with_no_tools_offered_the_fallback_writes_one_complete(self):
        """CONTROL: the fallback to stream() writes its own rows, once."""
        self._turn(_text_chunks(), tools=[])
        self.assertEqual(len(self._model_rows("complete")), 1)

    def test_a_request_that_got_no_response_writes_no_complete(self):
        """CONTROL: the failure path keeps its own row and gains none."""
        refused = urllib.error.URLError(
            ConnectionRefusedError(111, "Connection refused"))

        def _refuse(*_a, **_k):
            raise refused

        with self.assertLogs("intergen.llm", level="ERROR"):
            with mock.patch("urllib.request.urlopen", _refuse), self._record():
                out = list(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        self.assertEqual(out, [])
        self.assertEqual(len(self._model_rows("no_response")), 1)
        self.assertEqual(self._model_rows("complete"), [])
        self.assertEqual(self._model_rows("first_token"), [])

    def test_the_conversational_path_is_unchanged(self):
        """CONTROL: stream() writes both rows, as it always has."""
        response = _Response(_text_chunks())
        with mock.patch("urllib.request.urlopen", lambda *_a, **_k: response), \
             self._record():
            out = list(self.llm.stream(_MSGS))
        self.assertEqual("".join(out), "sshd is enabled.")
        self.assertEqual(len(self._model_rows("first_token")), 1)
        self.assertEqual(len(self._model_rows("complete")), 1)


if __name__ == "__main__":
    unittest.main()
