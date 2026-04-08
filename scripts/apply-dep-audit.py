#!/usr/bin/env python3
"""Apply dependency audit results to package.yml files.

Reads the BLFS database and our package templates, computes missing
dependencies per the InterGenOS dependency policy, and updates
package.yml files with the missing deps.

Policy (decided 2026-04-08):
- Required + Recommended (BLFS): always add if dep is in our tree
- Optional functional: add if dep is in our tree ("if you have it, use it")
- Optional docs/tests only: skip (Doxygen, texlive, gtk-doc, etc.)
- Python module page false positives: filter out

Usage:
    python3 scripts/apply-dep-audit.py [--dry-run] [--tier desktop]
"""

import argparse
import importlib
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "build" / "blfs-packages.db"

# Doc/test-only deps to skip
DOC_TEST_ONLY_ANCHORS = {
    'doxygen', 'texlive', 'gtk-doc', 'gi-docgen', 'lcov', 'valgrind',
    'python-sphinx', 'sphinx', 'graphviz', 'xmlto', 'asciidoc',
    'hotdoc', 'pandoc', 'python2',
}

# False positive deps from shared BLFS Python Modules page.
# These appear as required/recommended for every Python module because
# the parser picks up page-level deps from the shared section header.
PYTHON_PAGE_FALSE_POSITIVES = {
    'pygobject3', 'at-spi2-core',
    'docbook-xsl', 'libxslt', 'lynx',  # Also from shared page
    'hatch-fancy-pypi-readme', 'hatch-vcs',  # Build backend deps, page-level
}

# Build styles that indicate pure Python packages
PYTHON_BUILD_STYLES = {'python-pep517', 'python-module', 'python3-module'}

# Packages known to be pure Python regardless of build_style
PURE_PYTHON_PACKAGES = {
    'Mako', 'cython', 'docutils', 'editables', 'markdown', 'pathspec',
    'setuptools-scm', 'trove-classifiers', 'pycairo', 'pygments',
    'pygobject3', 'pluggy', 'hatchling', 'hatch-fancy-pypi-readme',
    'hatch-vcs',
}


def resolve_blfs_anchor(conn, igos_name):
    """Find BLFS package ID for an InterGenOS package name."""
    row = conn.execute(
        "SELECT id, name, version FROM packages WHERE anchor_id = ?",
        (igos_name,)
    ).fetchone()
    if row:
        return row

    alias = conn.execute(
        "SELECT blfs_anchor FROM aliases WHERE igos_name = ?",
        (igos_name,)
    ).fetchone()
    if alias:
        row = conn.execute(
            "SELECT id, name, version FROM packages WHERE anchor_id = ?",
            (alias[0],)
        ).fetchone()
        if row:
            return row
    return None


def resolve_dep_igos_name(conn, dep_anchor):
    """Resolve a BLFS dep anchor to (igos_package_name, tier).

    Returns our package name — not the BLFS anchor — so deps are
    declared using names that match our package.yml 'name:' fields.
    """
    # Check if we have a reverse alias (BLFS anchor → our name)
    alias = conn.execute(
        "SELECT igos_name FROM aliases WHERE blfs_anchor = ?",
        (dep_anchor,)
    ).fetchone()
    if alias:
        igos = conn.execute(
            "SELECT blfs_anchor, tier FROM igos_status WHERE blfs_anchor = ?",
            (alias[0],)
        ).fetchone()
        if igos:
            return (alias[0], igos[1])  # Return our name, not BLFS anchor

    # Direct match (anchor == our package name)
    igos = conn.execute(
        "SELECT blfs_anchor, tier FROM igos_status WHERE blfs_anchor = ?",
        (dep_anchor,)
    ).fetchone()
    if igos:
        return igos

    return None


def compute_missing_deps(conn, pkg, parser_mod):
    """Compute missing deps for a package based on BLFS + policy."""
    blfs = resolve_blfs_anchor(conn, pkg.name)
    if not blfs:
        return []

    blfs_id = blfs[0]
    our_deps = set(pkg.dependencies.build)

    blfs_deps = conn.execute(
        """SELECT dep_anchor, dep_name, dep_version, dep_type, note
           FROM dependencies WHERE package_id = ?""",
        (blfs_id,)
    ).fetchall()

    missing = []
    for dep_anchor, dep_name, dep_version, dep_type, note in blfs_deps:
        resolved = resolve_dep_igos_name(conn, dep_anchor)
        if not resolved:
            continue

        dep_igos_name, dep_tier = resolved

        # Already declared?
        if dep_igos_name in our_deps or dep_anchor in our_deps:
            continue

        # Skip doc/test-only deps
        if dep_anchor.lower() in DOC_TEST_ONLY_ANCHORS:
            continue

        # Skip optional deps with doc/test notes
        if dep_type == 'optional' and note:
            note_lower = note.lower()
            if any(kw in note_lower for kw in (
                'documentation', 'api docs', 'man pages', 'generating docs',
                'tests', 'test suite', 'testing', 'building documentation',
            )):
                continue

        # Filter Python module page false positives
        is_python_pkg = (pkg.build_style in PYTHON_BUILD_STYLES
                         or pkg.name in PURE_PYTHON_PACKAGES)
        if is_python_pkg and dep_igos_name in PYTHON_PAGE_FALSE_POSITIVES:
            continue

        # Never add self as a dependency
        if dep_igos_name == pkg.name:
            continue

        missing.append(dep_igos_name)

    return sorted(set(missing))


