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

WHAT IS READ, AND WITH WHAT RIGHTS. The package database at /var/lib/igos/pkm.db
(overridden by IGOS_PKM_DB, the same variable the package manager itself reads)
is opened READ-ONLY and immutable, so nothing here can write to it and a write
in flight elsewhere cannot make this read fail. The file is world-readable on an
installed machine, so this needs no privilege, raises no authorization dialog
and starts no daemon — the same rule `intergen --version` already follows.

WHEN A WRITE IS IN FLIGHT. An immutable open does not read the database's
write-ahead log, so a row committed by a package operation that has not yet
checkpointed is not visible here — and what IS visible is the row before it.
A read that cannot see the newest committed row is not a read that may answer
with the one underneath it, so a non-empty write-ahead log is treated as the
record being unreadable for the moment and answered with None.

WHAT HAPPENS WHEN THERE IS NO RECORD. A checkout, a container, a machine where
the package was never installed: there is no record, and this returns None. The
callers then name the version the running code carries and say plainly that the
release is unknown. They never invent one, and they never quietly print a
version that leaves the reader thinking they have been told the release.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: The package database, as the package manager itself locates it.
PKM_DB = Path(os.environ.get("IGOS_PKM_DB", "/var/lib/igos/pkm.db"))

#: The name this package is recorded under.
PACKAGE_NAME = "intergen"


def installed_identity(db_path=None):
    """``(version, release)`` for the installed assistant package, or ``None``.

    ``release`` is an ``int``. ``None`` means the question could not be
    answered — the database is absent, unreadable, or holds no live row for
    this package — and never means "release zero" or "not installed" as a
    positive claim. Any failure returns ``None``; nothing here raises, because
    printing a version must not be able to fail over a database read.
    """
    path = Path(db_path) if db_path else PKM_DB
    if not path.is_file():
        return None
    # A COMMITTED ROW CAN STILL BE INVISIBLE TO THIS READ. The open below is
    # immutable, which is what lets it read a root-owned file with no
    # privilege and without a writer being able to make it fail — and an
    # immutable open deliberately ignores the write-ahead log. So while a
    # newer row sits in that log, this read returns the row BEFORE it, as a
    # positive answer with nothing on it saying the answer is old. Measured
    # 2026-09-22: with release 298 committed and its writer still connected,
    # this reader answered 297 while a plain read-only reader answered 298.
    # A log file with bytes in it therefore means the record cannot be read
    # right now, which is the answer this module already has a word for.
    wal = path.with_name(path.name + "-wal")
    try:
        if wal.is_file() and wal.stat().st_size > 0:
            return None
    except OSError:
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            row = con.execute(
                "SELECT version, release FROM installed "
                "WHERE name = ? AND superseded_by IS NULL",
                (PACKAGE_NAME,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    version, release = row
    if not version:
        return None
    try:
        return str(version), int(release)
    except (TypeError, ValueError):
        # A row whose release is not an integer is a record that cannot say
        # which release this is, which is the same answer as having no record.
        return None


def version_line(db_path=None):
    """The identity to print, and whether the release in it is known.

    Returns ``(text, release_known)``. With a record: ``("0.1.0-296", True)``,
    the package manager's own form. Without one: the version the running code
    carries and ``False``, so a caller can add the sentence that says the
    release could not be read.
    """
    record = installed_identity(db_path)
    if record is None:
        import intergen
        return intergen.__version__, False
    version, release = record
    return f"{version}-{release}", True


def identity(db_path=None):
    """The installed version and release as one string, or the running
    version alone when there is no record.

    The status surfaces use this: a machine with the daemon down and the same
    machine with it running must answer "which InterGen is this" identically,
    so they read one function rather than each formatting their own.
    """
    text, _release_known = version_line(db_path)
    return text
