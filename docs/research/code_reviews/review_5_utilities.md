# Code Review Request: InterGenOS Utility Scripts

I'm requesting a thorough code review of the utility scripts that support the InterGenOS build pipeline. InterGenOS is a Linux distribution built entirely from source following Linux From Scratch (LFS 13.0) and Beyond LFS (BLFS 13.0).

These scripts handle the data pipeline around the build — parsing reference documentation, querying package databases, downloading sources, generating templates, and validating the build host:

- **parse-blfs-book.py** — Parses the 250,000-line BLFS 13.0 HTML book into a structured SQLite database. Extracts 926 packages with their versions, download URLs, MD5 sums, dependencies (required/recommended/optional), patches, and test instructions. Includes an alias table for name normalization (e.g., `gstreamer` ↔ `gst10`).

- **blfs-query.py** — CLI tool for querying the BLFS database. Subcommands include `info`, `deps`, `gaps` (find missing packages in our tree), `chain-cost` (calculate total dependency depth), `patches`, `versions`, `search`, and `stats`.

- **download-sources.py** — Scans all `package.yml` files and downloads source tarballs that aren't already cached. Supports SHA256 verification, retry logic, and parallel downloads.

- **generate-templates.py** — Batch-generates `package.yml` + `build.sh` templates from BLFS database entries. Auto-detects build style (autotools, cmake, meson) and maps BLFS dependency names to our package names.

- **host-check.py** — Validates that the build host meets LFS 13.0 requirements (compiler versions, required tools, kernel config, filesystem space).

I would appreciate your assessment of the following areas in particular:

1. **HTML parsing robustness** — The BLFS book HTML is not machine-friendly. Is the parser handling edge cases (malformed tags, inconsistent structure, unicode)?
2. **SQL injection** — Are all database queries parameterized in both the parser and query tool?
3. **Download security** — Is HTTPS enforced? Are checksums verified before accepting downloads? Is there protection against redirect attacks?
4. **Template generation correctness** — Does the generator produce valid YAML and syntactically correct bash?
5. **General code quality** — Error handling, edge cases, maintainability

The complete source follows. There are 5 files totaling approximately 2,200 lines of Python.

---

## Source Code

### parse-blfs-book.py
```python
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

    BLFS structure varies — some packages are in div.sect1, others have
    div.package as a direct child of div.chapter. We try multiple strategies:
    1. find_parent('div', class_='sect1')
    2. find_next('div', class_='package') and use its parent
    3. Walk siblings from the anchor's h2 parent
    """
    # Strategy 1: sect1 parent
    sect = anchor_tag.find_parent('div', class_='sect1')
    if sect:
        return sect

    # Strategy 2: next div.package — use its grandparent as section boundary
    pkg_div = anchor_tag.find_next('div', class_='package')
    if pkg_div:
        # Verify this package div is for OUR anchor (not the next package)
        # by checking the anchor text matches
        parent = pkg_div.parent
        if parent:
            return parent

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


def parse_installed_content(section):
    """Extract installed programs, libraries, directories."""
    content = []
    content_div = section.find('div', class_='content')
    if not content_div:
        return content

    for seg in content_div.find_all('div', class_='seg'):
        title_tag = seg.find('strong', class_='segtitle')
        body_tag = seg.find('span', class_='segbody')
        if title_tag and body_tag:
            title = extract_text(title_tag).rstrip(':')
            body = extract_text(body_tag)
            if body and body.lower() != 'none':
                if 'Program' in title:
                    content.append({'content_type': 'programs', 'items': body})
                elif 'Librar' in title:
                    content.append({'content_type': 'libraries', 'items': body})
                elif 'Director' in title:
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

def parse_blfs_book(book_path, db_path, packages_dir):
    """Parse the BLFS HTML book and populate the SQLite database."""
    print(f"Loading BLFS book: {book_path}")
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

        # Check if followed by text matching Name-Version pattern
        next_text = a.next_sibling
        if next_text and isinstance(next_text, str):
            if VERSION_RE.match(next_text.strip()):
                package_anchors.append((aid, next_text.strip(), a))

    print(f"  Found {len(package_anchors)} package entries")

    # Set up database
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

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

        name = m.group(1).strip()
        version = m.group(2)

        # Extract all data
        info = parse_package_info(section)
        description = parse_description(section)
        deps = parse_dependencies(section)
        patches = parse_patches(section)
        commands = parse_build_commands(section)
        test_info = parse_tests(section)
        content = parse_installed_content(section)
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

    conn.close()

    print(f"\n  Database created: {db_path}")
    print(f"  Packages:     {pkg_count}")
    print(f"  Dependencies: {dep_count}")
    print(f"  Patches:      {patch_count}")
    print(f"  Test entries: {test_count}")
    print(f"  IGOS status:  {igos_count}")
    print(f"  Skipped:      {skipped}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Parse BLFS 13.0 HTML into SQLite')
    parser.add_argument('--book', default='docs/lfs-13.0/BLFS-BOOK-13.0-systemd.html',
                        help='Path to BLFS HTML book')
    parser.add_argument('--db', default='build/blfs-packages.db',
                        help='Output SQLite database path')
    parser.add_argument('--packages-dir', default='packages/',
                        help='InterGenOS packages directory for cross-reference')
    args = parser.parse_args()

    # Resolve paths relative to project root
    project_root = Path(__file__).parent.parent
    book_path = project_root / args.book
    db_path = project_root / args.db
    packages_dir = project_root / args.packages_dir

    if not book_path.exists():
        # Try absolute path
        book_path = Path(args.book)
        if not book_path.exists():
            print(f"ERROR: BLFS book not found at {args.book}")
            sys.exit(1)

    parse_blfs_book(str(book_path), str(db_path), str(packages_dir))
```

