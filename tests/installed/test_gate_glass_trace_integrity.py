"""GATE 12 — trace correlation and record integrity (section 9 line 11).

WHAT THIS TIER LINE IS FOR. The always-on trace is the record a user reads to find out
what their assistant actually did. Its value rests on two properties: every row of a
turn can be joined to that turn, and the record can be put back in order. Both are
properties of the file a running daemon produces, so neither can be checked by a test
that writes its own rows into a temporary directory.

WHAT IS MEASURED. This machine's own trace file, as written by the shipped daemon.

WHAT IS NOT MEASURED, STATED PLAINLY: the terminal-frame integrity of the browser
surface is asserted from the trace, not by driving a browser. That leg belongs with the
turn-lifecycle gate's unproven residue.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import collections
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

PLACEHOLDER_TURN = "no-turn"

#: The package database the rest of this tier's tooling reads.
PKM_DB = Path("/var/lib/igos/pkm.db")


def installed_release_install_date() -> float:
    """When the assistant package this tier measures was installed, as an epoch.

    Read from the same row of the same database that
    scripts/run-installed-gates.py records as ``install_date`` in record.json, so
    the bound this gate applies and the date the record reports are one fact.

    A database that cannot be read, or a date that cannot be parsed, FAILS the
    gate. An unreadable bound must never quietly become no bound: that would put
    the whole file back in scope while the message said the rows were bounded.
    """
    if not PKM_DB.is_file():
        pytest.fail(
            f"{PKM_DB} is absent, so the date this release was installed cannot "
            f"be read and the trace cannot be bounded to it.")
    try:
        con = sqlite3.connect(f"file:{PKM_DB}?immutable=1", uri=True)
        try:
            row = con.execute(
                "SELECT install_date FROM installed "
                "WHERE name = ? AND superseded_by IS NULL", ("intergen",)).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        pytest.fail(f"the package database could not be read ({exc}), so the "
                    f"trace cannot be bounded to this release's install date")
    if row is None or not row[0]:
        pytest.fail("the assistant package records no install date, so the trace "
                    "cannot be bounded to the release under test")
    try:
        return datetime.fromisoformat(row[0]).timestamp()
    except ValueError as exc:
        pytest.fail(f"the recorded install date {row[0]!r} could not be parsed "
                    f"({exc}); the trace cannot be bounded to it")


def rows_since(rows: list, since: float) -> tuple[list, int, int]:
    """(rows in scope, rows dropped as older, rows kept because undatable).

    A row whose timestamp cannot be read is KEPT. The bound may only ever remove
    a row it can prove was written before this release was installed; an
    unplaceable row is not proof of anything, and dropping it would let the gate
    go quiet about rows it never examined.
    """
    in_scope, older, undatable = [], 0, 0
    for row in rows:
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            undatable += 1
            in_scope.append(row)
            continue
        if ts < since:
            older += 1
            continue
        in_scope.append(row)
    return in_scope, older, undatable


@pytest.fixture(scope="module")
def trace_rows(real_home):
    """This machine's trace, bounded to the release this tier is measuring.

    WHY THE BOUND, added 2026-08-24. The trace is append-only and the writer
    rotates it by SIZE alone (64 MB, keeping five), so on a machine that has been
    upgraded the rows a PREVIOUS release wrote are still in the file and stay
    there for as long as it takes to write 64 MB — measured on this box, 37 KB
    after a full day of use. Both properties below are properties of the software
    that wrote a row, so unbounded they are asserted against rows no shipped
    change can ever alter: a release that fixed them would keep failing, and
    because scripts/check-release-validation.py refuses on any failing gate, no
    upgraded machine could validate any release at all. A gate that cannot go
    green after the fix does not protect the property, it teaches people to route
    around it.

    The bound is the installed release's own install date, which is what makes
    this a statement about the release under test rather than about the machine's
    history. Bounding to zero rows FAILS: "the release wrote nothing I can read"
    is a finding, never a pass.
    """
    path = real_home / ".local" / "state" / "intergen" / "glass.jsonl"
    if not path.is_file():
        pytest.fail(
            f"This machine has no trace file at {path}. The trace is described as "
            "always-on; its absence is itself the finding, and this gate refuses to "
            "skip past it.")
    rows, unparseable = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            unparseable += 1
    if not rows:
        pytest.fail(f"The trace file {path} holds no parseable rows.")

    since = installed_release_install_date()
    in_scope, older, undatable = rows_since(rows, since)
    when = datetime.fromtimestamp(since).isoformat(timespec="seconds")
    if not in_scope:
        pytest.fail(
            f"\n{path} holds {len(rows)} row(s) and NONE of them was written on or "
            f"after {when}, when the release under test was installed.\n"
            f"  rows older than the install: {older}\n"
            "The trace is described as always-on, so a release that has been "
            "installed and has answered nothing has either not run or is not "
            "tracing. Either way nothing about this release's record has been "
            "measured, and that is a failure and not a pass.")
    return in_scope, unparseable, path, {"total": len(rows), "older": older,
                                         "undatable": undatable, "since": when}


def test_every_recorded_row_can_be_joined_to_the_turn_that_produced_it(trace_rows):
    rows, _unparseable, path, bound = trace_rows
    orphaned = [r for r in rows if r.get("turn_id") in (None, "", PLACEHOLDER_TURN)]

    events = collections.Counter(
        (r.get("phase"), r.get("event")) for r in orphaned)
    report = ["", f"TRACE FILE: {path}",
              f"  rows written on or after {bound['since']}, when this release "
              f"was installed: {len(rows)} of {bound['total']} in the file "
              f"({bound['older']} older, {bound['undatable']} undatable and "
              f"therefore kept)",
              f"  rows with no usable turn identifier: {len(orphaned)}", ""]
    for (phase, event), count in events.most_common():
        report.append(f"  {count:4d}  {phase}/{event}")
    report.append("")
    report.append(
        "Every row above was written with the literal placeholder identifier, so it "
        "cannot be joined to the turn that produced it — and rows from different turns "
        "share that one placeholder, so they cannot be separated from each other "
        "either. Model work appears here, not only start-up bookkeeping.")

    assert not orphaned, "\n".join(report)


def test_the_record_can_be_put_back_in_order_across_the_whole_file(trace_rows):
    """The sequence number must order the file, not just one daemon run.

    The counter restarts at zero in each daemon process, so the same sequence number
    appears many times in one append-only file. A reader cannot use it to order the
    record, which is what an append-only record is for.
    """
    rows, _unparseable, path, bound = trace_rows
    seqs = [r.get("seq") for r in rows if r.get("seq") is not None]
    duplicates = [s for s, c in collections.Counter(seqs).items() if c > 1]

    assert not duplicates, (
        f"\nThe sequence numbers in {path} do not order the record.\n"
        f"  rows in scope (written on or after {bound['since']}): {len(rows)} "
        f"of {bound['total']} in the file\n"
        f"  rows carrying a sequence number : {len(seqs)}\n"
        f"  distinct sequence numbers       : {len(set(seqs))}\n"
        f"  values that appear more than once: {len(duplicates)}\n"
        "The counter starts again at zero in every daemon process, so a reader "
        "reconstructing the record cannot tell which row came first. Timestamps are "
        "present but a wall clock is not a sequence — two rows can share one."
    )


def test_no_row_of_the_record_is_unreadable(trace_rows):
    """A torn line is tolerated by the reader; it is still a hole in the record."""
    _rows, unparseable, path, _bound = trace_rows
    assert unparseable == 0, (
        f"\n{unparseable} line(s) in {path} could not be parsed. The shipped reader "
        "skips malformed lines so the rest of the record survives, which is correct — "
        "but a skipped line is a piece of the user's record that no longer exists, and "
        "nothing counts them."
    )
