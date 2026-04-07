# Code Review Request: InterGenOS Package Manager (pkm)

I'm requesting a thorough code review of the package manager for InterGenOS, a Linux distribution built entirely from source following Linux From Scratch 13.0.

pkm is a custom package manager designed to work with the InterGenOS build system. It installs packages from `.igos.tar.gz` archives, tracks installed files via a SQLite database combined with Slackware-style text manifests, and provides a natural-language CLI interface (e.g., `pkm install firefox`, `pkm files bash`, `pkm provides /usr/bin/gcc`).

Key capabilities include `--root` support for installing to alternate targets (used by the system installer), dependency resolution with reverse-dependency checking on removal, config file preservation during package removal, and SHA256 integrity verification.

I would appreciate your assessment of the following areas in particular:

1. **SQL injection** — Are database queries properly parameterized throughout? Are there any paths where user input reaches SQL unescaped?
2. **File operation safety** — Symlink attacks during extraction, path traversal in archive contents, TOCTOU races during install/remove
3. **Config file preservation** — The removal logic is supposed to skip `/etc` files modified by the user. Is this implemented correctly?
4. **Dependency resolution** — Does the dependency checker correctly prevent removal of packages that others depend on?
5. **Archive extraction security** — Tar slip vulnerabilities? Does it validate paths before extracting?
6. **General code quality** — Error handling, edge cases, maintainability

The complete source follows. There are 7 files totaling approximately 1,050 lines of Python.

---

## Source Code

### __init__.py
```python
"""pkm — InterGenOS Package Manager

A natural-language CLI for installing, removing, querying, and managing
packages on a running InterGenOS system. Uses SQLite for fast queries
while maintaining human-readable text manifests for transparency.

Usage:
    pkm install <pkg>           Install a package from archive
    pkm remove <pkg>            Remove a package (checks dependencies)
    pkm list installed          List installed packages
    pkm search <term>           Search packages by name/description
    pkm info <pkg>              Show package details
    pkm files <pkg>             List files in a package
    pkm provides <file>         Find which package owns a file
    pkm verify <pkg>            Verify package integrity
    pkm depends <pkg>           Show dependency tree
    pkm history                 Show operation history
"""

__version__ = "0.1.0"
```

### __main__.py
```python
"""Entry point for pkm: python3 -m pkm"""
from .cli import main
main()
```

