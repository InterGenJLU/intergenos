# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression tests for the M1 Glass Pipeline (intergen.glass).

Covers the writer contract (turn threading, monotonic seq, in-place secret
redaction, always-on/loud-disable, rotation + marker, warmup override, reader)
and the two emission paths the acceptance test rides on:

* the router writes its model-facing conversation buffer via _append_history —
  the exact (a)/(c) write that the streamed web path skips; its emission is the
  telemetry that later proves M2a;
* the decomposer records its verdict WITH the matched signals — the (f) trace.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.tests import glass_rows


def _reset(tmp: str, disabled: bool = False) -> None:
    """Point glass at a clean temp state dir and rebuild the singleton so it
    re-reads the env (path + enabled)."""
    os.environ["XDG_STATE_HOME"] = tmp
    if disabled:
        os.environ["INTERGEN_GLASS"] = "0"
    else:
        os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _rows(tmp: str) -> list[dict]:
    return glass_rows.read(tmp)


def _turn_rows(tmp: str) -> list[dict]:
    """Rows a caller emitted, without the writer's own bookkeeping.

    The writer records its own state in the "glass" phase — the rotation marker
    that explains a gap, and the sequence_resumed row that says where a new
    process picked the counter up (N-02/N-03). Those are real rows a reader
    wants, but they are not the emissions these contract tests are about.

    Excluding one phase is NOT a substitute for naming the row wanted: the next
    row the writer learns to emit need not be in the "glass" phase, and then
    position zero moves again. Every case below names its row through
    intergen/tests/glass_rows.py; this stays only for the cases that count what
    a caller emitted, where the exclusion is the point.
    """
    return [r for r in _rows(tmp) if r.get("phase") != "glass"]


class GlassWriterContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _reset(self.tmp)

    def test_turn_threading_and_monotonic_seq(self) -> None:
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            self.assertEqual(glass.current_turn_id(), tid)
            glass.emit("route", "turn_start", detail={"user_msg": "hi"})
            glass.emit("delivery", "final", detail={"text": "yo"}, dur_ms=5.0)
        # The count is of the rows this turn EMITTED, not of everything in the
        # file: only() below fails unless there is exactly one of each, which is
        # the count that used to be written as len(rows) == 2. That older form
        # was a statement about the writer's bookkeeping as well, and it moved
        # every time the writer learned to say something new. Whether a turn
        # writes ONLY these rows is asserted where it belongs, in
        # test_glass_turn_lifecycle's joinable-from-its-id-alone case.
        rows = glass_rows.where(_rows(self.tmp), turn_id=tid)
        start = glass_rows.only(rows, phase="route", event="turn_start")
        final = glass_rows.only(rows, phase="delivery", event="final")
        self.assertTrue(all(r["turn_id"] == tid for r in rows))
        self.assertEqual([r["seq"] for r in rows],
                         sorted(r["seq"] for r in rows))
        self.assertEqual(start["iface"], "web")
        self.assertIsNotNone(final["t_rel_ms"])
        self.assertEqual(final["dur_ms"], 5.0)

    def test_secret_redaction_is_in_place_and_attested(self) -> None:
        with glass.turn(glass.new_turn_id(), "dbus"):
            glass.emit("prompt", "assembled", detail={
                "password": "hunter2", "api_key": "sk-xyz", "system": "ok",
                "nested": {"bearer_token": "b", "keep": 1}})
        d = glass_rows.only(_rows(self.tmp),
                            phase="prompt", event="assembled")["detail"]
        # redacted VALUE, attested placeholder naming the key — never silent
        self.assertEqual(d["password"], "<redacted:password>")
        self.assertEqual(d["api_key"], "<redacted:api_key>")
        self.assertEqual(d["nested"]["bearer_token"], "<redacted:bearer_token>")
        # non-secret content preserved verbatim
        self.assertEqual(d["system"], "ok")
        self.assertEqual(d["nested"]["keep"], 1)

    def test_disabled_is_loud_and_noop(self) -> None:
        tmp2 = tempfile.mkdtemp()
        _reset(tmp2, disabled=True)
        try:
            self.assertFalse(glass.glass_enabled())
            with glass.turn(glass.new_turn_id(), "web"):
                glass.emit("route", "should_not_write")
            self.assertFalse((Path(tmp2) / "intergen" / "glass.jsonl").exists())
        finally:
            _reset(self.tmp)  # restore enabled for other tests

    def test_warmup_override_turn_id_and_iface(self) -> None:
        glass.emit("warmup", "daemon_start", turn_id="boot-1", iface="daemon")
        r = glass_rows.only(_rows(self.tmp),
                            phase="warmup", event="daemon_start")
        self.assertEqual(r["turn_id"], "boot-1")
        self.assertEqual(r["iface"], "daemon")
        self.assertIsNone(r["t_rel_ms"])  # no turn-start anchor for a boot row

    def test_file_is_owner_only(self) -> None:
        with glass.turn(glass.new_turn_id(), "web"):
            glass.emit("route", "x")
        p = Path(self.tmp) / "intergen" / "glass.jsonl"
        self.assertEqual(oct(os.stat(p).st_mode)[-3:], "600")

    def test_rotation_rolls_and_drops_marker(self) -> None:
        orig = glass._ROTATE_BYTES
        glass._ROTATE_BYTES = 400  # tiny cap to force a roll
        try:
            with glass.turn(glass.new_turn_id(), "web"):
                for i in range(60):
                    glass.emit("route", "pad", detail={"i": i, "x": "y" * 20})
        finally:
            glass._ROTATE_BYTES = orig
        base = Path(self.tmp) / "intergen" / "glass.jsonl"
        self.assertTrue(base.with_name("glass.jsonl.1").exists())
        markers = glass_rows.where(_rows(self.tmp), event="rotation")
        self.assertTrue(markers, "a rotation marker must self-explain the gap")

    def test_reader_round_trips(self) -> None:
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("route", "turn_start", detail={"user_msg": "hello"})
        row = glass_rows.only(
            glass.read_rows(Path(self.tmp) / "intergen" / "glass.jsonl"),
            phase="route", event="turn_start")
        self.assertEqual(row["turn_id"], tid)


