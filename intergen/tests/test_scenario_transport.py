# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the transport interface (WP-1.2).

Pins the ScenarioTransport contract and the TurnResult normalization without a
live daemon: tool-name extraction, the mock transport's bookkeeping, the
fail-loud stance of the un-wired session-boundary primitives, and mode
validation on the real transport. The live ClientTransport is exercised against
a real daemon by the seed-scenario runs in a later work package.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario.transport import (
    ClientTransport,
    MockTransport,
    ScenarioTransport,
    TurnResult,
    _tool_names,
)


class TestToolNameExtraction(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(_tool_names([{"name": "web_search"}]), ["web_search"])

    def test_tool_and_tool_name_keys(self):
        self.assertEqual(
            _tool_names([{"tool": "run_command"}, {"tool_name": "read_file"}]),
            ["run_command", "read_file"],
        )

    def test_malformed_entries_skipped(self):
        # a non-dict, a dict with no name, and an empty name all skip cleanly
        self.assertEqual(
            _tool_names(["oops", {"args": {}}, {"name": ""}, {"name": "ok"}]),
            ["ok"],
        )

    def test_empty(self):
        self.assertEqual(_tool_names([]), [])
        self.assertEqual(_tool_names(None), [])


class TestMockTransport(unittest.TestCase):
    def test_default_reply_and_bookkeeping(self):
        t = MockTransport()
        with t:
            t.await_ready()
            t.reset()
            r = t.ask("hello?")
        self.assertEqual(r.source, "mock")
        self.assertEqual(t.asked, ["hello?"])
        self.assertEqual(t.reset_count, 1)
        self.assertEqual(t.ready_calls, 1)
        self.assertTrue(t.closed)  # context manager closed it

    def test_scripted_reply(self):
        scripted = TurnResult(text="Paris", source="keyword",
                              tools_called=["web_search"])
        t = MockTransport(replies={"capital of france?": scripted})
        self.assertEqual(t.ask("capital of france?").tools_called, ["web_search"])
        self.assertEqual(t.ask("other").source, "mock")  # falls back to default

    def test_is_a_scenario_transport(self):
        self.assertIsInstance(MockTransport(), ScenarioTransport)


class _BareTransport(ScenarioTransport):
    """Implements only the required abstract methods — leaves the session-
    boundary primitives at their fail-loud base defaults."""

    def ask(self, message: str) -> TurnResult:
        return TurnResult(text="ok")

    def reset(self) -> None:
        pass

    def await_ready(self, timeout_s: float | None = None) -> None:
        pass


class TestBoundaryPrimitivesFailLoud(unittest.TestCase):
    def test_restart_and_new_session_raise_when_unwired(self):
        # A transport that does NOT override the boundary primitives must fail
        # loud, never silently skip a session boundary.
        t = _BareTransport()
        with self.assertRaises(NotImplementedError):
            t.restart_daemon()
        with self.assertRaises(NotImplementedError):
            t.new_session()
        self.assertIsNone(t.memory_db_path())  # default: no DB to snapshot


class TestMockBoundaryBookkeeping(unittest.TestCase):
    def test_records_boundaries_and_db_path(self):
        t = MockTransport(memory_db_path="/tmp/x/memory.db")
        t.restart_daemon()
        t.new_session()
        t.restart_daemon()
        self.assertEqual(t.restart_count, 2)
        self.assertEqual(t.new_session_count, 1)
        self.assertEqual(
            t.boundaries, ["restart-before", "new-session-before", "restart-before"])
        self.assertEqual(t.memory_db_path(), "/tmp/x/memory.db")


class TestClientTransportMode(unittest.TestCase):
    def test_bad_mode_rejected_before_touching_daemon(self):
        with self.assertRaises(ValueError):
            ClientTransport(mode="carrier-pigeon")


if __name__ == "__main__":
    unittest.main()
