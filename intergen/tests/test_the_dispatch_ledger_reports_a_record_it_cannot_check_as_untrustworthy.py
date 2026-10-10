# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A record whose integrity cannot be checked is reported as untrustworthy, never as absent.

THE RULE. The review plan's records part requires that "a record whose integrity cannot
be checked at all is reported as untrustworthy rather than as intact". The reading and the
command that prints it held that rule for a record they could read, and broke it five ways
for one they could not:

- the verdict tested emptiness before damage, so an existing record the reader could not
  open (its read error set, no line and no row read) was labelled NO RECORD, "nothing has
  been written here";
- absence was decided by ``Path.exists()``, which is False for a symbolic link whose target
  is missing and for a file below a directory that cannot be searched, so both came back
  NO RECORD and undamaged;
- the count view printed how many rows survived the read and exited 0, with no verdict,
  where every other view refused the same damaged record;
- the views read the record twice, once for the verdict and once for the rows, so a change
  landing between the two reads printed surviving rows beneath a clean verdict;
- the wipe repeated the ``exists()`` decision, so a record below a directory that cannot be
  searched was reported "already empty", exit 0, with every byte left in place.

A sixth case of the same kind sits beside them: a line that is valid JSON but not one row
object is counted as unreadable by the verdict, yet the rows reader yielded it and the
human view ended in a traceback on it.

Each case below is the smallest record that shows one of these, read through the function
or the command a person would use. Hermetic: a throwaway directory per test, no service,
socket, model server, display or network; every mode a case changes is restored by a
cleanup before the directory is removed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from intergen import audit_log
from intergen.interfaces.provenance import AuditRecord, Provenance

TREE = Path(audit_log.__file__).resolve().parents[1]
ROOT = os.geteuid() == 0
ROOT_REASON = "a mode-000 file or directory is readable by root, so the case cannot be built"


def a_record(**overrides) -> AuditRecord:
    fields = dict(
        timestamp="2026-10-09T18:00:00+00:00",
        tool_name="read_file",
        arguments={"path": "/etc/fstab"},
        declared_provenance=Provenance.USER_DIRECT.value,
        effective_provenance=Provenance.USER_DIRECT.value,
        ingress_tools_this_turn=[],
        user_decision="executed",
    )
    fields.update(overrides)
    return AuditRecord(**fields)