### cli.py
```python
"""pkm CLI — Natural-language command interface for InterGenOS package management."""

import argparse
import sys

from . import __version__
from .database import PackageDB
from .installer import PackageInstaller
from .remover import PackageRemover
from .verifier import PackageVerifier


def main():
    parser = argparse.ArgumentParser(
        prog="pkm",
        description="InterGenOS Package Manager",
    )
    parser.add_argument("--version", action="version", version=f"pkm {__version__}")
    parser.add_argument("--db", help="Database path override")

    sub = parser.add_subparsers(dest="command", metavar="command")

    # -- install --
    p_install = sub.add_parser("install", help="Install a package")
    p_install.add_argument("packages", nargs="+", metavar="package")
    p_install.add_argument("--archive", help="Path to .igos.tar.gz archive")

    # -- remove --
    p_remove = sub.add_parser("remove", help="Remove a package")
    p_remove.add_argument("package")
    p_remove.add_argument("--force", action="store_true", help="Remove even if others depend on it")

    # -- list --
    p_list = sub.add_parser("list", help="List packages")
    p_list.add_argument("what", choices=["installed", "available", "upgradable"], nargs="?", default="installed")
    p_list.add_argument("--tier", help="Filter by tier")

    # -- search --
    p_search = sub.add_parser("search", help="Search packages")
    p_search.add_argument("term")

    # -- info --
    p_info = sub.add_parser("info", help="Show package details")
    p_info.add_argument("package")

    # -- files --
    p_files = sub.add_parser("files", help="List files in a package")
    p_files.add_argument("package")

    # -- provides --
    p_provides = sub.add_parser("provides", help="Find which package owns a file")
    p_provides.add_argument("file")

    # -- verify --
    p_verify = sub.add_parser("verify", help="Verify package integrity")
    p_verify.add_argument("package", nargs="?")
    p_verify.add_argument("--all", action="store_true", dest="verify_all")

    # -- depends --
    p_depends = sub.add_parser("depends", help="Show dependencies")
    p_depends.add_argument("package")
    p_depends.add_argument("--reverse", action="store_true", help="Show reverse dependencies")

    # -- history --
    p_history = sub.add_parser("history", help="Show operation history")
    p_history.add_argument("package", nargs="?")

    # -- import --
    p_import = sub.add_parser("import", help="Import existing text manifests into database")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    db = PackageDB(args.db)

    try:
        if args.command == "install":
            cmd_install(db, args)
        elif args.command == "remove":
            cmd_remove(db, args)
        elif args.command == "list":
            cmd_list(db, args)
        elif args.command == "search":
            cmd_search(db, args)
        elif args.command == "info":
            cmd_info(db, args)
        elif args.command == "files":
            cmd_files(db, args)
        elif args.command == "provides":
            cmd_provides(db, args)
        elif args.command == "verify":
            cmd_verify(db, args)
        elif args.command == "depends":
            cmd_depends(db, args)
        elif args.command == "history":
            cmd_history(db, args)
        elif args.command == "import":
            cmd_import(db, args)
    finally:
        db.close()


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def cmd_install(db, args):
    installer = PackageInstaller(db)
    for pkg_name in args.packages:
        archive = args.archive if len(args.packages) == 1 else None
        ok, msg = installer.install(pkg_name, archive_path=archive)
        if ok:
            print(f"  {msg}")
        else:
            print(f"  ERROR: {msg}", file=sys.stderr)
            sys.exit(1)


def cmd_remove(db, args):
    remover = PackageRemover(db)
    ok, msg = remover.remove(args.package, force=args.force)
    if ok:
        print(f"  {msg}")
    else:
        print(f"  ERROR: {msg}", file=sys.stderr)
        sys.exit(1)


def cmd_list(db, args):
    if args.what == "installed":
        packages = db.list_installed(tier=args.tier)
        if not packages:
            print("  No packages installed" + (f" in tier '{args.tier}'" if args.tier else ""))
            return
        print(f"  Installed packages ({len(packages)}):")
        for pkg in packages:
            tier = f" [{pkg['tier']}]" if pkg["tier"] else ""
            desc = f" — {pkg['description'][:50]}" if pkg.get("description") else ""
            print(f"    {pkg['name']:30s} {pkg['version']:15s}{tier}{desc}")
    elif args.what == "available":
        print("  'pkm list available' requires 'pkm update' (Phase 2 — not yet implemented)")
    elif args.what == "upgradable":
        print("  'pkm list upgradable' requires 'pkm update' (Phase 2 — not yet implemented)")


def cmd_search(db, args):
    results = db.search(args.term)
    if not results:
        print(f"  No packages matching '{args.term}'")
        return
    print(f"  Search results for '{args.term}' ({len(results)} matches):")
    for pkg in results:
        tier = f" [{pkg['tier']}]" if pkg["tier"] else ""
        desc = f" — {pkg['description'][:50]}" if pkg.get("description") else ""
        print(f"    {pkg['name']:30s} {pkg['version']:15s}{tier}{desc}")


def cmd_info(db, args):
    pkg = db.get_installed(args.package)
    if not pkg:
        print(f"  Package '{args.package}' is not installed")
        return

    print(f"  {'=' * 50}")
    print(f"  {pkg['name']} {pkg['version']}")
    print(f"  {'=' * 50}")
    for key in ["tier", "description", "license", "build_date", "install_date",
                 "install_method", "uncompressed_size"]:
        val = pkg.get(key)
        if val:
            if key == "uncompressed_size" and isinstance(val, int) and val > 0:
                val = f"{val / 1024 / 1024:.1f} MB" if val > 1024*1024 else f"{val / 1024:.0f} KB"
            print(f"  {key:20s}: {val}")

    deps = db.get_depends(args.package)
    if deps:
        print(f"\n  Dependencies ({len(deps)}):")
        for d in deps:
            print(f"    [{d['type']:8s}] {d['name']}")

    rdeps = db.get_reverse_depends(args.package)
    if rdeps:
        print(f"\n  Required by ({len(rdeps)}):")
        for d in rdeps:
            print(f"    {d['name']} {d['version']}")

    files = db.get_files(args.package)
    file_count = len([f for f in files if not f["is_dir"]])
    print(f"\n  Files: {file_count}")
    print()


def cmd_files(db, args):
    files = db.get_files(args.package)
    if not files:
        print(f"  Package '{args.package}' not found or has no tracked files")
        return
    print(f"  Files in {args.package} ({len(files)}):")
    for f in files:
        prefix = "d " if f["is_dir"] else "  "
        print(f"  {prefix}/{f['path']}")


def cmd_provides(db, args):
    result = db.find_owner(args.file)
    if result:
        print(f"  /{result['path']} is owned by {result['name']} {result['version']}")
    else:
        print(f"  No package owns '{args.file}'")


def cmd_verify(db, args):
    verifier = PackageVerifier(db)

    if args.verify_all or not args.package:
        if not args.verify_all and not args.package:
            print("  Usage: pkm verify <package> or pkm verify --all")
            return
        results = verifier.verify_all()
        ok_count = 0
        problem_count = 0
        for name, version, result in results:
            if result["missing"] or result["modified"]:
                problem_count += 1
                print(f"  PROBLEM: {name} {version} — {len(result['missing'])} missing, {len(result['modified'])} modified")
            else:
                ok_count += 1
        print(f"\n  Verified: {ok_count} OK, {problem_count} with issues")
    else:
        result = verifier.verify(args.package)
        if result is None:
            print(f"  Package '{args.package}' is not installed")
            return
        if not result["missing"] and not result["modified"]:
            print(f"  {args.package}: OK ({result['total']} files verified)")
        else:
            if result["missing"]:
                print(f"  MISSING ({len(result['missing'])}):")
                for f in result["missing"][:20]:
                    print(f"    /{f}")
                if len(result["missing"]) > 20:
                    print(f"    ... and {len(result['missing']) - 20} more")
            if result["modified"]:
                print(f"  MODIFIED ({len(result['modified'])}):")
                for f in result["modified"][:20]:
                    print(f"    /{f}")


def cmd_depends(db, args):
    if args.reverse:
        rdeps = db.get_reverse_depends(args.package)
        if not rdeps:
            print(f"  No packages depend on '{args.package}'")
            return
        print(f"  Packages that depend on {args.package} ({len(rdeps)}):")
        for d in rdeps:
            print(f"    {d['name']} {d['version']} ({d['type']})")
    else:
        deps = db.get_depends(args.package)
        if not deps:
            pkg = db.get_installed(args.package)
            if not pkg:
                print(f"  Package '{args.package}' is not installed")
            else:
                print(f"  {args.package} has no tracked dependencies")
            return
        print(f"  Dependencies of {args.package} ({len(deps)}):")
        for d in deps:
            installed = db.get_installed(d["name"])
            status = f" [installed: {installed['version']}]" if installed else " [not installed]"
            print(f"    [{d['type']:8s}] {d['name']}{status}")


def cmd_history(db, args):
    entries = db.get_history(package_name=args.package)
    if not entries:
        print("  No history recorded")
        return
    print(f"  Package history ({len(entries)} entries):")
    for e in entries:
        status = "OK" if e["success"] else "FAILED"
        ver = ""
        if e["old_version"] and e["new_version"]:
            ver = f" {e['old_version']} → {e['new_version']}"
        elif e["new_version"]:
            ver = f" {e['new_version']}"
        elif e["old_version"]:
            ver = f" {e['old_version']}"
        method = f" ({e['method']})" if e["method"] else ""
        print(f"    {e['timestamp'][:19]}  {e['operation']:10s} {e['package_name']}{ver}{method} [{status}]")


def cmd_import(db, args):
    print("  Importing existing text manifests...")
    count = db.import_manifests()
    print(f"  Imported {count} package(s) into pkm database")


if __name__ == "__main__":
    main()
```

