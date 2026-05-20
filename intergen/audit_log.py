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
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Iterator

from intergen.interfaces.provenance import AuditRecord

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
        path.parent.mkdir(parents=True, exist_ok=True)
        # Lock down log directory to user-only (0o700) so other users on a
        # shared system cannot read tool-dispatch history.
        try:
            os.chmod(
                path.parent,
                stat.S_IRWXU,  # 0o700
            )
        except OSError:
            # Best-effort; if chmod fails on the parent (e.g. it's a symlink
            # to a path the user does not own), keep going. The file itself
            # gets 0o600 below.
            pass

        line = json.dumps(record.to_jsonl_dict(), separators=(",", ":"))
        first_write = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        if first_write:
            try:
                os.chmod(
                    path,
                    stat.S_IRUSR | stat.S_IWUSR,  # 0o600
                )
            except OSError:
                # Same best-effort posture as the directory chmod.
                pass
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort log writer
        logger.warning("audit_log: write_record best-effort failure: %s", exc)
        return False


def read_records(log_path: Path | None = None) -> Iterator[dict]:
    """Yield each parsed audit record dict from the log.

    Skips malformed lines with a debug-level breadcrumb; the log is
    append-only by design so corruption is rare and rare-corruption MUST
    NOT prevent reading the rest. Used by `intergen tool-log`.
    """
    path = log_path if log_path is not None else default_log_path()
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_number, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.debug(
                        "audit_log: skipping malformed line %d: %s",
                        line_number,
                        exc,
                    )
                    continue
    except OSError as exc:
        logger.warning("audit_log: read_records OSError: %s", exc)
        return


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
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return True
    except OSError as exc:
        logger.warning("audit_log: clear_log OSError: %s", exc)
        return False


def record_count(log_path: Path | None = None) -> int:
    """Count audit records currently in the log. Used for status display."""
    return sum(1 for _ in read_records(log_path))
