# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every model request is recorded exactly as sent.

prompt/assembled is written inside the router's assembly and was documented as
the exact prompt fed to the model. It was not: the freeform route appends its
guards after that row is written, and the screens' regenerations, the tool
route's retry, the system map's synthesis, the tool synthesis, the agentic
synthesis attempt, the vision request and the overflow retry's trimmed payload
sent prompts no row recorded. The model client now records each request where
it builds the bytes it sends (glass.model_request): model/request, carrying
the body's sha256 and a call id that the same call's model/complete row
carries, with the caller named.

THE TEST THAT FINDS THIS CLASS: a capture server of the test's own stands in
for the model endpoint and keeps the bytes each request delivered; every
request row must equal, by sha256 and by content, what the server received —
for every request site reachable in-process. A recorded prompt that differs
from the bytes sent cannot pass it, whichever site sent them.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import pytest

import intergen.glass as glass
from intergen import persona
from intergen.interfaces.types import HardwareTierLevel, Message, MessageRole
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tests import glass_rows
from intergen.tool_registry import ToolRegistry

pytestmark = pytest.mark.reaches_a_server_it_starts_itself


def _sse(text: str) -> bytes:
    chunks = [
        {"choices": [{"delta": {"content": text}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "timings": {"prompt_n": 11, "predicted_n": 2}},
    ]
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n"
                    for c in chunks) + b"data: [DONE]\n\n"


class _Capture:
    """A loopback model endpoint that keeps every request body it receives
    and answers from a script: (status, content type, body) per request, the
    last one repeated."""

    def __init__(self, script=None):
        self.bodies: list[bytes] = []
        self.script = list(script or [(200, "text/event-stream",
                                       _sse("fake answer"))])
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                capture.bodies.append(body)
                n = len(capture.bodies) - 1
                status, ctype, out = capture.script[min(n, len(capture.script) - 1)]
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *_a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = (f"http://127.0.0.1:{self.server.server_address[1]}"
                    "/v1/chat/completions")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("INTERGEN_GLASS", raising=False)
    glass._glass = None
    yield tmp_path
    glass._glass = None


def _llm(capture: _Capture) -> LLMRouter:
    llm = LLMRouter(config=None)
    llm._endpoint = capture.url
    return llm


def _requests(state) -> list[dict]:
    return [r["detail"] for r in glass_rows.read(state)
            if r.get("phase") == "model" and r.get("event") == "request"]


def _assert_recorded_as_sent(state, capture: _Capture, *, count: int) -> list[dict]:
    rows = _requests(state)
    assert len(capture.bodies) == count, len(capture.bodies)
    assert len(rows) == count, [r.get("caller") for r in rows]
    for row, body in zip(rows, capture.bodies):
        assert row["body_sha256"] == hashlib.sha256(body).hexdigest()
        assert row["body_bytes"] == len(body)
        if row.get("images"):
            assert json.loads(row["body"]) == glass._request_view(
                json.loads(body), [])
        else:
            assert row["body"].encode() == body   # the bytes the server got
        row["request"] = json.loads(row["body"])
    return rows


def _completes(state) -> list[dict]:
    return [r["detail"] for r in glass_rows.read(state)
            if r.get("phase") == "model" and r.get("event") == "complete"]


def _msgs():
    return [Message(role=MessageRole.SYSTEM, content="You are a test."),
            Message(role=MessageRole.USER, content="hello there")]


def test_chat_through_stream_is_recorded_as_sent_and_joined(state):
    cap = _Capture()
    try:
        with glass.calling("probe_chat"):
            _llm(cap).chat(_msgs())
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=1)
    assert rows[0]["caller"] == "probe_chat"
    assert [c["call_id"] for c in _completes(state)] == [rows[0]["call_id"]]


def test_stream_with_tools_is_recorded_as_sent(state):
    cap = _Capture()
    try:
        llm = _llm(cap)
        schemas = ToolRegistry()
        schemas.discover_tools()
        with glass.calling("probe_tools"):
            list(llm.stream_with_tools(_msgs(), tools=schemas.get_tool_schemas()))
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=1)
    assert rows[0]["caller"] == "probe_tools"
    assert rows[0]["request"]["tools"]
    assert _completes(state)[0]["call_id"] == rows[0]["call_id"]