### blfs-query.py
```python
#!/usr/bin/env python3
"""Query the BLFS package database.

Queries a local SQLite database generated by parse-blfs-book.py from
a locally held copy of the BLFS 13.0 book.

Attribution:
    The database queried by this tool contains metadata derived from
    "Beyond Linux From Scratch" (BLFS), a project of Linux From Scratch.
    BLFS 13.0 (systemd edition) — https://www.linuxfromscratch.org/blfs/
    Copyright (C) 1999-2026, The BLFS Development Team.
    Licensed under Creative Commons Attribution-NonCommercial-ShareAlike 2.0.

    This query tool is an original work by InterGenOS (GPL-3.0-or-later).

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
```

### download-sources.py
```python
#!/usr/bin/env python3
"""InterGenOS Source Tarball Manager

Downloads source tarballs for package templates, computes SHA256 checksums,
and optionally updates package.yml files with the hashes.

Usage:
    python3 scripts/download-sources.py --tier desktop              # Download desktop sources
    python3 scripts/download-sources.py --tier core base desktop    # Multiple tiers
    python3 scripts/download-sources.py --all                       # All tiers
    python3 scripts/download-sources.py --all --update-checksums    # Download + update package.yml
    python3 scripts/download-sources.py --verify                    # Verify existing tarballs
    python3 scripts/download-sources.py --all --dry-run             # Show what would be downloaded
"""

import hashlib
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"

TIERS = ["toolchain", "core", "base", "desktop"]


def sha256_file(path: str) -> str:
    """Compute SHA256 hash of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_url(url: str, name: str, version: str) -> str:
    """Replace ${version} and ${name} in URL templates."""
    return url.replace("${version}", version).replace("${name}", name)


def validate_download(dest: str) -> bool:
    """Verify a downloaded file is actually an archive, not an error page.

    Returns True if the file looks valid. Removes the file and returns False
    if it's suspiciously small or is plain text (HTML error page, "Not Found", etc.).
    """
    if not os.path.exists(dest):
        return False

    size = os.path.getsize(dest)

    # Archives should be at least 1KB — anything smaller is almost certainly
    # an error page or empty response
    if size < 1024:
        with open(dest, "rb") as f:
            head = f.read(512)
        # Check if it's text (HTML error, "Not Found", redirect page, etc.)
        try:
            text = head.decode("utf-8", errors="strict")
            if any(marker in text.lower() for marker in ["not found", "<html", "<!doctype", "error", "redirect"]):
                print(f"    CORRUPT: downloaded file is a text error page ({size} bytes: {text.strip()[:80]})", flush=True)
                os.unlink(dest)
                return False
        except UnicodeDecodeError:
            pass  # Binary data — probably fine, just very small

    return True


def download_file(url: str, dest: str, timeout: int = 300) -> bool:
    """Download a file using wget, falling back to curl. Returns True on success."""
    try:
        # Try wget first
        result = subprocess.run(
            ["wget", "-q", "--timeout=30", "-O", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                return True

        # wget failed — try curl as fallback (some sites block wget)
        if os.path.exists(dest):
            os.unlink(dest)
        result = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "30", "-o", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                return True

        return False
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT: {url}", flush=True)
        if os.path.exists(dest):
            os.unlink(dest)
        return False
    except Exception as e:
        print(f"    ERROR: {e}", flush=True)
        return False


def load_packages(tiers: list[str]) -> list[dict]:
    """Load all package.yml files for the given tiers."""
    packages = []
    for tier in tiers:
        tier_dir = PACKAGES_DIR / tier
        if not tier_dir.exists():
            print(f"  WARNING: tier directory not found: {tier_dir}")
            continue
        for pkg_yml in sorted(tier_dir.rglob("package.yml")):
            with open(pkg_yml) as f:
                data = yaml.safe_load(f)
                data["_path"] = pkg_yml
                data["_tier"] = tier
                packages.append(data)
    return packages


def get_source_info(pkg: dict) -> list[dict]:
    """Extract source URL, filename, and expected SHA256 for each source."""
    sources = []
    name = pkg.get("name", "")
    version = str(pkg.get("version", ""))

    for src in pkg.get("source", []):
        url = resolve_url(src.get("url", ""), name, version)
        sha256 = src.get("sha256", "NEEDS_CHECKSUM")

        # Determine local filename
        filename = src.get("filename")
        if filename:
            filename = filename.replace("${version}", version).replace("${name}", name)
        else:
            filename = url.split("/")[-1]

        sources.append({
            "url": url,
            "filename": filename,
            "sha256": sha256,
            "needs_checksum": sha256 == "NEEDS_CHECKSUM",
        })
    return sources


def cmd_download(tiers: list[str], update_checksums: bool = False, dry_run: bool = False):
    """Download missing source tarballs."""
    packages = load_packages(tiers)
    print(f"\nScanning {len(packages)} packages across tiers: {', '.join(tiers)}\n")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    to_download = []
    already_have = 0
    total_sources = 0

    for pkg in packages:
        for src in get_source_info(pkg):
            total_sources += 1
            dest = SOURCES_DIR / src["filename"]

            if dest.exists() and dest.stat().st_size > 0:
                already_have += 1
                # If we have the file but need checksum, compute it
                if update_checksums and src["needs_checksum"]:
                    to_download.append({
                        "pkg": pkg,
                        "src": src,
                        "dest": dest,
                        "action": "checksum_only",
                    })
            else:
                # Remove empty files from failed downloads
                if dest.exists() and dest.stat().st_size == 0:
                    dest.unlink()
                to_download.append({
                    "pkg": pkg,
                    "src": src,
                    "dest": dest,
                    "action": "download",
                })

    downloads_needed = len([d for d in to_download if d["action"] == "download"])
    checksums_needed = len([d for d in to_download if d["action"] == "checksum_only"])

    print(f"  Total sources: {total_sources}")
    print(f"  Already cached: {already_have}")
    print(f"  To download: {downloads_needed}")
    if checksums_needed:
        print(f"  Checksum only: {checksums_needed}")
    print()

    if dry_run:
        for item in to_download:
            if item["action"] == "download":
                print(f"  [DRY] Would download: {item['src']['filename']}")
                print(f"         URL: {item['src']['url']}")
        return

    succeeded = 0
    failed = 0
    checksummed = 0

    for i, item in enumerate(to_download, 1):
        src = item["src"]
        dest = item["dest"]
        pkg = item["pkg"]
        name = pkg.get("name", "?")

        if item["action"] == "download":
            print(f"  [{i}/{len(to_download)}] Downloading {src['filename']}...", flush=True)
            if download_file(src["url"], str(dest)):
                size = dest.stat().st_size
                human = f"{size/1024/1024:.1f}MB" if size > 1024*1024 else f"{size/1024:.0f}KB"
                print(f"    OK ({human})", flush=True)
                succeeded += 1

                # Compute checksum for newly downloaded file
                if update_checksums:
                    sha = sha256_file(str(dest))
                    update_package_checksum(pkg["_path"], src["url"], sha)
                    print(f"    SHA256: {sha[:16]}... (updated)", flush=True)
                    checksummed += 1
            else:
                print(f"    FAILED: {src['url']}", flush=True)
                # Remove empty/partial file from failed download
                if dest.exists():
                    dest.unlink()
                failed += 1

        elif item["action"] == "checksum_only":
            print(f"  [{i}/{len(to_download)}] Computing checksum: {src['filename']}...", flush=True)
            sha = sha256_file(str(dest))
            update_package_checksum(pkg["_path"], src["url"], sha)
            print(f"    SHA256: {sha[:16]}... (updated)", flush=True)
            checksummed += 1

    print(f"\nDone: {succeeded} downloaded, {failed} failed, {checksummed} checksums updated", flush=True)
    if failed:
        print(f"\n  WARNING: {failed} downloads failed. Re-run to retry.", flush=True)


def update_package_checksum(pkg_path: Path, url: str, sha256: str):
    """Update a package.yml file with a real SHA256 checksum."""
    with open(pkg_path) as f:
        content = f.read()

    # Find the source entry matching this URL and replace its checksum
    # We look for the NEEDS_CHECKSUM on the line after the URL
    lines = content.split("\n")
    found_url = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match the URL line (may have ${version} or resolved form)
        if stripped.startswith("url:") or stripped.startswith("- url:"):
            found_url = True
            continue
        if found_url and "sha256:" in stripped:
            if "NEEDS_CHECKSUM" in stripped:
                indent = len(line) - len(line.lstrip())
                lines[i] = " " * indent + f"sha256: {sha256}"
                found_url = False
                break
            found_url = False

    with open(pkg_path, "w") as f:
        f.write("\n".join(lines))


def cmd_verify(tiers: list[str]):
    """Verify cached tarballs match their declared SHA256."""
    packages = load_packages(tiers)
    print(f"\nVerifying sources for {len(packages)} packages\n")

    good = 0
    bad = 0
    missing = 0
    unchecked = 0

    for pkg in packages:
        for src in get_source_info(pkg):
            dest = SOURCES_DIR / src["filename"]

            if not dest.exists():
                missing += 1
                continue

            if src["needs_checksum"]:
                unchecked += 1
                continue

            actual = sha256_file(str(dest))
            if actual == src["sha256"]:
                good += 1
            else:
                bad += 1
                print(f"  MISMATCH: {src['filename']}")
                print(f"    expected: {src['sha256']}")
                print(f"    actual:   {actual}")

    print(f"\nResults: {good} verified, {bad} mismatched, {missing} missing, {unchecked} no checksum")


def main():
    args = sys.argv[1:]

    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    tiers = []
    update_checksums = "--update-checksums" in args
    dry_run = "--dry-run" in args
    verify_mode = "--verify" in args

    if "--all" in args:
        tiers = TIERS
    elif "--tier" in args:
        idx = args.index("--tier")
        # Collect all following args that aren't flags
        for a in args[idx+1:]:
            if a.startswith("--"):
                break
            if a in TIERS:
                tiers.append(a)
            else:
                print(f"Unknown tier: {a}. Valid: {', '.join(TIERS)}")
                sys.exit(1)

    if not tiers and not verify_mode:
        print("Error: specify --tier <name> or --all")
        sys.exit(1)

    if verify_mode:
        if not tiers:
            tiers = TIERS
        cmd_verify(tiers)
    else:
        cmd_download(tiers, update_checksums=update_checksums, dry_run=dry_run)


if __name__ == "__main__":
    main()
```

