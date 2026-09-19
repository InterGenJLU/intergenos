#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed preflight gate: no build launches while a hardware-proof row is
still open.

Decided 2026-09-18: a finding against a critical component — the kernel and the
Secure Boot chain, the bootloader, the installer, the package manager and its
download helpers, the assistant and its GPU engines, the welcomer, networking
and DNS, the observability agents — opens a row in a gating table at the moment
it is classified, and the row closes only when the defect has been reproduced
and the fix shown on every machine that carries the affected hardware. Unit
tests never close one. The rule was written to be MECHANICAL: the build
pre-flight reads the table and refuses to launch while any row is unclosed. Read
by hand it is a rule somebody has to remember at the one moment they are most
eager to start a build.

This gate is that mechanism, and nothing more. It is a table reader. It is given
the path to a document, the heading of the section to read inside it, and it
reports what state each row is in. It knows no paths of its own, carries no
copy of any table, and holds no opinion about what the rows mean. The table it
reads for this project lives outside this repository; a different deployment
points it at a different file, and a reader of this script learns the mechanism
without being told the contents.

THE STATE VOCABULARY, and why each answer is what it is:

  open                 the row is open. REFUSE. This is the whole point.
  in flight: <id>      work is under way and the proof is not in. REFUSE —
                       "someone is on it" is the state the rule exists to stop
                       a build from launching in.
  closed: <evidence>   proven. PASS.
  waived: <record>     the operator waived this row by name and the waiver is
                       recorded. PASS, and SAY SO on the way past: a waiver
                       that scrolls by unannounced is a waiver nobody reviewed.

Anything else REFUSES and is reported as unrecognised, including the states a
project's own habits grow around the vocabulary — `closed-proposed`, for one,
which is a seat proposing a closure, not a closure. Only the operator declares a
row closed, so a gate that read a proposal as a closure would let a build launch
on a seat's own say-so. Guessing at an unfamiliar state is exactly the silent
failure this gate exists to remove, so an unreadable row is a refusal and the
line is printed for a person to look at.

There is deliberately NO command-line waiver. A waiver belongs in the table
where it is dated, attributed and reviewable; a `--waive HP-4` flag would move
that decision into a launch command nobody reads afterwards.

Exit 0 when every row passes, 1 when any row refuses, 2 on a usage or setup
error — an unreadable document, a missing section, a table that does not have
the columns this gate needs. A gate that cannot read its input reports that
rather than reporting clean.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The heading of the section to read. Overridable, because the heading is a
# property of the document and not of this gate.
DEFAULT_SECTION = "Hardware-proof gating"

# An ATX heading: one or more #, then the text.
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")
# A separator cell: dashes with optional alignment colons.
_SEPARATOR_CELL = re.compile(r"^:?-{1,}:?$")

PASSING = ("closed", "waived")
REFUSING = ("open", "in flight")
# A proposal is not a closure. Only the operator declares a row closed, so a
# seat's proposed closure is named here and refused rather than falling through
# to the unrecognised case — the refusal is the same, the sentence is useful.
PROPOSED = ("closed-proposed", "closed proposed")


def _cells(line: str) -> list[str] | None:
    """Split one markdown table row into its cells, or None if it is not a row.

    The CLOSING pipe is optional, which is what the markdown these tables are
    written in allows. That is not a detail: the first version of this gate
    required it, and against the real table a single row that had been written
    without one ended the table after the FIRST row. The gate then read one row,
    found no open state in it, and would have reported a clean table while six
    further rows — three of them in flight — sat unread below. A gate that
    stops early and calls it clean is the failure this gate exists to remove,
    so the parser accepts both forms and the row count is printed on every run
    for a person to compare against the document.
    """
    text = line.strip()
    if not text.startswith("|"):
        return None
    text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    # Split on UNESCAPED pipes only. A backslash-escaped pipe is a literal
    # character inside a cell, which is how the format lets a cell quote a
    # command like `--archive-trust=strict\|repo-only`. Splitting on it instead
    # shifts every cell after it one to the left, so a gate reading the state
    # column by position would read the wrong text and a gate reading it by
    # name would refuse the whole document. Measured against the real table,
    # which contains exactly that.
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current))
    return [c.strip() for c in cells]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells)


def _strip_decoration(text: str) -> str:
    """Remove the emphasis and code marks a state cell is written with.

    The table is prose as well as data: a state is written `open`, or **open**,
    or `in flight: CUT-12` with a paragraph of evidence after it. What is being
    classified is how the cell STARTS, so the marks in front of the first word
    are removed and nothing else is touched.
    """
    return text.lstrip("`*_ \t")


def classify(state_cell: str) -> tuple[str, str]:
    """Return (verdict, reason) for one state cell.

    verdict is one of PASS, REFUSE, UNREADABLE. The reason is a plain sentence
    for a person reading a refusal, not a code for another program.
    """
    text = _strip_decoration(state_cell)
    if not text:
        return "UNREADABLE", "the state cell is empty"
    lowered = text.lower()
    for word in PROPOSED:
        if lowered.startswith(word):
            return ("REFUSE",
                    "a closure has been PROPOSED but not declared; only the "
                    "operator closes a row")
    for word in REFUSING:
        if lowered.startswith(word):
            return "REFUSE", f"the row is {word}"
    for word in PASSING:
        # `closed:` and `closed ` are the state; `closed-proposed` is not, and
        # neither is any other word this one is merely a prefix of.
        after = lowered[len(word):len(word) + 1]
        if lowered.startswith(word) and after in ("", ":", " "):
            return "PASS", f"the row is {word}"
    return ("UNREADABLE",
            "the state does not begin with one of "
            f"{', '.join(REFUSING + PASSING)}")


