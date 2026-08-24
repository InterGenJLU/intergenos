# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""REC-18 C03 — the glass retention ceiling must be the truth about the disk.

MEASURED ON THE SHIPPED PATH BEFORE THIS FILE EXISTED
(cut095-glass-evidence/stepA-measure/b-retention-measurement.log). Two findings,
both reproduced by the tests below:

  1. THE STATED CEILING UNDERCOUNTS BY A WHOLE FILE. glass.py said "Roll at
     64 MB, keep 5 (~320 MB ceiling)". Five kept files is 320 MiB, but the LIVE
     file is on the same disk, so the real worst case is six files — 384 MiB.
     Driven at a reduced rotation size, six files were on disk holding more
     bytes than the stated ceiling allowed.

  2. ONE OVERSIZED ROW DEFEATS THE CEILING ENTIRELY. Rotation is decided before
     a row is written, so a row larger than the rotation size rotates the file
     away and is then written whole into the fresh one. Measured: with an 8 KiB
     cap, a single row left a 25,051-byte live file, and the total on disk
     passed even the corrected bound. A stream of such rows rolls the entire
     retained history out of the record in a few writes.

WHAT IS ASSERTED HERE. That the number the module states about itself is the
number a disk would measure, and that no single row can put a file over the
rotation size. A ceiling nobody can exceed is a fact; a ceiling that holds only
for rows of an ordinary size is a hope, and the difference is invisible to
whoever is relying on the number.

THE TRUNCATION IS ATTESTED, NEVER SILENT. An oversized row is still written,
keeps its turn, its sequence number, its phase and its event, and carries what
was dropped and how many bytes it was. Silently shortening the trace would put
this module in the position of deciding what the record does not have to say,
which is the one thing it exists not to do.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass


class RetentionCeilingIsTheTruth(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-retention-")
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("INTERGEN_GLASS", None)
        glass._glass = None
        # Reduced so the drive takes seconds instead of writing hundreds of
        # megabytes. What is under test is a RATIO between the cap, the number
        # kept and the live file, and a ratio does not care about the scale.
        self._rotate = glass._ROTATE_BYTES
        # getattr, so the red state of this file fails on the CONTRACT rather
        # than erroring in setUp before a single assertion is reached.
        self._maxrow = getattr(glass, "_MAX_ROW_BYTES", None)
        glass._ROTATE_BYTES = 8 * 1024
        if self._maxrow is not None:
            glass._MAX_ROW_BYTES = glass._ROTATE_BYTES // 8

    def tearDown(self) -> None:
        glass._ROTATE_BYTES = self._rotate
        if self._maxrow is not None:
            glass._MAX_ROW_BYTES = self._maxrow
        glass._glass = None

    # -- helpers ---------------------------------------------------------
    def _dir(self) -> Path:
        return Path(self.tmp) / "intergen"

    def _files(self) -> list[Path]:
        return sorted(p for p in self._dir().iterdir()
                      if p.name.startswith("glass.jsonl"))

    def _bytes_on_disk(self) -> int:
        return sum(p.stat().st_size for p in self._files())

    def _ceiling(self) -> int:
        return (glass._ROTATE_KEEP + 1) * glass._ROTATE_BYTES

    def _rows(self, path: Path) -> list[dict]:
        with open(path) as f:
            return [json.loads(x) for x in f]

    # -- the arithmetic --------------------------------------------------
    def test_the_module_states_the_ceiling_it_can_actually_reach(self) -> None:
        """The number in the module and the number on the disk are the same
        number. Stated separately from the drive below, because a constant that
        drifts from the code it describes is the defect C03 names."""
        self.assertEqual(
            glass.retention_ceiling_bytes(),
            (glass._ROTATE_KEEP + 1) * glass._ROTATE_BYTES,
            "the stated ceiling must count the live file as well as the kept "
            "ones — they are on the same disk")

    # -- the drive -------------------------------------------------------
    def test_ordinary_rows_stay_under_the_stated_ceiling(self) -> None:
        g = glass.get_glass()
        payload = "x" * 400
        for i in range(400):
            g.emit("probe", "row", detail={"i": i, "payload": payload})
        self.assertGreater(len(self._files()), 1,
                           "control: the drive never rotated, so this test "
                           "measured nothing")
        self.assertLessEqual(self._bytes_on_disk(), self._ceiling())

    def test_no_single_row_can_put_a_file_over_the_rotation_size(self) -> None:
        """The oversized-row case. Rotation is decided before a row is written,
        so an unbounded row lands whole in the fresh file and the cap means
        nothing for it."""
        g = glass.get_glass()
        g.emit("probe", "huge", detail={"payload": "y" * (glass._ROTATE_BYTES * 3)})
        for p in self._files():
            with self.subTest(file=p.name):
                self.assertLessEqual(
                    p.stat().st_size, glass._ROTATE_BYTES,
                    f"{p.name} is larger than the rotation size; a single row "
                    f"defeated the ceiling")

    def test_an_oversized_row_stays_under_the_ceiling_in_bulk(self) -> None:
        """A stream of them, which is the case that empties the record."""
        g = glass.get_glass()
        for i in range(20):
            g.emit("probe", "huge",
                   detail={"i": i, "payload": "y" * (glass._ROTATE_BYTES * 3)})
        self.assertLessEqual(self._bytes_on_disk(), self._ceiling())

    # -- the truncation is attested --------------------------------------
    def test_an_oversized_row_is_still_recorded_and_still_joinable(self) -> None:
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("prompt", "assembled",
                       detail={"text": "z" * (glass._ROTATE_BYTES * 3)})
            glass.emit("delivery", "final", detail={"text": "done"})
        rows = [r for f in self._files() for r in self._rows(f)]
        assembled = [r for r in rows if r.get("event") == "assembled"]
        self.assertEqual(len(assembled), 1, rows)
        self.assertEqual(assembled[0]["turn_id"], tid)
        self.assertEqual(assembled[0]["phase"], "prompt")

    def test_the_dropped_bytes_are_named_not_silently_gone(self) -> None:
        glass.emit("prompt", "assembled",
                   detail={"text": "z" * (glass._ROTATE_BYTES * 3)})
        rows = [r for f in self._files() for r in self._rows(f)]
        row = [r for r in rows if r.get("event") == "assembled"][0]
        note = row["detail"].get("glass_oversized_row")
        self.assertIsNotNone(
            note, f"an oversized row was shortened with no attestation: {row}")
        self.assertGreater(note["original_bytes"], note["limit_bytes"])
        self.assertIn("truncated_detail", row["detail"])

    def test_an_ordinary_row_is_not_touched(self) -> None:
        """Control. The truncation must fire only where it is needed, or the
        two tests above would pass on a writer that shortens everything."""
        glass.emit("prompt", "assembled", detail={"text": "a short prompt"})
        rows = [r for f in self._files() for r in self._rows(f)]
        row = [r for r in rows if r.get("event") == "assembled"][0]
        self.assertEqual(row["detail"], {"text": "a short prompt"})


if __name__ == "__main__":
    unittest.main()
