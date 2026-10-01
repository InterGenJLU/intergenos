# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A record whose integrity cannot be checked must be reported as untrustworthy.

THE RULE AS THE GOVERNING RECORD STATES IT. The review plan's records part requires
that "a record whose integrity cannot be checked at all is reported as untrustworthy
rather than as intact", and its mechanism annex requires the proof: "a sealed copy of a
record is altered by one byte and the integrity read-back must refuse it, with the
refusal captured beside the pass on the untampered record". It adds the condition every
control in that plan is held to: "a control is only reported sound when it has been seen
to refuse something during the leg".

WHAT THIS FILE MEASURES. Before this correction the ledger's reader skipped a line it
could not parse, left a debug-level breadcrumb nobody reads, and yielded the rest, so a
record altered by one byte came back one row shorter with no signal; the user-facing
command then printed that shorter count as the record. There was no integrity read-back
of any kind -- the words integrity, trust, tamper and untrustworthy appeared nowhere in
the writer or in the command.

WHAT IS DELIBERATELY NOT BUILT HERE. No digest, no signature and no sidecar, and the
writer references none. That is a decision of record, and it bounds what any read-back
can establish: a whole line removed, or replaced with a different well-formed line, is
not detectable from the record alone. The read-back is therefore required never to call
a record intact. Its positive verdict says only that no damage was detected, and names
in the same breath what it cannot establish. These cases assert that wording, because a
read-back that overclaimed would be worse than none: a reader would trust a record the
read-back cannot actually vouch for.

Hermetic: a throwaway directory per test, no service, socket, model server, display or
network, and no file outside the test's own directory is read or written.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen import audit_log
from intergen.interfaces.provenance import AuditRecord, Provenance


def a_record(**overrides) -> AuditRecord:
    fields = dict(
        timestamp="2026-10-01T00:00:00+00:00",
        tool_name="read_file",
        arguments={"path": "/etc/fstab"},
        declared_provenance=Provenance.USER_DIRECT.value,
        effective_provenance=Provenance.USER_DIRECT.value,
        ingress_tools_this_turn=[],
        user_decision="executed",
    )
    fields.update(overrides)
    return AuditRecord(**fields)


