# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Append-only JSONL audit log for InterGen tool dispatch.

Per D-008 RFC §9 (docs/architecture/intergen-provenance-gate-design.md):
every tool dispatch is recorded as one JSON line to
`$XDG_STATE_HOME/intergen/tool-dispatch.jsonl` (per-user). The log is
the user-owned record of what the dispatcher decided + what the user
allowed; `intergen tool-log` is the user-facing reader.

User-data-wipe path: `intergen tool-log --clear` truncates the file.
Reset path provided as `clear_log()`.

Per Q5 of T0-4-E propose-and-wait (operator-escalated 2026-05-19; my lean
as provisional default until operator rules): retention is 30 days via a
logrotate config at `/etc/logrotate.d/intergen-tool-dispatch` shipped by
the intergen package. The Python module here does not perform rotation
itself — logrotate is the canonical rotation mechanism on Linux.

The writer is best-effort: failures while writing the log MUST NOT crash
the dispatcher. The gate must continue working even if the log filesystem
is full, read-only, or otherwise unavailable; observability degrades
gracefully.

REDACTION IS AT THE WRITE BOUNDARY, not at the call sites. Every row this
module writes passes through `intergen.glass.redact_persisted`, the same
shared function used at the package's other persistence boundaries. The
rule it enforces is the one `intergen/trace.py` states: credentials are
never persisted, secrets stay out of logs. This writer did not enforce it
until 2026-10-01, and nothing above it did either: `arguments` is the
tool call's full argument dict, `excerpt` is raw ingress content,
`result_summary` is free text and `details` is a raw governance payload,
so a credential typed into a command line reached this file verbatim and
was kept for the retention above, while the same bytes written to either
other record came back as a named placeholder. Applying the function HERE
rather than at each caller is the whole point: a writer that adds a field
tomorrow gets the same treatment without knowing the function exists.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import stat
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Iterator

from intergen.glass import redact_persisted
from intergen.interfaces.provenance import AuditRecord
from intergen.private_state import private_dir, private_open

logger = logging.getLogger(__name__)


def default_log_path() -> Path:
    """Resolve the canonical log path per XDG Base Directory spec."""
    xdg_state = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state"
    )
    return Path(xdg_state) / "intergen" / "tool-dispatch.jsonl"


def write_record(record: AuditRecord, log_path: Path | None = None) -> bool:
    """Append a single audit record as one JSON line.

    Returns True on success, False on best-effort failure (caller does not
    need to inspect; this is observability-tier).
    """
    path = log_path if log_path is not None else default_log_path()
    try:
        # The mode is set AT CREATION rather than chmod-ed afterwards. The
        # previous create-then-chmod sequence left a window in which the file
        # already held its first dispatch record and was still world-readable,
        # and the chmod ran only on the FIRST write — a log that already
        # existed at 0644 (rotated in by logrotate, restored from a backup)
        # was never corrected.
        private_dir(path.parent)

        # THE CHOKEPOINT, on the whole row. Not on `arguments` alone: every
        # field here can carry caller-supplied text, and a field added later
        # would otherwise arrive unscanned. The function names what it removed
        # rather than dropping it, so the ledger stays readable; none of this
        # record's own field names is credential-shaped, so no field of a plain
        # dispatch record is replaced -- which the test module asserts, because
        # a reader shown a placeholder where a timestamp belongs would be worse
        # off than before.
        line = json.dumps(
            redact_persisted(record.to_jsonl_dict()), separators=(",", ":"))
        with private_open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort log writer
        logger.warning("audit_log: write_record best-effort failure: %s", exc)
        return False


def _record_lines(path: Path) -> list[tuple[int, str | None]]:
    """The record's lines as the writer wrote them, each decoded STRICTLY as UTF-8.

    The writer emits UTF-8 only and ends every row with a newline byte, so the file is
    split at each newline byte and each line is decoded on its own. A line whose bytes
    do not decode comes back as None beside its line number. Decoding with replacement
    characters instead would turn a damaged byte into valid text before the row is
    parsed, and the record would read as undamaged. Both readers below consume this one
    result, so what the rows reader yields and what the read-back counts are the same
    lines. Raises OSError when the file cannot be read.
    """
    data = path.read_bytes()
    lines: list[tuple[int, str | None]] = []
    for line_number, raw in enumerate(data.split(b"\n"), start=1):
        try:
            lines.append((line_number, raw.decode("utf-8")))
        except UnicodeDecodeError:
            lines.append((line_number, None))
    return lines


