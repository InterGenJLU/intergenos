# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every glass row the daemon's start-up and warm-up write names the boot.

Measured on an installed release before this test existed: the warm-up's two
generations wrote prompt/assembled, model/first_token, model/semantic_health,
model/complete and memory rows with the literal placeholder identifier, and
the engine's offload check did the same during start-up, while the boot's own
bookkeeping rows carried an explicit boot id. The installed-system gate on
trace integrity refuses rows that cannot be joined to what produced them.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from contextlib import contextmanager

import intergen.glass as glass
from intergen.dbus_daemon import InterGenDaemon


def _reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _rows(tmp: str) -> list[dict]:
    from intergen.tests import glass_rows
    return [r for r in glass_rows.read(tmp) if r.get("phase") != "glass"]


class _Llm:
    def stream(self, msgs, max_tokens=1):
        glass.emit("prompt", "assembled")
        glass.emit("model", "first_token")
        glass.emit("model", "complete")
        yield "x"

    def stream_with_tools(self, msgs, tools, max_tokens=1):
        glass.emit("prompt", "assembled", detail={"with_tools": True})
        yield "x"


class _Router:
    @contextmanager
    def bind_conversation(self, conv):
        yield

    def _build_messages(self, text, with_tools=False):
        glass.emit("memory", "skip", detail={"reason": "test"})
        return [{"role": "user", "content": text}]


class _Tools:
    def get_tool_schemas(self):
        return []


class _Llama:
    def is_running(self):
        return True


def _daemon() -> InterGenDaemon:
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._boot_turn = "boot-77"
    d._boot_t0 = 0.0
    d._llm = _Llm()
    d._router = _Router()
    d._tools = _Tools()
    d._llama = _Llama()
    d._conversation = None
    return d


class BootRowsCarryTheBootTurn(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _reset(self.tmp)

    def test_every_warmup_row_names_the_boot(self) -> None:
        d = _daemon()
        d._start_warmup()
        for t in threading.enumerate():
            if t.name == "intergen-warmup":
                t.join(timeout=10)
        rows = _rows(self.tmp)
        events = {(r["phase"], r["event"]) for r in rows}
        # The deep emissions happened (the test doubles are not bypassed) …
        self.assertIn(("model", "first_token"), events)
        self.assertIn(("memory", "skip"), events)
        self.assertIn(("warmup", "cache_warm_done"), events)
        # … and not one of them is orphaned.
        orphaned = [(r["phase"], r["event"]) for r in rows
                    if r["turn_id"] != "boot-77"]
        self.assertEqual(orphaned, [])

    def test_start_service_runs_under_the_boot_scope(self) -> None:
        d = _daemon()
        seen: list[str] = []
        d._start_service_inner = lambda: seen.append(glass.current_turn_id())
        d.start_service()
        self.assertEqual(seen, ["boot-77"])
        self.assertEqual(glass.current_turn_id(), "")