class TheIntegrityReadBackRefusesATamperedRecord(unittest.TestCase):
    """The known-tamper control, and the pass on the untampered record beside it."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-integrity-")
        self.addCleanup(self._td.cleanup)
        self.path = Path(self._td.name) / "intergen" / "tool-dispatch.jsonl"
        for i in range(3):
            self.assertTrue(audit_log.write_record(
                a_record(timestamp=f"2026-10-01T0{i}:00:00+00:00",
                         result_summary=f"row {i}"),
                self.path))
        self.sealed = self.path.read_bytes()
        self.assertEqual(len(self.sealed.splitlines()), 3)

    def _alter_one_byte(self) -> tuple[int, int]:
        """Change exactly ONE byte of the sealed copy, inside the second row.

        A quotation mark is turned into a space, which is the smallest edit that
        makes a line stop being JSON. Returns the offset changed and the byte count
        before and after, so the cell can assert the edit really was one byte.
        """
        before = self.path.read_bytes()
        offset = before.index(b"\n") + before[before.index(b"\n"):].index(b'"')
        altered = bytearray(before)
        altered[offset] = ord(" ")
        self.path.write_bytes(bytes(altered))
        after = self.path.read_bytes()
        self.assertEqual(len(before), len(after), "the edit changed the file LENGTH")
        self.assertEqual(sum(1 for x, y in zip(before, after) if x != y), 1,
                         "the edit changed more than one byte")
        return offset, len(after)

    # ---- the control that must REFUSE ------------------------------------

    def test_a_one_byte_alteration_is_refused_and_named(self):
        """The known-tamper control the governing annex requires."""
        self._alter_one_byte()
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(
            reading.trustworthy,
            "a record altered by one byte was not refused: "
            f"{reading.verdict}")
        self.assertEqual(reading.unreadable_lines, [2],
                         "the refusal did not NAME the line it refused on")
        self.assertIn("UNTRUSTWORTHY", reading.verdict)

    def test_the_refusal_says_how_many_rows_went_missing(self):
        """A reader must be told the record is short, not handed the remainder."""
        self._alter_one_byte()
        reading = audit_log.read_integrity(self.path)
        self.assertEqual(reading.lines, 3)
        self.assertEqual(reading.rows, 2)
        self.assertEqual(reading.lines - reading.rows, 1)
        self.assertTrue(any("1" in line and "could not be read" in line
                            for line in reading.report()),
                        f"the report does not say a line was lost: {reading.report()}")

    def test_a_row_missing_a_required_field_is_refused(self):
        """Damage that still parses as JSON is still damage."""
        rows = [json.loads(l) for l in
                self.path.read_text(encoding="utf-8").splitlines()]
        del rows[1]["timestamp"]
        self.path.write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.trustworthy,
                         f"a row with no timestamp was accepted: {reading.verdict}")
        self.assertEqual(reading.incomplete_rows, [2])

    def test_rows_whose_timestamps_go_backwards_are_refused(self):
        """An append-only record's timestamps do not decrease."""
        rows = [json.loads(l) for l in
                self.path.read_text(encoding="utf-8").splitlines()]
        rows[1]["timestamp"] = "2025-01-01T00:00:00+00:00"
        self.path.write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.trustworthy,
                         f"a backwards timestamp was accepted: {reading.verdict}")
        self.assertEqual(reading.out_of_order_rows, [2])

    # ---- the pass beside it, which must NOT overclaim ---------------------

    def test_the_untampered_record_passes(self):
        """The pass captured beside the refusal, as the annex requires."""
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.trustworthy, reading.report())
        self.assertEqual(reading.lines, 3)
        self.assertEqual(reading.rows, 3)
        self.assertEqual(reading.unreadable_lines, [])
        self.assertEqual(reading.incomplete_rows, [])
        self.assertEqual(reading.out_of_order_rows, [])

    def test_the_passing_verdict_never_calls_the_record_intact(self):
        """The read-back must not vouch for what it cannot establish.

        With no digest, no signature and no sidecar, a line removed whole or
        replaced with another well-formed line is not detectable from this record.
        A verdict of "intact" would tell a reader the opposite.
        """
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.trustworthy)
        self.assertNotIn("intact", reading.verdict.lower(),
                         "the passing verdict calls the record intact")
        self.assertIn("NO DETECTABLE DAMAGE", reading.verdict)
        text = " ".join(reading.report()).lower()
        for phrase in ("cannot establish", "removed", "digest"):
            self.assertIn(phrase, text,
                          f"the report does not say what it cannot establish: "
                          f"{reading.report()}")

    def test_a_whole_row_removed_is_reported_as_undetectable_not_as_a_pass(self):
        """The limit, measured rather than argued.

        Removing a line leaves a record that passes every check this read-back can
        make. The cell asserts BOTH halves: the reading does not claim damage it
        cannot see, AND it does not claim the record is sound either.
        """
        rows = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join(rows[:2]) + "\n", encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.trustworthy,
                        "a removed line is not detectable, so this must not be a "
                        "refusal -- a false refusal is as bad as a false pass")
        self.assertNotIn("intact", reading.verdict.lower())
        self.assertIn("cannot establish",
                      " ".join(reading.report()).lower())

    # ---- the empty and absent records ------------------------------------

    def test_an_absent_record_is_reported_as_absent_not_as_sound(self):
        missing = Path(self._td.name) / "nowhere" / "tool-dispatch.jsonl"
        reading = audit_log.read_integrity(missing)
        self.assertEqual(reading.lines, 0)
        self.assertEqual(reading.rows, 0)
        self.assertIn("NO RECORD", reading.verdict)

    def test_an_empty_record_is_reported_as_empty(self):
        self.path.write_text("", encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertEqual(reading.rows, 0)
        self.assertIn("NO RECORD", reading.verdict)


class RowsThatCannotBeJoinedToATurnAreReportedAsSuch(unittest.TestCase):
    """The natural pair the cut names: a row that cannot be joined is named.

    The dispatch ledger's schema carries no turn identifier at all, and the turn
    record carries a sentinel for rows raised outside any conversation turn. Either
    way a reader joining a record to a turn gets nothing for those rows, and the
    read-back has to say so rather than leave the reader to discover it.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-joins-")
        self.addCleanup(self._td.cleanup)
        self.path = Path(self._td.name) / "intergen" / "tool-dispatch.jsonl"

    def test_a_ledger_row_with_no_turn_identifier_is_counted_and_named(self):
        self.assertTrue(audit_log.write_record(a_record(), self.path))
        reading = audit_log.read_integrity(self.path)
        self.assertEqual(reading.rows, 1)
        self.assertEqual(reading.unjoinable_rows, 1,
                         "a row that cannot be joined to a turn was not counted")
        self.assertTrue(
            any("turn" in line.lower() for line in reading.report()),
            f"the report never mentions the join: {reading.report()}")

    def test_a_row_carrying_a_sentinel_turn_identifier_is_counted_too(self):
        """``no-turn`` is not a turn: a sentinel is the absence of a join."""
        rows = [
            dict(a_record().to_jsonl_dict(), turn_id="no-turn"),
            dict(a_record().to_jsonl_dict(), turn_id="glass-sequence"),
            dict(a_record().to_jsonl_dict(), turn_id="boot-1790089540"),
            dict(a_record().to_jsonl_dict(), turn_id="09767e49bae205d3"),
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertEqual(reading.rows, 4)
        self.assertEqual(reading.unjoinable_rows, 3,
                         "a sentinel turn identifier was counted as a join")

    def test_counting_unjoinable_rows_is_not_itself_a_refusal(self):
        """A row with no turn is a reporting fact, not damage.

        Every ledger row has always lacked a turn identifier, so treating it as
        damage would refuse every record on every machine and teach a reader to
        ignore the verdict.
        """
        self.assertTrue(audit_log.write_record(a_record(), self.path))
        reading = audit_log.read_integrity(self.path)
        self.assertEqual(reading.unjoinable_rows, 1)
        self.assertTrue(reading.trustworthy,
                        "an unjoinable row was treated as damage")


if __name__ == "__main__":
    unittest.main()
