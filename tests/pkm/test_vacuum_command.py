#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm vacuum` — explicit database compaction, with a fail-closed space check.

WHY THIS COMMAND EXISTS. Removing packages and collapsing duplicated ownership
records leave free pages INSIDE the database file. SQLite reuses them but never
returns them to the filesystem, and on a system whose one-time record update
collapsed hundreds of thousands of duplicates that is a real amount of disk.
Decided 2026-08-06, with three properties, each of which has cases here:

  EXPLICIT     nothing in pkm compacts on its own. A maintenance step that
               frees a lot of records ADVISES, in one line, and stops. Deciding
               when to pay for a whole-file rewrite belongs to the person whose
               disk it is.
  FAIL-CLOSED  a VACUUM rebuilds the database into a new file beside the old
               one, so the peak requirement is about twice the current size. A
               rebuild that runs out of disk halfway leaves a full filesystem
               AND no compaction — strictly worse than not starting. The check
               refuses rather than trying.
  REPORTED     it follows the same announce/part/outcome shape as every other
               long pkm operation (pkm.progress).

ON THE SPACE-CHECK CASE. It patches the free-space reading rather than filling
a real filesystem: the assertion is about what the command DECIDES when told
there is no room, and manufacturing a genuinely full filesystem in a test would
prove the same thing at the cost of being unable to run unattended.
"""

import argparse
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output, preflight
from pkm.database import PackageDB


def _flat(text):
    """Collapse wrapped prose to one line.

    pkm wraps free text with a hanging indent, so a phrase a case cares
    about can be split across two lines by nothing more than terminal
    width. Asserting on the flattened form tests the message rather than
    the wrap column."""
    return " ".join(text.split())


def _capture(fn, level=output.NORMAL):
    buf = io.StringIO()
    prior = output.process_level()
    output.set_process_level(level)
    output._process_reporter.stream = buf
    output._process_reporter.err_stream = buf
    try:
        with redirect_stdout(buf):
            rc = fn()
    finally:
        output.set_process_level(prior)
        output._process_reporter.stream = None
        output._process_reporter.err_stream = None
    return rc, buf.getvalue()


class VacuumTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def make_reclaimable(self, packages=30, files_each=4000):
        """Fill the database, then delete most of it, so the file carries a
        large free-page count — the state a package removal or a record
        collapse leaves behind.

        The corpus is sized to clear VACUUM_ADVICE_MIN_BYTES with room to
        spare (measured 12.0 MiB of free pages against an 8 MiB threshold,
        built in 2.1s on the development machine 2026-08-06), so the
        cases exercise the compaction path rather than the correct
        not-worth-it refusal. Sizing it just above the threshold would make
        every case here hostage to the exact page arithmetic."""
        for p in range(packages):
            pid = self.db.add_installed(f"bulk{p}", "1.0", tier="core")
            self.db.add_files(
                pid, [f"usr/share/bulk{p}/file{i}" for i in range(files_each)])
        for p in range(packages - 1):
            self.db.remove_installed(f"bulk{p}")
        self.db.conn.commit()


class ReclaimableReportingTest(VacuumTestBase):
    def test_reclaimable_bytes_reads_the_databases_own_answer(self):
        self.make_reclaimable()
        reclaimable = self.db.reclaimable_bytes()
        free_pages = self.db.conn.execute(
            "PRAGMA freelist_count").fetchone()[0]
        page_size = self.db.conn.execute("PRAGMA page_size").fetchone()[0]
        self.assertEqual(reclaimable, free_pages * page_size)
        self.assertGreater(reclaimable, 0)

    def test_an_unreadable_figure_advises_nothing_rather_than_guessing(self):
        """A figure that cannot be read is reported as nothing to do, never
        estimated. sqlite3.Connection.execute cannot be patched in place
        (it is a read-only attribute), so the connection itself is replaced
        with one whose execute raises — the same condition from the
        method's point of view."""
        class Broken:
            def execute(self, *_a, **_k):
                raise sqlite3.Error("cannot read")

        real_conn = self.db.conn
        try:
            self.db.conn = Broken()
            self.assertEqual(self.db.reclaimable_bytes(), 0)
        finally:
            self.db.conn = real_conn