def record_presence(path: Path) -> tuple[str, str]:
    """Whether the record at ``path`` is ``"absent"``, ``"present"`` or ``"uncheckable"``.

    Absence is decided from the path's OWN stat (``lstat``), never from ``Path.exists()``,
    which is False for a symbolic link whose target is missing and for a file below a
    directory that cannot be searched: an existing record whose integrity cannot be checked
    would read as no record at all. Genuine ENOENT alone is absence. A link is followed once,
    to see that its target can be examined. Anything else is uncheckable, and the second
    element names what was found: the link and its target, or the error.
    """
    try:
        own = os.lstat(path)
    except FileNotFoundError:
        return "absent", ""
    except OSError as exc:
        return "uncheckable", f"{path} could not be examined: {exc}"
    if stat.S_ISLNK(own.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            return "uncheckable", f"{path} is a symbolic link that could not be read: {exc}"
        try:
            os.stat(path)
        except FileNotFoundError:
            return "uncheckable", f"{path} is a symbolic link to {target}, which does not exist"
        except OSError as exc:
            return ("uncheckable",
                    f"{path} is a symbolic link to {target}, which could not be examined: {exc}")
    return "present", ""


def read_records(log_path: Path | None = None) -> Iterator[dict]:
    """Yield each parsed audit record dict from the log.

    A line this cannot parse is passed over so that the rest of the record stays
    readable -- but it is no longer passed over QUIETLY. The breadcrumb was at debug
    level, which nothing turns on, so a record altered by one byte came back one row
    shorter and every caller, including the user-facing command, printed the remainder
    as if it were the record. It is a warning now, and the count of what was lost is
    available to a caller through :func:`read_integrity`, which is what the command
    prints above the rows. Yielding rows and judging the record are different jobs:
    this function does the first and must not become the second, because a reader that
    cannot see a damaged record at all is worse off than one shown it with a warning.
    A line whose bytes are not UTF-8 is passed over the same way, with its own warning;
    the lines come from :func:`_record_lines`, as the read-back's do. A record that is
    absent yields nothing; one that cannot be checked (see :func:`record_presence`) yields
    nothing with a warning that names what was found. A line that is JSON but not one row
    object is passed over with a warning too: the reading counts it as unreadable, so the
    rows yielded are exactly the rows the reading counts.
    """
    path = log_path if log_path is not None else default_log_path()
    _reading, rows, passed_over = _read_once(path)
    for note in passed_over:
        logger.warning("%s", note)
    yield from rows


@functools.lru_cache(maxsize=None)
def _serialized_fields(kind: str) -> frozenset[str]:
    """Fields serialized for every AuditRecord row of ``kind``.

    Read from AuditRecord.to_jsonl_dict() itself rather than listed here, so the
    check follows the writer: a field the writer starts serializing is required from
    then on. A field the writer serializes only when it holds a value is not.
    """
    values = {f.name: None for f in fields(AuditRecord)
              if f.default is MISSING and f.default_factory is MISSING}
    return frozenset(AuditRecord(**values, kind=kind).to_jsonl_dict())


_DEFAULT_KIND = next(f.default for f in fields(AuditRecord) if f.name == "kind")

# Fields the writer serializes only beside a kind other than the default one, so a
# row that carries one of them without a kind has lost its kind field.
_KIND_FIELDS = (frozenset(f.name for f in fields(AuditRecord))
                - _serialized_fields(_DEFAULT_KIND))


def _missing_serialized_field(row: dict) -> bool:
    """Whether ``row`` lacks a field the writer serializes for a row of its kind."""
    kind = row.get("kind", _DEFAULT_KIND)
    expected = _serialized_fields(kind if isinstance(kind, str) else repr(kind))
    if not expected <= row.keys():
        return True
    return "kind" not in row and bool(_KIND_FIELDS & row.keys())

# A turn identifier that is not a turn. The turn record raises rows outside any
# conversation -- a sequence reseed, a boot, a dispatch with no turn around it -- and
# marks them with one of these. A reader joining rows to turns gets nothing for them, so
# they are counted as unjoinable rather than counted as joined.
_SENTINEL_TURN_PREFIXES = ("no-turn", "glass-", "boot-")


@dataclass(frozen=True)
class IntegrityReading:
    """What can and cannot be established about one record file, read back.

    WHY THERE IS NO DIGEST HERE. No digest, no signature and no sidecar, and this writer
    references none: that is a decision of record, and it bounds this reading exactly.
    What a digest would buy is detection of a substitution -- a line removed whole, or
    replaced by another well-formed line. Nothing in the record binds one row to the next
    or to the machine that wrote it, so this reading CANNOT see that, and it says so
    rather than implying otherwise by silence.

    A digest on this writer would also not be free. The writer is best-effort by mandate:
    a failure writing the record must not fail the request. A running digest would have to
    be read, extended and written back under the same lock as the append, which turns
    every dispatch into a read-modify-write on a file the writer is allowed to fail at --
    and a chain whose last link failed to land is indistinguishable from a chain someone
    truncated, so the first dropped write would make the record permanently unverifiable.
    That trade is the operator's to make, not this reading's, and the governing record
    settles it: no digest.

    So the verdict is never ``intact``. It is ``UNTRUSTWORTHY`` when damage was detected or
    the record could not be read (the reason named), ``NO DETECTABLE DAMAGE`` when it was
    read in full and none was, and ``NO RECORD`` only when there is no record or it was read
    in full and holds nothing -- and the report names the limit beside the verdict every time.
    """

    path: Path
    lines: int = 0
    rows: int = 0
    unreadable_lines: list[int] = field(default_factory=list)
    undecodable_lines: list[int] = field(default_factory=list)
    incomplete_rows: list[int] = field(default_factory=list)
    out_of_order_rows: list[int] = field(default_factory=list)
    unjoinable_rows: int = 0
    exists: bool = True      # False only when the path's own stat found nothing there
    read_error: str = ""     # why the record could not be read or checked, when it could not

    @property
    def damaged(self) -> bool:
        """Was damage DETECTED. Not: is the record sound."""
        return bool(self.unreadable_lines or self.undecodable_lines or self.incomplete_rows
                    or self.read_error)

    @property
    def verdict(self) -> str:
        # A read error and damage are decided BEFORE emptiness. A record that exists but could
        # not be read has no line and no row, which is what an empty record looks like, yet its
        # integrity was never checked: calling it NO RECORD would say nothing had been written.
        if not self.exists:
            return "NO RECORD — nothing has been written here, so there is nothing to check"
        if self.read_error:
            return ("UNTRUSTWORTHY — this record could not be read, so its integrity cannot be "
                    f"checked: {self.read_error}")
        if self.damaged:
            return "UNTRUSTWORTHY — this record has been damaged since it was written"
        if self.lines == 0 and self.rows == 0:
            return "NO RECORD — nothing has been written here, so there is nothing to check"
        return ("NO DETECTABLE DAMAGE — every row this record holds is readable and "
                "complete")

    def report(self) -> list[str]:
        """The verdict and every reading behind it, one line each, for a person."""
        out = [self.verdict]
        if not self.exists:
            out.append(f"  no file at {self.path}")
            return out
        if self.read_error:
            out.append(f"  the record could not be read at all: {self.read_error}")
            return out
        out.append(f"  {self.lines} line(s) in the file, {self.rows} readable row(s)")
        if self.undecodable_lines:
            out.append(
                f"  {len(self.undecodable_lines)} line(s) hold bytes that are not UTF-8 "
                f"text, which the writer never writes; they could not be read and are "
                f"missing from anything that reads this record: {self.undecodable_lines}")
        if self.unreadable_lines:
            out.append(
                f"  {len(self.unreadable_lines)} line(s) could not be read and are "
                f"missing from anything that reads this record: "
                f"{self.unreadable_lines}")
        if self.incomplete_rows:
            out.append(
                f"  {len(self.incomplete_rows)} row(s) are missing a field the writer "
                f"serializes for rows of their kind: {self.incomplete_rows}")
        if self.out_of_order_rows:
            out.append(
                f"  {len(self.out_of_order_rows)} row(s) carry a timestamp earlier "
                f"than at least one prior row: {self.out_of_order_rows}. Writers that run "
                f"at the same time can append in that order, so this is an "
                f"observation, not damage.")
        if self.unjoinable_rows:
            out.append(
                f"  {self.unjoinable_rows} of {self.rows} row(s) cannot be joined to a "
                f"conversation turn: they carry no turn identifier, or a sentinel in "
                f"place of one. This is how the record has always been written and is "
                f"not damage; it is named because a reader joining these rows to turns "
                f"gets nothing back for them.")
        out.append(
            "  WHAT THIS CANNOT ESTABLISH: that no row was removed, and that no row was "
            "replaced by another well-formed row. This record carries no digest, no "
            "signature and no sidecar, by decision, and nothing in it binds one row to "
            "the next, so a substitution of that kind leaves no trace for this reading "
            "to find. A clean reading here is the absence of detected damage and never "
            "a statement that the record is intact.")
        return out


def read_integrity(log_path: Path | None = None) -> IntegrityReading:
    """Validate each line's UTF-8, its JSON structure and the fields serialized for each row.

    The lines come from :func:`_record_lines`, decoded strictly: a line whose bytes are
    not UTF-8 is named and is damage, as a line that is not JSON is. Absence is decided by
    :func:`record_presence`: a record that cannot be checked carries what was found as its
    read error, and the verdict names it.
    """
    path = log_path if log_path is not None else default_log_path()
    return _read_once(path)[0]


@dataclass(frozen=True)
class RecordSnapshot:
    """One read of the record: the integrity reading and the rows it counted, from the same bytes."""

    reading: IntegrityReading
    rows: list[dict]


def read_snapshot(log_path: Path | None = None) -> RecordSnapshot:
    """Read the record ONCE; the verdict, the rows and their count all come from that read.

    A view that took the verdict from one read and the rows from a second could print rows
    the verdict never saw: a change landing between the two reads left surviving rows beneath
    a clean verdict. Every line passed over is logged as :func:`read_records` logs it.
    """
    path = log_path if log_path is not None else default_log_path()
    reading, rows, passed_over = _read_once(path)
    for note in passed_over:
        logger.warning("%s", note)
    return RecordSnapshot(reading=reading, rows=rows)


def _read_once(path: Path) -> tuple[IntegrityReading, list[dict], list[str]]:
    """The reading, its readable rows and a note for each line passed over, from ONE read."""
    presence, found = record_presence(path)
    if presence == "absent":
        return IntegrityReading(path=path, exists=False), [], []
    if presence == "uncheckable":
        return (IntegrityReading(path=path, read_error=found), [],
                [f"audit_log: the record could not be checked and nothing was read from it: "
                 f"{found}"])
    try:
        record_lines = _record_lines(path)
    except OSError as exc:
        return (IntegrityReading(path=path, read_error=str(exc)), [],
                [f"audit_log: the record could not be read and nothing was read from it: {exc}"])

    lines = 0
    rows: list[dict] = []
    passed_over: list[str] = []
    unreadable: list[int] = []
    undecodable: list[int] = []
    incomplete: list[int] = []
    out_of_order: list[int] = []
    unjoinable = 0
    previous_timestamp: str | None = None
    for line_number, raw in record_lines:
        if raw is None:
            lines += 1
            undecodable.append(line_number)
            passed_over.append(
                f"audit_log: line {line_number} of {path} holds bytes that are not UTF-8 text "
                f"and is NOT included in what this reader returned")
            continue
        if not raw.strip():
            continue
        lines += 1
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            unreadable.append(line_number)
            passed_over.append(
                f"audit_log: line {line_number} of {path} could not be read and is NOT "
                f"included in what this reader returned: {exc}")
            continue
        if not isinstance(row, dict):
            unreadable.append(line_number)
            passed_over.append(
                f"audit_log: line {line_number} of {path} is JSON but not one row object and "
                f"is NOT included in what this reader returned")
            continue
        rows.append(row)
        if _missing_serialized_field(row):
            incomplete.append(line_number)
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            # Compared with the latest timestamp of every prior row, not only the row
            # before it: a row is named when it is earlier than at least one prior row.
            if previous_timestamp is not None and timestamp < previous_timestamp:
                out_of_order.append(line_number)
            previous_timestamp = max(previous_timestamp or "", timestamp)
        turn = row.get("turn_id")
        if not turn or str(turn).startswith(_SENTINEL_TURN_PREFIXES):
            unjoinable += 1
    reading = IntegrityReading(
        path=path,
        lines=lines,
        rows=len(rows),
        unreadable_lines=unreadable,
        undecodable_lines=undecodable,
        incomplete_rows=incomplete,
        out_of_order_rows=out_of_order,
        unjoinable_rows=unjoinable,
    )
    return reading, rows, passed_over


def clear_log(log_path: Path | None = None) -> bool:
    """Truncate the log to zero bytes (user-data-wipe path).

    Invoked by `intergen tool-log --clear`. Returns True on success, False
    on best-effort failure.
    """
    path = log_path if log_path is not None else default_log_path()
    try:
        if not path.exists():
            return True
        # Truncate rather than unlink so the inode + 0o600 permissions
        # are preserved across user-data-wipe cycles.
        with private_open(path, "w", encoding="utf-8") as f:
            f.write("")
        return True
    except OSError as exc:
        logger.warning("audit_log: clear_log OSError: %s", exc)
        return False


def record_count(log_path: Path | None = None) -> int:
    """Count audit records currently in the log. Used for status display."""
    return sum(1 for _ in read_records(log_path))
