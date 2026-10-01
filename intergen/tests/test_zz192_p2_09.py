# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The glass reader says tracing is off only when it has nothing to show.

``intergen glass`` is the surface a person uses to see what their assistant
did. The command already accepts the duty of telling that person when the
trace is not being written — it prints "(glass is DISABLED —
INTERGEN_GLASS=0)" — but the notice sits INSIDE the empty-trace branch
(intergen/cli.py, the ``if not rows:`` block around line 1455), so it is
reached only when the file holds no rows at all.

The state that matters is the other one. A machine that ran turns with the
glass pipeline on, and then had ``INTERGEN_GLASS=0`` set in the environment
the reader runs in, has rows on disk AND a disabled tracer. There the
command takes the listing path: it prints "N turns in <path> (newest last)"
and the rows, byte-for-byte the way it prints a live trace, and says nothing.
The reader is shown a stale record as the current one — the rows stop where
tracing stopped and nothing on the surface marks that edge. Measured against
the real command on 2026-09-30: the listing with tracing off and the listing
with tracing on differ in nothing but the path they name.

The notice itself works; these cases pin WHERE it has to appear. The disabled
state is a reader-process property (``glass.glass_enabled()`` reads that
process's own environment), which is why the cases drive it directly instead
of through a daemon.

Deliberately unasserted: ``--json``. That path is machine-readable raw JSONL
and a human notice printed into it would be a defect of its own, so nothing
here requires one. Two human-facing paths are covered — the default listing
and ``--tail``.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

_FAKE_PATH = Path("/nonexistent/state/intergen/glass.jsonl")


def _stale_rows() -> list[dict]:
    """Three rows of one finished turn, as the writer left them."""
    return [
        {"ts": "2026-09-30T10:00:00", "seq": 1, "turn_id": "aabbccdd11223344",
         "phase": "turn", "event": "turn_start", "iface": "cli",
         "t_rel_ms": 0, "detail": {"user_msg": "what is my battery level"}},
        {"ts": "2026-09-30T10:00:01", "seq": 2, "turn_id": "aabbccdd11223344",
         "phase": "route", "event": "decided", "iface": "cli",
         "t_rel_ms": 12, "detail": {"source": "local", "query_type": "fact",
                                    "semantic_score": 0.9}},
        {"ts": "2026-09-30T10:00:02", "seq": 3, "turn_id": "aabbccdd11223344",
         "phase": "deliver", "event": "final", "iface": "cli",
         "t_rel_ms": 30, "detail": {"iface": "cli", "text": "78 percent"}},
    ]


class TheGlassReaderNamesADisabledTracerTests(unittest.TestCase):
    """What the reader surface says while nothing is being recorded."""

    def _run(self, argv: list[str], rows: list[dict]) -> str:
        """`intergen glass <argv>` over a given trace, with tracing OFF.

        No file, no service, no bus: the reader's three seams are the rows it
        reads, the path it names and whether the tracer is enabled.
        """
        from intergen import cli
        from intergen import glass
        buf = io.StringIO()
        with mock.patch.object(glass, "read_rows",
                               new=lambda path=None: iter(list(rows))), \
                mock.patch.object(glass, "glass_enabled",
                                  new=lambda: False), \
                mock.patch.object(glass, "default_glass_path",
                                  new=lambda: _FAKE_PATH):
            with redirect_stdout(buf):
                cli.cmd_glass(argv)
        return buf.getvalue()

    def test_the_glass_listing_says_tracing_is_off_even_with_stale_rows(self):
        """The defect, stated as an assertion."""
        out = self._run([], _stale_rows())
        # A command that printed nothing would satisfy the assertion below
        # without rendering a listing at all.
        self.assertIn("aabbccdd11223344", out,
                      "the listing rendered no rows, so the assertion below "
                      "would pass or fail vacuously")
        self.assertIn("turns in", out,
                      "the listing did not take the turn-summary path")
        self.assertIn(
            "disabled", out.lower(),
            "intergen glass listed a stale trace as if it were live: the "
            "disabled-tracer notice lives inside the `if not rows:` branch "
            "(intergen/cli.py:1455), so a reader with rows on disk and "
            "INTERGEN_GLASS=0 in the environment is told nothing. Output "
            "was:\n" + out)

    def test_the_tail_listing_says_tracing_is_off_even_with_stale_rows(self):
        """`--tail` is the other human-facing read of the same trace."""
        out = self._run(["--tail"], _stale_rows())
        self.assertIn("aabbccdd11223344", out,
                      "the tail rendered no rows, so the assertion below "
                      "would pass or fail vacuously")
        self.assertIn(
            "disabled", out.lower(),
            "intergen glass --tail printed the last rows of a trace that is "
            "no longer being written and named no such thing. Output "
            "was:\n" + out)

    def test_the_notice_fires_on_an_empty_trace_so_this_instrument_is_not_blind(self):
        """The true positive: on an empty trace the notice IS printed.

        Green at this snapshot. Without it, the two cases above could be
        failing because the notice does not exist anywhere, or because the
        patching never reached the command.
        """
        out = self._run([], [])
        self.assertIn("No glass trace yet", out)
        self.assertIn("disabled", out.lower())
