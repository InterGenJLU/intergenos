# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool-enabled turn's answer is screened, a model call that fails part-way is
recorded, and only a served turn's answer is counted.

THREE RULES, each with its own cells.

1. The tool path's answer is screened. ``stream()`` runs the semantic-health
   screen over every completion it assembles; ``stream_with_tools()``, the model
   call every tool-enabled turn makes, ran none, so a corrupted text answer on a
   tool-enabled turn reached the person unflagged and never reached the
   engine-health counter. The shipped readers of the tool path are measured
   here as well as the call itself: the router's tool route, the web server's
   tool route and the daemon's warm-up. What a reader DOES with a flag is its
   own rule and is not changed here; the cells pin that the answer it hands on
   is the one it was handed, except the router's tool route, which now meets a
   flagged answer with the reaction ladder's first rung (its rule and cells are
   in test_a_flagged_tool_route_answer_meets_the_first_rung.py).

2. A model call that fails part-way through its stream is recorded, on both
   paths. A read that failed after the first token left ``first_token`` on the
   trace and nothing after it. Now the call's ``complete`` row is written with
   the text that arrived and ``finish_reason`` "error" naming the failure; when
   nothing had arrived the call is a ``no_response``, recorded the one way every
   such call is. The failure itself still reaches the caller unchanged. A server
   that closes its stream before finishing the reply raises nothing at all — the
   transport reads a chunked stream cut short as an ordinary end — and its call
   read as a finished one; it is recorded the same two ways.

3. Only a served turn's answer enters the engine-health window. Three flags in
   five generations raise an alarm that tells a person their machine is serving
   garbage. The daemon's start-up and warm-up are bound to the boot's
   identifier so their rows can be joined to it, and the screen's test — was a
   turn identifier present — read that binding as a served turn, so every
   warm-up reply ("Hi", which short_nonsense flags, correctly) entered the
   window at every boot. Screening the tool path would have added the tool
   warm-up's reply as a second.

   The test is the turn's own terminal cell, and it is read for its VALUE: a
   turn is served until it has ended. Read for its presence, two more states
   counted as served. A worker thread bound into a turn keeps the turn's cell
   after the turn has ended (the route deadline ends a turn and abandons its
   routing thread, which runs on), and a scope() entered inside a turn kept the
   turn's cell for work that is not the turn's answer. And a turn's ending was
   claimed only by a glass writer that was switched on, so with
   INTERGEN_GLASS=0 a turn never read as ended.

Cells marked CONTROL hold before the change and after it; every other cell
fails before it. For the cells of rule 3 that name a turn that has ended, a
scope entered inside a turn or a switched-off writer, the change is the one
that reads the cell's value.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

from intergen import glass
from intergen import llm as llm_module
from intergen.interfaces.types import (
    HardwareTierLevel,
    Message,
    MessageRole,
    RouteResult,
    ToolCall,
    ToolSchema,
)
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

# U+FFFD is broken unicode: charset_sanity flags it anywhere in a reply, and the
# sentence around it is a whole one, so it is the only flag.
CORRUPT = "The sshd service is enabled� and running."
CLEAN = "The sshd service is enabled and running."


class _Response:
    """A urllib response stand-in: SSE byte lines, then [DONE] — or, given a
    failure, the lines and then that failure raised by the read; or, with
    done=False, the lines and then an end with no marker, the way the transport
    reads a stream the server closed part-way."""

    def __init__(self, chunks, *, fail_with: Exception | None = None,
                 done: bool = True):
        self._lines = [b"data: " + json.dumps(c).encode() for c in chunks]
        if fail_with is None and done:
            self._lines.append(b"data: [DONE]")
        self._fail = fail_with
        self.closed = False

    def __iter__(self):
        yield from self._lines
        if self._fail is not None:
            raise self._fail

    def close(self):
        self.closed = True


def _text(*pieces, finish="stop"):
    chunks = [{"choices": [{"delta": {"content": p}, "finish_reason": None}]}
              for p in pieces]
    if finish:
        chunks.append({"choices": [{"delta": {}, "finish_reason": finish}],
                       "timings": _TIMINGS})
    return chunks


