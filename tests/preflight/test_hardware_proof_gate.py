#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/preflight-hardware-proof-gate.py — no build while a row is unclosed.

A finding against a critical component opens a row in a gating table, and the
row closes only when the defect has been reproduced and the fix shown on every
machine carrying the affected hardware. The rule was written to be mechanical:
the build pre-flight reads the table and refuses to launch while any row is
unclosed. This is the gate that does that reading.

What is asserted here: each state in the declared vocabulary gets the verdict
the rule gives it; a proposed closure is refused, because only the operator
declares a row closed; a state the gate does not recognise refuses rather than
passing; a table it cannot find, cannot parse, or that has no rows is a setup
error and never a clean answer; a waiver passes and is announced; and — the
case that was found by firing this gate at a real table rather than at a
fixture — a row written without its closing pipe, and a cell containing an
escaped pipe, are both read correctly, because the first version of this gate
stopped at the first such row and would have called six unread rows clean.

Every fixture here is written by the test. The gate knows no paths of its own
and this file names no document.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "preflight-hardware-proof-gate.py"

_spec = importlib.util.spec_from_file_location("preflight_hardware_proof_gate",
                                               GATE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

HEADER = (
    "| Row | Component and deviation | Machines | State |\n"
    "|---|---|---|---|\n"
)


def _doc(rows, section="Hardware-proof gating", before="", after=""):
    body = "".join(rows)
    return (f"# A document\n\nSome prose.\n\n{before}"
            f"## {section} — observed deviations\n\n"
            f"State values and so on.\n\n"
            f"{HEADER}{body}\n{after}")


def _row(rid, state, machines="a machine", closing_pipe=True):
    line = f"| {rid} | a component and what was observed | {machines} | {state} "
    return line + ("|\n" if closing_pipe else "\n")


class ClassifyTest(unittest.TestCase):
    """The state vocabulary, one case per value the rule defines."""

    def test_open_refuses(self):
        self.assertEqual(_mod.classify("open")[0], "REFUSE")

    def test_in_flight_refuses(self):
        verdict, reason = _mod.classify("in flight: SOME-CUT-12")
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("in flight", reason)

    def test_closed_passes(self):
        self.assertEqual(_mod.classify("closed: some/evidence/path")[0], "PASS")

    def test_waived_passes(self):
        self.assertEqual(_mod.classify("waived: ledger row 1234")[0], "PASS")

    def test_a_proposed_closure_is_not_a_closure(self):
        """The case that matters most: a seat proposing a closure must not let
        a build launch. Only the operator declares a row closed."""
        verdict, reason = _mod.classify("closed-proposed 12:40")
        self.assertEqual(verdict, "REFUSE")
        self.assertIn("PROPOSED", reason)

    def test_a_word_closed_is_a_prefix_of_does_not_pass(self):
        """`closed` must not match `closedown`, `closedish` or anything else it
        merely begins."""
        self.assertEqual(_mod.classify("closedown of the row")[0], "UNREADABLE")

    def test_an_unknown_state_is_unreadable_not_clean(self):
        self.assertEqual(_mod.classify("LANDED on dev this morning")[0],
                         "UNREADABLE")

    def test_an_empty_state_is_unreadable(self):
        self.assertEqual(_mod.classify("   ")[0], "UNREADABLE")

    def test_emphasis_and_code_marks_do_not_hide_the_state(self):
        for written in ("`open`", "**open**", "_open_", "` open`"):
            with self.subTest(written=written):
                self.assertEqual(_mod.classify(written)[0], "REFUSE")

    def test_the_state_is_read_case_insensitively(self):
        self.assertEqual(_mod.classify("OPEN")[0], "REFUSE")
        self.assertEqual(_mod.classify("Closed: proof")[0], "PASS")


class CellSplittingTest(unittest.TestCase):
    """The row parser, against the two shapes a real table turned out to use."""

    def test_a_row_without_its_closing_pipe_is_still_a_row(self):
        self.assertEqual(_mod._cells("| a | b | c"), ["a", "b", "c"])

    def test_a_row_with_its_closing_pipe_is_a_row(self):
        self.assertEqual(_mod._cells("| a | b | c |"), ["a", "b", "c"])

    def test_an_escaped_pipe_stays_inside_its_cell(self):
        self.assertEqual(_mod._cells(r"| a | strict\|repo-only | c |"),
                         ["a", "strict|repo-only", "c"])

    def test_a_line_that_is_not_a_row_is_not_a_row(self):
        self.assertIsNone(_mod._cells("just some prose"))


class GateRunTest(unittest.TestCase):
    """The gate as the build launcher runs it: a path in, an exit code out."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, text, *extra):
        doc = self.root / "table.md"
        doc.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(GATE), "--gating-table", str(doc), *extra],
            capture_output=True, text=True)

    def test_every_row_closed_passes(self):
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`"),
                            _row("HP-2", "`closed: evidence/two`")]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertIn("2 row(s)", r.stdout)

    def test_one_open_row_refuses_and_names_it(self):
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`"),
                            _row("HP-2", "`open`")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HP-2", r.stderr)
        self.assertNotIn("REFUSES  HP-1", r.stderr)

    def test_one_in_flight_row_refuses(self):
        r = self._run(_doc([_row("HP-1", "`in flight: SOME-CUT-9`")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HP-1", r.stderr)

    def test_a_waiver_passes_and_is_announced(self):
        r = self._run(_doc([_row("HP-1", "`waived: ledger row 1234`")]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WAIVED", r.stdout)
        self.assertIn("HP-1", r.stdout)

    def test_a_waiver_is_announced_even_when_another_row_refuses(self):
        """A waiver is reported every time it is relied on, including on a run
        that stops for a different row."""
        r = self._run(_doc([_row("HP-1", "`waived: ledger row 1234`"),
                            _row("HP-2", "`open`")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("WAIVED", r.stdout)

    def test_there_is_no_command_line_waiver(self):
        """A waiver belongs in the table, where it is dated and attributed."""
        r = self._run(_doc([_row("HP-1", "`open`")]), "--waive", "HP-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments", r.stderr)

    def test_a_row_without_its_closing_pipe_is_read(self):
        """THE CASE FOUND AGAINST A REAL TABLE. The first version of this gate
        required the closing pipe, ended the table at the row that lacked one,
        and would have reported a clean table with six rows unread."""
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`"),
                            _row("HP-2", "`open`", closing_pipe=False),
                            _row("HP-3", "`closed: evidence/three`")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("3 read", r.stderr)
        self.assertIn("HP-2", r.stderr)

    def test_an_escaped_pipe_in_a_cell_does_not_break_the_row(self):
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`",
                                 machines=r"strict\|repo-only")]))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_unescaped_pipe_in_a_cell_is_a_setup_error_not_a_pass(self):
        """A cell containing a bare pipe shifts every cell after it, so the
        state column is no longer the state column. Refusing to read it is the
        only honest answer."""
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`",
                                 machines="strict|repo-only")]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("cells where the header states", r.stderr)

    def test_a_missing_document_is_a_setup_error(self):
        r = subprocess.run(
            [sys.executable, str(GATE), "--gating-table",
             str(self.root / "absent.md")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SETUP ERROR", r.stderr)

    def test_a_missing_section_is_a_setup_error_not_a_clean_answer(self):
        r = self._run(_doc([_row("HP-1", "`closed: e`")],
                           section="Some other section"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("read nothing", r.stderr)

    def test_a_section_with_no_table_is_a_setup_error(self):
        r = self._run("# A document\n\n## Hardware-proof gating\n\nProse.\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no table", r.stderr)

    def test_a_header_with_no_rows_is_a_setup_error(self):
        r = self._run(_doc([]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("must not report clean", r.stderr)

    def test_a_table_without_the_named_columns_is_a_setup_error(self):
        text = ("# A document\n\n## Hardware-proof gating\n\n"
                "| Item | Notes |\n|---|---|\n| HP-1 | closed |\n")
        r = self._run(text)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no 'Row'", r.stderr)

    def test_the_columns_are_found_by_name_not_by_position(self):
        """A table that states its columns in another order is read correctly."""
        text = ("# A document\n\n## Hardware-proof gating\n\n"
                "| State | Row | Machines |\n|---|---|---|\n"
                "| `open` | HP-9 | a machine |\n")
        r = self._run(text)
        self.assertEqual(r.returncode, 1)
        self.assertIn("HP-9", r.stderr)

    def test_the_section_stops_at_the_next_heading_of_the_same_level(self):
        """A table in a LATER section is not this section's table."""
        after = ("## Maintenance\n\n"
                 "| Row | Component and deviation | Machines | State |\n"
                 "|---|---|---|---|\n| X-1 | other | m | `open` |\n")
        r = self._run(_doc([_row("HP-1", "`closed: evidence/one`")],
                           after=after))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 row(s)", r.stdout)

    def test_a_subsection_inside_the_section_does_not_end_it(self):
        rows = [_row("HP-1", "`closed: evidence/one`")]
        text = _doc(rows) + "\n### A note\n\nmore prose\n"
        r = self._run(text)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_proposed_closure_refuses_the_build(self):
        r = self._run(_doc([_row("HP-1", "`closed-proposed 12:40` and then "
                                         "a paragraph of evidence")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROPOSED", r.stderr)

    def test_an_unrecognised_state_refuses_the_build(self):
        r = self._run(_doc([_row("HP-1", "LANDED on dev this morning")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNREADABLE", r.stderr)

    def test_the_section_heading_is_an_argument(self):
        r = self._run(_doc([_row("HP-1", "`open`")], section="Another gate"),
                      "--section", "Another gate")
        self.assertEqual(r.returncode, 1)

    def test_the_column_headers_are_arguments(self):
        text = ("# A document\n\n## Hardware-proof gating\n\n"
                "| Item | Verdict |\n|---|---|\n| HP-1 | `open` |\n")
        r = self._run(text, "--id-column", "Item", "--state-column", "Verdict")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HP-1", r.stderr)

    def test_the_gate_requires_a_path_and_never_guesses_one(self):
        r = subprocess.run([sys.executable, str(GATE)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--gating-table", r.stderr)


if __name__ == "__main__":
    unittest.main()
