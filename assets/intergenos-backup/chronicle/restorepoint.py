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
