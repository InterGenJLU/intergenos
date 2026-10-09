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
import os
import subprocess
import sys
import tempfile
import threading
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
        self.assertTrue(
            reading.damaged,
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
        self.assertTrue(reading.damaged,
                        f"a row with no timestamp was accepted: {reading.verdict}")
        self.assertEqual(reading.incomplete_rows, [2])

    # ---- the pass beside it, which must NOT overclaim ---------------------

    def test_the_untampered_record_passes(self):
        """The pass captured beside the refusal, as the annex requires."""
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.damaged, reading.report())
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
        self.assertFalse(reading.damaged)
        self.assertNotIn("intact", reading.verdict.lower(),
                         "the passing verdict calls the record intact")
        self.assertIn("NO DETECTABLE DAMAGE", reading.verdict)
        text = " ".join(reading.report()).lower()
        for stem in ("remov", "replac"):
            self.assertIn(stem, text,
                          f"the report does not state that complete-row removal and "
                          f"replacement go undetected: {reading.report()}")

    def test_a_whole_row_removed_is_reported_as_undetectable_not_as_a_pass(self):
        """The limit, measured rather than argued.

        Removing a line leaves a record that passes every check this read-back can
        make. The cell asserts BOTH halves: the reading does not claim damage it
        cannot see, AND it does not claim the record is sound either.
        """
        rows = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join(rows[:2]) + "\n", encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.damaged,
                         "a removed line is not detectable, so this must not be a "
                         "refusal -- a false refusal is as bad as a false pass")
        self.assertNotIn("intact", reading.verdict.lower())
        text = " ".join(reading.report()).lower()
        for stem in ("remov", "replac"):
            self.assertIn(stem, text)

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
        self.assertFalse(reading.damaged,
                         "an unjoinable row was treated as damage")