def _tool_call(name="run_command", *, finish=True):
    args = json.dumps({"command": "systemctl is-enabled sshd",
                       "source_of_request": "user_direct"})
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"id": "call-1", "function": {"name": name, "arguments": ""}}]},
            "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [
            {"function": {"arguments": args}}]}, "finish_reason": None}]},
    ]
    if finish:
        chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                       "timings": _TIMINGS})
    return chunks


class _Sink:
    """Stands in for the health monitor's record(), counting what reaches it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, flags) -> bool:
        self.calls.append(list(flags))
        return False


class _Case(unittest.TestCase):

    def setUp(self):
        # tool_calling named so a tool-enabled call cannot fall through to stream().
        self.llm = LLMRouter({"tool_calling": True})
        self.sink = _Sink()
        self.llm.set_semantic_flag_sink(self.sink)
        self.rows: list[tuple] = []
        lock = threading.Lock()

        def _record(*a, **k):
            with lock:
                self.rows.append((a, k))

        patcher = mock.patch.object(llm_module.glass, "emit", side_effect=_record)
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextmanager
    def answering(self, *responses):
        """The model endpoint answers each request with the next response."""
        pending = list(responses)

        def _urlopen(*_a, **_k):
            return pending.pop(0)

        with mock.patch("urllib.request.urlopen", _urlopen):
            yield

    def model_rows(self, event):
        return [k.get("detail") or {} for a, k in self.rows
                if a[:2] == ("model", event)]

    def model_events(self):
        return [a[1] for a, _k in self.rows if a[0] == "model"]

    def tool_turn(self, chunks, *, tools=_TOOLS):
        with self.answering(_Response(chunks)), glass.turn("turn-1", "test"):
            return list(self.llm.stream_with_tools(_MSGS, tools=tools))


# ---- rule 1: the tool path's answer is screened ---------------------------------

class TheToolPathAnswerIsScreened(_Case):

    def test_a_corrupted_text_answer_is_flagged_and_counted(self):
        out = self.tool_turn(_text("The sshd service is ",
                                   "enabled� and running."))
        self.assertEqual("".join(out), CORRUPT,
                         "the answer reaches the caller exactly as it did")
        flagged = self.model_rows("semantic_health")
        self.assertEqual(len(flagged), 1,
                         "a corrupted answer on a tool-enabled turn was not flagged")
        self.assertEqual(flagged[0]["flags"], ["charset_sanity"])
        self.assertEqual(flagged[0]["raw"], CORRUPT)
        self.assertEqual(self.llm._last_semantic_flags, ["charset_sanity"])
        self.assertEqual(self.sink.calls, [["charset_sanity"]],
                         "the engine-health counter never saw the answer")

    def test_the_flag_is_written_before_the_complete_row_as_on_the_conversational_path(self):
        # Every model row of the call, in order; the call's request row, which
        # the client writes from the bytes it sends, comes first.
        self.tool_turn(_text("The sshd service is ", "enabled� and running."))
        self.assertEqual(self.model_events(),
                         ["request", "first_token", "semantic_health", "complete"])

    def test_a_clean_text_answer_enters_the_window_clean(self):
        out = self.tool_turn(_text("The sshd service is ", "enabled and running."))
        self.assertEqual("".join(out), CLEAN)
        self.assertEqual(self.model_rows("semantic_health"), [])
        self.assertEqual(self.sink.calls, [[]],
                         "a served answer must be counted, clean or not")

    def test_text_handed_over_before_a_tool_call_is_screened(self):
        chunks = _text("Let me check� that. ", finish=None) + _tool_call()
        out = self.tool_turn(chunks)
        self.assertEqual([type(i) for i in out], [str, ToolCall])
        self.assertEqual(self.sink.calls, [["charset_sanity"]],
                         "the text the person was shown before the tool call "
                         "was not screened")

    def test_text_the_dialect_buffer_hands_over_is_screened(self):
        """Content that opens like a dialect tool call but is not one is held
        back and handed over at the end of the stream; it is still an answer."""
        text = "<function=ls is a command�"
        out = self.tool_turn(_text(text))
        self.assertEqual(out, [text])
        self.assertEqual(self.sink.calls, [["charset_sanity"]])

    def test_a_tool_call_alone_enters_nothing(self):
        """CONTROL: a tool call is not an answer; its synthesis is screened
        where it is made."""
        out = self.tool_turn(_tool_call())
        self.assertEqual([type(i) for i in out], [ToolCall])
        self.assertEqual(self.sink.calls, [])
        self.assertEqual(self.model_rows("semantic_health"), [])

    def test_a_refused_dialect_call_is_not_screened_as_an_answer(self):
        """CONTROL: the line the caller receives is the code's own words, not
        the model's, so it is not the model's answer to screen."""
        out = self.tool_turn(_text(
            "<function=format_disk><parameter=device>/dev/sda</parameter>"))
        self.assertEqual(len(out), 1)
        self.assertIn("came back malformed", out[0])
        self.assertEqual(self.sink.calls, [])

    def test_the_conversational_path_is_screened_as_before(self):
        """CONTROL"""
        with self.answering(_Response(_text("The sshd service is ",
                                            "enabled� and running."))), \
                glass.turn("turn-1", "test"):
            out = list(self.llm.stream(_MSGS))
        self.assertEqual("".join(out), CORRUPT)
        self.assertEqual(self.sink.calls, [["charset_sanity"]])

    def test_with_no_tools_offered_the_answer_is_screened_once(self):
        """CONTROL: the fall-through to stream() screens there, and only there."""
        self.tool_turn(_text("The sshd service is ", "enabled� and running."),
                       tools=[])
        self.assertEqual(self.sink.calls, [["charset_sanity"]])


# ---- rule 1, at the shipped readers of the tool path --------------------------

def _web_router(llm):
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    r._llm = llm
    r._last_semantic_score = None
    r.detach_conversation()
    return r


def _tool_router(llm, registry):
    """A bare router that reaches the tool route for real, as the router's own
    tool-route cases build it."""
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = mock.Mock()
    r._metrics = None
    r._state_cache = None
    r._memory = None
    r._first_interaction = False
    r._hardware_tier = HardwareTierLevel.TIER_2
    r._lock_dispatch = False
    r._turn_forbids_tools = False
    r._tools = registry
    r._max_history = 10
    r._conversation_history = []
    r._llm = llm
    r._review_callback = None
    return r


class _RecordingWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)


class _Tools:
    def get_tool_schemas(self):
        return list(_TOOLS)


class TheShippedReadersOfTheToolPath(_Case):

    def test_the_routers_tool_route(self):
        from intergen.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.discover_tools()
        router = _tool_router(self.llm, registry)
        with self.answering(_Response(_text("The sshd service is ",
                                            "enabled� and running."))), \
                glass.turn("turn-r", "dbus"):
            res = router._try_llm_tools("is sshd enabled?")
        self.assertTrue(res.handled)
        self.assertEqual(res.source, "llm_tools")
        # The route meets the flag with the ladder's first rung (its rule and
        # cells: test_a_flagged_tool_route_answer_meets_the_first_rung.py).
        from intergen.router import _SEMANTIC_INCOHERENCE_FALLBACK
        self.assertEqual(res.text, _SEMANTIC_INCOHERENCE_FALLBACK,
                         "the tool route served a flagged answer")
        flagged = self.model_rows("semantic_health")
        self.assertEqual([r["raw"] for r in flagged], [CORRUPT],
                         "the router's tool route served an unscreened answer")
        self.assertEqual(self.sink.calls, [["charset_sanity"]])

    def test_the_web_servers_tool_route(self):
        from intergen.web_server import ConnectionContext, WebServer
        router = _web_router(self.llm)
        server = WebServer(router=router, llm=self.llm, tools=_Tools(),
                           governance=None)
        ctx = ConnectionContext(client_id="c1", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())
        route = RouteResult(text="", source="llm_tools", handled=False)
        with self.answering(_Response(_text("The sshd service is ",
                                            "enabled� and running."))), \
                glass.turn("turn-w", "web"):
            asyncio.run(server._stream_llm_response(
                ctx, "turn-w", "is sshd enabled?", route))
        ends = [m for m in ctx.ws.sent if m.get("type") == "stream_end"]
        self.assertEqual(len(ends), 1, ctx.ws.sent)
        self.assertEqual(ends[0]["full_response"], CORRUPT,
                         "the web tool route hands on the answer it was handed")
        flagged = self.model_rows("semantic_health")
        self.assertEqual([r["raw"] for r in flagged], [CORRUPT],
                         "the web server's tool route served an unscreened answer")
        self.assertEqual(self.sink.calls.count(["charset_sanity"]), 1)


# ---- rule 2: a call that fails part-way is recorded ---------------------------

class ACallThatFailsPartWayIsRecorded(_Case):

    def _drain(self, gen):
        got = []
        for item in gen:
            got.append(item)
        return got

    def test_the_conversational_path_after_the_first_token(self):
        boom = ConnectionResetError(104, "Connection reset by peer")
        got = []
        with self.answering(_Response(_text("sshd is ", finish=None),
                                      fail_with=boom)):
            with self.assertRaises(ConnectionResetError) as cm:
                for t in self.llm.stream(_MSGS):
                    got.append(t)
        self.assertIs(cm.exception, boom, "the failure reaches the caller unchanged")
        self.assertEqual(got, ["sshd is "])
        self.assertEqual(len(self.model_rows("first_token")), 1)
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1, "first_token was left alone")
        self.assertEqual(complete[0]["finish_reason"], "error")
        self.assertEqual(complete[0]["error_type"], "ConnectionResetError")
        self.assertIn("Connection reset by peer", complete[0]["error"])
        self.assertEqual(complete[0]["text"], "sshd is ")
        self.assertEqual(self.model_rows("no_response"), [])
        self.assertIsNone(self.llm.transport_error,
                          "a server that answered part of a reply is not "
                          "an unreachable one")

    def test_the_conversational_path_before_the_first_token(self):
        boom = TimeoutError("timed out")
        with self.assertLogs("intergen.llm", level="ERROR"):
            with self.answering(_Response([], fail_with=boom)):
                with self.assertRaises(TimeoutError) as cm:
                    self._drain(self.llm.stream(_MSGS))
        self.assertIs(cm.exception, boom)
        no_response = self.model_rows("no_response")
        self.assertEqual(len(no_response), 1, "the call left no row at all")
        self.assertEqual(no_response[0]["error_type"], "TimeoutError")
        self.assertEqual(no_response[0]["transport"], "timeout")
        self.assertEqual(self.model_rows("first_token"), [])
        self.assertEqual(self.model_rows("complete"), [])

    def test_the_tool_path_after_the_first_token(self):
        boom = ConnectionResetError(104, "Connection reset by peer")
        got = []
        with self.answering(_Response(_text("sshd is ", finish=None),
                                      fail_with=boom)):
            with self.assertRaises(ConnectionResetError) as cm:
                for t in self.llm.stream_with_tools(_MSGS, tools=_TOOLS):
                    got.append(t)
        self.assertIs(cm.exception, boom)
        self.assertEqual(got, ["sshd is "])
        self.assertEqual(len(self.model_rows("first_token")), 1)
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1, "first_token was left alone")
        self.assertEqual(complete[0]["finish_reason"], "error")
        self.assertEqual(complete[0]["error_type"], "ConnectionResetError")
        self.assertEqual(complete[0]["text"], "sshd is ")
        self.assertIsNone(complete[0]["tool_call"])
        self.assertEqual(self.model_rows("no_response"), [])
        self.assertIsNone(self.llm.transport_error)

    def test_the_tool_path_part_way_through_a_tool_call(self):
        boom = ConnectionResetError(104, "Connection reset by peer")
        with self.answering(_Response(_tool_call(finish=False), fail_with=boom)):
            with self.assertRaises(ConnectionResetError):
                self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1, "first_token was left alone")
        self.assertEqual(complete[0]["finish_reason"], "error")
        self.assertEqual(complete[0]["tool_call"], "run_command")

    def test_the_tool_path_before_the_first_token(self):
        boom = TimeoutError("timed out")
        with self.assertLogs("intergen.llm", level="ERROR"):
            with self.answering(_Response([], fail_with=boom)):
                with self.assertRaises(TimeoutError) as cm:
                    self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        self.assertIs(cm.exception, boom)
        self.assertEqual(len(self.model_rows("no_response")), 1,
                         "the call left no row at all")
        self.assertEqual(self.model_rows("first_token"), [])
        self.assertEqual(self.model_rows("complete"), [])

    def test_the_conversational_path_a_stream_closed_after_the_first_token(self):
        with self.answering(_Response(_text("sshd is ", finish=None), done=False)):
            got = self._drain(self.llm.stream(_MSGS))
        self.assertEqual(got, ["sshd is "], "what arrived is still handed over")
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "error",
                         "a reply the server never finished read as a finished one")
        self.assertEqual(complete[0]["error_type"], "StreamEndedEarly")
        self.assertEqual(complete[0]["text"], "sshd is ")
        self.assertEqual(self.model_rows("no_response"), [])

    def test_the_conversational_path_a_stream_closed_before_the_first_token(self):
        with self.assertLogs("intergen.llm", level="ERROR"):
            with self.answering(_Response([], done=False)):
                got = self._drain(self.llm.stream(_MSGS))
        self.assertEqual(got, [])
        no_response = self.model_rows("no_response")
        self.assertEqual(len(no_response), 1,
                         "a call that got nothing was recorded as complete")
        self.assertEqual(no_response[0]["error_type"], "StreamEndedEarly")
        self.assertEqual(self.model_rows("complete"), [])

    def test_the_tool_path_a_stream_closed_after_the_first_token(self):
        with self.answering(_Response(_text("sshd is ", finish=None), done=False)):
            got = self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        self.assertEqual(got, ["sshd is "])
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "error")
        self.assertEqual(complete[0]["error_type"], "StreamEndedEarly")
        self.assertEqual(complete[0]["text"], "sshd is ")
        self.assertEqual(self.model_rows("no_response"), [])

    def test_the_tool_path_a_stream_closed_before_the_first_token(self):
        with self.assertLogs("intergen.llm", level="ERROR"):
            with self.answering(_Response([], done=False)):
                got = self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        self.assertEqual(got, [])
        self.assertEqual([r["error_type"] for r in self.model_rows("no_response")],
                         ["StreamEndedEarly"],
                         "a call that got nothing was recorded as complete")
        self.assertEqual(self.model_rows("complete"), [])

    def test_a_reply_finished_before_its_end_marker_is_a_finished_reply(self):
        """CONTROL: the finish reason arrived, so the server finished the
        reply; only the marker after it is missing."""
        with self.answering(_Response(_text("sshd is ", "enabled."), done=False)):
            self._drain(self.llm.stream(_MSGS))
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "stop")
        self.assertNotIn("error_type", complete[0])

    def test_a_tool_reply_finished_before_its_end_marker_is_a_finished_reply(self):
        """CONTROL"""
        with self.answering(_Response(_text("sshd is ", "enabled."), done=False)):
            self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "stop")
        self.assertNotIn("error_type", complete[0])

    def test_a_stream_that_ends_normally_names_no_failure(self):
        """CONTROL"""
        self.tool_turn(_text("sshd is ", "enabled."))
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "stop")
        self.assertNotIn("error_type", complete[0])

    def test_a_failure_after_the_call_completed_writes_nothing_more(self):
        """CONTROL: the model call finished; what failed is the handling of its
        result, which still reaches the caller unchanged."""
        boom = RuntimeError("the handling failed")
        with mock.patch.object(self.llm, "_extract_provenance", side_effect=boom):
            with self.answering(_Response(_tool_call())):
                with self.assertRaises(RuntimeError) as cm:
                    self._drain(self.llm.stream_with_tools(_MSGS, tools=_TOOLS))
        self.assertIs(cm.exception, boom)
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "tool_calls")
        self.assertNotIn("error_type", complete[0])

    def test_a_caller_that_stops_at_the_tool_call_records_no_failure(self):
        """CONTROL: closing the stream is the caller's choice, not a failure."""
        with self.answering(_Response(_tool_call())):
            gen = self.llm.stream_with_tools(_MSGS, tools=_TOOLS)
            first = next(gen)
            gen.close()
        self.assertIsInstance(first, ToolCall)
        complete = self.model_rows("complete")
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["finish_reason"], "tool_calls")
        self.assertNotIn("error_type", complete[0])


