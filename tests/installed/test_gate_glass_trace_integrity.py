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

import pytest

PLACEHOLDER_TURN = "no-turn"


@pytest.fixture(scope="module")
def trace_rows(real_home):
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
    return rows, unparseable, path


def test_every_recorded_row_can_be_joined_to_the_turn_that_produced_it(trace_rows):
    rows, _unparseable, path = trace_rows
    orphaned = [r for r in rows if r.get("turn_id") in (None, "", PLACEHOLDER_TURN)]

    events = collections.Counter(
        (r.get("phase"), r.get("event")) for r in orphaned)
    report = ["", f"TRACE FILE: {path}",
              f"  rows: {len(rows)}   rows with no usable turn identifier: "
              f"{len(orphaned)}", ""]
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
    rows, _unparseable, path = trace_rows
    seqs = [r.get("seq") for r in rows if r.get("seq") is not None]
    duplicates = [s for s, c in collections.Counter(seqs).items() if c > 1]

    assert not duplicates, (
        f"\nThe sequence numbers in {path} do not order the record.\n"
        f"  rows carrying a sequence number : {len(seqs)}\n"
        f"  distinct sequence numbers       : {len(set(seqs))}\n"
        f"  values that appear more than once: {len(duplicates)}\n"
        "The counter starts again at zero in every daemon process, so a reader "
        "reconstructing the record cannot tell which row came first. Timestamps are "
        "present but a wall clock is not a sequence — two rows can share one."
    )


def test_no_row_of_the_record_is_unreadable(trace_rows):
    """A torn line is tolerated by the reader; it is still a hole in the record."""
    _rows, unparseable, path = trace_rows
    assert unparseable == 0, (
        f"\n{unparseable} line(s) in {path} could not be parsed. The shipped reader "
        "skips malformed lines so the rest of the record survives, which is correct — "
        "but a skipped line is a piece of the user's record that no longer exists, and "
        "nothing counts them."
    )