class AdviseNeverAutoFireTest(VacuumTestBase):
    """A maintenance step advises in one line and never compacts by itself."""

    def test_advice_is_one_line_and_names_the_command(self):
        self.make_reclaimable()
        rc, text = _capture(lambda: self.db.advise_vacuum_if_reclaimable())
        self.assertGreater(rc, 0)
        self.assertIn("pkm vacuum", text)
        # ONE line of advice. A multi-line lecture on every migration is how
        # users learn to read past a package manager's output.
        self.assertEqual(len([ln for ln in text.splitlines() if ln.strip()
                              and "pkm vacuum" in ln]), 1)

    def test_advice_is_withheld_when_there_is_little_to_reclaim(self):
        rc, text = _capture(lambda: self.db.advise_vacuum_if_reclaimable())
        self.assertEqual(rc, 0)
        self.assertEqual(text, "")

    def test_advising_does_not_compact(self):
        """The load-bearing half of 'advise, never auto-fire': the file must be
        exactly as large after the advice as before it."""
        self.make_reclaimable()
        before = self.db.db_path.stat().st_size
        _capture(lambda: self.db.advise_vacuum_if_reclaimable())
        self.assertEqual(self.db.db_path.stat().st_size, before)

    def test_no_pkm_code_path_calls_vacuum_by_itself(self):
        """Read pkm's own source: the only caller of the compaction method is
        the command a user types. A future automatic caller fails here."""
        src_dir = Path(cli.__file__).parent
        callers = []
        for path in sorted(src_dir.glob("*.py")):
            for n, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "db.vacuum()" in stripped or ".vacuum()" in stripped:
                    callers.append(f"{path.name}:{n}: {stripped}")
        self.assertEqual(
            len(callers), 1,
            "compaction must be reachable ONLY from the vacuum command; "
            "found: " + " | ".join(callers))
        self.assertIn("cli.py", callers[0])


class VacuumSpaceCheckTest(VacuumTestBase):
    def test_the_requirement_is_about_twice_the_file(self):
        """The rebuilt copy stands beside the original until the swap."""
        check = self.db.vacuum_space_check()
        self.assertIsNotNone(check)
        self.assertEqual(check["required_bytes"], check["db_bytes"] * 2)

    def test_refuses_when_there_is_not_enough_room(self):
        self.make_reclaimable()
        before = self.db.db_path.stat().st_size
        args = argparse.Namespace(vacuum_dry_run=False)
        with patch.object(preflight, "check_free_space",
                          return_value={"ok": False,
                                        "available_bytes": 1024,
                                        "required_bytes": 10 ** 9,
                                        "required_with_margin": 10 ** 9}):
            rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        self.assertEqual(rc, 1)
        self.assertIn("Insufficient disk space", text)
        # FAIL-CLOSED means it did not try: the file is untouched.
        self.assertEqual(self.db.db_path.stat().st_size, before)

    def test_refuses_when_the_database_cannot_be_measured(self):
        args = argparse.Namespace(vacuum_dry_run=False)
        with patch.object(self.db, "vacuum_space_check", return_value=None):
            rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        self.assertEqual(rc, 1)
        self.assertIn("Refusing", text)


