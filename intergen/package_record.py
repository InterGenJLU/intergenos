# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""What this machine's package database says about the assistant package.

WHY THIS EXISTS. `intergen --version` and the Version line of `intergen status`
printed "0.1.0" — the version literal in `intergen/__init__.py` — and stopped
there. That string has not moved in the life of the project, while the package
that places the code is on its 296th release, so the two commands named a
version that cannot tell two builds apart. Every other component on the machine
is identified the way the package manager identifies it, version and release
together ("0.1.0-296"), and the release is the half that actually changes.

The release is a PACKAGING fact. The running code cannot know it — it is
assigned when the package is built, not when the source is written — so it is
read from the record the package manager keeps, and from nowhere else. A
literal typed into this tree would be a second copy that drifts the first time
a release is bumped, which is exactly the failure the existing `--version` test
was written to prevent.

THREE ANSWERS, NEVER TWO. A read either names the release, or establishes that
this machine holds no record of the package, or establishes nothing at all.
The third is not the second: a database that cannot be read says nothing about
whether the package is installed. The states are kept apart here and carried
all the way to what a person reads, because a command that says "no record
exists" when it simply could not look has told them something untrue.

HOW IT IS READ. Two ways, in this order, with a decline at the end:

  1. An ordinary read-only connection, bounded by a busy timeout. This is the
     database's own consistency mechanism: it sees a row committed a moment
     ago the same way the package manager sees it, and a writer holding the
     database busy makes the read fail rather than answer from underneath.
     It is the only kind of read whose answer is authoritative, so it is
     always tried first.
  2. An immutable read, and ONLY for a database that has no write-ahead log
     with bytes in it, whose identity and size are re-checked afterwards and
     must not have moved. On an installed machine the package database is
     owned by root inside a root-owned directory, and reading a write-ahead
     database the ordinary way needs a shared-memory file this user cannot
     create there: measured on an installed machine on 2026-09-22, every
     statement on an ordinary read-only connection failed with "attempt to
     write a readonly database" while an immutable read answered correctly in
     under a millisecond. Without this fallback the release would be reported
     as unknown on every such machine, for every user, which is the whole of
     what this module exists to report.

WHY THE FALLBACK IS SAFE WHERE IT IS USED, AND WHY IT IS NARROW. An immutable
read ignores the write-ahead log, so where a log holds committed rows it cannot
see, it answers with the row UNDERNEATH them — a positive answer with nothing
on it to say the answer is old. That is the one thing this module must never
do, so the fallback is refused whenever such a log exists, and the database's
identity, size and modification time, and the log's presence and size, are read
before and after and must be unchanged. A database that moves under the read is
declined, not answered.

The sidecar names are derived from the RESOLVED path, after symbolic links are
followed, so an alias pointing at a database elsewhere is checked where the
database really is rather than beside the alias.

WHAT IS NEVER DONE. Nothing here writes to the database, raises an
authorization dialog, or starts a daemon. An ordinary read-only connection to a
write-ahead database does create the shared-memory sidecar where the filesystem
permits it — that is SQLite's own mechanism for reading such a database, and it
leaves the database itself untouched.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: The package database, as the package manager itself locates it.
PKM_DB = Path(os.environ.get("IGOS_PKM_DB", "/var/lib/igos/pkm.db"))

#: The name this package is recorded under.
PACKAGE_NAME = "intergen"

#: A live row was read: the release is known.
READ_RECORD = "record"
#: The database was read and holds no live row for this package.
READ_NO_RECORD = "no_record"
#: Nothing was established — the database is absent, or could not be read.
READ_UNREADABLE = "unreadable"

#: How long a read waits on a busy database before giving up and declining.
#: Bounded on purpose: printing a version must never hang on a package
#: operation, and an answer that is late is not better than an honest decline.
_BUSY_TIMEOUT_MS = 2000

_QUERY = ("SELECT version, release FROM installed "
          "WHERE name = ? AND superseded_by IS NULL")