def test_the_overflow_retry_records_the_trimmed_payload_it_sends(state):
    overflow = json.dumps({"error": {"type": "exceed_context_size_error"}}).encode()
    cap = _Capture([(400, "application/json", overflow),
                    (200, "text/event-stream", _sse("after the trim"))])
    long_history = _msgs() + [Message(role=MessageRole.USER, content=f"m{i}")
                              for i in range(4)]
    schemas = ToolRegistry()
    schemas.discover_tools()
    try:
        list(_llm(cap).stream_with_tools(long_history,
                                         tools=schemas.get_tool_schemas()))
    except Exception:  # noqa: BLE001 — the record is what is under test
        pass
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=2)
    assert rows[1]["retry_of"] == rows[0]["call_id"]
    assert rows[1]["retry_reason"] == "context_overflow"
    # The handler may change the payload in place before the retry (it trims
    # a long one); whatever it sends, the retry's own row holds it as sent —
    # _assert_recorded_as_sent compared each row with the bytes received.
    assert rows[1]["body"].encode() == cap.bodies[1]


def test_the_agentic_synthesis_attempt_is_recorded_with_its_output(state):
    cap = _Capture([(200, "text/event-stream", _sse("synthesized"))])
    payload = {"messages": [{"role": "user", "content": "summarize"}],
               "stream": True, "max_tokens": 32}
    try:
        with glass.calling("probe_synthesis"):
            _llm(cap)._synthesis_attempt(payload)
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=1)
    done = [c for c in _completes(state) if c.get("call_id") == rows[0]["call_id"]]
    assert done and done[0]["text"] == "synthesized"


def test_the_vision_request_is_recorded_with_the_image_by_digest(state):
    reply = json.dumps({"choices": [{"message": {"content": "TEXT"}}]}).encode()
    cap = _Capture([(200, "application/json", reply)])
    image = "data:image/png;base64," + "iVBORw0KGgo" * 40
    try:
        llm = _llm(cap)
        llm._has_vision = True
        llm.transcribe_image(image)
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=1)
    assert image not in json.dumps(rows[0])
    digest = hashlib.sha256(image.encode()).hexdigest()
    assert rows[0]["images"] == [{"sha256": digest, "chars": len(image)}]


def _router(capture: _Capture) -> ConversationRouter:
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = mock.Mock(side_effect=AssertionError("unexpected dispatch"))
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    return ConversationRouter(
        tool_registry=registry, semantic_matcher=matcher, llm=_llm(capture),
        hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=True)


def test_the_freeform_guard_appended_after_the_assembly_is_in_the_request(state):
    cap = _Capture()
    try:
        r = _router(cap)
        r._current_query_type = "diagnostic"
        r._try_llm_freeform("why does my laptop feel slow lately")
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=1)
    assert rows[0]["caller"] == "llm_freeform"
    sent = [m["content"] for m in rows[0]["request"]["messages"]]
    assert persona.FREEFORM_STATE_GUARD in sent
    assembled = [r["detail"] for r in glass_rows.read(state)
                 if r.get("event") == "assembled"][0]
    assert assembled["view"] == "assembly"
    assert persona.FREEFORM_STATE_GUARD not in [
        m["content"] for m in assembled["messages"]]


def test_a_screen_regeneration_is_recorded_as_sent_and_named(state):
    cap = _Capture([
        (200, "text/event-stream",
         _sse("I've run the update for you and it finished successfully.")),
        (200, "text/event-stream",
         _sse("Updating is done with sudo pkm upgrade; nothing was run here.")),
    ])
    try:
        r = _router(cap)
        r._current_query_type = "general"
        r._try_llm_freeform("can you update my system")
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=len(cap.bodies))
    callers = [row["caller"] for row in rows]
    assert callers[0] == "llm_freeform"
    assert "claim_screen_regeneration" in callers


def test_the_system_map_synthesis_is_recorded_as_sent(state):
    cap = _Capture()
    try:
        r = _router(cap)
        r._try_system_map("is everything ok", "failed units: none\nload: 0.10")
    finally:
        cap.close()
    rows = _assert_recorded_as_sent(state, cap, count=len(cap.bodies))
    assert rows and rows[0]["caller"] == "system_map_synthesis"
    assert any("failed units: none" in m["content"]
               for m in rows[0]["request"]["messages"])