class VacuumCommandTest(VacuumTestBase):
    def test_compacts_and_reports_what_it_returned(self):
        self.make_reclaimable()
        before = self.db.db_path.stat().st_size
        args = argparse.Namespace(vacuum_dry_run=False)
        rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        after = self.db.db_path.stat().st_size
        self.assertEqual(rc, 0)
        self.assertLess(after, before)
        self.assertIn("Compacting the package database", text)
        self.assertIn("returned to the filesystem", _flat(text))

    def test_the_brackets_survive_quiet(self):
        self.make_reclaimable()
        args = argparse.Namespace(vacuum_dry_run=False)
        rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args),
                            level=output.QUIET)
        self.assertEqual(rc, 0)
        self.assertIn("Compacting the package database", text)
        self.assertIn("returned to the filesystem", _flat(text))

    def test_installed_records_survive_the_rebuild(self):
        """A maintenance command that loses records would be catastrophic, and
        'it is only a VACUUM' is exactly the reasoning that would let it go
        unchecked. Compare the whole installed set across the rebuild."""
        self.make_reclaimable()
        self.db.add_installed("keeper", "2.0", tier="core")
        before = sorted((p["name"], p["version"])
                        for p in self.db.list_installed())
        args = argparse.Namespace(vacuum_dry_run=False)
        _capture(lambda: cli.cmd_vacuum(self.db, args))
        after = sorted((p["name"], p["version"])
                       for p in self.db.list_installed())
        self.assertEqual(before, after)
        self.assertIn(("keeper", "2.0"), after)

    def test_says_so_and_stops_when_there_is_nothing_worth_reclaiming(self):
        args = argparse.Namespace(vacuum_dry_run=False)
        before = self.db.db_path.stat().st_size
        rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        self.assertEqual(rc, 0)
        self.assertIn("Nothing worth reclaiming", text)
        self.assertEqual(self.db.db_path.stat().st_size, before)

    def test_dry_run_changes_nothing_and_answers_both_questions(self):
        self.make_reclaimable()
        before = self.db.db_path.stat().st_size
        args = argparse.Namespace(vacuum_dry_run=True)
        rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        self.assertEqual(rc, 0)
        self.assertEqual(self.db.db_path.stat().st_size, before)
        self.assertIn("would", text)          # how much would be returned
        self.assertIn("room to do it", text)  # and whether there is room

    def test_dry_run_states_a_refusal_it_would_make(self):
        self.make_reclaimable()
        args = argparse.Namespace(vacuum_dry_run=True)
        with patch.object(preflight, "check_free_space",
                          return_value={"ok": False,
                                        "available_bytes": 1024,
                                        "required_bytes": 10 ** 9,
                                        "required_with_margin": 10 ** 9}):
            rc, text = _capture(lambda: cli.cmd_vacuum(self.db, args))
        self.assertEqual(rc, 0)
        self.assertIn("would refuse", text)


class VacuumIsWiredIntoTheCliTest(unittest.TestCase):
    """Rule 15 and the dispatch wiring — a command nobody can reach, or that
    ships without a manual page, is not a delivered command."""

    def test_the_command_runs_end_to_end_as_a_real_process(self):
        """Not a handler call: the actual CLI, parsed from a real argument
        vector, dispatched, against a real database file. A command can be
        written, tested at the handler, and still be unreachable because it
        was never wired into the dispatch table — this is the case that
        would catch that."""
        import subprocess
        import sys as _sys
        repo_root = Path(cli.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "pkm.db"
            db = PackageDB(dbp, root=str(Path(td) / "root"))
            db.add_installed("solo", "1.0", tier="core")
            db.close()
            res = subprocess.run(
                [_sys.executable, "-m", "pkm", "--db", str(dbp),
                 "vacuum", "--dry-run"],
                cwd=str(repo_root), capture_output=True, text=True,
                env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo_root),
                     "HOME": td, "NO_COLOR": "1"},
            )
        self.assertEqual(res.returncode, 0,
                         f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertIn("Package database:", res.stdout)

    def test_the_preview_runs_without_root(self):
        """A read-only question about your own machine must not require sudo
        — the same user-control point the upgrade preview already settled.
        The dry run reads two PRAGMAs and one file size."""
        self.assertTrue(
            cli._is_dry_run_invocation(
                argparse.Namespace(vacuum_dry_run=True)))
        self.assertFalse(
            cli._is_dry_run_invocation(
                argparse.Namespace(vacuum_dry_run=False)))

    def test_it_holds_the_mutation_lock(self):
        """It rewrites the whole database file. It changes no record, but it
        must not run beside an install that is mid-transaction."""
        self.assertIn("vacuum", cli.PKM_MUTATING_COMMANDS)

    def test_it_is_documented_in_the_man_page(self):
        man = (Path(cli.__file__).resolve().parents[1]
               / "packages" / "core" / "pkm" / "pkm.1")
        self.assertTrue(man.is_file(), f"man page not found at {man}")
        text = man.read_text(encoding="utf-8")
        self.assertIn("vacuum", text)
        self.assertIn("\\-\\-dry\\-run", text)
        # The two properties a user has to know before running it.
        self.assertIn("refuses", text)
        self.assertIn("Nothing runs this on its own", text)


if __name__ == "__main__":
    unittest.main()
