"""pkm database layer — SQLite operations for package metadata.

Handles the hybrid approach: SQLite as primary database for speed,
text manifests generated alongside for human inspection.
"""

import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("IGOS_PKM_DB", "/var/lib/igos/pkm.db"))
MANIFEST_DIR = Path("/var/lib/igos/packages")
ARCHIVE_DIR = Path("/var/lib/igos/archives")


# Regex for sha256 suffix in manifest FILE LIST entries (RFC v1, 2026-05-01).
# Anchored at end-of-line with a leading space + literal "sha256:" + 64 hex
# chars. This handles paths containing whitespace correctly (e.g.,
# linux-firmware files like "brcmfmac43455-sdio.Raspberry Pi Foundation-...txt.xz")
# where naive whitespace-split parsers truncate the path.
_SHA256_SUFFIX_RE = re.compile(r' sha256:([0-9a-f]{64})$')


def _parse_manifest_line(line):
    """Parse a manifest FILE LIST entry into (path, sha256_or_none).

    Manifest format (RFC v1, 2026-05-01):
      "path/"           → directory entry, no hash
      "path"            → file entry without hash (typically symlink)
      "path sha256:HEX" → file entry with sha256 hash (HEX is 64 hex chars)

    Paths may contain whitespace; anchoring the hash suffix at end-of-line
    via regex is correct, splitting on first whitespace is not.
    """
    line = line.rstrip("\n")
    m = _SHA256_SUFFIX_RE.search(line)
    if m:
        return line[:m.start()], m.group(1)
    return line, None

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
    superseded_by TEXT,
    superseded_at TEXT,
    -- Q9 (O-026 + O-015):
    --   held: 1 → package excluded from `pkm upgrade` without explicit
    --   --ignore-holds flag (emergency security override).
    --   install_reason: 'manual' (user explicitly invoked `pkm install <name>`)
    --   or 'dependency' (pulled in by another package's runtime deps).
    --   `pkm autoremove` removes only install_reason='dependency' packages
    --   that have no reverse-deps.
    held INTEGER DEFAULT 0,
    install_reason TEXT DEFAULT 'manual',
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

    def __init__(self, db_path=None, root="/"):
        # NOTE: manifest paths are stored POSIX-relative ("usr/bin/bash",
        # not "/usr/bin/bash"). Do not pass leading-slash paths through
        # self.root / path constructions — pathlib's absolute-right-operand
        # rule silently drops self.root, breaking install-target scenarios.
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.root = Path(root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate_supersedes_columns()
        self._migrate_q9_columns()

    def _migrate_supersedes_columns(self):
        """Idempotent migration: add superseded_by + superseded_at columns
        to pre-existing `installed` tables that predate the supersedes RFC."""
        for col in ("superseded_by", "superseded_at"):
            try:
                self.conn.execute(f"ALTER TABLE installed ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        self.conn.commit()

    def _migrate_q9_columns(self):
        """Idempotent migration: add Q9 hold + install_reason columns to
        pre-existing `installed` tables. Q9 (O-026 + O-015) adds hold/pin
        and autoremove primitives.

        Defaults match the SCHEMA: held=0 (not held), install_reason=
        'manual' (existing packages are treated as user-installed for
        autoremove safety — autoremove only touches 'dependency' rows).
        """
        migrations = [
            ("held", "INTEGER DEFAULT 0"),
            ("install_reason", "TEXT DEFAULT 'manual'"),
        ]
        for col, decl in migrations:
            try:
                self.conn.execute(
                    f"ALTER TABLE installed ADD COLUMN {col} {decl}"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

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
        """List all installed packages. Optionally filter by tier.

        Returns dicts with name/version/release/tier/description. release
        is included so callers doing version-aware compare (see pkm.version)
        can disambiguate same-version-different-release upgrades without
        an extra get_installed roundtrip per package.
        """
        if tier:
            rows = self.conn.execute(
                "SELECT name, version, release, tier, description, install_reason "
                "FROM installed WHERE tier = ? ORDER BY name",
                (tier,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT name, version, release, tier, description, install_reason "
                "FROM installed ORDER BY name"
            ).fetchall()
        return [
            {"name": r[0], "version": r[1], "release": r[2], "tier": r[3],
             "description": r[4], "install_reason": r[5] or "manual"}
            for r in rows
        ]

    def add_installed(self, name, version, release=1, tier=None, description=None,
                      license_=None, build_date=None, install_method="archive",
                      archive_path=None, uncompressed_size=0, compressed_size=0,
                      install_reason="manual", commit=True):
        """Register a package as installed.

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic supersede in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        install_reason: 'manual' (user-requested) or 'dependency'
        (dep-resolution-pulled). Default 'manual' — explicit dep installs
        must pass install_reason='dependency'. Q9 autoremove only touches
        'dependency' rows that have zero reverse-deps.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT OR REPLACE INTO installed
               (name, version, release, tier, description, license,
                build_date, install_date, install_method, archive_path,
                uncompressed_size, compressed_size, install_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, version, release, tier, description, license_,
             build_date, now, install_method, archive_path,
             uncompressed_size, compressed_size, install_reason)
        )
        if commit:
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

    def add_files(self, package_id, file_list, hashes=None, commit=True):
        """Register files owned by a package.

        file_list: list of relative paths (e.g., "usr/bin/bash")
        hashes: optional dict mapping path → sha256 hex. When provided, used
                as the authoritative checksum (e.g., from the package's
                manifest); otherwise, computed from the live filesystem
                if the file exists at install time.
        commit: when True (default), commit immediately. Set to False when
                called inside an outer transaction.
        """
        for path in file_list:
            is_dir = path.endswith("/")
            is_config = path.startswith("etc/") and not is_dir
            checksum = (hashes or {}).get(path)
            if not is_dir and not is_config and checksum is None:
                abs_path = str(self.root / path)
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
                print(f"  WARNING: integrity error on file entry {path}", file=sys.stderr)
        if commit:
            self.conn.commit()

        # Track config files separately for protection. The Q4 (O-021) fix:
        # original_checksum must be recorded ONCE at first install of the
        # config path and never ratcheted forward by subsequent re-registrations
        # (which would silently re-baseline the user-edited-detection check
        # against new stock — defeating preservation across upgrades).
        #
        # On INSERT (first install): all three values are recorded.
        # On CONFLICT (re-register during upgrade): only package_id updates;
        # original_checksum is preserved from the first install. The upgrade
        # orchestration explicitly ratchets the baseline forward via
        # update_original_checksum(path, new_sha) for files the user has not
        # edited (see pkm.configprotect.ratchet_baselines).
        config_paths = [p for p in file_list if p.startswith("etc/") and not p.endswith("/")]
        for cp in config_paths:
            abs_path = "/" + cp
            checksum = (hashes or {}).get(cp)
            if checksum is None and os.path.isfile(abs_path):
                checksum = _sha256(abs_path)
            self.conn.execute(
                """INSERT INTO config_files (path, package_id, original_checksum)
                   VALUES (?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET package_id = excluded.package_id""",
                (cp, package_id, checksum)
            )
        if commit:
            self.conn.commit()

    # ------------------------------------------------------------------
    # Config-file baseline tracking (Q4 .pkmnew sidecar support)
    # ------------------------------------------------------------------

    def get_original_checksum(self, path):
        """Return the recorded original_checksum for a config-file path.

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf")

        Returns:
            sha256 hex string of the stock content as last recorded baseline,
            or None if the path is not tracked as a config file.
        """
        row = self.conn.execute(
            "SELECT original_checksum FROM config_files WHERE path = ?",
            (path.lstrip("/"),)
        ).fetchone()
        return row[0] if row else None

    def update_original_checksum(self, path, new_checksum, commit=True):
        """Update the recorded original_checksum for a config-file path.

        Called by the upgrade orchestration after verifying the live file's
        pre-upgrade sha256 matched the recorded baseline (i.e., user did not
        edit). The upgrade replaces the live file with new stock, and this
        records the new stock's sha256 as the baseline going forward.

        For user-edited files, the upgrade orchestration writes a .pkmnew
        sidecar instead of replacing the live file, and the recorded
        baseline is NOT updated (so subsequent upgrades continue to detect
        the user's edits).

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf")
            new_checksum: sha256 hex string of the new stock content
            commit: when True (default), commit immediately. Set False when
                    called inside an outer transaction.

        Returns:
            int — number of rows updated. 0 if the path is not tracked.
        """
        cur = self.conn.execute(
            "UPDATE config_files SET original_checksum = ? WHERE path = ?",
            (new_checksum, path.lstrip("/"))
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def refresh_baseline(self, path, commit=True):
        """Re-record the original_checksum from the current live file.

        User-facing operation: after the user manually accepts a .pkmnew
        sidecar (typically via `mv /etc/foo.conf.pkmnew /etc/foo.conf`),
        this records the new live content's sha256 as the baseline so
        subsequent upgrades treat the new content as the "original" for
        the user-edited detection check.

        Args:
            path: relative path (no leading slash, e.g. "etc/foo.conf") or
                  absolute path (leading slash stripped).
            commit: when True (default), commit immediately.

        Returns:
            (success: bool, message: str) — success indicates the live
            file exists and the config_files row was updated.
        """
        rel = path.lstrip("/")
        abs_path = str(self.root / rel)
        if not os.path.isfile(abs_path):
            return False, f"{abs_path}: file not found"
        new_sum = _sha256(abs_path)
        rowcount = self.update_original_checksum(rel, new_sum, commit=commit)
        if rowcount == 0:
            return False, f"{path}: not tracked as a config file"
        return True, f"refreshed baseline for {path} → {new_sum[:16]}..."

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

    def find_owner(self, filepath, include_superseded=False):
        """Find which package owns a file path.

        By default, returns only the active (non-superseded) owner. Set
        include_superseded=True to also see retired records (e.g., to
        audit the chain of supersedes for a path).
        """
        path = filepath.lstrip("/")
        if include_superseded:
            sql = """SELECT i.name, i.version, f.path, i.superseded_by
                     FROM files f JOIN installed i ON f.package_id = i.id
                     WHERE f.path = ?"""
        else:
            sql = """SELECT i.name, i.version, f.path, i.superseded_by
                     FROM files f JOIN installed i ON f.package_id = i.id
                     WHERE f.path = ? AND i.superseded_by IS NULL"""
        row = self.conn.execute(sql, (path,)).fetchone()
        if row:
            return {
                "name": row[0],
                "version": row[1],
                "path": row[2],
                "superseded_by": row[3],
            }
        return None

    # ------------------------------------------------------------------
    # Supersedes
    # ------------------------------------------------------------------

    def mark_superseded(self, predecessor_name, successor_name):
        """Mark predecessor as superseded by successor; record timestamp.

        The predecessor's `installed` record is preserved (audit trail);
        ownership of overlapping file paths is transferred separately via
        transfer_file_ownership(). Both operations should run inside the
        same SQLite transaction at the supersede gate (RFC §4b).
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE installed SET superseded_by = ?, superseded_at = ? WHERE name = ?",
            (successor_name, now, predecessor_name),
        )

    def is_superseded(self, name):
        """Return successor name if package was superseded, else None."""
        row = self.conn.execute(
            "SELECT superseded_by FROM installed WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row and row[0] else None

    def transfer_file_ownership(self, predecessor_name, successor_id, paths, hashes=None):
        """Transfer file records from predecessor to successor for given paths.

        Used during atomic supersede: paths the successor wrote that overlap
        the predecessor's manifest get re-pointed to the successor's package_id
        (with the successor's content hash). Paths the predecessor owned but
        the successor did not touch remain with the predecessor — they are
        retired alongside the predecessor's marker record but do not move.

        hashes: optional dict mapping path → sha256 hex; updates checksum column.
        """
        pred = self.get_installed(predecessor_name)
        if not pred:
            return 0
        normalized = [p.lstrip("/") for p in paths]
        moved = 0
        for path in normalized:
            new_checksum = (hashes or {}).get(path)
            if new_checksum is not None:
                self.conn.execute(
                    """UPDATE files SET package_id = ?, checksum = ?
                       WHERE package_id = ? AND path = ?""",
                    (successor_id, new_checksum, pred["id"], path),
                )
            else:
                self.conn.execute(
                    """UPDATE files SET package_id = ?
                       WHERE package_id = ? AND path = ?""",
                    (successor_id, pred["id"], path),
                )
            moved += self.conn.total_changes
        return moved

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def add_depends(self, package_id, deps, commit=True):
        """Add dependency records. deps: list of (dep_name, dep_type).

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic install in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        """
        for dep_name, dep_type in deps:
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO depends (package_id, dep_name, dep_type) VALUES (?, ?, ?)",
                    (package_id, dep_name, dep_type)
                )
            except sqlite3.IntegrityError:
                pass
        if commit:
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
    # Q9 hold + autoremove (O-026 + O-015)
    # ------------------------------------------------------------------

    def set_held(self, name, held=True, commit=True):
        """Mark a package held (or release the hold).

        Args:
            name: package name.
            held: True to hold (exclude from `pkm upgrade --all`), False
                to release the hold.
            commit: pass-through commit semantics.

        Returns:
            int — rowcount. 1 on success, 0 if the package is not installed.
        """
        cur = self.conn.execute(
            "UPDATE installed SET held = ? WHERE name = ?",
            (1 if held else 0, name),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def list_held(self):
        """Return list of held package names (held=1). Sorted by name."""
        rows = self.conn.execute(
            "SELECT name FROM installed WHERE held = 1 ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def is_held(self, name):
        """Return True iff the named package is currently held."""
        row = self.conn.execute(
            "SELECT held FROM installed WHERE name = ?", (name,)
        ).fetchone()
        return bool(row and row[0])

    def set_install_reason(self, name, reason, commit=True):
        """Update install_reason for a package.

        Args:
            name: package name.
            reason: 'manual' or 'dependency'.
            commit: pass-through commit semantics.

        Returns:
            int — rowcount. 1 on success, 0 if not installed.

        Raises:
            ValueError on invalid reason.
        """
        if reason not in ("manual", "dependency"):
            raise ValueError(
                f"install_reason must be 'manual' or 'dependency'; got {reason!r}"
            )
        cur = self.conn.execute(
            "UPDATE installed SET install_reason = ? WHERE name = ?",
            (reason, name),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def find_orphan_packages(self):
        """Return packages eligible for `pkm autoremove`.

        Eligibility: install_reason='dependency' AND no currently-installed
        package has this one in its runtime/build deps list. Superseded
        rows are excluded — they don't contribute to live rev-dep state.

        Returns:
            list[dict] — each row {name, version, tier} of orphan packages
            sorted by name.
        """
        rows = self.conn.execute(
            """SELECT i.name, i.version, i.tier
               FROM installed i
               WHERE i.install_reason = 'dependency'
                 AND i.superseded_by IS NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM depends d
                   JOIN installed o ON d.package_id = o.id
                   WHERE d.dep_name = i.name AND o.superseded_by IS NULL
                 )
               ORDER BY i.name"""
        ).fetchall()
        return [{"name": r[0], "version": r[1], "tier": r[2]} for r in rows]

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
                      new_version=None, method=None, success=True, commit=True):
        """Log a package operation.

        commit: when True (default), commit immediately. Set to False when
        called inside an outer transaction (e.g. atomic supersede in the
        installer), so the caller manages BEGIN/COMMIT/ROLLBACK.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO history
               (timestamp, operation, package_name, old_version, new_version, method, success)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now, operation, package_name, old_version, new_version, method, success)
        )
        if commit:
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

    def verify_package(self, name, strict=True):
        """Verify all files for a package exist on the filesystem.

        Args:
            name: Package name.
            strict: If True (default), check both file existence AND content
                    hash (SHA-256). If False, check existence only — faster
                    but cannot detect tampering or stale content.

        Returns:
            None if package not installed.
            Otherwise dict with:
              - total: file count
              - missing: paths that don't exist on FS
              - modified: paths whose content differs from manifest hash
                          (always empty when strict=False)
              - superseded_by: name of successor if this package was
                               superseded; None otherwise
        """
        pkg = self.get_installed(name)
        if not pkg:
            return None

        # When superseded, the predecessor's file records were transferred
        # to the successor at supersede time. Surface that explicitly so
        # callers can route queries to the active owner.
        superseded_by = pkg.get("superseded_by")

        rows = self.conn.execute(
            "SELECT path, is_dir, checksum FROM files WHERE package_id = ? AND is_dir = 0",
            (pkg["id"],)
        ).fetchall()

        total = len(rows)
        missing = []
        modified = []

        for path, is_dir, expected_checksum in rows:
            abs_path = str(self.root / path)
            if not os.path.lexists(abs_path):
                missing.append(path)
            elif strict and expected_checksum:
                try:
                    actual = _sha256(abs_path)
                    if actual != expected_checksum:
                        modified.append(path)
                except (OSError, PermissionError):
                    pass

        return {
            "total": total,
            "missing": missing,
            "modified": modified,
            "superseded_by": superseded_by,
        }


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
    """Parse a text manifest into a dict.

    Tolerates both the original format (path-only file entries) and the
    extended format introduced with the supersedes RFC:

      - Optional "SUPERSEDES: <name>-<version>" header (this package
        replaces another at install time)
      - Optional "SUPERSEDED_BY: <name>-<version>" header (this package
        was retired in favor of another)
      - Optional "<path><SP>sha256:<hex>" file entries

    Old manifests without these fields parse cleanly and return
    file_hashes={}. The hashes here serve as redundant verification —
    pkm's SQLite `files.checksum` column is the authoritative source,
    populated from the live filesystem at install time.
    """
    meta = {}
    files = []
    file_hashes = {}
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
        elif line.startswith("SUPERSEDES:"):
            meta["supersedes"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUPERSEDED_BY:"):
            meta["superseded_by"] = line.split(":", 1)[1].strip()
        elif line.startswith("DESCRIPTION:"):
            pass  # Next line has the description
        elif ":" in line and not in_files and line.strip().startswith(meta.get("name", "\x00")):
            # Description line: "bash: The GNU Bourne Again shell"
            meta["description"] = line.split(":", 1)[1].strip()
        elif line.strip() == "FILE LIST:":
            in_files = True
        elif in_files and line.strip():
            # Each file entry is "<path>", "<path>/", or "<path> sha256:<hex>".
            # _parse_manifest_line anchors the hash suffix at end-of-line so
            # paths containing whitespace parse correctly.
            path, h = _parse_manifest_line(line)
            files.append(path)
            if h is not None:
                file_hashes[path] = h

    meta["files"] = files
    meta["file_hashes"] = file_hashes
    return meta if "name" in meta else None