class RouterHistoryWriteEmission(unittest.TestCase):
    """The model-facing buffer write (_append_history) emits — its ABSENCE on the
    streamed path is the (a)/(c) root cause, so its emission is load-bearing."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _reset(self.tmp)

    def test_append_history_emits_history_write(self) -> None:
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
        r._conversation_history = []
        r._max_history = 20
        with glass.turn(glass.new_turn_id(), "web"):
            r._append_history("what is the capital of Brazil?", "Brasília.")
        hw = glass_rows.where(_rows(self.tmp), event="history_write")
        self.assertEqual(len(hw), 1)
        written = glass_rows.only(_rows(self.tmp), event="history_write")
        self.assertEqual(written["detail"]["store"], "conversation_history")
        self.assertEqual(written["detail"]["response"], "Brasília.")
        self.assertEqual(written["detail"]["len_after"], 2)


class DecomposerVerdictEmission(unittest.TestCase):
    """The (f) trace: the decomposer records is_compound + the matched signals."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _reset(self.tmp)

    def test_plus_misroute_is_visible(self) -> None:
        # M5 (decomposer.py): arithmetic "plus" is an OPERATOR, not a conjunction,
        # so "2 plus 2" is one un-decomposed turn (is_compound=False — the
        # anti-lobotomy win where the model answers arithmetic in a single turn).
        # This guards that the decomposer STILL EMITS its (f) decompose verdict for
        # the arithmetic case — the plus route stays visible in glass — now
        # recording the corrected non-compound value; the misroute it once caught is
        # fixed. On the non-compound path the emitted detail is only
        # {is_compound, needs_decomposition} (matched_signals/threshold live on the
        # compound branch), so those keys are no longer asserted here.
        from intergen.decomposer import analyze_query
        from intergen.interfaces.types import HardwareTierLevel
        with glass.turn(glass.new_turn_id(), "web"):
            analyze_query("what is 2 plus 2", HardwareTierLevel.TIER_2)
        dec = glass_rows.where(_rows(self.tmp), event="decompose")
        self.assertTrue(dec)                        # verdict EMITTED (visibility)
        d = glass_rows.last(_rows(self.tmp), event="decompose")["detail"]
        self.assertFalse(d["is_compound"])          # arithmetic plus is NOT compound
        self.assertFalse(d["needs_decomposition"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