def vocabulary() -> dict[str, tuple[str, ...]]:
    """The states this gate recognises, for a caller that wants to print them."""
    return {"passing": PASSING, "refusing": REFUSING + PROPOSED}


def read_section(document: Path, section: str) -> list[str]:
    """Return the lines of the named section, up to the next heading at the
    same level or shallower.

    Raises ValueError when the section is absent. A gate asked to read a
    section that is not there has measured nothing, and saying "no open rows"
    would be a lie about a document it never found.
    """
    text = document.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    level = None
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m is None:
            continue
        if start is None:
            if m.group("text").lower().startswith(section.lower()):
                start = i + 1
                level = len(m.group("hashes"))
            continue
        if len(m.group("hashes")) <= level:
            return lines[start:i]
    if start is None:
        raise ValueError(
            f"no heading beginning {section!r} in {document} — the gate read "
            f"nothing, which is not the same as finding no open rows")
    return lines[start:]


def read_rows(document: Path, section: str,
              id_column: str = "Row",
              state_column: str = "State") -> list[dict]:
    """Return one dict per data row of the section's first table.

    The columns are found BY NAME in the header row, so a table that grows a
    column, or states its columns in another order, is read correctly rather
    than by counting from the left.
    """
    body = read_section(document, section)
    header = None
    indexes = None
    rows: list[dict] = []
    for line in body:
        cells = _cells(line)
        if cells is None:
            if header is not None:
                break          # the table ended
            continue
        if _is_separator(cells):
            continue
        if header is None:
            header = [c.strip() for c in cells]
            lowered = [c.lower() for c in header]
            try:
                indexes = (lowered.index(id_column.lower()),
                           lowered.index(state_column.lower()))
            except ValueError:
                raise ValueError(
                    f"the table under {section!r} in {document} has no "
                    f"{id_column!r} and {state_column!r} columns; it states: "
                    f"{header}")
            continue
        i_id, i_state = indexes
        if len(cells) != len(header):
            raise ValueError(
                f"a row under {section!r} in {document} has {len(cells)} "
                f"cells where the header states {len(header)}. The row is "
                f"read by column NAME, so a row that does not line up with "
                f"the header cannot be read at all: {cells[:2]}")
        rows.append({"id": cells[i_id], "state": cells[i_state]})
    if header is None:
        raise ValueError(
            f"no table under {section!r} in {document} — the gate read "
            f"nothing, which is not the same as finding no open rows")
    if not rows:
        raise ValueError(
            f"the table under {section!r} in {document} has a header and no "
            f"rows; a scan that finds nothing must not report clean")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gating-table", required=True, metavar="PATH",
                    help="the document holding the gating table. Required: "
                         "this gate never guesses a path, because a guessed "
                         "one that is absent would look exactly like a clean "
                         "table.")
    ap.add_argument("--section", default=DEFAULT_SECTION,
                    help=f"heading of the section to read "
                         f"(default: {DEFAULT_SECTION!r})")
    ap.add_argument("--id-column", default="Row",
                    help="header of the column naming each row (default: Row)")
    ap.add_argument("--state-column", default="State",
                    help="header of the column holding each row's state "
                         "(default: State)")
    args = ap.parse_args(argv)

    document = Path(args.gating_table)
    if not document.is_file():
        print(f"[hardware-proof-gate] SETUP ERROR: {document} is not a "
              f"readable file. The gating table is where the answer lives; "
              f"without it this gate has measured nothing.", file=sys.stderr)
        return 2
    try:
        rows = read_rows(document, args.section,
                         args.id_column, args.state_column)
    except (OSError, ValueError) as e:
        print(f"[hardware-proof-gate] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    refusals = []
    unreadable = []
    waived = []
    for row in rows:
        verdict, reason = classify(row["state"])
        if verdict == "REFUSE":
            refusals.append((row, reason))
        elif verdict == "UNREADABLE":
            unreadable.append((row, reason))
        elif _strip_decoration(row["state"]).lower().startswith("waived"):
            waived.append(row)

    # A waiver passes and is announced whether the run passes or refuses: it is
    # a decision somebody made, and it is reported every time it is relied on.
    for row in waived:
        print(f"[hardware-proof-gate] WAIVED: {row['id']} — "
              f"{_strip_decoration(row['state'])}")

    if not refusals and not unreadable:
        print(f"[hardware-proof-gate] PASS: {len(rows)} row(s) read from "
              f"{document}; none open or in flight"
              + (f"; {len(waived)} waived" if waived else ""))
        return 0

    print(f"[hardware-proof-gate] HALT: the build does not launch while a "
          f"hardware-proof row is unclosed.", file=sys.stderr)
    print(f"  table   : {document}", file=sys.stderr)
    print(f"  section : {args.section}", file=sys.stderr)
    print(f"  rows    : {len(rows)} read, {len(refusals)} open or in flight, "
          f"{len(unreadable)} unreadable", file=sys.stderr)
    for row, reason in refusals:
        print(f"  REFUSES  {row['id']}: {reason}", file=sys.stderr)
        print(f"           states: {row['state'][:200]}", file=sys.stderr)
    for row, reason in unreadable:
        print(f"  UNREADABLE {row['id']}: {reason}", file=sys.stderr)
        print(f"           states: {row['state'][:200]}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  A row closes when the defect has been reproduced and the fix "
          "shown on every machine carrying the affected hardware, and the "
          "row's state says so. Only the operator declares a row closed or "
          "waives it, in the table, by name — this gate has no flag for it.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