def update_package_yml(yml_path, new_deps, dry_run=False):
    """Add missing deps to the build: list in a package.yml file.

    Handles both formats:
      build: []           -> build:\n- dep1\n- dep2
      build:\n- existing  -> build:\n- existing\n- dep1\n- dep2

    Strategy: find the build section boundaries, extract existing deps,
    merge with new deps, and replace the entire build section.
    """
    text = yml_path.read_text()
    lines = text.split('\n')

    # Find the "  build:" line index
    build_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('build:') and i > 0:
            # Verify we're inside the dependencies block
            # (check that a prior line is 'dependencies:' or another dep key)
            for j in range(i - 1, max(i - 5, -1), -1):
                if lines[j].strip() == 'dependencies:':
                    build_idx = i
                    break
                if lines[j].strip() in ('host:', 'runtime:', 'build:'):
                    build_idx = i
                    break
            if build_idx is not None:
                break

    if build_idx is None:
        return False, "no build: line found in dependencies"

    build_line = lines[build_idx]

    # Determine current deps and detect indentation style
    existing_deps = []
    section_end = build_idx + 1  # line after "  build:"
    indent = '  '  # default: 2-space (same level as key)

    if '[]' in build_line:
        # Empty: "  build: []"
        pass
    else:
        # Has deps: walk subsequent lines that are list items
        for i in range(build_idx + 1, len(lines)):
            line = lines[i]
            # Match both "  - dep" (2-space) and "    - dep" (4-space)
            dep_match = re.match(r'^(\s+)- (.+)$', line)
            if dep_match:
                indent = dep_match.group(1)  # capture actual indent
                existing_deps.append(dep_match.group(2).strip())
                section_end = i + 1
            elif line.strip() == '':
                # Skip blank lines within the dep list
                section_end = i + 1
            else:
                break

    # Merge: existing deps + new deps (preserve order, no duplicates)
    merged = list(existing_deps)
    for dep in new_deps:
        if dep not in merged:
            merged.append(dep)

    # Rebuild the build section using detected indent style
    new_build_lines = ['  build:']
    for dep in merged:
        new_build_lines.append(f'{indent}- {dep}')

    # Replace lines[build_idx:section_end] with new_build_lines
    new_lines = lines[:build_idx] + new_build_lines + lines[section_end:]
    new_text = '\n'.join(new_lines)

    if not dry_run:
        yml_path.write_text(new_text)
    return True, new_text


def main():
    ap = argparse.ArgumentParser(description="Apply dep audit to package.yml files")
    ap.add_argument('--dry-run', action='store_true', help="Show changes without writing")
    ap.add_argument('--tier', help="Filter by tier")
    ap.add_argument('--packages-dir', default=str(PROJECT_ROOT / 'packages'))
    ap.add_argument('--db', default=str(DB_PATH))
    args = ap.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))
    parser_mod = importlib.import_module('igos-build.parser')

    conn = sqlite3.connect(args.db)
    dag_tiers = {'desktop', 'base', 'extra'}

    total_packages = 0
    total_updated = 0
    total_deps_added = 0
    changes = []

    for tier in sorted(dag_tiers):
        if args.tier and tier != args.tier:
            continue
        tier_dir = Path(args.packages_dir) / tier
        if not tier_dir.exists():
            continue

        templates = parser_mod.discover_templates(tier_dir)
        for t in templates:
            try:
                pkg = parser_mod.parse_template(t)
            except Exception as e:
                print(f"  WARNING: Failed to parse {t}: {e}", file=sys.stderr)
                continue

            # Skip pass packages
            if pkg.pass_number and pkg.pass_number > 1:
                continue
            if '-pass' in pkg.name:
                continue

            total_packages += 1
            missing = compute_missing_deps(conn, pkg, parser_mod)
            if not missing:
                continue

            yml_path = t
            ok, result = update_package_yml(yml_path, missing, dry_run=args.dry_run)
            if ok:
                total_updated += 1
                total_deps_added += len(missing)
                changes.append((pkg.name, tier, missing))
                action = "would add" if args.dry_run else "added"
                print(f"  [{tier:7s}] {pkg.name:30s} {action} {len(missing)} deps: {', '.join(missing)}")

    conn.close()

    print()
    print(f"{'DRY RUN — ' if args.dry_run else ''}Summary:")
    print(f"  Packages scanned:  {total_packages}")
    print(f"  Packages updated:  {total_updated}")
    print(f"  Total deps added:  {total_deps_added}")


if __name__ == '__main__':
    main()