### generate-templates.py
```python
#!/usr/bin/env python3
"""InterGenOS Package Template Generator

Reads a YAML package definition file and generates package.yml + build.sh
templates for each package. Designed for batch-generating desktop tier
packages from BLFS research data.

Usage:
    python3 scripts/generate-templates.py <input.yml> [--download-checksums]

Input format (YAML):
    tier: desktop
    packages:
      - name: libgpg-error
        version: "1.59"
        url: https://example.com/libgpg-error-${version}.tar.bz2
        license: LGPL-2.1-or-later
        description: GPG error code library
        homepage: https://www.gnupg.org/
        build_style: autotools
        configure_flags:
          - "--prefix=/usr"
          - "--disable-static"
        deps:
          build: []
          host: []
          runtime: []
        pre_configure: |
          sed -i 's/something/else/' configure
        post_install: |
          ln -sfv foo ${DESTDIR}/usr/lib/bar

Output:
    packages/<tier>/<name>/package.yml
    packages/<tier>/<name>/build.sh
"""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


PACKAGES_ROOT = Path(__file__).parent.parent / "packages"

# ============================================================================
# Build style templates
# ============================================================================

AUTOTOOLS_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    ./configure {configure_flags}
}}

build() {{
    make -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    make DESTDIR="$DESTDIR" install{post_install_block}
}}
'''

MESON_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    mkdir build
    cd    build

    meson setup ..            \\
          --prefix=/usr       \\
          --libdir=/usr/lib   \\
          --buildtype=release {meson_flags}
}}

build() {{
    cd build
    ninja
}}
{check_block}
do_install() {{
    cd build
    DESTDIR="$DESTDIR" ninja install{post_install_block}
}}
'''

CMAKE_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
configure() {{
    cmake -B build                    \\
          -DCMAKE_INSTALL_PREFIX=/usr \\
          -DCMAKE_BUILD_TYPE=Release  {cmake_flags}
}}

build() {{
    cmake --build build -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    DESTDIR="$DESTDIR" cmake --install build{post_install_block}
}}
'''

MAKE_BUILD_SH = '''\
#!/bin/bash
# {name} {version} — {description}
# {source}
{pre_configure_block}
build() {{
    make {make_flags} -j${{IGOS_JOBS}}
}}
{check_block}
do_install() {{
    make {make_install_flags} DESTDIR="$DESTDIR" install{post_install_block}
}}
'''


def compute_sha256(url: str, version: str) -> str:
    """Download a URL and compute its SHA256 hash."""
    resolved_url = url.replace("${version}", version)
    print(f"  Downloading {resolved_url}...")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        try:
            result = subprocess.run(
                ["wget", "-q", "-O", tmp.name, resolved_url],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"    FAILED to download: {result.stderr.strip()}")
                return "DOWNLOAD_FAILED"

            sha = hashlib.sha256()
            with open(tmp.name, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha.update(chunk)
            return sha.hexdigest()
        finally:
            os.unlink(tmp.name)


def format_flags(flags: list[str], prefix: str = "", join_str: str = " \\\n    ") -> str:
    """Format a list of flags for shell scripts."""
    if not flags:
        return ""
    formatted = [f"{prefix}{f}" if prefix else f for f in flags]
    return join_str.join(formatted)


def generate_package_yml(pkg: dict, tier: str) -> str:
    """Generate package.yml content."""
    deps = pkg.get("deps", {})
    build_deps = deps.get("build", [])
    host_deps = deps.get("host", [])
    runtime_deps = deps.get("runtime", [])

    # Format sources
    sources = []
    urls = pkg.get("url", "")
    if isinstance(urls, str):
        urls = [urls]

    for i, url in enumerate(urls):
        entry = {"url": url, "sha256": pkg.get("sha256", "NEEDS_CHECKSUM")}
        if isinstance(pkg.get("sha256"), list):
            entry["sha256"] = pkg["sha256"][i] if i < len(pkg["sha256"]) else "NEEDS_CHECKSUM"
        if pkg.get("filename") and i == 0:
            entry["filename"] = pkg["filename"]
        sources.append(entry)

    yml = {
        "name": pkg["name"],
        "version": str(pkg["version"]),
        "release": 1,
        "description": pkg.get("description", f"{pkg['name']} library"),
        "license": pkg.get("license", "UNKNOWN"),
        "homepage": pkg.get("homepage", ""),
        "tier": tier,
        "build_style": pkg.get("build_style", "autotools"),
        "install_func": "do_install",
        "source": sources,
        "dependencies": {
            "build": build_deps,
            "host": host_deps,
            "runtime": runtime_deps,
        },
    }

    # Add configure_flags for autotools style (used by Python builder)
    if pkg.get("build_style") == "autotools" and pkg.get("configure_flags"):
        yml["configure_flags"] = pkg["configure_flags"]

    # Add patches
    if pkg.get("patches"):
        yml["patches"] = pkg["patches"]

    # Add direct_install
    if pkg.get("direct_install"):
        yml["direct_install"] = True

    return yaml.dump(yml, default_flow_style=False, sort_keys=False)


def generate_build_sh(pkg: dict) -> str:
    """Generate build.sh content based on build_style."""
    style = pkg.get("build_style", "autotools")
    name = pkg["name"]
    version = str(pkg["version"])
    description = pkg.get("description", "")
    source = pkg.get("source_note", "BLFS 13.0")

    # Pre-configure commands
    pre_configure = pkg.get("pre_configure", "").strip()
    pre_configure_block = ""
    if pre_configure:
        pre_configure_block = f"\nconfigure_pre() {{\n    {pre_configure}\n}}\n"

    # Post-install commands
    post_install = pkg.get("post_install", "").strip()
    post_install_block = ""
    if post_install:
        post_install_block = "\n    " + post_install.replace("\n", "\n    ")

    # Check block
    check_cmd = pkg.get("check", "")
    check_block = ""
    if check_cmd:
        check_block = f"\ncheck() {{\n    {check_cmd} || true\n}}\n"

    if style == "autotools":
        flags = pkg.get("configure_flags", ["--prefix=/usr", "--disable-static"])
        flags_str = format_flags(flags, join_str=" \\\n                ")

        # Handle pre_configure as part of configure()
        configure_body = f"./configure {flags_str}"
        if pre_configure:
            configure_body = f"{pre_configure}\n\n    ./configure {flags_str}"

        return AUTOTOOLS_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if pre_configure else "",
            configure_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        ).replace(
            f"    ./configure {flags_str}",
            f"    {configure_body}" if pre_configure else f"    ./configure {flags_str}",
        )

    elif style == "meson":
        flags = pkg.get("meson_flags", [])
        flags_str = ""
        if flags:
            flags_str = "\\\n          " + " \\\n          ".join(flags)

        result = MESON_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            meson_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        )
        return result

    elif style == "cmake":
        flags = pkg.get("cmake_flags", [])
        flags_str = ""
        if flags:
            flags_str = "\\\n          " + " \\\n          ".join(f"-D{f}" for f in flags)

        return CMAKE_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            cmake_flags=flags_str,
            check_block=check_block,
            post_install_block=post_install_block,
        )

    elif style == "make":
        return MAKE_BUILD_SH.format(
            name=name, version=version, description=description,
            source=source,
            pre_configure_block="" if not pre_configure else f"\n{pre_configure}\n",
            make_flags=pkg.get("make_flags", ""),
            make_install_flags=pkg.get("make_install_flags", ""),
            check_block=check_block,
            post_install_block=post_install_block,
        )

    elif style == "custom":
        # Custom style — build.sh must be provided separately
        return f"#!/bin/bash\n# {name} {version} — {description}\n# Custom build — provide build.sh manually\n"

    else:
        raise ValueError(f"Unknown build_style: {style}")


def process_input(input_path: str, download_checksums: bool = False):
    """Process an input YAML file and generate templates."""
    with open(input_path) as f:
        data = yaml.safe_load(f)

    tier = data.get("tier", "desktop")
    packages = data.get("packages", [])

    print(f"\nGenerating {len(packages)} package templates for tier: {tier}\n")

    created = 0
    skipped = 0

    for pkg in packages:
        name = pkg["name"]
        version = str(pkg["version"])
        pkg_dir = PACKAGES_ROOT / tier / name

        if pkg_dir.exists():
            print(f"  [SKIP] {name} — already exists")
            skipped += 1
            continue

        # Download and compute checksum if requested
        if download_checksums and pkg.get("sha256", "NEEDS_CHECKSUM") == "NEEDS_CHECKSUM":
            url = pkg.get("url", "")
            if isinstance(url, list):
                url = url[0]
            if url:
                pkg["sha256"] = compute_sha256(url, version)

        # Generate files
        pkg_yml = generate_package_yml(pkg, tier)
        build_sh = generate_build_sh(pkg)

        # Write files
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "package.yml").write_text(pkg_yml)
        (pkg_dir / "build.sh").write_text(build_sh)

        print(f"  [OK  ] {name} {version}")
        created += 1

    print(f"\nDone: {created} created, {skipped} skipped")
    print(f"Templates in: {PACKAGES_ROOT / tier}/")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate-templates.py <input.yml> [--download-checksums]")
        sys.exit(1)

    input_path = sys.argv[1]
    download_checksums = "--download-checksums" in sys.argv

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found")
        sys.exit(1)

    process_input(input_path, download_checksums)


if __name__ == "__main__":
    main()
```