# ---- rule 3: only a served turn's answer is counted ---------------------------

class _WarmupRouter:
    @contextmanager
    def bind_conversation(self, conv):
        yield

    def _build_messages(self, text, with_tools=False):
        return [Message(role=MessageRole.SYSTEM, content="You are InterGen."),
                Message(role=MessageRole.USER, content=text)]


class _Llama:
    def is_running(self):
        return True


class OnlyAServedTurnIsCounted(_Case):

    def test_a_warm_up_reply_under_the_boot_binding_is_not_counted(self):
        with glass.scope("boot-77", "daemon"):
            self.llm._screen_semantic_health("Hi", [])
        self.assertEqual(self.sink.calls, [],
                         "a warm-up reply entered the engine-health window")
        self.assertEqual([r["flags"] for r in
                          self.model_rows("semantic_health_not_counted")],
                         [["short_nonsense"]])
        self.assertEqual(self.llm._last_semantic_flags, ["short_nonsense"],
                         "the verdict is still recorded")

    def test_work_for_an_answered_exchange_is_not_counted(self):
        """The memory index binds the exchange's own turn identifier on its
        worker thread, after the turn has been answered."""
        def _work():
            with glass.scope("a1b2c3d4e5f60718", "web"):
                self.llm._screen_semantic_health("Hi", [])

        t = threading.Thread(target=_work)
        t.start()
        t.join(timeout=10)
        self.assertEqual(self.sink.calls, [])

    def test_the_daemons_warm_up_counts_nothing(self):
        """The daemon's own warm-up, both generations, through the real model
        call: each reply is screened and recorded, and neither is counted."""
        from intergen.dbus_daemon import InterGenDaemon
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._boot_turn = "boot-77"
        d._boot_t0 = 0.0
        d._llm = self.llm
        d._router = _WarmupRouter()
        d._tools = _Tools()
        d._llama = _Llama()
        d._conversation = None
        with self.answering(_Response(_text("Hi", finish="length")),
                            _Response(_text("Hi", finish="length"))):
            d._start_warmup()
            for t in threading.enumerate():
                if t.name == "intergen-warmup":
                    t.join(timeout=10)
        self.assertEqual(len(self.model_rows("complete")), 2,
                         "both warm-up generations ran")
        self.assertEqual(self.sink.calls, [],
                         "the warm-up's replies entered the engine-health window")
        self.assertEqual(len(self.model_rows("semantic_health_not_counted")), 2,
                         "each warm-up reply is screened, and its verdict is "
                         "recorded as not counted")

    def test_a_served_turn_is_counted(self):
        """CONTROL"""
        with glass.turn("turn-1", "dbus"):
            self.llm._screen_semantic_health("Hi", [])
        self.assertEqual(self.sink.calls, [["short_nonsense"]])

    def test_a_worker_thread_bound_into_a_served_turn_is_counted(self):
        """CONTROL: the web server runs the model call on a worker thread bound
        into the turn; that answer is a served one."""
        with glass.turn("turn-1", "web"):
            ctx = glass.bind_context()
            t = threading.Thread(
                target=ctx.run,
                args=(self.llm._screen_semantic_health, "Hi", []))
            t.start()
            t.join(timeout=10)
        self.assertEqual(self.sink.calls, [["short_nonsense"]])

    def test_outside_any_binding_nothing_is_counted(self):
        """CONTROL"""
        self.llm._screen_semantic_health("Hi", [])
        self.assertEqual(self.sink.calls, [])

    # -- a turn that has ended, and a scope entered inside a turn -------------

    @contextmanager
    def through_a_glass_writer(self, *, switched_off=False):
        """Every emission also reaches a real glass writer, as it does in the
        product. The case's recorder stands in for the module-level emit; this
        hands each row on to a writer behind it, so a turn's ending is claimed
        where the product claims it. The writer writes to a temporary
        directory, or is switched off the way INTERGEN_GLASS=0 switches it off.
        Yields the path of the file it writes."""
        record = glass.emit.side_effect
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                    "INTERGEN_GLASS": "0" if switched_off else "1"}):
                writer = glass.GlassLogger(log_dir=tmp)

            def _record_and_write(*a, **k):
                record(*a, **k)
                writer.emit(*a, **k)

            with mock.patch.object(glass, "_glass", writer), \
                    mock.patch.object(glass.emit, "side_effect",
                                      _record_and_write):
                yield os.path.join(tmp, "glass.jsonl")

    @staticmethod
    def written_rows(path):
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def screen_on_a_worker(self, ctx):
        t = threading.Thread(target=ctx.run,
                             args=(self.llm._screen_semantic_health, "Hi", []))
        t.start()
        t.join(timeout=10)

    def not_counted(self):
        return [r["flags"] for r in
                self.model_rows("semantic_health_not_counted")]

    def test_a_worker_bound_into_a_turn_and_run_after_it_ended_is_not_counted(self):
        """The route deadline ends a turn and abandons its routing thread, which
        runs on with the turn's context bound. What it produces after the
        turn's ending reaches no one."""
        with self.through_a_glass_writer() as path:
            with glass.turn("turn-1", "web"):
                ctx = glass.bind_context()
                glass.emit("delivery", "timeout", detail={"iface": "web"})
            self.screen_on_a_worker(ctx)
            endings = [r["event"] for r in self.written_rows(path)
                       if r["turn_id"] == "turn-1"
                       and glass.is_terminal_event(r["phase"], r["event"])]
        self.assertEqual(endings, ["timeout"],
                         "the turn's one ending is the writer's own row")
        self.assertEqual(self.sink.calls, [],
                         "an answer produced after the turn ended entered the "
                         "engine-health window")
        self.assertEqual(self.not_counted(), [["short_nonsense"]])

    def test_a_scope_entered_inside_a_served_turn_is_not_counted(self):
        """Work bound by scope() is not a served turn's answer, and entering
        the scope inside a turn does not make it one."""
        with glass.turn("turn-1", "web"):
            with glass.scope("a1b2c3d4e5f60718", "web"):
                self.llm._screen_semantic_health("Hi", [])
        self.assertEqual(self.sink.calls, [],
                         "work inside a scope entered the engine-health window")
        self.assertEqual(self.not_counted(), [["short_nonsense"]])

    def test_the_turns_own_answer_after_a_scope_inside_it_closes_is_counted(self):
        """CONTROL: leaving the scope gives the turn its cell back."""
        with glass.turn("turn-1", "web"):
            with glass.scope("a1b2c3d4e5f60718", "web"):
                pass
            self.llm._screen_semantic_health("Hi", [])
        self.assertEqual(self.sink.calls, [["short_nonsense"]])

    def test_with_the_writer_switched_off_an_answer_after_the_ending_is_not_counted(self):
        """INTERGEN_GLASS=0 switches the record off, not the turn's ending."""
        with self.through_a_glass_writer(switched_off=True) as path:
            with glass.turn("turn-1", "web"):
                ctx = glass.bind_context()
                glass.emit("delivery", "timeout", detail={"iface": "web"})
            self.screen_on_a_worker(ctx)
            written = self.written_rows(path)
        self.assertEqual(written, [], "a switched-off writer writes nothing")
        self.assertEqual(self.sink.calls, [],
                         "an answer produced after the turn ended entered the "
                         "engine-health window")
        self.assertEqual(self.not_counted(), [["short_nonsense"]])

    def test_with_the_writer_switched_off_a_turn_that_ends_unreported_has_ended(self):
        """A turn with no ending of its own is given one at its exit, and with
        the writer switched off that ending still ends it."""
        with self.through_a_glass_writer(switched_off=True):
            with glass.turn("turn-1", "web"):
                ctx = glass.bind_context()
            self.screen_on_a_worker(ctx)
        self.assertEqual(self.sink.calls, [],
                         "an answer produced after the turn ended entered the "
                         "engine-health window")
        self.assertEqual(self.not_counted(), [["short_nonsense"]])

    def test_with_the_writer_switched_off_a_served_turn_is_counted(self):
        """CONTROL"""
        with self.through_a_glass_writer(switched_off=True):
            with glass.turn("turn-1", "web"):
                self.screen_on_a_worker(glass.bind_context())
        self.assertEqual(self.sink.calls, [["short_nonsense"]])


if __name__ == "__main__":
    unittest.main()
