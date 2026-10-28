# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A battery record carries each turn's decision path.

The battery already reads the target's trace rows for the turn ids it minted
(glass-excerpt.jsonl) and grades from a few of them. A turn's decision path —
every ladder stage in order, the retrieval with its candidates and threshold,
the tool calls with their consent state, the prompt's sources, each screen's
verdict and what it changed — is one decision/path row on the same trace, so a
graded turn can be explained from the record without going back to the
machine. decision-paths.jsonl holds it: one line per question or replay turn,
in the order sent, read from the rows already fetched.

A turn with no path is a line that says why (the frames named no turn id, the
trace was not readable, or it holds no path for the turn), never a missing
line: a reader counting lines against turns must not have to guess which turn
a gap belongs to.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen.tests import web_battery as wb
from intergen.tests import ws_harness
from intergen.tests.test_battery_replay import (HEADER, _ack, _Machine,
                                                _no_popen, _response, _turn)

PATH_T01 = {"path_schema": 1, "ladder": [
    {"pass": 1, "stage": "empty_input", "outcome": "declined"},
    {"pass": 1, "stage": "direct_answer", "outcome": "matched"}]}


def _row(tid, phase, event, detail):
    return {"turn_id": tid, "phase": phase, "event": event, "detail": detail}


class WritingTheDecisionPaths(unittest.TestCase):

    def _write(self, keyed, rows, err=""):
        tmp = Path(tempfile.mkdtemp(prefix="decision-paths-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / wb.DECISION_PATHS_NAME
        carried = wb._write_decision_paths(out, keyed, rows, err)
        return carried, [json.loads(x) for x in out.read_text().splitlines()]

    def test_each_turn_has_a_line_in_order_and_a_missing_path_says_why(self):
        rows = {"t01": [_row("t01", "route", "decided", {"source": "x"}),
                        _row("t01", "decision", "path", PATH_T01),
                        _row("t01", "decision", "path_late",
                             {"section": "tools", "entry": {"tool": "late"}})],
                "t02": [_row("t02", "route", "decided", {"source": "y"})]}
        carried, lines = self._write(
            [("q1", "t01"), ("q2", "t02"), ("q3", "")], rows)
        self.assertEqual(carried, 1)
        self.assertEqual([x["turn"] for x in lines], ["q1", "q2", "q3"])
        self.assertEqual(lines[0]["paths"], [PATH_T01])
        self.assertEqual(lines[0]["late"],
                         [{"section": "tools", "entry": {"tool": "late"}}])
        self.assertNotIn("why_none", lines[0])
        self.assertEqual(lines[1]["paths"], [])
        self.assertEqual(lines[1]["why_none"],
                         "the trace holds no decision path for this turn")
        self.assertIsNone(lines[2]["turn_id"])
        self.assertEqual(lines[2]["why_none"], "the frames named no turn id")

    def test_an_unreadable_trace_is_named_on_every_line(self):
        carried, lines = self._write([("q1", "t01"), ("q2", "t02")], None,
                                     err="glass.jsonl unreadable (rc=2)")
        self.assertEqual(carried, 0)
        self.assertEqual(
            {x["why_none"] for x in lines},
            {"the trace was not readable: glass.jsonl unreadable (rc=2)"})

    def test_the_turn_ids_are_the_first_each_turn_named(self):
        results = [(SimpleNamespace(key="q1"),
                    _turn([{"type": "x"}, _ack("t01"), _ack("t09")])),
                   (SimpleNamespace(key="q2"), _turn([{"type": "x"}]))]
        self.assertEqual(wb._turn_ids(results), [("q1", "t01"), ("q2", "")])


class _TraceMachine(_Machine):
    """The replay record's machine double, with a trace that holds a decision
    path for every turn the replay sends."""

    def run(self, argv, **kwargs):
        if list(argv)[-1].startswith("grep -F "):
            self.calls.append(list(argv))
            out = "".join(json.dumps(_row(t, "decision", "path",
                                          {**PATH_T01, "turn": t})) + "\n"
                          for t in ("t01", "t02"))
            return subprocess.CompletedProcess(list(argv), 0, stdout=out,
                                               stderr="")
        return super().run(argv, **kwargs)


class TheReplayRecordCarriesEachTurnsPath(unittest.TestCase):

    def test_the_sealed_record_holds_the_path_of_each_turn(self):
        tmp = Path(tempfile.mkdtemp(prefix="replay-paths-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        replay = tmp / "case.tsv"
        replay.write_bytes(("# case\n" + HEADER + "\n"
                            "1\t2\tmachine\t\t\tis the firewall on?\t-\t-\n"
                            "2\t2\tmachine\t\t\tand the disk?\t-\t-\n").encode())
        record = tmp / "record"

        async def drive(turns, local_port, token, deadline_s, frames_out,
                        target, ssh_port, reads):
            results = []
            with frames_out.open("w", encoding="utf-8") as fh:
                for t in turns:
                    frames = [_ack(t.key), _response("It is on.",
                                                     "direct_answer", t.key)]
                    for m in frames:
                        fh.write(json.dumps({"question": t.key, "frame": m})
                                 + "\n")
                    results.append((t, _turn(frames, text="It is on.")))
            return results

        with mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(wb.subprocess, "run", _TraceMachine().run), \
                mock.patch.object(wb.subprocess, "Popen", _no_popen), \
                mock.patch.object(wb, "_checkout_above", lambda start: None), \
                mock.patch.object(wb, "_drive_replay", drive), \
                mock.patch.object(ws_harness, "daemon_reachable",
                                  lambda host, port: True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            wb.main(["--local", "--replay", str(replay), "--output",
                     str(record), "--tree-sha", "abc1234"])

        lines = [json.loads(x) for x in
                 (record / wb.DECISION_PATHS_NAME).read_text().splitlines()]
        self.assertEqual([(x["turn"], x["turn_id"]) for x in lines],
                         [("t01", "t01"), ("t02", "t02")])
        self.assertEqual([x["paths"][0]["turn"] for x in lines], ["t01", "t02"])
        summary = json.loads((record / "summary.json").read_text())
        self.assertEqual(summary["decision_paths"],
                         {"file": wb.DECISION_PATHS_NAME, "turns": 2,
                          "with_a_path": 2})
        sealed = (record / wb.SEAL_NAME).read_text().split()[1::2]
        self.assertIn(wb.DECISION_PATHS_NAME, sealed)


if __name__ == "__main__":
    unittest.main()