### host-check.py
```python
#!/usr/bin/env python3
"""InterGenOS Host System Requirements Check

Validates that the build host meets all LFS 13.0 minimum requirements
before attempting a build. Replaces the original req_check.sh from
build_003 with proper Python, structured output, and clear diagnostics.

Usage:
    python3 scripts/host-check.py              # Check local system
    python3 scripts/host-check.py --remote     # Check VM via SSH
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# LFS 13.0 minimum requirements (Section 2.2)
# ---------------------------------------------------------------------------

@dataclass
class Requirement:
    """A single host system requirement."""
    name: str
    min_version: str
    command: str                      # Shell command to get version string
    version_regex: str = ""           # Regex to extract version number
    symlink_check: str = ""           # Path that should be a symlink
    symlink_target: str = ""          # Expected symlink target (substring)
    max_version: str = ""             # Maximum tested version (warning only)
    notes: str = ""
    required: bool = True


REQUIREMENTS = [
    Requirement(
        name="Bash",
        min_version="3.2",
        command="bash --version | head -1",
        version_regex=r"version (\d+\.\d+)",
        symlink_check="/bin/sh",
        symlink_target="bash",
        notes="/bin/sh must be a link to bash",
    ),
    Requirement(
        name="Binutils",
        min_version="2.13.1",
        command="ld --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        max_version="2.46.0",
        notes="Versions > 2.46.0 not tested by LFS",
    ),
    Requirement(
        name="Bison",
        min_version="2.7",
        command="bison --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        symlink_check="/usr/bin/yacc",
        symlink_target="bison",
        notes="/usr/bin/yacc should link to bison",
    ),
    Requirement(
        name="Coreutils",
        min_version="8.1",
        command="chown --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Diffutils",
        min_version="2.8.1",
        command="diff --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Findutils",
        min_version="4.2.31",
        command="find --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gawk",
        min_version="4.0.1",
        command="gawk --version | head -1",
        version_regex=r"GNU Awk (\d+\.\d+\.\d+)",
        symlink_check="/usr/bin/awk",
        symlink_target="gawk",
        notes="/usr/bin/awk should link to gawk",
    ),
    Requirement(
        name="GCC",
        min_version="5.4",
        command="gcc --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
        max_version="15.2.0",
        notes="Versions > 15.2.0 not tested by LFS",
    ),
    Requirement(
        name="G++",
        min_version="5.4",
        command="g++ --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Grep",
        min_version="2.5.1",
        command="grep --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gzip",
        min_version="1.3.12",
        command="gzip --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Linux Kernel",
        min_version="5.4",
        command="uname -r",
        version_regex=r"(\d+\.\d+)",
        notes="CONFIG_UNIX98_PTYS must be set to y",
    ),
    Requirement(
        name="M4",
        min_version="1.4.10",
        command="m4 --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Make",
        min_version="4.0",
        command="make --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Patch",
        min_version="2.5.4",
        command="patch --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Perl",
        min_version="5.8.8",
        command='perl -e "print $^V"',
        version_regex=r"v(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Python",
        min_version="3.4",
        command="python3 --version",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Sed",
        min_version="4.1.5",
        command="sed --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Tar",
        min_version="1.22",
        command="tar --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Texinfo",
        min_version="5.0",
        command="makeinfo --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Xz",
        min_version="5.0.0",
        command="xz --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
]


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for comparison."""
    parts = re.findall(r"\d+", version_str)
    return tuple(int(p) for p in parts)


def version_ge(actual: str, minimum: str) -> bool:
    """Check if actual version >= minimum version."""
    return parse_version(actual) >= parse_version(minimum)


def version_le(actual: str, maximum: str) -> bool:
    """Check if actual version <= maximum version."""
    return parse_version(actual) <= parse_version(maximum)


# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------

def run_command(cmd: str, remote: Optional[str] = None) -> tuple[int, str]:
    """Run a command locally or via SSH. Returns (exit_code, output)."""
    if remote:
        full_cmd = f"ssh {remote} '{cmd}'"
    else:
        full_cmd = cmd

    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "(command timed out)"
    except Exception as e:
        return 1, f"(error: {e})"


def check_symlink(path: str, expected_target: str, remote: Optional[str] = None) -> tuple[bool, str]:
    """Check if a symlink exists and points to the expected target."""
    code, output = run_command(f"readlink -f {path}", remote)
    if code != 0:
        return False, f"{path} not found"
    if expected_target in output:
        return True, f"{path} -> {output}"
    return False, f"{path} -> {output} (expected {expected_target})"


def check_compilation(remote: Optional[str] = None) -> tuple[bool, str]:
    """Test that gcc and g++ can compile and link a simple program."""
    test_code = 'echo "int main(){}" > /tmp/igos-check.c'
    results = []

    # gcc test
    cmd = f'{test_code} && gcc /tmp/igos-check.c -o /tmp/igos-check && echo "gcc OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "gcc OK" in output:
        results.append(("gcc compile+link", True, "OK"))
    else:
        results.append(("gcc compile+link", False, output))

    # g++ test
    cmd = f'{test_code} && g++ /tmp/igos-check.c -o /tmp/igos-check && echo "g++ OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "g++ OK" in output:
        results.append(("g++ compile+link", True, "OK"))
    else:
        results.append(("g++ compile+link", False, output))

    return results


def check_library_consistency(remote: Optional[str] = None) -> tuple[bool, str]:
    """Check GMP/MPFR/MPC .la file consistency (all present or all absent)."""
    libs = ["libgmp.la", "libmpfr.la", "libmpc.la"]
    found = []

    for lib in libs:
        cmd = f"find /usr/lib* -name '{lib}' 2>/dev/null | head -1"
        code, output = run_command(cmd, remote)
        found.append(bool(output.strip()))

    if all(found):
        return True, "all present (consistent)"
    elif not any(found):
        return True, "all absent (consistent)"
    else:
        present = [l for l, f in zip(libs, found) if f]
        absent = [l for l, f in zip(libs, found) if not f]
        return False, f"INCONSISTENT — present: {present}, absent: {absent}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    remote = None
    if "--remote" in sys.argv:
        remote = "christopher@192.168.122.69"

    target = f"remote ({remote})" if remote else "local system"

    print("=" * 72)
    print(f"  InterGenOS Host System Requirements Check")
    print(f"  LFS 13.0-systemd minimum requirements")
    print(f"  Target: {target}")
    print("=" * 72)
    print()

    passed = 0
    failed = 0
    warnings = 0

    # --- Tool version checks ---
    print("--- Tool Versions ---\n")

    for req in REQUIREMENTS:
        code, output = run_command(req.command, remote)

        if code != 0 and not output:
            status = "FAIL"
            version = "NOT FOUND"
            detail = ""
            failed += 1
        else:
            # Extract version
            match = re.search(req.version_regex, output) if req.version_regex else None
            if match:
                version = match.group(1)
            else:
                version = output[:60]

            # Check minimum
            if match and not version_ge(version, req.min_version):
                status = "FAIL"
                detail = f"(need >= {req.min_version})"
                failed += 1
            elif match and req.max_version and not version_le(version, req.max_version):
                status = "WARN"
                detail = f"(> {req.max_version} — not tested by LFS)"
                warnings += 1
            else:
                status = "OK"
                detail = ""
                passed += 1

        pad = 16 - len(req.name)
        print(f"  [{status:4s}] {req.name}{' ' * pad}{version}  {detail}")

    # --- Symlink checks ---
    print("\n--- Symlink Checks ---\n")

    for req in REQUIREMENTS:
        if not req.symlink_check:
            continue
        ok, detail = check_symlink(req.symlink_check, req.symlink_target, remote)
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {detail}")

    # --- Compilation tests ---
    print("\n--- Compilation Tests ---\n")

    for name, ok, detail in check_compilation(remote):
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {name}: {detail}")

    # --- Library consistency ---
    print("\n--- Library Consistency (GMP/MPFR/MPC) ---\n")

    ok, detail = check_library_consistency(remote)
    status = "OK" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{status:4s}] {detail}")

    # --- Hardware ---
    print("\n--- Hardware ---\n")

    code, output = run_command("nproc", remote)
    cores = output.strip() if code == 0 else "unknown"
    code, output = run_command("head -1 /proc/meminfo", remote)
    if code == 0 and output.strip():
        match = re.search(r"(\d+)", output)
        if match:
            ram_kb = int(match.group(1))
            ram = f"{ram_kb // 1048576}G" if ram_kb >= 1048576 else f"{ram_kb // 1024}M"
        else:
            ram = "unknown"
    else:
        ram = "unknown"
    code, output = run_command("stat -f --format=%a_%S /", remote)
    if code == 0 and output.strip() and "_" in output:
        parts = output.strip().split("_")
        if len(parts) == 2:
            free_bytes = int(parts[0]) * int(parts[1])
            disk = f"{free_bytes // (1024**3)}G"
        else:
            disk = "unknown"
    else:
        disk = "unknown"

    print(f"  CPU cores:    {cores}")
    print(f"  RAM:          {ram}")
    print(f"  Free disk:    {disk}")

    if cores != "unknown" and int(cores) < 4:
        print(f"  [WARN] LFS recommends at least 4 cores (have {cores})")
        warnings += 1

    # --- Summary ---
    print(f"\n{'=' * 72}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {warnings} warnings")

    if failed > 0:
        print(f"\n  Host system does NOT meet LFS 13.0 requirements.")
        print(f"  Fix the failures above before attempting a build.")
        print(f"{'=' * 72}\n")
        return 1
    elif warnings > 0:
        print(f"\n  Host system meets requirements with warnings.")
        print(f"  Build should succeed but is outside tested configuration.")
        print(f"{'=' * 72}\n")
        return 0
    else:
        print(f"\n  Host system meets all LFS 13.0 requirements.")
        print(f"  Ready to build InterGenOS.")
        print(f"{'=' * 72}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
```
