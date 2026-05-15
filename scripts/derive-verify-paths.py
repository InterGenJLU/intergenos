#!/usr/bin/env python3
"""Derive verify_paths: blocks for package.yml from LFS/BLFS book content.

Reads installed_content data from build/blfs-packages.db (populated by
scripts/parse-blfs-book.py against both LFS and BLFS books) and appends
a verify_paths: block to each matching packages/<tier>/<pkg>/package.yml.

The verify_paths field declares 2-3 load-bearing files the package
produces. A pre-squashfs audit checks each path exists on the chroot's
disk; missing paths halt the build. This is the canonical defense
against the linux-firmware-class regression (package recipe present,
package builds, package files silently absent from squashfs).

Behavior:
  - Idempotent: if a package.yml already has verify_paths, skip it.
  - Conservative: 2-3 paths per package, prefers programs (most
    user-visible). Falls back to libraries or directories.
  - Name match is lowercase-equal — book "Bzip2" ↔ packages/core/bzip2.
  - Packages without book data (our first-party packages, alternates,
    pass1 bootstrap variants) get skipped — owner hand-writes those.

Usage:
    python3 scripts/derive-verify-paths.py [--dry-run] [--db PATH] \\
                                           [--packages-dir PATH]
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path


# Match parenthetical aside ("(link to X)", "(deprecated)", ...)
PAREN_RE = re.compile(r'\s*\([^)]*\)')

# A valid filename / library / directory-name: no whitespace, no braces or
# other shell-glob meta, and either starts with a lowercase letter OR has
# at least one structural char (dot/slash/dash/underscore/digit) — that
# rejects sentence-fragment descriptors like "Numerous" or "Several" that
# BLFS sometimes uses in place of explicit file lists.
INVALID_CHARS = set(' \t{}<>()*?[]"\'\\$')


def looks_like_filename(s):
    if not s:
        return False
    if any(c in INVALID_CHARS for c in s):
        return False
    if len(s) < 2:
        return False
    # If it's a path (starts with /), require depth ≥ 3 so we don't accept
    # description-fragment partial paths like "/usr/libexec".
    if s.startswith('/'):
        return s.count('/') >= 3
    # Has structural char → looks like a real filename / lib name
    if any(c in s for c in '._/-0123456789'):
        return True
    # All-lowercase single word → likely a command name (e.g., "bzip2",
    # "rsync", "htop"). Above check already accepts most binaries since
    # most have digits/dashes; this catches the few plain ones.
    if s[0].islower() and s.islower():
        return True
    # PascalCase or single capitalized word without separators → looks
    # like a descriptive noun (Numerous, Several, Many), not a filename.
    return False


def split_items(text):
    """Split a comma-and-listed segbody into clean item names.

    LFS/BLFS write item lists as: "bunzip2 (link to bzip2), bzcat
    (link to bzip2), bzcmp (link to bzdiff), and bzdiff". Strip the
    parenthetical asides, split on commas and the trailing "and", drop
    empties and items that don't look like filenames.
    """
    no_parens = PAREN_RE.sub('', text)
    # Split on commas, semicolons, "and ", "\n"
    parts = re.split(r',|;|\sand\s|\n', no_parens)
    items = []
    for p in parts:
        p = p.strip()
        if not p or p.lower() in ('none', 'no', ''):
            continue
        # Trailing punctuation cleanup
        p = p.rstrip('.,;')
        if looks_like_filename(p):
            items.append(p)
    return items


def derive_paths(pkg_name, content_rows):
    """Pick up to 3 verify_paths for one package from book content rows.

    content_rows: iterable of (content_type, items) tuples where
    content_type is 'programs' | 'libraries' | 'directories' and items
    is the raw segbody text.

    Strategy:
      1. A program matching the package name (or starting with it) —
         most direct identity check.
      2. Another program — at least one binary should land.
      3. A library or directory — covers lib-only packages.
    """
    programs, libraries, directories = [], [], []
    for ct, items in content_rows:
        for item in split_items(items):
            if ct == 'programs':
                programs.append(item)
            elif ct == 'libraries':
                libraries.append(item)
            elif ct == 'directories':
                directories.append(item)

    paths = []
    name_l = pkg_name.lower()

    # Pull a program matching the name first
    name_match = None
    for p in programs:
        pl = p.lower()
        if pl == name_l or pl.startswith(name_l) or name_l.startswith(pl):
            name_match = p
            break
    def _prog_path(p):
        return p if p.startswith('/') else f'/usr/bin/{p}'

    if name_match:
        paths.append(_prog_path(name_match))
        programs = [p for p in programs if p != name_match]

    # Then any remaining program (caps at 2 programs total)
    if programs and len(paths) < 2:
        paths.append(_prog_path(programs[0]))

    # Library — strip ".so" trailing-form so verifier can match either
    # libbz2.so or libbz2.so.* on disk. Keep raw name; the verifier is
    # responsible for glob/prefix matching.
    if libraries and len(paths) < 3:
        lib = libraries[0]
        if not lib.startswith('/'):
            lib = f'/usr/lib/{lib}'
        paths.append(lib)
    elif directories and len(paths) < 3:
        d = directories[0]
        # Require depth >= 3 segments (e.g. /usr/share/X). Bare top-level
        # dirs like /usr/libexec or /etc aren't package-identifying.
        if d.startswith('/') and d.count('/') >= 3:
            paths.append(d)

    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='build/blfs-packages.db')
    ap.add_argument('--packages-dir', default='packages')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print planned edits but do not write files')
    ap.add_argument('--only', help='Process only this package name (debug)')
    ap.add_argument('--skip-tiers', default='toolchain',
                    help='Comma-separated tier names to skip (default: '
                         'toolchain — LFS Ch5-7 tmp tools, discarded after '
                         'bootstrap, never landed in final chroot).')
    args = ap.parse_args()
    skip_tiers = {t.strip() for t in args.skip_tiers.split(',') if t.strip()}

    root = Path(__file__).parent.parent
    db_path = root / args.db
    pkgs_dir = root / args.packages_dir

    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        print("Run scripts/parse-blfs-book.py first.")
        sys.exit(1)

    db = sqlite3.connect(str(db_path))

    # Aggregate content rows keyed by lowercased package name
    name_to_content = {}
    for lname, ct, items in db.execute(
        "SELECT LOWER(p.name), ic.content_type, ic.items "
        "FROM packages p JOIN installed_content ic ON ic.package_id = p.id"
    ):
        name_to_content.setdefault(lname, []).append((ct, items))

    print(f"Loaded book content for {len(name_to_content)} package names")

    updated = 0
    no_data = 0
    already = 0
    dry_planned = 0

    for tier_dir in sorted(pkgs_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        if tier_dir.name in skip_tiers:
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / 'package.yml'
            if not yml.exists():
                continue

            pkg_name = pkg_dir.name
            if args.only and pkg_name != args.only:
                continue

            yml_text = yml.read_text()
            if re.search(r'^verify_paths\s*:', yml_text, re.MULTILINE):
                already += 1
                continue

            content = name_to_content.get(pkg_name.lower())
            if not content:
                no_data += 1
                continue

            paths = derive_paths(pkg_name, content)
            if not paths:
                no_data += 1
                continue

            block = '\nverify_paths:\n' + ''.join(f'  - {p}\n' for p in paths)
            new_text = yml_text.rstrip() + '\n' + block.lstrip('\n')

            if args.dry_run:
                print(f"\n[{tier_dir.name}/{pkg_name}]")
                print(block.rstrip())
                dry_planned += 1
            else:
                yml.write_text(new_text)
                updated += 1

    print()
    if args.dry_run:
        print(f"Dry-run: would update {dry_planned}")
    else:
        print(f"Updated:                     {updated}")
    print(f"Skipped (already had field): {already}")
    print(f"Skipped (no book data):      {no_data}")


if __name__ == '__main__':
    main()
