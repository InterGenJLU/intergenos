# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Reproduce F-D through the daemon on a private bus and a fake HTTP model.

The daemon's real export, Ask, router and model client run in a
child of dbus-run-session. No production service is started or reconfigured.
The hostname and uptime tool has fixed outputs: its production process runner
needs a user service manager, which the private test bus deliberately lacks.
Their routing, registry dispatch and answer rendering remain real.
The router has no embedding engine, memory database or cloud provider; its
locked tool policy is the Tier 1 policy. This measures the serial bus path,
not model quality or any GPU. The two one-second budgets below are the test's
explicit acceptance contract, measured from each client's send to its reply.

The 200-second case is a strict expected failure until the queue fix lands.
Only DirectAnswerBudgetExceeded is expected: startup, routing, transport and
fault-injection failures remain failures. Use --runxfail to capture the RED.
The zero-latency control must satisfy the same complete-answer assertions.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from intergen.tests.fake_model_server import CONTENT, FakeModelServer


pytestmark = pytest.mark.reaches_a_server_it_starts_itself
DIRECT_ASKS = [("what's my hostname", 1000), ("what's my uptime", 1000)]
MODEL_ASK = "Describe a quiet river in one sentence."


class DirectAnswerBudgetExceeded(AssertionError):
    """Only the named queue defect is eligible for expected-failure status."""


