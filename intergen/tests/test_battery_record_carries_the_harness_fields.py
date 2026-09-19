# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A battery record says what the harness measured about the end of the turn.

The harness computes two things about every turn it drives: WHEN the terminal
frame arrived (``terminal_at``), and how many frames the server put on the wire
AFTER it (``late_frames``). Decided 2026-09-18: both are computed and neither
reached the record, so a sealed battery record could not answer the question
the settle window was added to answer — did this turn keep speaking after it
ended, and when did it end? A reader of the record had to take the absence on
faith, which is the same thing the harness was changed to stop doing.

Two properties are pinned here.

  * EVERY terminal path observes the drain. The streaming terminal
    (``stream_end``) and the failure terminal (``error``) read on for a short
    settle window; the non-streaming fast reply (``response``) used to return
    the moment it arrived, so on that path a recorded ``late_frames`` of 0
    would have been a zero the instrument never looked for, and ``terminal_at``
    was never set at all. A count that was not observed must not read like a
    count that was.
  * THE RECORD CARRIES BOTH FIELDS, for every question, in ``verdicts.tsv`` and
    in ``summary.json``. A turn that never reached a terminal frame writes an
    empty time rather than a zero, because there was no arrival to time.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import aiohttp

from intergen.tests import web_battery as wb
from intergen.tests import ws_harness


# ── the harness half: a scripted socket, as the settle-window tests use ────
class _Msg:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data


class _ScriptedWS:
    """Hands out a fixed script of frames, then blocks — so a harness that
    read on with no bound would hang here rather than pass quietly."""

    def __init__(self, frames, then="block"):
        self._frames = list(frames)
        self._then = then
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive(self):
        if self._frames:
            return self._frames.pop(0)
        if self._then == "close":
            return _Msg(aiohttp.WSMsgType.CLOSED)
        await asyncio.Event().wait()   # never returns

    async def close(self):
        pass


def _text(payload):
    return _Msg(aiohttp.WSMsgType.TEXT, json.dumps(payload))


def _drive(frames, then="block", settle_s=0.2, deadline_s=5.0):
    r = ws_harness.WSTurnResult(query="q")
    asyncio.run(ws_harness._drive_turn(
        _ScriptedWS(frames, then), r, gate_decision=None,
        gate_action="respond", deadline_s=deadline_s, settle_s=settle_s))
    return r


class TheFastReplyPathIsObservedLikeTheOthers(unittest.TestCase):

    def test_a_response_terminated_turn_records_when_it_ended(self):
        r = _drive([_text({"type": "response", "turn_id": "t",
                           "content": "hi"})])
        self.assertTrue(r.terminal)
        self.assertEqual(r.text, "hi")
        self.assertIsNotNone(r.terminal_at)

    def test_a_frame_sent_after_a_response_frame_is_recorded(self):
        """The blind spot on the non-streaming path: before this, a card sent
        after the fast reply could not be seen at all."""
        r = _drive([
            _text({"type": "response", "turn_id": "t", "content": "hi"}),
            _text({"type": "tool_executed", "tool_name": "read_file",
                   "success": True, "summary": "318 bytes"}),
        ])
        self.assertTrue(r.terminal)
        self.assertEqual(r.text, "hi")
        self.assertIn("tool_executed", [t for _at, t in r.events])
        self.assertEqual(r.late_frames, 1)

    def test_a_fast_reply_with_nothing_after_it_is_not_a_failure(self):
        r = _drive([_text({"type": "response", "turn_id": "t",
                           "content": "hi"})])
        self.assertEqual(r.late_frames, 0)
        self.assertEqual(r.closed_by, "client")


# ── the record half ────────────────────────────────────────────────────────
GREETING = wb.Question("q01", "greeting", "hi", model_required=False)


def _turn(*, terminal_at=1.25, late_frames=0, terminal=True):
    frames = [{"type": "turn_ack", "turn_id": "t1"},
              {"type": "response", "turn_id": "t1", "content": "hello"}]
    return SimpleNamespace(
        messages=frames, text="hello", terminal=terminal, closed_by="client",
        elapsed_s=1.5, events=[(0.0, f.get("type")) for f in frames],
        terminal_at=terminal_at, late_frames=late_frames)


class TheRecordCarriesTheHarnessFields(unittest.TestCase):

    def test_the_verdict_carries_both_fields(self):
        v = wb._grade(GREETING, _turn(terminal_at=1.25, late_frames=2), None)
        self.assertEqual(v.terminal_at, 1.25)
        self.assertEqual(v.late_frames, 2)

    def test_a_turn_with_no_terminal_frame_records_no_time_not_a_zero(self):
        v = wb._grade(GREETING,
                      SimpleNamespace(messages=[], text="", terminal=False,
                                      closed_by="deadline", elapsed_s=30.0,
                                      events=[], terminal_at=None,
                                      late_frames=0),
                      None)
        self.assertIsNone(v.terminal_at)
        self.assertEqual(wb._verdict_cells(v)[wb.VERDICT_COLUMNS.index(
            "terminal_at")], "")

    def test_a_turn_object_without_the_fields_raises(self):
        """A double that cannot supply what the harness measures must fail
        loudly; a default would record a measurement nobody took."""
        blind = SimpleNamespace(messages=[], text="", terminal=False,
                                closed_by="client", elapsed_s=1.0, events=[])
        with self.assertRaises(AttributeError):
            wb._grade(GREETING, blind, None)

    def test_verdicts_tsv_names_both_columns_and_writes_them(self):
        v = wb._grade(GREETING, _turn(terminal_at=0.75, late_frames=3), None)
        with TemporaryDirectory() as d:
            path = Path(d) / "verdicts.tsv"
            wb._write_verdicts(path, [v])
            lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        self.assertIn("terminal_at", header)
        self.assertIn("late_frames", header)
        row = dict(zip(header, lines[1].split("\t")))
        self.assertEqual(row["terminal_at"], "0.75")
        self.assertEqual(row["late_frames"], "3")
        self.assertEqual(len(lines[1].split("\t")), len(header))

    def test_every_question_in_the_record_carries_them(self):
        verdicts = [wb._grade(GREETING, _turn(terminal_at=float(i),
                                              late_frames=i), None)
                    for i in range(14)]
        per_question = [v.__dict__ for v in verdicts]
        self.assertEqual(len(per_question), 14)
        for i, row in enumerate(per_question):
            self.assertIn("terminal_at", row)
            self.assertIn("late_frames", row)
            self.assertEqual(row["late_frames"], i)
        # summary.json is written with json.dumps; the fields must survive it.
        back = json.loads(json.dumps({"per_question": per_question}))
        self.assertEqual(back["per_question"][7]["terminal_at"], 7.0)


if __name__ == "__main__":
    unittest.main()