### database.py
```python
"""pkm database layer — SQLite operations for package metadata.

Handles the hybrid approach: SQLite as primary database for speed,
text manifests generated alongside for human inspection.
"""

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/var/lib/igos/pkm.db")
MANIFEST_DIR = Path("/var/lib/igos/packages")
ARCHIVE_DIR = Path("/var/lib/igos/archives")

SCHEMA = """
CREATE TABLE IF NOT EXISTS installed (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,
    description TEXT,
    license TEXT,
    build_date TEXT,
    install_date TEXT,
    install_method TEXT,
    archive_path TEXT,
    uncompressed_size INTEGER,
    compressed_size INTEGER,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    is_dir BOOLEAN DEFAULT 0,
    is_config BOOLEAN DEFAULT 0,
    checksum TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_package ON files(package_id);

CREATE TABLE IF NOT EXISTS depends (
    id INTEGER PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
    dep_name TEXT NOT NULL,
    dep_type TEXT NOT NULL,
    UNIQUE(package_id, dep_name, dep_type)
);

CREATE TABLE IF NOT EXISTS available (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    release INTEGER DEFAULT 1,
    tier TEXT,
    description TEXT,
    archive_url TEXT,
    source_url TEXT,
    checksum TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL,
    package_name TEXT NOT NULL,
    old_version TEXT,
    new_version TEXT,
    method TEXT,
    success BOOLEAN
);

CREATE TABLE IF NOT EXISTS config_files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    package_id INTEGER REFERENCES installed(id) ON DELETE SET NULL,
    original_checksum TEXT
);
"""


class PackageDB:
    """Package database interface."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------------
    # Installed packages
    # ------------------------------------------------------------------

    def get_installed(self, name):
        """Get an installed package by name. Returns dict or None."""
        row = self.conn.execute(
            "SELECT * FROM installed WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.execute("SELECT * FROM installed LIMIT 0").description]
        return dict(zip(cols, row))

    def list_installed(self, tier=None):
        """List all installed packages. Optionally filter by tier."""
        if tier:
            rows = self.conn.execute(
                "SELECT name, version, tier, description FROM installed WHERE tier = ? ORDER BY name",
                (tier,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT name, version, tier, description FROM installed ORDER BY name"
            ).fetchall()
        return [{"name": r[0], "version": r[1], "tier": r[2], "description": r[3]} for r in rows]

    def add_installed(self, name, version, release=1, tier=None, description=None,
                      license_=None, build_date=None, install_method="archive",
                      archive_path=None, uncompressed_size=0, compressed_size=0):
        """Register a package as installed."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO installed
               (name, version, release, tier, description, license,
                build_date, install_date, install_method, archive_path,
                uncompressed_size, compressed_size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, version, release, tier, description, license_,
             build_date, now, install_method, archive_path,
             uncompressed_size, compressed_size)
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def remove_installed(self, name):
        """Remove an installed package record and its files."""
        pkg = self.get_installed(name)
        if pkg:
            self.conn.execute("DELETE FROM files WHERE package_id = ?", (pkg["id"],))
            self.conn.execute("DELETE FROM depends WHERE package_id = ?", (pkg["id"],))
            self.conn.execute("DELETE FROM installed WHERE id = ?", (pkg["id"],))
            self.conn.commit()
        return pkg

    # ------------------------------------------------------------------
    # File ownership
    # ------------------------------------------------------------------

    def add_files(self, package_id, file_list):
        """Register files owned by a package.

        file_list: list of relative paths (e.g., "usr/bin/bash")
        """
        for path in file_list:
            is_dir = path.endswith("/")
            is_config = path.startswith("etc/") and not is_dir
            checksum = None
            if not is_dir and not is_config:
                abs_path = "/" + path
                if os.path.isfile(abs_path):
                    try:
                        checksum = _sha256(abs_path)
                    except (OSError, PermissionError):
                        pass
            try:
                self.conn.execute(
                    """INSERT OR REPLACE INTO files
                       (package_id, path, is_dir, is_config, checksum)
                       VALUES (?, ?, ?, ?, ?)""",
                    (package_id, path.rstrip("/"), is_dir, is_config, checksum)
                )
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()

        # Track config files separately for protection
        config_paths = [p for p in file_list if p.startswith("etc/") and not p.endswith("/")]
        for cp in config_paths:
            abs_path = "/" + cp
            checksum = _sha256(abs_path) if os.path.isfile(abs_path) else None
            self.conn.execute(
                "INSERT OR REPLACE INTO config_files (path, package_id, original_checksum) VALUES (?, ?, ?)",
                (cp, package_id, checksum)
            )
        self.conn.commit()

    def get_files(self, name):
        """Get all files owned by a package."""
        pkg = self.get_installed(name)
        if not pkg:
            return []
        rows = self.conn.execute(
            "SELECT path, is_dir FROM files WHERE package_id = ? ORDER BY path",
            (pkg["id"],)
        ).fetchall()
        return [{"path": r[0], "is_dir": bool(r[1])} for r in rows]

    def find_owner(self, filepath):
        """Find which package owns a file path."""
        # Normalize: strip leading /
        path = filepath.lstrip("/")
        row = self.conn.execute(
            """SELECT i.name, i.version, f.path
               FROM files f JOIN installed i ON f.package_id = i.id
               WHERE f.path = ?""",
            (path,)
        ).fetchone()
        if row:
            return {"name": row[0], "version": row[1], "path": row[2]}
        return None

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def add_depends(self, package_id, deps):
        """Add dependency records. deps: list of (dep_name, dep_type)."""
        for dep_name, dep_type in deps:
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO depends (package_id, dep_name, dep_type) VALUES (?, ?, ?)",
                    (package_id, dep_name, dep_type)
                )
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()

    def get_depends(self, name):
        """Get dependencies for a package."""
        pkg = self.get_installed(name)
        if not pkg:
            return []
        rows = self.conn.execute(
            "SELECT dep_name, dep_type FROM depends WHERE package_id = ? ORDER BY dep_type, dep_name",
            (pkg["id"],)
        ).fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]

    def get_reverse_depends(self, name):
        """Get packages that depend on this package."""
        rows = self.conn.execute(
            """SELECT i.name, i.version, d.dep_type
               FROM depends d JOIN installed i ON d.package_id = i.id
               WHERE d.dep_name = ?
               ORDER BY i.name""",
            (name,)
        ).fetchall()
        return [{"name": r[0], "version": r[1], "type": r[2]} for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, term):
        """Search installed packages by name or description."""
        rows = self.conn.execute(
            """SELECT name, version, tier, description FROM installed
               WHERE name LIKE ? OR description LIKE ?
               ORDER BY name""",
            (f"%{term}%", f"%{term}%")
        ).fetchall()
        return [{"name": r[0], "version": r[1], "tier": r[2], "description": r[3]} for r in rows]

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def log_operation(self, operation, package_name, old_version=None,
                      new_version=None, method=None, success=True):
        """Log a package operation."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO history
               (timestamp, operation, package_name, old_version, new_version, method, success)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, operation, package_name, old_version, new_version, method, success)
        )
        self.conn.commit()

    def get_history(self, package_name=None, limit=50):
        """Get operation history."""
        if package_name:
            rows = self.conn.execute(
                "SELECT * FROM history WHERE package_name = ? ORDER BY timestamp DESC LIMIT ?",
                (package_name, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM history ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = ["id", "timestamp", "operation", "package_name",
                "old_version", "new_version", "method", "success"]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------
    # Migration — import existing text manifests
    # ------------------------------------------------------------------

    def import_manifests(self, manifest_dir=None):
        """Import existing text manifests into SQLite.

        Reads /var/lib/igos/packages/* and populates the installed and files tables.
        """
        mdir = Path(manifest_dir) if manifest_dir else MANIFEST_DIR
        if not mdir.exists():
            return 0

        imported = 0
        for manifest_file in sorted(mdir.iterdir()):
            if not manifest_file.is_file():
                continue

            content = manifest_file.read_text()
            meta = _parse_manifest(content)
            if not meta:
                continue

            # Skip if already in DB
            existing = self.get_installed(meta["name"])
            if existing:
                continue

            pkg_id = self.add_installed(
                name=meta["name"],
                version=meta["version"],
                description=meta.get("description", ""),
                build_date=meta.get("build_date"),
                install_method="source",
                uncompressed_size=meta.get("size", 0),
            )

            if meta.get("files"):
                self.add_files(pkg_id, meta["files"])

            imported += 1

        return imported

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify_package(self, name):
        """Verify all files for a package exist on the filesystem.

        Returns: (total_files, missing_files, modified_files)
        """
        pkg = self.get_installed(name)
        if not pkg:
            return None

        rows = self.conn.execute(
            "SELECT path, is_dir, checksum FROM files WHERE package_id = ? AND is_dir = 0",
            (pkg["id"],)
        ).fetchall()

        total = len(rows)
        missing = []
        modified = []

        for path, is_dir, expected_checksum in rows:
            abs_path = "/" + path
            if not os.path.lexists(abs_path):
                missing.append(path)
            elif expected_checksum:
                try:
                    actual = _sha256(abs_path)
                    if actual != expected_checksum:
                        modified.append(path)
                except (OSError, PermissionError):
                    pass

        return {"total": total, "missing": missing, "modified": modified}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sha256(filepath):
    """Compute SHA256 of a file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _parse_manifest(content):
    """Parse a text manifest into a dict."""
    meta = {}
    files = []
    in_files = False

    for line in content.splitlines():
        if line.startswith("PACKAGE NAME:"):
            # Parse "name-version" format
            full = line.split(":", 1)[1].strip()
            # Split on last hyphen before version number
            match = re.match(r'^(.+?)-(\d.*)$', full)
            if match:
                meta["name"] = match.group(1)
                meta["version"] = match.group(2)
        elif line.startswith("PACKAGE VERSION:"):
            meta["version"] = line.split(":", 1)[1].strip()
        elif line.startswith("UNCOMPRESSED SIZE:"):
            size_str = line.split(":", 1)[1].strip()
            # Extract bytes from "12.5M (13107200 bytes)"
            m = re.search(r'\((\d+) bytes\)', size_str)
            if m:
                meta["size"] = int(m.group(1))
        elif line.startswith("BUILD DATE:"):
            meta["build_date"] = line.split(":", 1)[1].strip()
        elif line.startswith("DESCRIPTION:"):
            pass  # Next line has the description
        elif ":" in line and not in_files and line.strip().startswith(meta.get("name", "\x00")):
            # Description line: "bash: The GNU Bourne Again shell"
            meta["description"] = line.split(":", 1)[1].strip()
        elif line.strip() == "FILE LIST:":
            in_files = True
        elif in_files and line.strip():
            files.append(line.strip())

    meta["files"] = files
    return meta if "name" in meta else None
```