def _http(server, path, payload=None):
    request = urllib.request.Request(
        server.url + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read()


@pytest.mark.parametrize("fault,expected,finished", [
    ("none", CONTENT, True), ("empty", "", True),
    ("truncated_stream", CONTENT, False), ("malformed_json", "", False),
])
def test_faults_cross_the_real_model_client(fault, expected, finished):
    from intergen.interfaces.types import Message, MessageRole
    from intergen.llm import LLMRouter

    with FakeModelServer() as server:
        _http(server, "/_control", {"script": [{"fault": fault}]})
        llm = LLMRouter({"endpoint": server.endpoint})
        text = "".join(llm.stream([Message(role=MessageRole.USER, content=MODEL_ASK)]))
        assert text == expected
        assert llm._last_stream_done is finished
        state = json.loads(_http(server, "/_control"))
        assert len(state["requests"]) == 1
        assert state["requests"][0]["script"]["fault"] == fault
        assert state["requests"][0]["stream"] is True


def test_latency_control_and_health_are_independent():
    with FakeModelServer() as server:
        _http(server, "/_control", {"script": [{"latency_ms": 250}, {"fault": "empty"}]})
        assert json.loads(_http(server, "/health")) == {"status": "ok"}
        assert json.loads(_http(server, "/props"))["chat_template_caps"]["supports_tools"]
        assert len(json.loads(_http(server, "/_control"))["pending"]) == 2
        start = time.monotonic()
        first = json.loads(_http(server, "/v1/chat/completions", {"messages": [], "stream": False}))
        assert time.monotonic() - start >= 0.25
        assert first["choices"][0]["message"]["content"] == CONTENT
        second = json.loads(_http(server, "/v1/chat/completions", {"messages": []}))
        assert second["choices"][0]["message"]["content"] == ""
        third = json.loads(_http(server, "/v1/chat/completions", {"messages": []}))
        assert third["choices"][0]["message"]["content"] == CONTENT
        assert [r["script"]["fault"] for r in server.snapshot()["requests"]] == ["none", "empty", "none"]


def test_malformed_json_is_malformed_on_the_wire():
    with FakeModelServer([{"fault": "malformed_json"}]) as server:
        body = _http(server, "/v1/chat/completions", {"messages": []})
        with pytest.raises(json.JSONDecodeError):
            json.loads(body)


@pytest.mark.parametrize("script", [None, [{} , {"fault": "typo"}],
                                       [{"latency_ms": -1}], [{"latency_ms": True}]])
def test_invalid_control_preserves_the_pending_script(script):
    with FakeModelServer([{"fault": "empty"}]) as server:
        with pytest.raises(urllib.error.HTTPError) as error:
            _http(server, "/_control", {"script": script})
        assert error.value.code == 400
        assert server.snapshot()["pending"] == [{"latency_ms": 0, "fault": "empty"}]


def test_environment_script_crosses_http(monkeypatch):
    monkeypatch.setenv("FAKE_MODEL_SCRIPT", '[{"fault":"empty"}]')
    with FakeModelServer() as server:
        body = json.loads(_http(server, "/v1/chat/completions", {"messages": []}))
        assert body["choices"][0]["message"]["content"] == ""


def _scenario(latency_ms):
    """Executed only in the test-owned dbus-run-session child."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    from intergen.dbus_daemon import InterGenDaemon, SERVICE_NAME, OBJECT_PATH, INTERFACE_NAME
    from intergen.interfaces.types import HardwareTierLevel, ToolResult
    from intergen.llm import LLMRouter
    from intergen.router import ConversationRouter
    from intergen.semantic import SemanticMatcher
    from intergen.tool_registry import ToolRegistry
    from intergen.tools.run_command import RunCommandTool

    assert os.environ.get("QUEUE_FAULT_PRIVATE_BUS") == "1"
    address = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    assert address.startswith("unix:")
    with FakeModelServer([{"latency_ms": latency_ms}]) as server:
        tools = ToolRegistry()
        probe = RunCommandTool()
        probe_outputs = {"hostname": "queue-fixture", "uptime -p": "up 3 hours"}

        def read_probe(arguments):
            command = arguments["command"]
            assert command in probe_outputs, "unexpected probe: " + command
            return ToolResult(call_id="", name="run_command", success=True,
                              content=probe_outputs[command])

        probe.execute = read_probe
        tools.register(probe)
        daemon = InterGenDaemon()
        daemon._llm = LLMRouter({"endpoint": server.endpoint, "request_timeout": 230})
        daemon._tools = tools
        daemon._router = ConversationRouter(
            tool_registry=tools, semantic_matcher=SemanticMatcher(embedder=None),
            llm=daemon._llm, hardware_tier=HardwareTierLevel.TIER_1, lock_dispatch=True)
        daemon._conversation = daemon._router.new_conversation()
        daemon._router.detach_conversation()
        daemon._review_callback_override = lambda *a, **k: "deny"
        daemon._export_dbus()
        assert daemon._reg_id is not None and daemon._owner_id is not None
        loop = GLib.MainLoop()
        loop_thread = threading.Thread(target=loop.run)
        loop_thread.start()

        def connection():
            return Gio.DBusConnection.new_for_address_sync(
                address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

        def ask(text):
            bus = connection()
            try:
                sent = time.monotonic()
                answer = bus.call_sync(
                    SERVICE_NAME, OBJECT_PATH, INTERFACE_NAME, "Ask",
                    GLib.Variant("(s)", (text,)), GLib.VariantType.new("(s)"),
                    Gio.DBusCallFlags.NONE, 230_000, None)
                arrived = time.monotonic()
                return {"ask": text, "sent_at": sent, "arrived_at": arrived,
                        "elapsed_ms": (arrived - sent) * 1000,
                        "reply": json.loads(answer.unpack()[0])}
            finally:
                bus.close_sync(None)

        try:
            # Read acquisition back on the isolated bus before any measured ask.
            ready = connection()
            try:
                end = time.monotonic() + 5
                while True:
                    owned = ready.call_sync(
                        "org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "NameHasOwner",
                        GLib.Variant("(s)", (SERVICE_NAME,)),
                        GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
                    if owned.unpack()[0]:
                        break
                    assert time.monotonic() < end, "daemon did not acquire its private bus name"
                    time.sleep(0.01)
            finally:
                ready.close_sync(None)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                model = pool.submit(ask, MODEL_ASK)
                end = time.monotonic() + 10
                while not server.snapshot()["requests"]:
                    assert not model.done(), model.result() if model.done() else None
                    assert time.monotonic() < end, "model request never reached the fake server"
                    time.sleep(0.01)
                directs = [pool.submit(ask, text) for text, _ in DIRECT_ASKS]
                model_result = model.result(timeout=230)
                results = [future.result(timeout=10) for future in directs]
            return {"injected_latency_ms": latency_ms, "model": model_result,
                    "direct": results, "server": server.snapshot(),
                    "budgets_ms": dict(DIRECT_ASKS)}
        finally:
            daemon._teardown_dbus()
            loop.quit()
            loop_thread.join(timeout=5)
            assert not loop_thread.is_alive(), "private daemon loop did not stop"


def _run_scenario(latency_ms):
    root = Path(__file__).resolve().parents[2]
    command = ["/usr/bin/dbus-run-session", "--", sys.executable, "-u", "-m",
               "intergen.tests.test_queue_fault_injection", "--scenario", str(latency_ms)]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                            timeout=latency_ms / 1000 + 45,
                            env={**os.environ, "QUEUE_FAULT_PRIVATE_BUS": "1"})
    print("scenario command:", command)
    print(result.stdout, end="")
    print(result.stderr, end="")
    assert result.returncode == 0, "private daemon scenario failed"
    rows = [line.removeprefix("QUEUE_RESULT ") for line in result.stdout.splitlines()
            if line.startswith("QUEUE_RESULT ")]
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["model"]["reply"]["used_llm"]
    assert CONTENT in record["model"]["reply"]["response"]
    requests = record["server"]["requests"]
    assert len(requests) == 1, "direct answers unexpectedly called the model"
    assert requests[0]["script"]["latency_ms"] == latency_ms
    assert (requests[0]["finished_at"] - requests[0]["received_at"]) * 1000 >= latency_ms
    for row in record["direct"]:
        assert row["reply"]["source"] == "direct_answer", row
        assert row["reply"]["handled"] and row["reply"]["response"], row
        assert not row["reply"]["used_llm"], row
        expected = "queue-fixture" if "hostname" in row["ask"] else "up 3 hours"
        assert expected in row["reply"]["response"], row
        if latency_ms:
            assert requests[0]["received_at"] <= row["sent_at"] < requests[0]["finished_at"]
    return record


def _assert_budgets(record):
    failures = [{"ask": row["ask"], "elapsed_ms": row["elapsed_ms"],
                 "budget_ms": record["budgets_ms"][row["ask"]]}
                for row in record["direct"]
                if row["elapsed_ms"] > record["budgets_ms"][row["ask"]]]
    if failures:
        raise DirectAnswerBudgetExceeded("F-D: " + json.dumps(failures))


def test_direct_answers_meet_their_budgets_without_injected_latency():
    _assert_budgets(_run_scenario(0))


@pytest.mark.xfail(strict=True, raises=DirectAnswerBudgetExceeded,
                   reason="F-D: a model turn holds the serial daemon queue; queue fix pending")
def test_direct_answers_meet_their_budgets_behind_a_200_second_model_turn():
    _assert_budgets(_run_scenario(200_000))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--scenario":
        raise SystemExit("use --scenario LATENCY_MS")
    print("QUEUE_RESULT " + json.dumps(_scenario(int(sys.argv[2]))), flush=True)
