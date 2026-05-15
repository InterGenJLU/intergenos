#!/usr/bin/env python3
"""Parse the BLFS 13.0 HTML book into a structured SQLite database.

This tool parses package metadata from the "Beyond Linux From Scratch" (BLFS)
book for use as a local development reference. The generated database is a
derived work and is NOT distributed — it is generated locally from a locally
held copy of the BLFS book.

Attribution:
    Beyond Linux From Scratch (BLFS) is a project of Linux From Scratch (LFS).
    BLFS 13.0 (systemd edition) — https://www.linuxfromscratch.org/blfs/
    Copyright (C) 1999-2026, The BLFS Development Team.
    Licensed under Creative Commons Attribution-NonCommercial-ShareAlike 2.0.

    This parser is an original work by InterGenOS (GPL-3.0-or-later).
    The generated database (build/blfs-packages.db) is for local development
    use only and must not be distributed separately from the BLFS book.

Usage:
    python3 scripts/parse-blfs-book.py [--book PATH] [--db PATH] [--packages-dir PATH]

Defaults:
    --book         docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html
    --db           build/blfs-packages.db
    --packages-dir packages/
"""

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:
    print("ERROR: BeautifulSoup4 required. Install with: pip3 install beautifulsoup4")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY,
    anchor_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    section TEXT,
    download_url TEXT,
    md5 TEXT,
    download_size TEXT,
    disk_space TEXT,
    build_time_sbu TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    dep_anchor TEXT NOT NULL,
    dep_name TEXT NOT NULL,
    dep_version TEXT,
    dep_type TEXT NOT NULL,
    note TEXT,
    UNIQUE(package_id, dep_anchor)
);