### installer.py
```python
"""pkm installer — Archive extraction and file deployment."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .database import PackageDB, ARCHIVE_DIR, MANIFEST_DIR, _sha256


class PackageInstaller:
    """Install packages from pre-built archives."""

    def __init__(self, db: PackageDB, root="/"):
        self.db = db
        self.root = Path(root)

    def install(self, name, archive_path=None):
        """Install a package from its .igos.tar.gz archive.

        Args:
            name: Package name
            archive_path: Path to archive, or None to search ARCHIVE_DIR

        Returns:
            (success: bool, message: str)
        """
        # Check if already installed
        existing = self.db.get_installed(name)
        if existing:
            return False, f"{name} {existing['version']} is already installed. Use 'pkm reinstall' to replace."

        # Find archive
        if not archive_path:
            archive_path = self._find_archive(name)
        if not archive_path:
            return False, f"No archive found for '{name}' in {ARCHIVE_DIR}"

        archive_path = Path(archive_path)
        if not archive_path.exists():
            return False, f"Archive not found: {archive_path}"

        # Extract to temp staging area for inspection
        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # Extract archive
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(staging)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Failed to extract archive: {result.stderr}"

            # Collect file list
            file_list = []
            for root, dirs, files in os.walk(staging):
                for d in sorted(dirs):
                    rel = os.path.relpath(os.path.join(root, d), staging)
                    if not os.path.islink(os.path.join(root, d)):
                        file_list.append(rel + "/")
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(root, f), staging)
                    file_list.append(rel)

            # Safety check: don't clobber root symlinks
            dangerous = []
            for entry in ("lib", "lib64", "bin", "sbin"):
                staged = staging / entry
                root_path = self.root / entry
                if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                    dangerous.append(entry)
            if dangerous:
                return False, (
                    f"DANGEROUS: Archive contains top-level dirs that would "
                    f"collide with root symlinks: {' '.join(dangerous)}"
                )

            # Deploy to target filesystem
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(self.root),
                 "--no-overwrite-dir", "--keep-directory-symlink"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Failed to deploy: {result.stderr}"

            # Parse version from archive name
            version = self._version_from_archive(name, archive_path.name)

            # Register in database
            pkg_id = self.db.add_installed(
                name=name,
                version=version,
                install_method="archive",
                archive_path=str(archive_path),
            )
            self.db.add_files(pkg_id, file_list)
            self.db.log_operation("install", name, new_version=version, method="archive")

            # Generate text manifest for transparency
            self._write_manifest(name, version, file_list)

            file_count = len([f for f in file_list if not f.endswith("/")])
            return True, f"Installed {name} {version} ({file_count} files)"

        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _find_archive(self, name):
        """Search ARCHIVE_DIR for an archive matching the package name."""
        if not ARCHIVE_DIR.exists():
            return None
        for f in sorted(ARCHIVE_DIR.iterdir(), reverse=True):
            if f.name.startswith(f"{name}-") and f.name.endswith(".igos.tar.gz"):
                return f
        return None

    def _version_from_archive(self, name, archive_name):
        """Extract version from archive filename like 'bash-5.2.37.igos.tar.gz'."""
        stem = archive_name.replace(".igos.tar.gz", "")
        if stem.startswith(f"{name}-"):
            return stem[len(f"{name}-"):]
        return "unknown"

    def _write_manifest(self, name, version, file_list):
        """Write a text manifest alongside the SQLite entry."""
        manifest_dir = self.root / "var" / "lib" / "igos" / "packages"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{name}-{version}"

        total_size = sum(
            os.path.getsize(str(self.root / f)) for f in file_list
            if not f.endswith("/") and os.path.isfile(str(self.root / f))
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        content = (
            f"PACKAGE NAME: {name}-{version}\n"
            f"PACKAGE VERSION: {version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS pkm\n"
            f"DESCRIPTION:\n"
            f"{name}: (installed via pkm)\n"
            f"\n"
            f"FILE LIST:\n"
        )
        content += "\n".join(file_list) + "\n"
        manifest_path.write_text(content)
```

