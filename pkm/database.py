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