CREATE TABLE IF NOT EXISTS patches (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    filename TEXT NOT NULL,
    url TEXT,
    required BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS build_commands (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    context TEXT NOT NULL,
    phase TEXT,
    commands TEXT NOT NULL,
    seq_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    command TEXT,
    notes TEXT,
    has_tests BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS installed_content (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    content_type TEXT NOT NULL,
    items TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS igos_status (
    id INTEGER PRIMARY KEY,
    blfs_anchor TEXT NOT NULL UNIQUE,
    tier TEXT,
    our_version TEXT,
    status TEXT DEFAULT 'not_included',
    deviations TEXT
);
"""

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

VERSION_RE = re.compile(r'^(.+?)[- ](\d[\d.]*\w*)$')


def extract_text(tag):
    """Get clean text content from a tag."""
    if tag is None:
        return ""
    return tag.get_text(strip=True)


def find_section(anchor_tag):
    """Find the package section for an anchor.

    BLFS structure varies. Each package has either its own div.sect1 /
    div.sect2 wrapper, or it appears as a div.package within a larger
    section (typical of the Python Modules section where ~100 packages
    share one sect1).

    BUG FIX 2026-05-11. Previous version returned `pkg_div.parent` when
    no sect1 was found, which is the SHARED parent for all packages in
    a multi-package section like Python Modules. parse_dependencies then
    called section.find('p', class_='required') — which returns the
    FIRST matching <p> in the entire shared parent, attributing the
    first package's deps to EVERY package in the parent. This produced
    the contaminated-rows pattern (Hatchling's deps appearing on
    Alabaster, Attrs, babel, certifi, ...) that broke Mode 3 cascade
    research. Owner-direct fix.

    New behavior: return the package's OWN tight bounding region:
      1. sect1 parent (when each package has its own sect1)
      2. sect2 parent (when nested inside a Modules-style sect1)
      3. The div.package that BELONGS to this anchor (closest-following
         div.package with NO intervening package anchor between)
    Returns None if no bounded region can be determined — caller should
    skip the package rather than attribute wrong deps.
    """
    # Strategy 1: sect1 parent (one-package-per-sect1 layout)
    sect = anchor_tag.find_parent('div', class_='sect1')
    if sect:
        # Make sure NO other package anchor lives in this sect1 — otherwise
        # it's the multi-package layout and we need a tighter bound.
        other_anchors = []
        for a in sect.find_all('a', id=True):
            if a is anchor_tag:
                continue
            aid = a.get('id', '')
            if aid.startswith('idm') or aid.startswith('id-') or len(aid) < 2:
                continue
            next_text = a.next_sibling
            if next_text and isinstance(next_text, str) and VERSION_RE.match(next_text.strip()):
                other_anchors.append(a)
                break
        if not other_anchors:
            return sect
        # else: fall through to a tighter bound

    # Strategy 2: sect2 parent (nested-section layout)
    sect2 = anchor_tag.find_parent('div', class_='sect2')
    if sect2:
        return sect2

    # Strategy 3: the div.package belonging to this anchor (multi-package
    # layout, e.g., Python Modules). Find the closest-following div.package
    # but verify NO other named anchor sits between this anchor and it —
    # if one does, that div.package belongs to the other anchor.
    pkg_div = anchor_tag.find_next('div', class_='package')
    if pkg_div:
        for a in anchor_tag.find_all_next('a', id=True):
            if a is pkg_div or pkg_div in (a.parents if hasattr(a, 'parents') else []):
                break
            try:
                # If 'a' appears before pkg_div in document order...
                anchor_pos = (anchor_tag.sourceline or 0, anchor_tag.sourcepos or 0)
                a_pos = (a.sourceline or 0, a.sourcepos or 0)
                pkg_pos = (pkg_div.sourceline or 0, pkg_div.sourcepos or 0)
            except AttributeError:
                continue
            if not (anchor_pos < a_pos < pkg_pos):
                continue
            # Skip auto-generated and short IDs (same filter as the
            # outer iteration)
            aid = a.get('id', '')
            if aid.startswith('idm') or aid.startswith('id-') or len(aid) < 2:
                continue
            # Skip anchors whose next sibling isn't a package name
            next_text = a.next_sibling
            if not (next_text and isinstance(next_text, str)
                    and VERSION_RE.match(next_text.strip())):
                continue
            # Another package anchor intervenes — this pkg_div is NOT for us
            return None
        return pkg_div  # Return the package div directly — tight bound,
        # ensures parse_dependencies sees only this package's <p class>.

    return None


def parse_package_info(section):
    """Extract download URL, MD5, size, disk space, build time from Package Information."""
    info = {}
    items = section.find_all('li', class_='listitem')
    for item in items:
        text = extract_text(item)
        if 'Download (HTTP)' in text or 'Download (' in text:
            link = item.find('a', class_='ulink')
            if link:
                info['download_url'] = link.get('href', '')
        elif 'MD5 sum' in text:
            match = re.search(r'[a-f0-9]{32}', text)
            if match:
                info['md5'] = match.group()
        elif 'Download size' in text:
            info['download_size'] = text.replace('Download size:', '').strip()
        elif 'disk space' in text:
            info['disk_space'] = text.replace('Estimated disk space required:', '').strip()
        elif 'build time' in text:
            info['build_time_sbu'] = text.replace('Estimated build time:', '').strip()
    return info


def parse_description(section):
    """Extract the introductory description paragraph."""
    pkg_div = section.find('div', class_='package')
    if not pkg_div:
        return ""
    # Find the first <p> after the introduction heading
    intro_h3 = pkg_div.find('h3', class_='sect2')
    if intro_h3:
        p = intro_h3.find_next_sibling('p')
        if p:
            return extract_text(p)
    return ""


def parse_dependencies(section):
    """Extract required, recommended, and optional dependencies."""
    deps = []
    for dep_type in ('required', 'recommended', 'optional'):
        p = section.find('p', class_=dep_type)
        if not p:
            continue
        for link in p.find_all('a', class_='xref'):
            href = link.get('href', '')
            anchor = href.lstrip('#')
            title = link.get('title', '')
            display = extract_text(link)

            # Extract version from title (e.g., "GnuTLS-3.8.12" → "3.8.12")
            dep_version = ""
            if title:
                m = VERSION_RE.match(title)
                if m:
                    dep_version = m.group(2)

            # Check for parenthetical note after the link
            note = ""
            next_sib = link.next_sibling
            if next_sib and isinstance(next_sib, str):
                paren = re.search(r'\(([^)]+)\)', next_sib)
                if paren:
                    note = paren.group(1)

            if anchor:
                deps.append({
                    'dep_anchor': anchor,
                    'dep_name': display or title,
                    'dep_version': dep_version,
                    'dep_type': dep_type,
                    'note': note,
                })
    return deps


def parse_patches(section):
    """Extract patch filenames and URLs from Additional Downloads."""
    patches = []
    for h4 in section.find_all('h4'):
        if 'Additional' in extract_text(h4) and 'Download' in extract_text(h4):
            # Patches are in the div.itemizedlist that follows this h4
            # Structure: h4 → div.itemizedlist → ul → li → p → a.ulink
            sibling = h4.find_next_sibling()
            while sibling and isinstance(sibling, Tag):
                # Stop at next heading
                if sibling.name in ('h4', 'h3', 'h2'):
                    break
                # Search all links within this sibling tree
                for link in sibling.find_all('a', class_='ulink'):
                    url = link.get('href', '')
                    if url.endswith('.patch') or 'patch' in url.split('/')[-1].lower():
                        filename = url.split('/')[-1]
                        patches.append({'filename': filename, 'url': url})
                sibling = sibling.find_next_sibling()
            break
    return patches


def parse_build_commands(section):
    """Extract build and install commands."""
    commands = []
    seq = 0
    install_div = section.find('div', class_='installation')
    if not install_div:
        return commands

    for pre in install_div.find_all('pre'):
        classes = pre.get('class', [])
        kbd = pre.find('kbd', class_='command')
        if not kbd:
            continue

        cmd_text = extract_text(kbd)
        # Decode HTML entities
        cmd_text = cmd_text.replace('&&', '&&')

        if 'userinput' in classes:
            context = 'user'
        elif 'root' in classes:
            context = 'root'
        else:
            context = 'user'

        # Guess phase from context
        phase = 'build' if context == 'user' else 'install'

        commands.append({
            'context': context,
            'phase': phase,
            'commands': cmd_text,
            'seq_order': seq,
        })
        seq += 1

    return commands


def parse_tests(section):
    """Extract test information."""
    install_div = section.find('div', class_='installation')
    if not install_div:
        return None

    full_text = install_div.get_text()

    # Check for "no test suite"
    no_test_patterns = [
        'does not have a working test suite',
        'does not come with a test suite',
        'does not have a test suite',
        'No test suite available',
    ]
    for pattern in no_test_patterns:
        if pattern.lower() in full_text.lower():
            return {'command': None, 'notes': pattern, 'has_tests': False}

    # Look for test commands
    for p in install_div.find_all('p'):
        p_text = extract_text(p)
        if 'to test' in p_text.lower():
            cmd_tag = p.find('strong')
            command = extract_text(cmd_tag) if cmd_tag else ""
            return {'command': command, 'notes': p_text, 'has_tests': True}

    return None


def _segs_in_range(start_node, stop_node):
    """Collect div.seg elements appearing between start_node and stop_node
    in document order, exclusive of stop_node. If stop_node is None,
    collect to end of document.
    """
    segs = []
    cur = start_node.find_next('div', class_='seg')
    while cur is not None:
        if stop_node is not None:
            # Are we past stop_node? Compare via sourcepos when both available.
            try:
                cs = (cur.sourceline or 0, cur.sourcepos or 0)
                ss = (stop_node.sourceline or 0, stop_node.sourcepos or 0)
                if cs >= ss:
                    break
            except AttributeError:
                pass
        segs.append(cur)
        cur = cur.find_next('div', class_='seg')
    return segs


def parse_installed_content(section, anchor_tag=None):
    """Extract installed programs, libraries, directories.

    Strategy differs between books:
    - BLFS: each package is its own div.sect1; all segs live inside it.
      A direct find_all under the sect1 catches them.
    - LFS:  packages are flat inside a single div.chapter (no per-package
      sect1). Each package's "Contents of X" segs live AFTER the section
      passed in, bounded by the next package's h2 title.

    Combine both: gather segs inside the section, AND walk forward from
    the section/anchor until the next h2.title (i.e., next package).
    """
    # Segs inside the section (BLFS direct hit)
    inside = list(section.find_all('div', class_='seg')) if section else []

    # Walk forward from the section (or anchor_tag) until next h2.title
    seed = section if section else anchor_tag
    forward = []
    if seed is not None:
        # Find the NEXT h2 with class="title" — that's the start of the
        # next package's section.
        def is_next_pkg_h2(tag):
            return (tag.name == 'h2'
                    and 'title' in (tag.get('class') or []))
        next_h2 = seed.find_next(is_next_pkg_h2)
        forward = _segs_in_range(seed, next_h2)

    # Dedupe while preserving order
    seen_ids = set()
    all_segs = []
    for seg in inside + forward:
        sid = id(seg)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        all_segs.append(seg)

    content = []
    for seg in all_segs:
        title_tag = seg.find('strong', class_='segtitle')
        body_tag = seg.find('span', class_='segbody')
        if title_tag and body_tag:
            title_l = extract_text(title_tag).rstrip(':').lower()
            body = extract_text(body_tag)
            if body and body.lower() != 'none':
                # Title-case varies between LFS (lowercase) and BLFS
                # (Title Case) — compare lower-cased.
                if 'program' in title_l:
                    content.append({'content_type': 'programs', 'items': body})
                elif 'librar' in title_l:
                    content.append({'content_type': 'libraries', 'items': body})
                elif 'director' in title_l:
                    content.append({'content_type': 'directories', 'items': body})
    return content


def find_section_name(anchor_tag):
    """Try to determine which BLFS section/chapter a package belongs to."""
    parent = anchor_tag.parent
    while parent:
        if isinstance(parent, Tag) and parent.name == 'div':
            classes = parent.get('class', [])
            if 'chapter' in classes or 'part' in classes:
                h = parent.find(['h1', 'h2'])
                if h:
                    return extract_text(h)
        parent = parent.parent
    return ""


# ---------------------------------------------------------------------------
# Cross-reference with InterGenOS package tree
# ---------------------------------------------------------------------------

def scan_igos_packages(packages_dir):
    """Scan our packages/ directory to build igos_status entries."""
    status = {}
    packages_path = Path(packages_dir)
    if not packages_path.exists():
        return status

    for tier_dir in packages_path.iterdir():
        if not tier_dir.is_dir():
            continue
        tier = tier_dir.name
        for pkg_dir in tier_dir.iterdir():
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / 'package.yml'
            if yml.exists():
                # Quick version extraction without full YAML parse
                version = ""
                try:
                    for line in yml.read_text().splitlines():
                        if line.startswith('version:'):
                            version = line.split(':', 1)[1].strip().strip("'\"")
                            break
                except Exception:
                    pass
                status[pkg_dir.name] = {
                    'tier': tier,
                    'our_version': version,
                    'status': 'planned',
                }
    return status


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

# LFS uses "8.7. Bzip2-1.0.8" — strip leading section number prefix.
LFS_SECTION_PREFIX_RE = re.compile(r'^\d+(\.\d+)*\.\s*')


def normalize_package_name(raw):
    """Clean a name extracted from book header text.

    LFS: "8.7. Bzip2" -> "Bzip2"
    BLFS: "Bzip2" -> "Bzip2" (no-op)
    """
    return LFS_SECTION_PREFIX_RE.sub('', raw).strip()


def init_db(db_path):
    """Initialize the database fresh (drop existing, apply schema)."""
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def parse_book_into_db(book_path, conn):
    """Parse one LFS/BLFS HTML book and INSERT package rows into the db.

    Both LFS and BLFS use the same DocBook-derived HTML structure for
    package sections — only the anchor naming convention differs (LFS:
    `ch-system-bzip2` + `contents-bzip2`; BLFS: free-form anchor IDs).
    Anchor-detection is heuristic (look for any anchor whose next text
    matches Name-Version) so it works for both.
    """
    print(f"Loading book: {book_path}")
    print(f"  (this may take a moment — it's a large file)")

    with open(book_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    print(f"  HTML parsed successfully")

    # Find all package anchors
    anchors = soup.find_all('a', id=True)
    package_anchors = []

    for a in anchors:
        aid = a.get('id', '')
        # Skip auto-generated IDs
        if aid.startswith('idm') or aid.startswith('id-'):
            continue
        # Skip very short IDs that are section markers
        if len(aid) < 2:
            continue
        # Skip the LFS "contents-X" anchors — those are subsections of a
        # package, not the package definition itself. They're parsed as
        # part of the parent section's find_section() bounding region.
        if aid.startswith('contents-'):
            continue

        # Check if followed by text matching Name-Version pattern
        next_text = a.next_sibling
        if next_text and isinstance(next_text, str):
            if VERSION_RE.match(next_text.strip()):
                package_anchors.append((aid, next_text.strip(), a))

    print(f"  Found {len(package_anchors)} package entries")

    # Parse each package
    parsed = 0
    skipped = 0

    for anchor_id, name_version, anchor_tag in package_anchors:
        section = find_section(anchor_tag)
        if not section:
            skipped += 1
            continue

        # Split name-version
        m = VERSION_RE.match(name_version)
        if not m:
            skipped += 1
            continue

        name = normalize_package_name(m.group(1))
        version = m.group(2)

        # Extract all data
        info = parse_package_info(section)
        description = parse_description(section)
        deps = parse_dependencies(section)
        patches = parse_patches(section)
        commands = parse_build_commands(section)
        test_info = parse_tests(section)
        content = parse_installed_content(section, anchor_tag)
        blfs_section = find_section_name(anchor_tag)

        # Insert package
        try:
            conn.execute(
                """INSERT INTO packages
                   (anchor_id, name, version, section, download_url, md5,
                    download_size, disk_space, build_time_sbu, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (anchor_id, name, version, blfs_section,
                 info.get('download_url', ''), info.get('md5', ''),
                 info.get('download_size', ''), info.get('disk_space', ''),
                 info.get('build_time_sbu', ''), description)
            )
            pkg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except sqlite3.IntegrityError:
            # Duplicate anchor — skip
            skipped += 1
            continue

        # Insert dependencies
        for dep in deps:
            try:
                conn.execute(
                    """INSERT INTO dependencies
                       (package_id, dep_anchor, dep_name, dep_version, dep_type, note)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (pkg_id, dep['dep_anchor'], dep['dep_name'],
                     dep['dep_version'], dep['dep_type'], dep['note'])
                )
            except sqlite3.IntegrityError:
                pass  # duplicate dep

        # Insert patches
        for patch in patches:
            conn.execute(
                "INSERT INTO patches (package_id, filename, url) VALUES (?, ?, ?)",
                (pkg_id, patch['filename'], patch['url'])
            )

        # Insert build commands
        for cmd in commands:
            conn.execute(
                """INSERT INTO build_commands
                   (package_id, context, phase, commands, seq_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (pkg_id, cmd['context'], cmd['phase'],
                 cmd['commands'], cmd['seq_order'])
            )

        # Insert test info
        if test_info:
            conn.execute(
                "INSERT INTO tests (package_id, command, notes, has_tests) VALUES (?, ?, ?, ?)",
                (pkg_id, test_info['command'], test_info['notes'],
                 test_info['has_tests'])
            )

        # Insert installed content
        for c in content:
            conn.execute(
                "INSERT INTO installed_content (package_id, content_type, items) VALUES (?, ?, ?)",
                (pkg_id, c['content_type'], c['items'])
            )

        parsed += 1

    conn.commit()
    print(f"  Parsed: {parsed}, skipped: {skipped}")
    return parsed, skipped


def finalize_db(conn, packages_dir, db_path):
    """Add IGOS cross-reference, aliases, and print summary. Closes conn."""
    # Cross-reference with our packages
    igos = scan_igos_packages(packages_dir)
    for pkg_name, data in igos.items():
        conn.execute(
            """INSERT OR REPLACE INTO igos_status
               (blfs_anchor, tier, our_version, status)
               VALUES (?, ?, ?, ?)""",
            (pkg_name, data['tier'], data['our_version'], data['status'])
        )

    # Alias table: maps our package names to BLFS anchor IDs where they differ.
    # This allows accurate gap analysis when BLFS uses different names.
    conn.execute("""CREATE TABLE IF NOT EXISTS aliases (
        igos_name TEXT NOT NULL,
        blfs_anchor TEXT NOT NULL,
        PRIMARY KEY(igos_name, blfs_anchor)
    )""")
    ALIASES = [
        # GStreamer (BLFS uses gst10- prefix)
        ('gstreamer', 'gstreamer10'),
        ('gst-plugins-base', 'gst10-plugins-base'),
        ('gst-plugins-bad', 'gst10-plugins-bad'),
        ('gst-plugins-good', 'gst10-plugins-good'),
        # Image libraries
        ('libjpeg-turbo', 'libjpeg'),
        # DocBook
        ('docbook-xml', 'DocBook'),
        ('docbook-xsl-nons', 'docbook-xsl'),
        # Network managers (case difference)
        ('networkmanager', 'NetworkManager'),
        ('modemmanager', 'ModemManager'),
    ]
    for igos_name, blfs_anchor in ALIASES:
        try:
            conn.execute(
                "INSERT INTO aliases (igos_name, blfs_anchor) VALUES (?, ?)",
                (igos_name, blfs_anchor)
            )
            # Also register in igos_status under the BLFS anchor
            existing = conn.execute(
                "SELECT tier, our_version FROM igos_status WHERE blfs_anchor = ?",
                (igos_name,)
            ).fetchone()
            if existing:
                conn.execute(
                    "INSERT OR REPLACE INTO igos_status (blfs_anchor, tier, our_version, status) VALUES (?, ?, ?, ?)",
                    (blfs_anchor, existing[0], existing[1], 'planned')
                )
        except sqlite3.IntegrityError:
            pass

    conn.commit()

    # Summary
    pkg_count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    dep_count = conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0]
    patch_count = conn.execute("SELECT COUNT(*) FROM patches").fetchone()[0]
    test_count = conn.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    igos_count = conn.execute("SELECT COUNT(*) FROM igos_status").fetchone()[0]
    content_count = conn.execute("SELECT COUNT(*) FROM installed_content").fetchone()[0]
    pkgs_with_content = conn.execute(
        "SELECT COUNT(DISTINCT package_id) FROM installed_content"
    ).fetchone()[0]

    conn.close()

    print(f"\n  Database created: {db_path}")
    print(f"  Packages:                  {pkg_count}")
    print(f"  Dependencies:              {dep_count}")
    print(f"  Patches:                   {patch_count}")
    print(f"  Test entries:              {test_count}")
    print(f"  Installed-content rows:    {content_count}")
    print(f"  Pkgs with installed_content: {pkgs_with_content}")
    print(f"  IGOS status:               {igos_count}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Parse LFS/BLFS 13.0 HTML into SQLite')
    parser.add_argument('--book', action='append', dest='books',
                        help='Path to an LFS or BLFS HTML book (repeatable). '
                             'Default: both LFS-BOOK-13.0-SYSD.html and '
                             'BLFS-BOOK-13.0-systemd.html under docs/lfs-13.0/')
    parser.add_argument('--db', default='build/blfs-packages.db',
                        help='Output SQLite database path')
    parser.add_argument('--packages-dir', default='packages/',
                        help='InterGenOS packages directory for cross-reference')
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    # Default: parse both LFS and BLFS books
    if not args.books:
        args.books = [
            'docs/lfs-13.0/LFS-BOOK-13.0-SYSD.html',
            'docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html',
        ]

    # Resolve book paths
    book_paths = []
    for b in args.books:
        bp = project_root / b
        if not bp.exists():
            bp = Path(b)
            if not bp.exists():
                print(f"ERROR: book not found at {b}")
                sys.exit(1)
        book_paths.append(bp)

    db_path = project_root / args.db
    packages_dir = project_root / args.packages_dir

    # Initialize DB once, parse each book, finalize once
    conn = init_db(str(db_path))
    for bp in book_paths:
        parse_book_into_db(str(bp), conn)
    finalize_db(conn, str(packages_dir), str(db_path))
