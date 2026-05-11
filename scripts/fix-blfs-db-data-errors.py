#!/usr/bin/env python3
"""Apply BLFS dep-db data error fixes.

Surfaced during Mode 3 cascade trace 2026-05-11. The BLFS dep db at
build/blfs-packages.db has three classes of importer-introduced errors
that create false build-cycles:

1. Self-loops: a package marked as requiring itself. Upstream BLFS never
   does this; it's an importer bug. 11 such entries deleted.

2. Runtime-only recommended deps: BLFS marks some recommended deps with
   "runtime" in the note, meaning recommended at program runtime not at
   build time. The importer captured these as build-time recommended,
   creating false build cycles. 24 such entries downgraded to optional
   so they don't gate the build graph; runtime functionality is
   preserved because the dep is still discoverable at execution time.

3. Spurious hatch-fancy-pypi-readme <-> hatch-vcs required edges. Per
   upstream BLFS HTML (verified 2026-05-11 against BLFS 13.0 systemd
   edition), neither hatch package requires the other; both depend on
   hatchling (hatch-vcs additionally requires setuptools_scm). The
   importer captured a false required edge in each direction, creating
   a 2-pkg SCC that blocked Mode 3 topological ordering. 2 such edges
   deleted.

   Broader pattern observation (NOT auto-fixed): the same area of the
   db has many other Python-module rows showing apparently-reversed
   src/dst directionality (e.g. "Hatchling -required-> hatch-fancy"
   instead of the BLFS truth "hatch-fancy -required-> hatchling"). The
   suspected importer bug is parser-section-boundary-related but the
   scope of any broader correction belongs to a separate, owner-
   approved investigation — this script only touches the specific
   SCC-causing rows enumerated above.

Idempotent: re-running won't double-delete or double-downgrade. Safe to
run after a fresh db import to re-apply the fixes.

Usage: python3 scripts/fix-blfs-db-data-errors.py

If the BLFS importer is fixed upstream, this script can be retired.
Until then, run it after every fresh import.
"""
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
DB_PATH = REPO / "build" / "blfs-packages.db"

if not DB_PATH.exists():
    print(f"ERROR: {DB_PATH} not found. Run the BLFS importer first.")
    sys.exit(1)

db = sqlite3.connect(str(DB_PATH))
db.row_factory = sqlite3.Row

print("=== BLFS db data fixes — idempotent runner ===")

# Fix 1: delete self-loops
self_loops = list(db.execute("""
    SELECT d.id, p.name, p.anchor_id, d.dep_type
    FROM dependencies d JOIN packages p ON d.package_id = p.id
    WHERE d.dep_anchor = p.anchor_id
"""))
print(f"\nSelf-loops to delete: {len(self_loops)}")
for r in self_loops:
    print(f"  - {r['name']} requires itself (anchor={r['anchor_id']}, type={r['dep_type']})")

# Fix 2: downgrade runtime-only recommended → optional
# Only target rows that haven't already been downgraded (idempotency)
runtime_only = list(db.execute("""
    SELECT d.id, p.name AS src, d.dep_name, d.note
    FROM dependencies d JOIN packages p ON d.package_id = p.id
    WHERE d.dep_type = 'recommended'
      AND d.note LIKE '%runtime%'
      AND COALESCE(d.note, '') NOT LIKE '%downgraded from recommended/runtime%'
"""))
print(f"\nRuntime-only recommended deps to downgrade (recommended → optional): {len(runtime_only)}")
for r in runtime_only:
    note_preview = (r["note"] or "")[:60]
    print(f"  - {r['src']} -[recommended/runtime]-> {r['dep_name']}: {note_preview}")

# Fix 3: delete spurious hatch <-> hatch required edges (verified
# 2026-05-11 against upstream BLFS HTML; neither hatch package requires
# the other per BLFS — they each depend on hatchling).
hatch_pair = list(db.execute("""
    SELECT d.id, p.name AS src, d.dep_anchor, d.dep_type
    FROM dependencies d JOIN packages p ON d.package_id = p.id
    WHERE d.dep_type = 'required'
      AND ((p.name = 'Hatch-Fancy-Pypi-Readme' AND d.dep_anchor = 'hatch-vcs')
        OR (p.name = 'Hatch_vcs' AND d.dep_anchor = 'hatch-fancy-pypi-readme'))
"""))
print(f"\nSpurious hatch<->hatch required edges to delete: {len(hatch_pair)}")
for r in hatch_pair:
    print(f"  - {r['src']} -[required]-> {r['dep_anchor']}")

if not self_loops and not runtime_only and not hatch_pair:
    print("\nNo data fixes to apply — db is clean.")
    sys.exit(0)

# Apply
db.execute("BEGIN")
for r in self_loops:
    db.execute("DELETE FROM dependencies WHERE id = ?", (r["id"],))
for r in runtime_only:
    db.execute(
        "UPDATE dependencies SET dep_type = 'optional', "
        "note = COALESCE(note, '') || ' [downgraded from recommended/runtime by fix-blfs-db-data-errors.py]' "
        "WHERE id = ?",
        (r["id"],))
for r in hatch_pair:
    db.execute("DELETE FROM dependencies WHERE id = ?", (r["id"],))
db.commit()

print(f"\nApplied: {len(self_loops)} self-loops deleted, "
      f"{len(runtime_only)} runtime-recommended downgraded, "
      f"{len(hatch_pair)} spurious hatch edges deleted.")
