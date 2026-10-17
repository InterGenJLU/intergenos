# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Restore-point layer — CAS of exactly a transaction's footprint (spec §2.3,
§2.4).

A pre-transaction restore point does NOT copy the whole system. pkm knows the
file set a transaction will touch and hands it to the engine through the
pre-transaction hook (pkm/pretxn.py): the outgoing package's currently-installed
files plus pkm.db. This layer captures the CURRENT bytes of exactly those paths
into the CAS, tagged with the transaction reason. It is small, fast, and
sufficient to reverse the transaction — pkm's own filesystem deploy is not
transactional and only its database side rolls back (spec §2.4), so this is what
makes a package transaction reversible at the filesystem level.

Paths in the footprint that do NOT exist at capture (a fresh install's not-yet-
created files) are recorded as absent, so a reversal knows to delete them.
"""

from . import manifest as _manifest
from . import paths as _paths

#: The package database every restore point carries: pkm's own footprint names
#: it as db_path (pkm/pretxn.py, PKM_DB_RELPATH under the system's root).
PKM_DB_PATH = "/var/lib/igos/pkm.db"


def on_demand_footprint(paths, reason, db_path=None):
    """The footprint of a restore point taken with no transaction pending.

    Restore points are taken before every pkm transaction and on demand (spec
    §10). A transaction hands its footprint over; an on-demand capture —
    `chronicle capture restore-point [PATH...]`, and the installer's restore
    point of the system as installed — has none, and until 2026-10-17 the
    engine refused it ("requires a footprint dict as scope"), so neither ever
    produced a restore point. Its footprint is the paths named, if any, plus
    the package database: the same shape pkm hands over for a fresh install,
    whose reversal is the database diff.

    Raises ValueError for anything but a list of absolute paths, so a
    malformed request is refused instead of capturing something else.
    """
    named = [] if paths is None else paths
    if not isinstance(named, (list, tuple)) or not all(
            isinstance(p, str) and p.startswith("/") for p in named):
        raise ValueError(
            "an on-demand restore point takes a list of absolute paths")
    return {
        "verb": "on-demand",
        "reason": reason or "on-demand restore point",
        "packages": [],
        "paths": sorted(set(named)),
        "db_path": db_path or PKM_DB_PATH,
    }


def capture_from_footprint(footprint, store_root, store, sequence, wall_clock):
    """Capture a restore point from a pkm pre-transaction footprint.

    Args:
        footprint: the dict pkm's pretxn hook produces — keys verb, reason,
            packages, paths (absolute outgoing paths), db_path.
        store_root: the store root to commit under (local-first, spec §10).
        store: a ContentStore at store_root.
        sequence, wall_clock: version identity.

    Returns:
        the committed version_id.
    """
    paths = list(footprint.get("paths", []))
    db_path = footprint.get("db_path")
    if db_path and db_path not in paths:
        paths.append(db_path)

    entries = []
    absent = []
    for p in paths:
        e = _manifest.capture_entry(p, p, store)
        if e is None:
            absent.append(p)
        else:
            entries.append(e)

    reason = footprint.get("reason") or "pre-transaction restore point"
    m = _manifest.build_manifest(
        _paths.LAYER_RESTORE_POINT, sequence, wall_clock, reason, entries
    )
    # Transaction metadata rides alongside the entries. It does not enter the
    # root hash (which covers file integrity); it records what the point is for
    # and which paths were absent (added by the transaction, so a reversal
    # deletes them rather than restoring bytes).
    m["restore_point"] = {
        "verb": footprint.get("verb"),
        "packages": footprint.get("packages", []),
        "db_path": db_path,
        "absent_paths": absent,
    }
    _manifest.commit_manifest(store_root, m)
    return m["version_id"]