class EveryFieldTheWriterSerializesIsChecked(unittest.TestCase):
    """One deletion control per field the writer serializes.

    The fields are read from AuditRecord.to_jsonl_dict() itself, so a field the
    writer starts serializing is covered here without a change to this module.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-fields-")
        self.addCleanup(self._td.cleanup)
        self.path = Path(self._td.name) / "tool-dispatch.jsonl"

    def _read_without(self, row: dict, field: str) -> audit_log.IntegrityReading:
        altered = dict(row)
        del altered[field]
        self.path.write_text(json.dumps(altered, separators=(",", ":")) + "\n",
                             encoding="utf-8")
        return audit_log.read_integrity(self.path)

    def test_each_field_of_a_dispatch_row_is_required(self):
        written = a_record().to_jsonl_dict()
        self.assertGreaterEqual(len(written), 11, f"the writer serialized {written}")
        for field in written:
            with self.subTest(field=field):
                reading = self._read_without(written, field)
                self.assertTrue(reading.damaged,
                                f"a row without {field!r} read as {reading.verdict}")
                self.assertEqual(reading.incomplete_rows, [1])

    def test_each_field_of_a_governance_row_is_required(self):
        written = a_record(kind="governance_decision", event_type="tier_change",
                           details={"from": 1, "to": 2}).to_jsonl_dict()
        self.assertIn("kind", written)
        self.assertIn("event_type", written)
        for field in written:
            if field == "details":
                continue  # serialized only when it holds a value; see the next case
            with self.subTest(field=field):
                reading = self._read_without(written, field)
                self.assertTrue(reading.damaged,
                                f"a governance row without {field!r} read as "
                                f"{reading.verdict}")
                self.assertEqual(reading.incomplete_rows, [1])

    def test_a_governance_row_the_writer_wrote_without_details_is_not_damage(self):
        """The writer leaves details out when it is empty, so its absence is valid."""
        written = a_record(kind="governance_decision", event_type="tier_change",
                           details=None).to_jsonl_dict()
        self.assertNotIn("details", written)
        self.path.write_text(json.dumps(written, separators=(",", ":")) + "\n",
                             encoding="utf-8")
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.damaged, reading.report())


class ConcurrentAppendsInReversedEventTimeOrder(unittest.TestCase):
    """Two valid appends whose event times are reversed in the file.

    The writer takes no lock and serializes no append sequence, so a writer that
    stamps its row first can append it second. Both appends succeed and no byte
    is changed after either, so the reversal is reported as an observation and is
    not damage.
    """

    def test_a_reversed_pair_from_two_writers_is_an_observation_not_damage(self):
        with tempfile.TemporaryDirectory(prefix="ledger-order-") as directory:
            path = Path(directory) / "intergen" / "tool-dispatch.jsonl"
            early_ready = threading.Event()
            late_written = threading.Event()
            results: dict[str, bool] = {}
            early = a_record(timestamp="2026-10-08T22:49:36.758266+00:00",
                             tool_name="early")
            late = a_record(timestamp="2026-10-08T22:49:36.758386+00:00",
                            tool_name="late")

            def early_writer() -> None:
                early_ready.set()
                if late_written.wait(timeout=10):
                    results["early"] = audit_log.write_record(early, path)

            def late_writer() -> None:
                if early_ready.wait(timeout=10):
                    results["late"] = audit_log.write_record(late, path)
                late_written.set()

            threads = [threading.Thread(target=early_writer),
                       threading.Thread(target=late_writer)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(results, {"early": True, "late": True})
            rows = list(audit_log.read_records(path))
            self.assertEqual([r["tool_name"] for r in rows], ["late", "early"])
            self.assertLess(early.timestamp, late.timestamp)
            reading = audit_log.read_integrity(path)
        self.assertFalse(reading.damaged,
                         f"two valid appends were reported as damage: {reading.report()}")
        self.assertEqual(reading.out_of_order_rows, [2])
        self.assertIn("NO DETECTABLE DAMAGE", reading.verdict)
        self.assertTrue(any("observation" in line for line in reading.report()),
                        f"the reversal is not named as an observation: {reading.report()}")


class NoPropertyClaimsMoreThanTheStructureShows(unittest.TestCase):
    """A complete row removed leaves a structurally valid record.

    Nothing in the record can show the removal, so the reading reports no damage;
    no public property of the reading may then claim more than that.
    """

    def test_after_a_complete_row_is_removed_no_property_claims_trust(self):
        with tempfile.TemporaryDirectory(prefix="ledger-removal-") as directory:
            path = Path(directory) / "tool-dispatch.jsonl"
            for index in range(2):
                self.assertTrue(audit_log.write_record(
                    a_record(timestamp=f"2026-10-08T17:0{index}:00+00:00",
                             arguments={"index": index}), path))
            first = path.read_text(encoding="utf-8").splitlines()[0]
            path.write_text(first + "\n", encoding="utf-8")
            reading = audit_log.read_integrity(path)
        self.assertEqual(reading.rows, 1)
        self.assertFalse(reading.damaged)
        claims = [name for name in dir(reading) if not name.startswith("_")
                  and ("trust" in name.lower() or "intact" in name.lower())]
        self.assertEqual(claims, [],
                         "a public property claims more than a structural check")
        text = " ".join(reading.report()).lower()
        for stem in ("remov", "replac"):
            self.assertIn(stem, text)


class TheCommandKeepsItsOutputChannels(unittest.TestCase):
    """The tool-log command, run as a process against a throwaway record.

    With --json, stdout carries JSON lines only, whether or not any row is
    readable, and the structural report goes to stderr on every run. Every view
    exits nonzero when structural damage is detected.
    """

    TREE = Path(audit_log.__file__).resolve().parents[1]

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-command-")
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)
        self.path = self.state / "intergen" / "tool-dispatch.jsonl"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, PYTHONPATH=str(self.TREE),
                   XDG_STATE_HOME=str(self.state))
        return subprocess.run(
            [sys.executable, "-s", "-m", "intergen.cli", "tool-log", *args],
            cwd=self.state, env=env, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL, check=False)

    def _write_rows(self, count: int) -> None:
        for index in range(count):
            self.assertTrue(audit_log.write_record(
                a_record(timestamp=f"2026-10-08T17:0{index}:00+00:00"), self.path))

    def _assert_json_only(self, stdout: str) -> int:
        lines = [line for line in stdout.splitlines() if line.strip()]
        for line in lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                self.fail(f"stdout carries a line that is not JSON: {line!r}")
        return len(lines)

    def test_json_on_a_clean_record(self):
        self._write_rows(1)
        done = self._run("--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._assert_json_only(done.stdout), 1)
        self.assertIn("NO DETECTABLE DAMAGE", done.stderr)

    def test_json_on_a_partially_damaged_record(self):
        self._write_rows(2)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text(lines[0] + "\n{not-json}\n", encoding="utf-8")
        done = self._run("--json")
        self.assertNotEqual(done.returncode, 0, "a damaged read exited 0")
        self.assertEqual(self._assert_json_only(done.stdout), 1)
        self.assertIn("UNTRUSTWORTHY", done.stderr)
        self.assertNotIn("UNTRUSTWORTHY", done.stdout)

    def test_json_on_a_record_with_no_readable_row(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not-json}\n", encoding="utf-8")
        done = self._run("--json")
        self.assertNotEqual(done.returncode, 0, "a damaged read exited 0")
        self.assertEqual(self._assert_json_only(done.stdout), 0)
        self.assertIn("UNTRUSTWORTHY", done.stderr)

    def test_json_with_no_record_at_all(self):
        done = self._run("--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._assert_json_only(done.stdout), 0)
        self.assertIn("NO RECORD", done.stderr)

    def test_the_human_view_of_a_damaged_record_exits_nonzero(self):
        self._write_rows(2)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text(lines[0] + "\n{not-json}\n", encoding="utf-8")
        done = self._run()
        self.assertNotEqual(done.returncode, 0, "a damaged read exited 0")
        self.assertIn("UNTRUSTWORTHY", done.stdout)

    def test_the_human_view_of_a_record_with_no_readable_row(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not-json}\n", encoding="utf-8")
        done = self._run()
        self.assertNotEqual(done.returncode, 0, "a damaged read exited 0")
        self.assertIn("UNTRUSTWORTHY", done.stdout)
        self.assertNotIn("Audit log empty", done.stdout)

    def test_the_human_view_of_a_clean_record_exits_zero(self):
        self._write_rows(1)
        done = self._run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("NO DETECTABLE DAMAGE", done.stdout)


class AByteThatIsNotUTF8IsDamageInEveryView(unittest.TestCase):
    """One byte of a writer-shaped row changed to 0xff, the file's length kept.

    The writer emits UTF-8 only, so a byte that does not decode is damage. A reader
    that decodes with replacement characters turns it into valid text before the row
    is parsed, and the record reads as undamaged. Each line is decoded strictly, a line
    that does not decode is named, and the rows reader passes over the same line, so
    the integrity view, the JSON view and the human view agree and each exits nonzero.
    """

    TREE = Path(audit_log.__file__).resolve().parents[1]

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-bytes-")
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)
        self.path = self.state / "intergen" / "tool-dispatch.jsonl"
        for index, summary in enumerate(("first", "marker", "third")):
            self.assertTrue(audit_log.write_record(
                a_record(timestamp=f"2026-10-09T15:0{index}:00+00:00",
                         result_summary=summary),
                self.path))
        before = self.path.read_bytes()
        self.altered = before.replace(b"marker", b"mar\xffer")
        self.assertEqual(len(before), len(self.altered), "the edit changed the file LENGTH")
        self.assertEqual(sum(1 for x, y in zip(before, self.altered) if x != y), 1,
                         "the edit changed more than one byte")
        self.path.write_bytes(self.altered)

    def _run(self, *args: str, state: Path | None = None) -> subprocess.CompletedProcess:
        home = state if state is not None else self.state
        env = dict(os.environ, PYTHONPATH=str(self.TREE), XDG_STATE_HOME=str(home))
        return subprocess.run(
            [sys.executable, "-s", "-m", "intergen.cli", "tool-log", *args],
            cwd=home, env=env, capture_output=True, text=True, errors="backslashreplace",
            timeout=60, stdin=subprocess.DEVNULL, check=False)

    def test_the_read_back_refuses_the_line_and_names_it(self):
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.damaged,
                        f"a byte that is not UTF-8 was not refused: {reading.report()}")
        self.assertIn("UNTRUSTWORTHY", reading.verdict)
        self.assertEqual(getattr(reading, "undecodable_lines", None), [2],
                         "the refusal did not NAME the line it refused on")
        self.assertTrue(any("UTF-8" in line and "[2]" in line for line in reading.report()),
                        f"the report does not name the line: {reading.report()}")

    def test_the_rows_reader_passes_over_the_line_and_keeps_the_rest(self):
        rows = list(audit_log.read_records(self.path))
        self.assertEqual([row["result_summary"] for row in rows], ["first", "third"])

    def test_the_integrity_view_exits_nonzero_and_names_the_line(self):
        done = self._run("--integrity")
        self.assertNotEqual(done.returncode, 0,
                            "the integrity view exited 0 over a byte that is not UTF-8")
        self.assertIn("UNTRUSTWORTHY", done.stdout)
        self.assertNotIn("NO DETECTABLE DAMAGE", done.stdout)
        self.assertNotIn("Traceback", done.stderr)

    def test_the_json_view_keeps_stdout_json_and_exits_nonzero(self):
        done = self._run("--json")
        self.assertNotEqual(done.returncode, 0, "the JSON view exited 0")
        self.assertNotIn("Traceback", done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        for line in lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                self.fail(f"stdout carries a line that is not JSON: {line!r}")
        self.assertEqual(len(lines), 2, "the two readable rows are not both on stdout")
        self.assertIn("UNTRUSTWORTHY", done.stderr)

    def test_the_human_view_exits_nonzero_and_says_why(self):
        done = self._run()
        self.assertNotEqual(done.returncode, 0, "the human view exited 0")
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("UNTRUSTWORTHY", done.stdout)

    def test_no_view_changes_the_record(self):
        for args in ((), ("--json",), ("--integrity",)):
            self._run(*args)
        self.assertEqual(self.path.read_bytes(), self.altered)

    def test_a_record_of_one_such_row_is_refused_by_every_view(self):
        """The reviewer's own fixture: one row, one byte of its summary changed."""
        state = self.state / "one-row"
        record = state / "intergen" / "tool-dispatch.jsonl"
        record.parent.mkdir(parents=True)
        row = {
            "timestamp": "2026-10-09T15:00:00+00:00", "tool_name": "read_file",
            "arguments": {"path": "/etc/fstab"}, "declared_provenance": "user_direct",
            "effective_provenance": "user_direct", "ingress_tools_this_turn": [],
            "user_decision": "executed", "exit_code": 0, "result_summary": "marker",
            "source_attribution": "", "excerpt": "",
        }
        valid = json.dumps(row, separators=(",", ":")).encode("utf-8") + b"\n"
        invalid = valid.replace(b"marker", b"mar\xffer")
        self.assertEqual((len(valid), len(invalid)), (306, 306))
        record.write_bytes(invalid)
        for args in ((), ("--json",), ("--integrity",)):
            with self.subTest(view=" ".join(args) or "human"):
                done = self._run(*args, state=state)
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                self.assertIn("UNTRUSTWORTHY", done.stderr if args == ("--json",) else done.stdout)
                self.assertNotIn("NO DETECTABLE DAMAGE", done.stdout + done.stderr)
        self.assertEqual(record.read_bytes(), invalid)


class TheOutOfOrderObservationSaysWhatItCompares(unittest.TestCase):
    """Each timestamp is compared with the latest timestamp of every prior row.

    With 10:00, 05:00 and 07:00 the third row is later than its neighbour but earlier
    than the first, so both the second and the third are named; the report must say
    they are earlier than at least one prior row, which is the comparison made.
    """

    def test_a_row_earlier_than_a_prior_row_but_not_its_neighbour(self):
        with tempfile.TemporaryDirectory(prefix="ledger-order-words-") as directory:
            path = Path(directory) / "intergen" / "tool-dispatch.jsonl"
            for stamp in ("10:00", "05:00", "07:00"):
                self.assertTrue(audit_log.write_record(
                    a_record(timestamp=f"2026-10-09T{stamp}:00+00:00"), path))
            reading = audit_log.read_integrity(path)
        self.assertFalse(reading.damaged, reading.report())
        self.assertEqual(reading.out_of_order_rows, [2, 3])
        named = [line for line in reading.report() if "[2, 3]" in line]
        self.assertEqual(len(named), 1, reading.report())
        self.assertIn("than at least one prior row", named[0])
        self.assertNotIn("than the row before them", named[0])


if __name__ == "__main__":
    unittest.main()
