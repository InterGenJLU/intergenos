# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""N-02 / N-03 — the glass sequence number must order the whole record.

MEASURED ON THE SHIPPED PATH BEFORE THIS FILE EXISTED
(cut095-glass-evidence/stepA-measure/a-shipped-measurement.log): two separate
processes writing to the same glass.jsonl both wrote seq [0, 1, 2]. The counter
is an itertools.count created at import, so it restarts at zero every time the
daemon starts.

WHY THAT MATTERS. "seq" exists to give a total replay order even for rows born
in the same millisecond — that is what its own comment says it is for. Across a
restart it does the opposite: a reader ordering by seq interleaves two different
runs into one plausible, wrong timeline, and nothing in the file says it
happened. Timestamps do not rescue it either, because two rows can share a
millisecond and a clock can move.

WHAT IS ASSERTED HERE. The tests drive REAL processes — the defect is a property
of process start, so a fixture that imports the module once cannot see it. Each
child writes its rows through the ordinary emit() path and exits; the tests then
read the file those processes actually wrote.

The contract, in three parts:
  1. A restarted process never reuses a sequence number already in the file.
  2. Every row names the run that wrote it, so if a resume ever fails the
     collision is VISIBLE in the record rather than silently plausible.
  3. The resume is recorded, not silent — a reader is told where the counter
     picked up and why.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


def _probe_rows(tmp: str) -> list[dict]:
    return [r for r in _rows(tmp) if r.get("phase") == "probe"]


class SequenceSurvivesARestart(unittest.TestCase):
    """One process at a time, several times over — the daemon's real shape."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-seq-")

    def _child(self, tag: str, rows: int = 3) -> None:
        """Run a SEPARATE python process that writes `rows` glass rows.

        A separate process, because the thing under test is what happens when
        the module is imported again. Headless by construction: no prompt, no
        tty, and a failure raises with both streams attached.
        """
        code = (
            "import intergen.glass as g\n"
            f"for i in range({rows}):\n"
            f"    g.emit('probe', 'row', detail={{'tag': {tag!r}, 'i': i}})\n"
        )
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = self.tmp
        env["PYTHONPATH"] = str(_REPO_ROOT)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("INTERGEN_GLASS", None)
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              cwd=str(_REPO_ROOT), capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"child {tag} failed\nstdout:{proc.stdout}\n"
                         f"stderr:{proc.stderr}")

    def test_the_harness_actually_writes_rows(self) -> None:
        """Control. Every assertion below is vacuous if this fails."""
        self._child("only")
        self.assertEqual(len(_probe_rows(self.tmp)), 3, _rows(self.tmp))

    def test_one_process_numbers_its_own_rows_consecutively(self) -> None:
        """Control for the property: within a run the counter already works, so
        a failure below is about the restart and not about counting."""
        self._child("only")
        seqs = [r["seq"] for r in _probe_rows(self.tmp)]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_a_restarted_process_does_not_reuse_a_sequence_number(self) -> None:
        self._child("first")
        self._child("second")
        seqs = [r["seq"] for r in _probe_rows(self.tmp)]
        self.assertEqual(len(seqs), 6, _rows(self.tmp))
        self.assertEqual(
            len(set(seqs)), 6,
            f"two runs wrote the same sequence numbers ({seqs}); a reader "
            f"ordering by seq would interleave them into one wrong timeline")

    def test_the_sequence_still_increases_in_file_order(self) -> None:
        """The order the file was written in and the order seq implies must be
        the same order — otherwise seq is worse than absent, because it looks
        authoritative."""
        self._child("first")
        self._child("second")
        seqs = [r["seq"] for r in _probe_rows(self.tmp)]
        self.assertEqual(seqs, sorted(seqs), _rows(self.tmp))

    def test_every_row_names_the_run_that_wrote_it(self) -> None:
        """The namespace half. If a resume ever fails — an unreadable file, a
        rotation racing a restart — the collision must be VISIBLE rather than
        silently plausible."""
        self._child("first")
        self._child("second")
        runs = {r.get("run") for r in _probe_rows(self.tmp)}
        self.assertNotIn(None, runs, "a glass row does not name its run")
        self.assertEqual(len(runs), 2,
                         f"two separate processes reported the same run id: "
                         f"{runs}")

    def test_the_resume_is_recorded_not_silent(self) -> None:
        """Attested, like every other gap in this record: each process says
        where it picked the counter up, and the second one names the number the
        first one actually stopped at.

        One marker per run, not one per file: a restart is a real discontinuity
        and the boundary is what a reader needs. (The red version of this test
        expected a single marker across both runs, which would have left the
        first run's boundary unrecorded.)"""
        self._child("first")
        first_high = max(r["seq"] for r in _rows(self.tmp))
        self._child("second")
        resumed = [r for r in _rows(self.tmp)
                   if r.get("phase") == "glass"
                   and r.get("event") == "sequence_resumed"]
        self.assertEqual(len(resumed), 2, _rows(self.tmp))
        self.assertIsNone(resumed[0]["detail"].get("resumed_from"),
                          resumed[0])
        self.assertEqual(resumed[1]["detail"].get("resumed_from"), first_high,
                         resumed[1])

    def test_a_first_run_on_an_empty_file_says_so(self) -> None:
        """The other branch of the same record: nothing to resume from is a
        fact worth writing, not a reason to stay quiet."""
        self._child("first")
        resumed = [r for r in _rows(self.tmp)
                   if r.get("phase") == "glass"
                   and r.get("event") == "sequence_resumed"]
        self.assertEqual(len(resumed), 1, _rows(self.tmp))
        self.assertIsNone(resumed[0]["detail"].get("resumed_from"),
                          resumed[0])


if __name__ == "__main__":
    unittest.main()