class _Record(unittest.TestCase):
    """A throwaway state directory, the record at the path the daemon resolves under it."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ledger-uncheckable-")
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name)
        self.path = self.state / "intergen" / "tool-dispatch.jsonl"

    def write_rows(self, *summaries: str) -> None:
        for index, summary in enumerate(summaries):
            self.assertTrue(audit_log.write_record(
                a_record(timestamp=f"2026-10-09T18:0{index}:00+00:00",
                         result_summary=summary), self.path))

    def lock(self, target: Path) -> None:
        """Mode 000 on ``target``, restored to the owner by a cleanup."""
        os.chmod(target, 0)
        self.addCleanup(os.chmod, target, 0o700 if target.is_dir() else 0o600)

    def run_command(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("INTERGEN_")}
        env.update(PYTHONPATH=str(TREE), XDG_STATE_HOME=str(self.state),
                   HOME=str(self.state / "home"), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-s", "-m", "intergen.cli", "tool-log", *args],
            cwd=self.state, env=env, capture_output=True, text=True,
            errors="backslashreplace", timeout=60, stdin=subprocess.DEVNULL, check=False)


class AnExistingRecordThatCannotBeReadIsUntrustworthy(_Record):
    """The verdict decides a read error and damage before emptiness."""

    @unittest.skipIf(ROOT, ROOT_REASON)
    def test_an_existing_record_that_cannot_be_opened_is_untrustworthy_and_names_the_error(self):
        self.write_rows("one")
        self.lock(self.path)
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("UNTRUSTWORTHY"),
                        f"an existing record that could not be read read as: {reading.verdict}")
        self.assertNotIn("NO RECORD", reading.verdict)
        self.assertIn("Permission denied", reading.verdict,
                      "the verdict does not name the read error")

    def test_a_missing_record_is_still_no_record(self):
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("NO RECORD"), reading.verdict)

    def test_an_empty_readable_record_is_still_no_record(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"")
        reading = audit_log.read_integrity(self.path)
        self.assertFalse(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("NO RECORD"), reading.verdict)

    def test_a_damaged_record_is_still_untrustworthy(self):
        self.write_rows("one", "two")
        lines = self.path.read_bytes().split(b"\n")
        self.path.write_bytes(lines[0] + b"\n{not-json}\n")
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("UNTRUSTWORTHY"), reading.verdict)


class AbsenceIsDecidedFromThePathsOwnStat(_Record):
    """Only a path whose own stat finds nothing is absent; anything else found is named."""

    def test_a_dangling_record_link_is_untrustworthy_and_names_its_target(self):
        target = self.state / "intergen" / "gone.jsonl"
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(target)
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("UNTRUSTWORTHY"), reading.verdict)
        self.assertIn(str(target), reading.verdict, "the verdict does not name the link's target")

    @unittest.skipIf(ROOT, ROOT_REASON)
    def test_a_record_below_a_directory_that_cannot_be_searched_is_untrustworthy(self):
        self.write_rows("one")
        self.lock(self.path.parent)
        reading = audit_log.read_integrity(self.path)
        self.assertTrue(reading.damaged, reading.report())
        self.assertTrue(reading.verdict.startswith("UNTRUSTWORTHY"), reading.verdict)
        self.assertIn("Permission denied", reading.verdict, "the verdict does not name the error")

    def test_the_rows_reader_warns_rather_than_returning_nothing_in_silence(self):
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(self.state / "intergen" / "gone.jsonl")
        with self.assertLogs(audit_log.logger, level="WARNING") as seen:
            rows = list(audit_log.read_records(self.path))
        self.assertEqual(rows, [])
        self.assertTrue(any("gone.jsonl" in line for line in seen.output), seen.output)


class TheCountViewReadsThroughTheIntegrityReading(_Record):
    """--count gives the verdict and a non-zero exit for a record that does not read clean."""

    def _damage_the_second_row(self) -> None:
        before = self.path.read_bytes()
        after = before.replace(b"marker", b"mar\xffer")
        self.assertEqual(len(before), len(after))
        self.assertEqual(sum(1 for x, y in zip(before, after) if x != y), 1)
        self.path.write_bytes(after)

    def test_the_count_of_a_damaged_record_is_refused_with_the_verdict(self):
        self.write_rows("first", "marker", "third")
        self._damage_the_second_row()
        done = self.run_command("--count")
        self.assertNotEqual(done.returncode, 0, f"a damaged record's count exited 0: {done.stdout!r}")
        self.assertEqual(done.stdout.strip(), "", "a count was printed for a damaged record")
        self.assertIn("UNTRUSTWORTHY", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    @unittest.skipIf(ROOT, ROOT_REASON)
    def test_the_count_of_a_record_that_cannot_be_read_is_refused(self):
        self.write_rows("first")
        self.lock(self.path)
        done = self.run_command("--count")
        self.assertNotEqual(done.returncode, 0, f"an unreadable record's count exited 0: {done.stdout!r}")
        self.assertEqual(done.stdout.strip(), "")
        self.assertIn("UNTRUSTWORTHY", done.stderr)

    def test_the_count_of_a_clean_record_is_unchanged(self):
        self.write_rows("first", "second", "third")
        done = self.run_command("--count")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "3\n")

    def test_the_count_with_no_record_is_zero(self):
        done = self.run_command("--count")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "0\n")


# Run inside a fresh interpreter: the rows-and-bytes reader is wrapped so that the record
# is changed by one byte right after the first time it is read, then the command runs.
_BETWEEN_READS = r'''
import sys
sys.path.insert(0, sys.argv[1])
from intergen import audit_log, cli
record = audit_log.default_log_path()
original = audit_log._record_lines
calls = []
def changed_after_the_first_read(path):
    lines = original(path)
    calls.append(str(path))
    if len(calls) == 1:
        data = record.read_bytes()
        record.write_bytes(data.replace(b"marker", b"mar\xffer"))
    return lines
audit_log._record_lines = changed_after_the_first_read
try:
    cli.cmd_tool_log(sys.argv[2:])
finally:
    print("READ_CALLS", len(calls), file=sys.stderr)
'''


class EachInvocationReadsTheRecordOnce(_Record):
    """The verdict, the rows and the count come from one snapshot of the record's bytes."""

    def _run_with_a_change_between_reads(self, *args: str) -> subprocess.CompletedProcess:
        self.write_rows("first", "marker", "third")
        env = {k: v for k, v in os.environ.items() if not k.startswith("INTERGEN_")}
        env.update(XDG_STATE_HOME=str(self.state), HOME=str(self.state / "home"),
                   PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-s", "-c", _BETWEEN_READS, str(TREE), *args],
            cwd=self.state, env=env, capture_output=True, text=True,
            errors="backslashreplace", timeout=60, stdin=subprocess.DEVNULL, check=False)

    @staticmethod
    def _readable_rows(report_text: str) -> int:
        found = re.search(r"(\d+) line\(s\) in the file, (\d+) readable row\(s\)", report_text)
        if not found:
            raise AssertionError(f"no line and row count in: {report_text!r}")
        return int(found.group(2))

    def test_the_human_view_prints_the_rows_its_verdict_counts(self):
        done = self._run_with_a_change_between_reads()
        self.assertIn("READ_CALLS 1", done.stderr, "the record was read more than once")
        shown = re.search(r"audit log — (\d+) of (\d+) record\(s\)", done.stdout)
        self.assertIsNotNone(shown, done.stdout)
        self.assertEqual(int(shown.group(2)), self._readable_rows(done.stdout),
                         "the rows printed are not the rows the verdict counted")

    def test_the_json_view_prints_the_rows_its_verdict_counts(self):
        done = self._run_with_a_change_between_reads("--json")
        self.assertIn("READ_CALLS 1", done.stderr, "the record was read more than once")
        printed = [line for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(len(printed), self._readable_rows(done.stderr),
                         "the rows printed are not the rows the verdict counted")


class TheWipeDecidesAbsenceFromThePathsOwnStat(_Record):
    """--clear: only genuine absence is "already empty"; a clear is proven by reading it back."""

    @unittest.skipIf(ROOT, ROOT_REASON)
    def test_clearing_a_record_below_a_directory_that_cannot_be_searched_fails_in_words(self):
        self.write_rows("first")
        before = self.path.read_bytes()
        self.lock(self.path.parent)
        done = self.run_command("--clear")
        os.chmod(self.path.parent, 0o700)
        self.assertNotEqual(done.returncode, 0, f"the clear claimed success: {done.stdout!r}")
        self.assertNotIn("already empty", done.stdout + done.stderr)
        self.assertIn("Permission denied", done.stderr, "the failure does not say what was found")
        self.assertEqual(self.path.read_bytes(), before, "the record was changed")

    def test_clearing_a_dangling_record_link_fails_in_words(self):
        target = self.state / "intergen" / "gone.jsonl"
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(target)
        done = self.run_command("--clear")
        self.assertNotEqual(done.returncode, 0, f"the clear claimed success: {done.stdout!r}")
        self.assertNotIn("already empty", done.stdout + done.stderr)
        self.assertIn(str(target), done.stderr, "the failure does not name the link's target")
        self.assertFalse(target.exists(), "the clear created the link's target")

    def test_clearing_with_no_record_is_already_empty(self):
        done = self.run_command("--clear")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Audit log already empty", done.stdout)

    def test_a_clear_is_proven_by_reading_the_record_back(self):
        self.write_rows("first", "second", "third")
        done = self.run_command("--clear")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Cleared 3 record(s)", done.stdout)
        self.assertIn("read back: 0 bytes", done.stdout, "the clear is not proven by a read-back")
        self.assertEqual(self.path.stat().st_size, 0)


class ALineThatIsJSONButNotARowIsDamageInEveryView(_Record):
    """The verdict counts such a line as unreadable; the rows reader and the views agree."""

    def _write_with_a_list_between_rows(self) -> None:
        self.write_rows("first", "third")
        rows = self.path.read_bytes().split(b"\n")
        self.path.write_bytes(rows[0] + b"\n[1, 2]\n" + rows[1] + b"\n")

    def test_the_rows_reader_does_not_yield_it(self):
        self._write_with_a_list_between_rows()
        with self.assertLogs(audit_log.logger, level="WARNING"):
            rows = list(audit_log.read_records(self.path))
        self.assertEqual([type(row) for row in rows], [dict, dict])

    def test_the_human_view_refuses_it_without_a_traceback(self):
        self._write_with_a_list_between_rows()
        done = self.run_command()
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("UNTRUSTWORTHY", done.stdout)
        self.assertIn("2 of 2 record(s)", done.stdout)


if __name__ == "__main__":
    unittest.main()