### remover.py
```python
"""pkm remover — Safe package removal with dependency checking."""

import os
import shutil
from pathlib import Path

from .database import PackageDB, MANIFEST_DIR, _sha256


class PackageRemover:
    """Remove packages safely, respecting dependencies and config files."""

    def __init__(self, db: PackageDB):
        self.db = db

    def remove(self, name, force=False):
        """Remove an installed package.

        Checks reverse dependencies unless force=True.
        Preserves modified config files.

        Returns:
            (success: bool, message: str)
        """
        pkg = self.db.get_installed(name)
        if not pkg:
            return False, f"Package '{name}' is not installed"

        # Check reverse dependencies
        if not force:
            rdeps = self.db.get_reverse_depends(name)
            if rdeps:
                dep_list = ", ".join(f"{d['name']}" for d in rdeps)
                return False, (
                    f"Cannot remove {name}: {len(rdeps)} package(s) depend on it: {dep_list}\n"
                    f"  Use 'pkm remove {name} --force' to remove anyway."
                )

        # Get file list
        files = self.db.get_files(name)
        if not files:
            # No files tracked — just remove the DB entry
            self.db.remove_installed(name)
            self.db.log_operation("remove", name, old_version=pkg["version"])
            return True, f"Removed {name} {pkg['version']} (no files tracked)"

        # Sort files in reverse order (deepest first) for clean removal
        file_paths = sorted(
            [f for f in files if not f["is_dir"]],
            key=lambda f: f["path"],
            reverse=True
        )
        dir_paths = sorted(
            [f for f in files if f["is_dir"]],
            key=lambda f: f["path"],
            reverse=True
        )

        removed_count = 0
        preserved_configs = []

        # Remove files (not directories yet)
        for f in file_paths:
            abs_path = "/" + f["path"]

            # Config file protection
            if f["path"].startswith("etc/"):
                if os.path.isfile(abs_path):
                    # Check if user modified it
                    config = self.db.conn.execute(
                        "SELECT original_checksum FROM config_files WHERE path = ?",
                        (f["path"],)
                    ).fetchone()
                    if config and config[0]:
                        try:
                            current = _sha256(abs_path)
                            if current != config[0]:
                                # User modified — preserve it
                                preserved_configs.append(f["path"])
                                continue
                        except (OSError, PermissionError):
                            pass

            # Remove the file
            try:
                if os.path.lexists(abs_path):
                    os.remove(abs_path)
                    removed_count += 1
            except (OSError, PermissionError) as e:
                pass  # Best effort — don't fail the whole removal

        # Remove empty directories (only if they're empty after file removal)
        for d in dir_paths:
            abs_path = "/" + d["path"]
            try:
                if os.path.isdir(abs_path) and not os.listdir(abs_path):
                    os.rmdir(abs_path)
            except (OSError, PermissionError):
                pass  # Directory not empty or permission denied — leave it

        # Remove manifest file
        manifest = MANIFEST_DIR / f"{name}-{pkg['version']}"
        if manifest.exists():
            manifest.unlink()

        # Remove from database
        self.db.remove_installed(name)
        self.db.log_operation("remove", name, old_version=pkg["version"])

        msg = f"Removed {name} {pkg['version']} ({removed_count} files)"
        if preserved_configs:
            msg += f"\n  Preserved {len(preserved_configs)} modified config file(s):"
            for cf in preserved_configs:
                msg += f"\n    /{cf}"

        return True, msg
```

### verifier.py
```python
"""pkm verifier — Package integrity checking."""

from .database import PackageDB


class PackageVerifier:
    """Verify package integrity against the database."""

    def __init__(self, db: PackageDB):
        self.db = db

    def verify(self, name):
        """Verify a single package. Returns result dict or None if not found."""
        result = self.db.verify_package(name)
        if result is None:
            return None
        return result

    def verify_all(self):
        """Verify all installed packages.

        Returns: list of (name, version, result_dict)
        """
        results = []
        for pkg in self.db.list_installed():
            result = self.db.verify_package(pkg["name"])
            if result:
                results.append((pkg["name"], pkg["version"], result))
        return results
```
