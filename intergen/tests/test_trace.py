# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Unit tests for intergen.trace — the request-scoped decision tracer.

Covers: OFF by default (no-op span + no file written); enabled nesting with
parent/child chaining, trace_id propagation, the JSONL sink, and duration;
content-capture gating via INTERGEN_TRACE_CONTENT; error status + exception
re-raise; and asyncio context isolation across concurrent spans.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.trace import Tracer


def _records(state_dir: str) -> list[dict]:
    p = Path(state_dir) / "intergen" / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def _make_tracer(state_dir: str, *, trace: bool = True, content: bool = False) -> Tracer:
    # Tracer reads env only at construction; build it inside the patched env so
    # the enabled flag + writable path are captured against the temp state dir.
    env = {
        "XDG_STATE_HOME": state_dir,
        "INTERGEN_TRACE": "1" if trace else "",
        "INTERGEN_TRACE_CONTENT": "1" if content else "",
    }
    with mock.patch.dict(os.environ, env):
        return Tracer()


class TraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_disabled_by_default_is_noop_and_writes_nothing(self) -> None:
        t = _make_tracer(self.state, trace=False)
        self.assertFalse(t.enabled)
        with t.span("classify", kind="router") as s:
            self.assertEqual(s.trace_id, "")
            self.assertEqual(t.current_trace_id(), "")
        self.assertEqual(_records(self.state), [])

    def test_enabled_nesting_parent_chain_and_sink(self) -> None:
        t = _make_tracer(self.state)
        self.assertTrue(t.enabled)
        self.assertIsNotNone(t._log_file)
        with t.span("request", kind="request") as root:
            rid = t.current_trace_id()
            self.assertTrue(rid)
            self.assertEqual(root.trace_id, rid)
            with t.span("router.semantic", kind="router") as child:
                child.set_attribute("routed_via", "semantic")
                child.set_attribute("score", 0.82)
                self.assertEqual(child.trace_id, rid)
                self.assertEqual(child.parent_span_id, root.span_id)

        recs = _records(self.state)
        # child closes (and writes) before the root
        self.assertEqual([r["name"] for r in recs], ["router.semantic", "request"])
        child_rec, root_rec = recs
        self.assertEqual(child_rec["parent_span_id"], root_rec["span_id"])
        self.assertIsNone(root_rec["parent_span_id"])
        self.assertEqual(child_rec["trace_id"], root_rec["trace_id"])
        self.assertEqual(child_rec["attributes"]["routed_via"], "semantic")
        self.assertEqual(child_rec["attributes"]["score"], 0.82)
        self.assertIsNotNone(child_rec["duration_ms"])
        self.assertEqual(child_rec["kind"], "router")
        self.assertEqual(child_rec["status"], "ok")

    def test_content_capture_off_gates_raw_content(self) -> None:
        t = _make_tracer(self.state, content=False)
        with t.span("llm.synth", kind="llm") as s:
            s.set_content("prompt", "SECRET")
            s.set_attribute("model", "2b")
        rec = _records(self.state)[-1]
        self.assertNotIn("prompt", rec["attributes"])
        self.assertEqual(rec["attributes"]["model"], "2b")

    def test_content_capture_on_records_content(self) -> None:
        t = _make_tracer(self.state, content=True)
        with t.span("llm.synth", kind="llm") as s:
            s.set_content("prompt", "CAPTURED")
        rec = _records(self.state)[-1]
        self.assertEqual(rec["attributes"]["prompt"], "CAPTURED")

    def test_error_status_recorded_and_reraised(self) -> None:
        t = _make_tracer(self.state)
        with self.assertRaises(ValueError):
            with t.span("boom") as s:  # noqa: F841
                raise ValueError("kaboom")
        rec = _records(self.state)[-1]
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["status_message"], "ValueError")

    def test_unknown_kind_falls_back_to_internal(self) -> None:
        t = _make_tracer(self.state)
        with t.span("x", kind="not-a-real-kind"):
            pass
        self.assertEqual(_records(self.state)[-1]["kind"], "internal")

    def test_decision_file_is_mode_0600(self) -> None:
        import stat as _stat
        t = _make_tracer(self.state)
        with t.span("x"):
            pass
        mode = _stat.S_IMODE(os.stat(t._log_file).st_mode)
        self.assertEqual(mode, 0o600)

    def test_records_carry_schema_version_and_monotonic_seq(self) -> None:
        t = _make_tracer(self.state)
        with t.span("a"):
            with t.span("b"):
                pass
        recs = _records(self.state)
        for r in recs:
            self.assertEqual(r["schema_version"], 1)
            self.assertIn("seq", r)
        by_name = {r["name"]: r["seq"] for r in recs}
        self.assertLess(by_name["a"], by_name["b"])  # "a" created before "b"

    def test_set_content_redacts_credential_shaped_keys(self) -> None:
        t = _make_tracer(self.state, content=True)
        with t.span("llm", kind="llm") as s:
            s.set_content("prompt", "hello world")           # ok — kept
            s.set_content("password", "hunter2")             # redacted
            s.set_content("api_key", "sk-abc123")            # redacted
            s.set_content("authorization", "Bearer xyz")     # redacted
            s.set_content("user_token", "t0k3n")             # redacted (substring)
        a = _records(self.state)[-1]["attributes"]
        self.assertEqual(a["prompt"], "hello world")
        self.assertEqual(a["password"], "[REDACTED]")
        self.assertEqual(a["api_key"], "[REDACTED]")
        self.assertEqual(a["authorization"], "[REDACTED]")
        self.assertEqual(a["user_token"], "[REDACTED]")

    def test_content_capture_refused_as_root(self) -> None:
        log_dir = os.path.join(self.state, "intergen")
        env = {"INTERGEN_TRACE": "1", "INTERGEN_TRACE_CONTENT": "1"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("intergen.trace.os.geteuid", return_value=0):
            t = Tracer(log_dir=log_dir)
        self.assertFalse(t.capture_content)
        with t.span("llm", kind="llm") as s:
            s.set_content("prompt", "should not record")
        self.assertNotIn("prompt", _records(self.state)[-1]["attributes"])


class TraceAsyncIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    async def test_concurrent_requests_get_independent_traces(self) -> None:
        t = _make_tracer(self.state)

        async def worker(name: str) -> tuple[str, str]:
            with t.span(name, kind="request") as s:
                await asyncio.sleep(0.01)
                # mid-span, this task sees only its own trace
                return s.trace_id, t.current_trace_id()

        (ta, ca), (tb, cb) = await asyncio.gather(worker("A"), worker("B"))
        self.assertEqual(ta, ca)
        self.assertEqual(tb, cb)
        self.assertNotEqual(ta, tb)  # no cross-contamination across tasks
        self.assertEqual(t.current_trace_id(), "")  # context cleaned up


if __name__ == "__main__":
    unittest.main()
