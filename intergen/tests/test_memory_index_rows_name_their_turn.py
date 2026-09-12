# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every trace row the memory index writes for an exchange names the turn.

The index embeds each finished exchange on its own worker thread, and a thread
does not inherit the turn binding. Measured on an installed release before this
test existed: every served turn left exactly one memory/indexed row with the
placeholder identifier in the user's record, which the installed-system
trace-integrity gate refuses — on the run after the one that wrote it, because
the gate reads the file before the turn it drives has been indexed.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest

import intergen.glass as glass
from intergen.memory import SessionTurnIndex


def _reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _rows(tmp: str) -> list[dict]:
    from intergen.tests import glass_rows
    return [r for r in glass_rows.read(tmp) if r.get("phase") == "memory"]


class _Embedder:
    def __init__(self, dim: int = 8):
        self.dim = dim

    def __call__(self, texts):
        return [[0.5] * self.dim for _ in texts]


class TheIndexRowsNameTheirTurn(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("XDG_STATE_HOME")
        _reset(self._tmp.name)

    def tearDown(self):
        glass._glass = None
        if self._prev is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._prev
        self._tmp.cleanup()

    def _wait_indexed(self, index: SessionTurnIndex, n: int, deadline_s: float = 10.0):
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if len(_rows_of(self._tmp.name, "indexed")) >= n:
                return
            time.sleep(0.02)

    def test_the_indexed_row_carries_the_turn_that_produced_it(self):
        index = SessionTurnIndex(embedder=_Embedder())
        try:
            with glass.turn("turn-abc123", "dbus"):
                index.index_turn("what does the manual say", "an answer")
            self._wait_indexed(index, 1)
        finally:
            index.stop() if hasattr(index, "stop") else None
        rows = _rows(self._tmp.name)
        indexed = [r for r in rows if r.get("event") == "indexed"]
        self.assertEqual(len(indexed), 1, f"expected one indexed row, rows: {rows}")
        self.assertEqual(
            indexed[0].get("turn_id"), "turn-abc123",
            "the memory index wrote its indexed row without the turn that produced "
            f"it (turn_id={indexed[0].get('turn_id')!r}); the worker thread does not "
            "inherit the turn binding, so the id must travel with the exchange.")
        placeholder = [r for r in rows if r.get("turn_id") in (None, "", "no-turn")]
        self.assertEqual(
            placeholder, [],
            f"memory rows with no usable turn identifier: {placeholder}")

    def test_an_exchange_indexed_outside_any_turn_is_still_indexed(self):
        """Control: with nothing to bind, the worker still indexes — the fix must
        not make the index depend on a turn being open."""
        index = SessionTurnIndex(embedder=_Embedder())
        try:
            index.index_turn("hello", "hi")
            self._wait_indexed(index, 1)
        finally:
            index.stop() if hasattr(index, "stop") else None
        indexed = [r for r in _rows(self._tmp.name) if r.get("event") == "indexed"]
        self.assertEqual(len(indexed), 1)


def _rows_of(tmp: str, event: str) -> list[dict]:
    return [r for r in _rows(tmp) if r.get("event") == event]


if __name__ == "__main__":
    unittest.main()
