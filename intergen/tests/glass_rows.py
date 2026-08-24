# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Read the turn record in a test by WHAT a row is, never by where it sits.

WHY THIS MODULE EXISTS. The turn record's writer emits rows of its own around a
turn: a sequence-resumed row when it opens the file, a rotation marker when the
file rolls, a synthesized terminal when a turn ends without one. Each of those
arrived in a release where tests that indexed row zero suddenly measured the
writer's bookkeeping instead of their own emission — and the failure looked like
the change under test being wrong, because the CONTROLS failed. A test that says
which row it means cannot be moved by a row the writer learns to emit next.

The rule this module makes cheap: name the row. ``only(rows, phase=..., event=...)``
says "the one prompt/assembled row"; ``last(...)`` says "the most recent verdict";
``where(...)`` returns the matching rows so a count is a count of THOSE rows and
not of everything in the file. When nothing matches, the error names every
(phase, event) pair the record actually holds, so the next reader is told what is
there instead of reading an IndexError.

This is test support. It reads the record; it never writes one, and nothing
shipped imports it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = ["read", "where", "only", "first", "last", "inventory"]

_RECORD = Path("intergen") / "glass.jsonl"


def read(state_home: str | Path) -> list[dict[str, Any]]:
    """Every row in the record under ``state_home``, in the order written.

    ``state_home`` is what XDG_STATE_HOME is pointed at for the test, i.e. the
    directory holding ``intergen/glass.jsonl``. A path to the file itself is
    also accepted, for a caller that already has one. A record that does not
    exist yet reads as no rows, which is a real state and not an error.
    """
    p = Path(state_home)
    if p.is_dir():
        p = p / _RECORD
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def _matches(row: dict[str, Any], phase, event, turn_id, pred) -> bool:
    if phase is not None and row.get("phase") != phase:
        return False
    if event is not None and row.get("event") != event:
        return False
    if turn_id is not None and row.get("turn_id") != turn_id:
        return False
    if pred is not None and not pred(row):
        return False
    return True


def where(rows: Iterable[dict[str, Any]], *,
          phase: str | None = None,
          event: str | None = None,
          turn_id: str | None = None,
          pred: Callable[[dict[str, Any]], bool] | None = None,
          ) -> list[dict[str, Any]]:
    """The rows matching every criterion given, in the order written.

    Count these rather than counting the file: a count of everything in the
    record is a statement about the writer's bookkeeping as well as the test's
    own emissions, and it changes the next time the writer says something.
    """
    return [r for r in rows if _matches(r, phase, event, turn_id, pred)]


def inventory(rows: Iterable[dict[str, Any]]) -> str:
    """What the record actually holds, as "phase/event xN", for a message."""
    counts: dict[str, int] = {}
    for r in rows:
        key = f"{r.get('phase')}/{r.get('event')}"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "the record is empty"
    return ", ".join(f"{k} x{n}" if n > 1 else k for k, n in counts.items())


def _describe(phase, event, turn_id, pred) -> str:
    bits = []
    if phase is not None:
        bits.append(f"phase={phase!r}")
    if event is not None:
        bits.append(f"event={event!r}")
    if turn_id is not None:
        bits.append(f"turn_id={turn_id!r}")
    if pred is not None:
        bits.append("a predicate")
    return " and ".join(bits) if bits else "any row"


def _pick(rows, which, phase, event, turn_id, pred):
    rows = list(rows)
    found = where(rows, phase=phase, event=event, turn_id=turn_id, pred=pred)
    want = _describe(phase, event, turn_id, pred)
    if not found:
        raise AssertionError(
            f"no row with {want} in the turn record. What is there: "
            f"{inventory(rows)}.")
    if which == "only" and len(found) != 1:
        raise AssertionError(
            f"expected exactly one row with {want}, found {len(found)}. "
            f"What is there: {inventory(rows)}.")
    return found[0] if which in ("only", "first") else found[-1]


def only(rows, *, phase=None, event=None, turn_id=None, pred=None) -> dict[str, Any]:
    """The single row matching; an error naming the record if there is not exactly one."""
    return _pick(rows, "only", phase, event, turn_id, pred)


def first(rows, *, phase=None, event=None, turn_id=None, pred=None) -> dict[str, Any]:
    """The earliest row matching; an error naming the record if there is none."""
    return _pick(rows, "first", phase, event, turn_id, pred)


def last(rows, *, phase=None, event=None, turn_id=None, pred=None) -> dict[str, Any]:
    """The latest row matching; an error naming the record if there is none."""
    return _pick(rows, "last", phase, event, turn_id, pred)