def _fingerprint(path: Path, wal: Path) -> tuple | None:
    """What the database and its write-ahead log look like right now.

    Read before and after an immutable read: if any of it has moved, the read
    happened across a change and its answer is not trusted.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    try:
        wal_size = wal.stat().st_size
    except FileNotFoundError:
        wal_size = -1
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, wal_size)


def _select(uri: str):
    """Run the one query on a connection opened at `uri`.

    Returns the row, or None when the database holds no live row. Raises
    sqlite3.Error when the read could not be made at all — the caller decides
    what to do about that, and the two outcomes are never mixed up.
    """
    con = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000)
    try:
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return con.execute(_QUERY, (PACKAGE_NAME,)).fetchone()
    finally:
        con.close()


def read_record(db_path=None):
    """``(state, record)`` for the installed assistant package.

    ``state`` is one of :data:`READ_RECORD`, :data:`READ_NO_RECORD` or
    :data:`READ_UNREADABLE`. ``record`` is ``(version, release)`` with a live
    row and ``None`` otherwise; ``release`` is an ``int``. Nothing here raises:
    printing a version must not be able to fail over a database read.
    """
    try:
        path = Path(db_path) if db_path else PKM_DB
        path = path.resolve()
    except OSError:
        return READ_UNREADABLE, None
    try:
        path.stat()
    except FileNotFoundError:
        # Nothing at that path. A machine with no package database — a
        # checkout, a container — holds no record of this package, and that
        # is something established rather than something unknown.
        return READ_NO_RECORD, None
    except OSError:
        # Something is in the way: a directory this user cannot traverse, a
        # mount that is not there, a name too long for the filesystem. The
        # database may well hold a record; this process cannot look. Asking
        # whether the path was a regular file, as this did until 2026-09-22,
        # answered no to BOTH cases alike, so a machine whose record was out of
        # reach was told it had no record at all. The two are told apart here.
        return READ_UNREADABLE, None

    try:
        uri = path.as_uri()
    except ValueError:
        return READ_UNREADABLE, None

    # 1. The authoritative read.
    try:
        return _parse(_select(f"{uri}?mode=ro"))
    except sqlite3.Error:
        pass

    # 2. The narrow fallback: no write-ahead log with bytes in it, and nothing
    #    about the database allowed to move while it is read.
    wal = path.with_name(path.name + "-wal")
    before = _fingerprint(path, wal)
    if before is None or before[-1] > 0:
        return READ_UNREADABLE, None
    try:
        row = _select(f"{uri}?mode=ro&immutable=1")
    except sqlite3.Error:
        return READ_UNREADABLE, None
    if _fingerprint(path, wal) != before:
        # The database changed underneath an immutable read, which is the one
        # condition in which that read may answer with an old row.
        return READ_UNREADABLE, None
    return _parse(row)


def _parse(row):
    """Turn a queried row into ``(state, record)``."""
    if row is None:
        return READ_NO_RECORD, None
    version, release = row
    if not version:
        return READ_NO_RECORD, None
    try:
        return READ_RECORD, (str(version), int(release))
    except (TypeError, ValueError):
        # A row whose release is not an integer cannot say which release this
        # is. The row exists, so this is not "no record" — it is a record this
        # code cannot read.
        return READ_UNREADABLE, None


def installed_identity(db_path=None):
    """``(version, release)`` for the installed assistant package, or ``None``.

    ``None`` means the release was not established — no record, or no readable
    one — and never means "release zero" or "not installed" as a positive
    claim. :func:`read_record` tells those two apart for callers that must say
    which happened.
    """
    _state, record = read_record(db_path)
    return record


def version_status(db_path=None):
    """``(text, release_known, state)`` — what to print and what is behind it.

    With a record: ``("0.1.0-296", True, READ_RECORD)``, the package manager's
    own form. Without one: the version the running code carries, ``False``, and
    the state that says whether this machine holds no record or the record
    could not be read.
    """
    state, record = read_record(db_path)
    if record is None:
        import intergen
        return intergen.__version__, False, state
    version, release = record
    return f"{version}-{release}", True, state


def version_line(db_path=None):
    """The identity to print, and whether the release in it is known.

    The two-value form of :func:`version_status`, for callers that show the
    same sentence however the read failed.
    """
    text, release_known, _state = version_status(db_path)
    return text, release_known


def identity(db_path=None):
    """``(text, release_known)`` — the installed version and release as one
    string, and whether the release in it was actually read.

    The status surfaces use this: a machine with the daemon down and the same
    machine with it running must answer "which InterGen is this" identically,
    so they read one function rather than each formatting their own. The second
    value travels with the text because a status line that prints the bare
    running version with nothing saying the release is unknown reads exactly
    like a status line that knows the release.
    """
    text, release_known, _state = version_status(db_path)
    return text, release_known
