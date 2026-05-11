#!/usr/bin/env python3
"""Apply BLFS dep-db data error fixes.

Surfaced during Mode 3 cascade trace 2026-05-11. The BLFS dep db at
build/blfs-packages.db has two classes of importer-introduced errors
that create false build-cycles:

1. Self-loops: a package marked as requiring itself. Upstream BLFS never
   does this; it's an importer bug. 11 such entries deleted.

2. Runtime-only recommended deps: BLFS marks some recommended deps with
   "runtime" in the note, meaning recommended at program runtime not at
   build time. The importer captured these as build-time recommended,
   creating false build cycles. 24 such entries downgraded to optional
   so they don't gate the build graph; runtime functionality is
   preserved because the dep is still discoverable at execution time.

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

if not self_loops and not runtime_only:
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
db.commit()

print(f"\nApplied: {len(self_loops)} self-loops deleted, {len(runtime_only)} runtime-recommended downgraded.")
