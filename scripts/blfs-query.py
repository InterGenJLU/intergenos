#!/usr/bin/env python3
"""Query the BLFS package database.

Usage:
    python3 scripts/blfs-query.py info <package>
    python3 scripts/blfs-query.py deps <package> [--recursive] [--type required|recommended|optional]
    python3 scripts/blfs-query.py gaps <package>
    python3 scripts/blfs-query.py chain-cost <package>
    python3 scripts/blfs-query.py patches [--required]
    python3 scripts/blfs-query.py tests [--with-commands] [--no-tests]
    python3 scripts/blfs-query.py versions [--diff]
    python3 scripts/blfs-query.py search <term>
    python3 scripts/blfs-query.py stats
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DB_DEFAULT = Path(__file__).parent.parent / "build" / "blfs-packages.db"


def get_conn(db_path):
    if not Path(db_path).exists():
        print(f"ERROR: Database not found at {db_path}")
        print(f"  Run: python3 scripts/parse-blfs-book.py")
        sys.exit(1)
    return sqlite3.connect(db_path)


def cmd_info(conn, pkg_name):
    """Show full package information."""
    row = conn.execute(
        "SELECT * FROM packages WHERE anchor_id = ? OR name LIKE ?",
        (pkg_name, f"%{pkg_name}%")
    ).fetchone()
    if not row:
        print(f"Package '{pkg_name}' not found")
        return

    cols = [d[0] for d in conn.execute("SELECT * FROM packages LIMIT 0").description]
    pkg = dict(zip(cols, row))
    pkg_id = pkg['id']

    print(f"{'=' * 60}")
    print(f"  {pkg['name']} {pkg['version']}")
    print(f"{'=' * 60}")
    print(f"  Anchor:    {pkg['anchor_id']}")
    print(f"  Section:   {pkg['section'][:60] if pkg['section'] else 'N/A'}")
    print(f"  URL:       {pkg['download_url'] or 'N/A'}")
    print(f"  MD5:       {pkg['md5'] or 'N/A'}")
    print(f"  Size:      {pkg['download_size'] or 'N/A'}")
    print(f"  Disk:      {pkg['disk_space'] or 'N/A'}")
    print(f"  Build:     {pkg['build_time_sbu'] or 'N/A'}")
    if pkg['description']:
        print(f"  Desc:      {pkg['description'][:80]}")

    # Dependencies
    deps = conn.execute(
        "SELECT dep_name, dep_version, dep_type, note FROM dependencies WHERE package_id = ? ORDER BY dep_type, dep_name",
        (pkg_id,)
    ).fetchall()
    if deps:
        print(f"\n  Dependencies ({len(deps)}):")
        for d in deps:
            note = f" ({d[3]})" if d[3] else ""
            print(f"    [{d[2]:11s}] {d[0]} {d[1]}{note}")

    # Patches
    patches = conn.execute(
        "SELECT filename, url FROM patches WHERE package_id = ?", (pkg_id,)
    ).fetchall()
    if patches:
        print(f"\n  Patches ({len(patches)}):")
        for p in patches:
            print(f"    {p[0]}")

    # Tests
    test = conn.execute(
        "SELECT command, notes, has_tests FROM tests WHERE package_id = ?", (pkg_id,)
    ).fetchone()
    if test:
        if test[2]:
            print(f"\n  Tests: {test[0] or 'see notes'}")
            if test[1]:
                print(f"    {test[1][:100]}")
        else:
            print(f"\n  Tests: No test suite")

    # IGOS status
    igos = conn.execute(
        "SELECT tier, our_version, status FROM igos_status WHERE blfs_anchor = ?",
        (pkg['anchor_id'],)
    ).fetchone()
    if igos:
        print(f"\n  InterGenOS: tier={igos[0]}, version={igos[1]}, status={igos[2]}")
    else:
        print(f"\n  InterGenOS: not in our tree")

    print()


def cmd_deps(conn, pkg_name, recursive=False, dep_type=None):
    """Show dependencies for a package."""
    row = conn.execute(
        "SELECT id, name, version FROM packages WHERE anchor_id = ?", (pkg_name,)
    ).fetchone()
    if not row:
        print(f"Package '{pkg_name}' not found")
        return

    pkg_id, name, version = row
    print(f"Dependencies for {name} {version}:")

    if not recursive:
        query = "SELECT dep_anchor, dep_name, dep_version, dep_type, note FROM dependencies WHERE package_id = ?"
        params = [pkg_id]
        if dep_type:
            query += " AND dep_type = ?"
            params.append(dep_type)
        query += " ORDER BY dep_type, dep_name"

        for d in conn.execute(query, params):
            note = f" ({d[4]})" if d[4] else ""
            igos = conn.execute(
                "SELECT tier FROM igos_status WHERE blfs_anchor = ?", (d[0],)
            ).fetchone()
            status = f" [IGOS: {igos[0]}]" if igos else " [NOT IN TREE]"
            print(f"  [{d[3]:11s}] {d[1]} {d[2]}{note}{status}")
    else:
        # Recursive dependency walk
        seen = set()
        _walk_deps(conn, pkg_id, seen, 0, dep_type)


def _walk_deps(conn, pkg_id, seen, depth, dep_type_filter):
    query = "SELECT dep_anchor, dep_name, dep_version, dep_type FROM dependencies WHERE package_id = ?"
    params = [pkg_id]
    if dep_type_filter:
        query += " AND dep_type = ?"
        params.append(dep_type_filter)

    for d in conn.execute(query, params):
        anchor, name, ver, dtype = d
        if anchor in seen:
            continue
        seen.add(anchor)
        indent = "  " * (depth + 1)
        igos = conn.execute(
            "SELECT tier FROM igos_status WHERE blfs_anchor = ?", (anchor,)
        ).fetchone()
        status = f" [{igos[0]}]" if igos else " [MISSING]"
        print(f"{indent}[{dtype}] {name} {ver}{status}")

        # Recurse
        child = conn.execute(
            "SELECT id FROM packages WHERE anchor_id = ?", (anchor,)
        ).fetchone()
        if child:
            _walk_deps(conn, child[0], seen, depth + 1, dep_type_filter)


def cmd_gaps(conn, pkg_name):
    """Show missing dependencies for a package."""
    row = conn.execute(
        "SELECT id, name, version FROM packages WHERE anchor_id = ?", (pkg_name,)
    ).fetchone()
    if not row:
        print(f"Package '{pkg_name}' not found")
        return

    pkg_id, name, version = row
    print(f"Dependency gaps for {name} {version}:")

    deps = conn.execute(
        "SELECT dep_anchor, dep_name, dep_version, dep_type FROM dependencies WHERE package_id = ? ORDER BY dep_type",
        (pkg_id,)
    ).fetchall()

    missing = []
    for d in deps:
        igos = conn.execute(
            "SELECT tier FROM igos_status WHERE blfs_anchor = ?", (d[0],)
        ).fetchone()
        if not igos:
            missing.append(d)

    if missing:
        print(f"  Missing {len(missing)} dependencies:")
        for d in missing:
            print(f"    [{d[3]:11s}] {d[1]} {d[2]}")
    else:
        print(f"  All dependencies satisfied!")


def cmd_chain_cost(conn, pkg_name):
    """Calculate cost of adding a package and all missing deps."""
    row = conn.execute(
        "SELECT id, name, version FROM packages WHERE anchor_id = ?", (pkg_name,)
    ).fetchone()
    if not row:
        print(f"Package '{pkg_name}' not found")
        return

    # Recursive walk, collect all missing
    needed = set()
    _find_missing_recursive(conn, row[0], needed, set())

    if needed:
        print(f"Adding {row[1]} requires {len(needed)} new package(s):")
        for anchor, name, ver in sorted(needed, key=lambda x: x[1]):
            print(f"  {name} {ver}")
    else:
        print(f"No new packages needed — all dependencies already in tree")


def _find_missing_recursive(conn, pkg_id, needed, visited):
    deps = conn.execute(
        "SELECT dep_anchor, dep_name, dep_version FROM dependencies WHERE package_id = ? AND dep_type IN ('required', 'recommended')",
        (pkg_id,)
    ).fetchall()

    for anchor, name, ver in deps:
        if anchor in visited:
            continue
        visited.add(anchor)

        igos = conn.execute(
            "SELECT tier FROM igos_status WHERE blfs_anchor = ?", (anchor,)
        ).fetchone()
        if not igos:
            needed.add((anchor, name, ver))

        child = conn.execute(
            "SELECT id FROM packages WHERE anchor_id = ?", (anchor,)
        ).fetchone()
        if child:
            _find_missing_recursive(conn, child[0], needed, visited)


def cmd_patches(conn, required_only=False):
    """List all packages with patches."""
    query = """SELECT p.name, p.version, pt.filename, pt.url
               FROM patches pt JOIN packages p ON pt.package_id = p.id
               ORDER BY p.name"""
    patches = conn.execute(query).fetchall()
    print(f"Packages with patches ({len(patches)}):")
    for p in patches:
        print(f"  {p[0]} {p[1]}: {p[2]}")


def cmd_tests(conn, with_commands=False, no_tests=False):
    """List test information."""
    if no_tests:
        rows = conn.execute(
            "SELECT p.name, p.version FROM tests t JOIN packages p ON t.package_id = p.id WHERE t.has_tests = 0 ORDER BY p.name"
        ).fetchall()
        print(f"Packages with NO test suite ({len(rows)}):")
        for r in rows:
            print(f"  {r[0]} {r[1]}")
    else:
        rows = conn.execute(
            "SELECT p.name, p.version, t.command, t.notes FROM tests t JOIN packages p ON t.package_id = p.id WHERE t.has_tests = 1 ORDER BY p.name"
        ).fetchall()
        print(f"Packages with test suites ({len(rows)}):")
        for r in rows:
            if with_commands and r[2]:
                print(f"  {r[0]} {r[1]}: {r[2]}")
            else:
                print(f"  {r[0]} {r[1]}")


def cmd_versions(conn, diff_only=False):
    """Compare our versions against BLFS."""
    rows = conn.execute("""
        SELECT p.anchor_id, p.name, p.version, i.our_version, i.tier
        FROM packages p
        JOIN igos_status i ON p.anchor_id = i.blfs_anchor
        WHERE i.our_version IS NOT NULL AND i.our_version != ''
        ORDER BY p.name
    """).fetchall()

    if diff_only:
        rows = [r for r in rows if r[2] != r[3]]

    header = "Version differences" if diff_only else "Version comparison"
    print(f"{header} ({len(rows)} packages):")
    for r in rows:
        match = "=" if r[2] == r[3] else "!"
        print(f"  [{match}] {r[1]:30s} BLFS={r[2]:15s} IGOS={r[3]:15s} ({r[4]})")


def cmd_search(conn, term):
    """Search packages by name or description."""
    rows = conn.execute(
        "SELECT anchor_id, name, version, description FROM packages WHERE name LIKE ? OR description LIKE ? OR anchor_id LIKE ? ORDER BY name",
        (f"%{term}%", f"%{term}%", f"%{term}%")
    ).fetchall()
    print(f"Search results for '{term}' ({len(rows)} matches):")
    for r in rows:
        igos = conn.execute(
            "SELECT tier FROM igos_status WHERE blfs_anchor = ?", (r[0],)
        ).fetchone()
        status = f" [{igos[0]}]" if igos else ""
        desc = f" — {r[3][:50]}" if r[3] else ""
        print(f"  {r[1]} {r[2]}{status}{desc}")


def cmd_stats(conn):
    """Show database statistics."""
    print("BLFS Package Database Statistics")
    print("=" * 40)
    pkgs = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    deps = conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0]
    patches = conn.execute("SELECT COUNT(*) FROM patches").fetchone()[0]
    tests_yes = conn.execute("SELECT COUNT(*) FROM tests WHERE has_tests = 1").fetchone()[0]
    tests_no = conn.execute("SELECT COUNT(*) FROM tests WHERE has_tests = 0").fetchone()[0]
    igos = conn.execute("SELECT COUNT(*) FROM igos_status").fetchone()[0]

    print(f"  Packages:          {pkgs}")
    print(f"  Dependencies:      {deps}")
    print(f"  Patches:           {patches}")
    print(f"  With test suites:  {tests_yes}")
    print(f"  No test suite:     {tests_no}")
    print(f"  IGOS packages:     {igos}")

    print(f"\n  Dependencies by type:")
    for row in conn.execute("SELECT dep_type, COUNT(*) FROM dependencies GROUP BY dep_type ORDER BY dep_type"):
        print(f"    {row[0]:12s} {row[1]}")

    print(f"\n  IGOS by tier:")
    for row in conn.execute("SELECT tier, COUNT(*) FROM igos_status GROUP BY tier ORDER BY COUNT(*) DESC"):
        print(f"    {row[0]:12s} {row[1]}")


def main():
    parser = argparse.ArgumentParser(description="Query the BLFS package database")
    parser.add_argument('--db', default=str(DB_DEFAULT), help="Database path")
    sub = parser.add_subparsers(dest='command')

    p_info = sub.add_parser('info', help='Show package info')
    p_info.add_argument('package')

    p_deps = sub.add_parser('deps', help='Show dependencies')
    p_deps.add_argument('package')
    p_deps.add_argument('--recursive', '-r', action='store_true')
    p_deps.add_argument('--type', choices=['required', 'recommended', 'optional'])

    p_gaps = sub.add_parser('gaps', help='Show missing deps')
    p_gaps.add_argument('package')

    p_cost = sub.add_parser('chain-cost', help='Cost of adding a package')
    p_cost.add_argument('package')

    p_patches = sub.add_parser('patches', help='List patches')
    p_patches.add_argument('--required', action='store_true')

    p_tests = sub.add_parser('tests', help='List test info')
    p_tests.add_argument('--with-commands', action='store_true')
    p_tests.add_argument('--no-tests', action='store_true', help='Show packages WITHOUT tests')

    p_ver = sub.add_parser('versions', help='Compare versions')
    p_ver.add_argument('--diff', action='store_true', help='Only show differences')

    p_search = sub.add_parser('search', help='Search packages')
    p_search.add_argument('term')

    sub.add_parser('stats', help='Database statistics')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    conn = get_conn(args.db)

    if args.command == 'info':
        cmd_info(conn, args.package)
    elif args.command == 'deps':
        cmd_deps(conn, args.package, args.recursive, args.type)
    elif args.command == 'gaps':
        cmd_gaps(conn, args.package)
    elif args.command == 'chain-cost':
        cmd_chain_cost(conn, args.package)
    elif args.command == 'patches':
        cmd_patches(conn, args.required)
    elif args.command == 'tests':
        cmd_tests(conn, args.with_commands, args.no_tests)
    elif args.command == 'versions':
        cmd_versions(conn, args.diff)
    elif args.command == 'search':
        cmd_search(conn, args.term)
    elif args.command == 'stats':
        cmd_stats(conn)

    conn.close()


if __name__ == '__main__':
    main()
