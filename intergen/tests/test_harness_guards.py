# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Harness self-integrity guards — the dyno must never grade a not-ready daemon.

Two fail-closed guards keep the eval harness from lying about what the floor
did (the hard-won eval lesson: a response can look like clean data and answer
nothing):

  1. Readiness gate — a not-ready daemon answers every turn with the
     'InterGen is starting up' stub (source='startup'); the runner's stub
     tripwire (run_turn) and the client's init gate (_await_ready) both abort
     rather than feed the grader a wall of stubs scored as real pass/fail.
  2. Memory isolation fail-closed — if the temp-DB swap throws, the client
     ABORTS instead of silently running against the user's real per-user DB.

Pure-data / mock tests; no model or live daemon required.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

from intergen.tests.client import InterGenTestClient
from intergen.tests.client import TestResponse as _TestResponse
from intergen.tests.runner import run_turn


class _FakeClient:
    """Minimal client whose ask() returns a scripted TestResponse."""

    def __init__(self, response: _TestResponse) -> None:
        self._response = response

    def ask(self, _message: str) -> _TestResponse:
        return self._response


class StubTripwireTests(unittest.TestCase):
    """run_turn aborts on a startup stub; passes real responses through."""

    def test_startup_stub_aborts(self) -> None:
        client = _FakeClient(_TestResponse(
            text="InterGen is starting up, please wait.", source="startup"))
        with self.assertRaises(RuntimeError) as ctx:
            run_turn(client, "what is my disk usage?")
        self.assertIn("starting up", str(ctx.exception))
        self.assertIn("what is my disk usage?", str(ctx.exception))

    def test_real_response_passes_through(self) -> None:
        client = _FakeClient(_TestResponse(
            text="209 GB free, 11% used", source="keyword", handled=True))
        result = run_turn(client, "disk?")
        self.assertEqual(result["source"], "keyword")
        self.assertEqual(result["text"], "209 GB free, 11% used")

    def test_dict_with_startup_source_aborts(self) -> None:
        # A raw-string client that yields a JSON stub dict must also trip.
        class _RawClient:
            def ask(self, _m: str) -> str:
                return '{"response": "InterGen is starting up, please wait.", ' \
                       '"source": "startup", "handled": false}'
        with self.assertRaises(RuntimeError):
            run_turn(_RawClient(), "anything")


class AwaitReadyTests(unittest.TestCase):
    """_await_ready returns once the daemon serves; aborts on timeout."""

    def _bare_client(self) -> InterGenTestClient:
        # Build without __init__ so no real daemon/model is started.
        return InterGenTestClient.__new__(InterGenTestClient)

    def test_returns_when_router_and_endpoint_serve(self) -> None:
        c = self._bare_client()
        c._status_direct = lambda: {  # type: ignore[attr-defined]
            "components": {"router": True}}
        c._model_endpoint_healthy = lambda: True  # type: ignore[attr-defined]
        c._ask_direct = lambda _m: {"source": "keyword"}  # type: ignore[attr-defined]
        # Should return without raising (endpoint healthy + probe non-startup).
        c._await_ready(timeout_s=5.0)

    def test_returns_even_when_managed_handle_absent(self) -> None:
        # The KEY correctness case: the daemon's managed-server handle is irrelevant
        # — a warm/reused server on the fixed endpoint serves inference. No
        # 'llama_server' component at all, yet the endpoint is healthy => ready.
        c = self._bare_client()
        c._status_direct = lambda: {  # type: ignore[attr-defined]
            "components": {"router": True, "llama_server": False}}
        c._model_endpoint_healthy = lambda: True  # type: ignore[attr-defined]
        c._ask_direct = lambda _m: {"source": "keyword"}  # type: ignore[attr-defined]
        c._await_ready(timeout_s=5.0)

    def test_aborts_when_router_down(self) -> None:
        c = self._bare_client()
        c._status_direct = lambda: {  # type: ignore[attr-defined]
            "components": {"router": False}}
        c._model_endpoint_healthy = lambda: True  # type: ignore[attr-defined]
        c._ask_direct = lambda _m: {"source": "startup"}  # type: ignore[attr-defined]
        with mock.patch("intergen.tests.client.time.sleep", lambda _s: None):
            with self.assertRaises(RuntimeError) as ctx:
                c._await_ready(timeout_s=0.05)
        self.assertIn("not ready", str(ctx.exception))

    def test_aborts_when_model_endpoint_down(self) -> None:
        # Router up but the MODEL endpoint is unreachable — must NOT declare
        # ready on a non-stub probe alone (freeform would route but error). The
        # bug the live run exposed: readiness must verify the endpoint serves.
        c = self._bare_client()
        c._status_direct = lambda: {  # type: ignore[attr-defined]
            "components": {"router": True}}
        c._model_endpoint_healthy = lambda: False  # type: ignore[attr-defined]
        c._ask_direct = lambda _m: {"source": "keyword"}  # type: ignore[attr-defined]
        with mock.patch("intergen.tests.client.time.sleep", lambda _s: None):
            with self.assertRaises(RuntimeError):
                c._await_ready(timeout_s=0.05)


class MemoryIsolationFailClosedTests(unittest.TestCase):
    """_isolate_memory_db ABORTS (does not proceed) when the swap fails."""

    def test_isolation_failure_raises_and_does_not_touch_real_db(self) -> None:
        c = InterGenTestClient.__new__(InterGenTestClient)
        sentinel = object()
        c._daemon = types.SimpleNamespace(_memory=sentinel, _router=None)
        # Force the temp-DB construction to blow up.
        with mock.patch("intergen.memory.MemoryManager",
                        side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError) as ctx:
                c._isolate_memory_db()
        self.assertIn("isolation FAILED", str(ctx.exception))
        # The daemon's memory pointer must be UNCHANGED — never swapped, never
        # left pointing at a half-made temp DB; the real DB is untouched.
        self.assertIs(c._daemon._memory, sentinel)
        self.assertIsNone(c._test_mem_dir)

    def test_isolation_success_swaps_memory(self) -> None:
        c = InterGenTestClient.__new__(InterGenTestClient)
        c._daemon = types.SimpleNamespace(_memory=object(), _router=None)
        try:
            c._isolate_memory_db()
            # On success the daemon points at a fresh isolated MemoryManager and
            # a temp dir was recorded for cleanup.
            self.assertIsNotNone(c._test_mem_dir)
            self.assertIsNotNone(c._daemon._memory)
        finally:
            import shutil
            if getattr(c, "_test_mem_dir", None):
                shutil.rmtree(c._test_mem_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
