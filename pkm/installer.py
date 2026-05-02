"""pkm installer — Archive extraction and file deployment.

Implements supersede-aware install per RFC §4 (Supersedes Primitive +
Content-Hash Manifest). The install path:

  1. Read the package's staged manifest (SUPERSEDES header, file list, optional
     per-file sha256 hashes).
  2. Validate predecessors named in SUPERSEDES — warn on missing or
     already-superseded entries (RFC §11) but proceed.
  3. Deploy archive contents to the target filesystem.
  4. Open a SQLite transaction wrapping the DB-side updates: register the
     package, register its files, transfer file ownership from each
     predecessor, mark predecessors superseded, log the operation. Commit
     only after deploy succeeded — gate-3 retirement timing per RFC §4a.
  5. Write a text manifest reflecting the post-install ownership state, with
     SHA-256 hash columns per file.

If the staged manifest lacks per-file hashes (legacy archive built before
Phase 2 tracker write-through), the installer computes them by hashing files
from the staged tree before deploy. Per RFC v2 §2g and the §8 OQ4 resolution
(DS PASS-with-nits): every install produces hashes regardless of archive
generation, so the content-hash property has no NULL-checksum holes.
"""

import os
import re
import stat
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .database import (
    PackageDB,
    ARCHIVE_DIR,
    MANIFEST_DIR,
    _sha256,
    _parse_manifest_line,
)


class PackageInstaller:
    """Install packages from pre-built archives."""

    def __init__(self, db: PackageDB, root="/"):
        self.db = db
        self.root = Path(root)

    def install(self, name, archive_path=None, queue=None):
        """Install a package from its .igos.tar.gz archive.

        Args:
            name: Package name.
            archive_path: Path to archive, or None to search ARCHIVE_DIR.
            queue: Optional iterable of package names representing the full
                   install queue this install is part of. When provided,
                   the installer enforces the install-order invariant: a
                   package P that declares supersedes:[Q] must install
                   AFTER Q if Q is also in the queue. Forge passes this
                   from `installer/backend/packages.py`. Ad-hoc invocations
                   (`pkm install foo-pass2` from the CLI) leave it None
                   and fall back to a warn-and-proceed posture.

        Returns:
            (success: bool, message: str)
        """
        existing = self.db.get_installed(name)
        if existing and not existing.get("superseded_by"):
            return False, f"{name} {existing['version']} is already installed. Use 'pkm reinstall' to replace."
        if existing and existing.get("superseded_by"):
            return False, (
                f"{name} {existing['version']} was superseded by "
                f"{existing['superseded_by']} on {existing.get('superseded_at')}. "
                f"To revert, run 'pkm reinstall {name}' explicitly — this will "
                f"un-retire {name} and is not recommended without a clear reason."
            )

        if not archive_path:
            archive_path = self._find_archive(name)
        if not archive_path:
            helper = self._find_helper(name)
            if helper:
                return False, (
                    f"No local archive for '{name}', but an install helper exists.\n"
                    f"         Run: pkm install-helper {name}"
                )
            return False, f"No archive found for '{name}' in {ARCHIVE_DIR}"

        archive_path = Path(archive_path)
        if not archive_path.exists():
            return False, f"Archive not found: {archive_path}"

        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # Extract archive to staging for inspection. Hardened tar flags:
            # --no-same-owner: don't preserve UID/GID from archive
            # --no-same-permissions: apply umask instead of archive perms
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(staging),
                 "--no-same-owner", "--no-same-permissions"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Failed to extract archive: {result.stderr}"

            # Read staged manifest (if present): SUPERSEDES + file list + hashes.
            supersedes_decl, manifest_files, manifest_hashes = self._read_staged_manifest(staging, name)

            # Build canonical file list from the staged tree itself; the
            # manifest's file list is a transparency artifact, not the
            # authoritative ownership record.
            file_list = []
            for root, dirs, files in os.walk(staging):
                for d in sorted(dirs):
                    rel = os.path.relpath(os.path.join(root, d), staging)
                    if not os.path.islink(os.path.join(root, d)):
                        file_list.append(rel + "/")
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(root, f), staging)
                    file_list.append(rel)

            # Predecessor validation. Missing or already-superseded predecessors
            # surface a warning but do not block install (RFC §11).
            predecessors_to_supersede = self._validate_predecessors(
                name, supersedes_decl or [], queue
            )
            if predecessors_to_supersede is None:
                return False, (
                    f"install-order invariant violated: {name} declares "
                    f"supersedes for a predecessor that is later in the install "
                    f"queue. Reorder the queue so the predecessor installs first."
                )

            # Compute or carry forward content hashes.
            # Primary path: hashes embedded in the staged manifest (Phase 2).
            # Fallback path: hash from the staged tree (legacy archive — RFC v2 §2g).
            hashes_for_db = self._build_hash_map(staging, file_list, manifest_hashes)

            # Safety check: don't clobber root-level symlinks (e.g., usr-merge).
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

            # Deploy to target filesystem. This is the gate-3 line: if the
            # tar succeeds, the supersede transaction below records the
            # ownership transfer; if it fails, no DB changes happen and
            # predecessors keep their records.
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(self.root),
                 "--no-overwrite-dir", "--keep-directory-symlink",
                 "--no-same-owner", "--no-same-permissions"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, f"Failed to deploy: {result.stderr}"

            # Restore setuid/setgid/sticky bits that hardened-tar dropped.
            try:
                with tarfile.open(str(archive_path)) as tf:
                    for member in tf.getmembers():
                        if not member.isfile():
                            continue
                        if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                            deployed = (self.root / member.name.lstrip("/")).resolve()
                            try:
                                deployed.relative_to(self.root.resolve())
                            except ValueError:
                                continue  # path escapes install root
                            if deployed.exists():
                                deployed.chmod(member.mode)
            except (OSError, tarfile.TarError) as e:
                print(f"  WARNING: setuid restore failed for {name}: {e}", file=sys.stderr)

            version = self._version_from_archive(name, archive_path.name)

            # Atomic supersede transaction. add_installed + add_files +
            # mark_superseded + transfer_file_ownership + log_operation all
            # ride inside one BEGIN/COMMIT, so any failure rolls back to a
            # consistent DB state. Filesystem deploy already happened — the
            # FS-level mv window is the documented atomicity tradeoff
            # (RFC §4c). If the DB transaction fails, recovery is
            # `pkm install <name>` re-run; idempotent because INSERT OR
            # REPLACE handles re-registration.
            self.db.conn.execute("BEGIN")
            try:
                pkg_id = self.db.add_installed(
                    name=name, version=version, install_method="archive",
                    archive_path=str(archive_path), commit=False,
                )
                self.db.add_files(pkg_id, file_list, hashes=hashes_for_db, commit=False)

                superseded_names = []
                for pred in predecessors_to_supersede:
                    overlap = self._paths_owned_by(pred["name"], file_list)
                    if overlap:
                        self.db.transfer_file_ownership(
                            pred["name"], pkg_id, overlap, hashes=hashes_for_db
                        )
                    self.db.mark_superseded(pred["name"], name)
                    superseded_names.append(pred["name"])

                self.db.log_operation(
                    "install", name, new_version=version, method="archive",
                    commit=False,
                )
                self.db.conn.execute("COMMIT")
            except Exception as e:
                self.db.conn.execute("ROLLBACK")
                return False, (
                    f"Install of {name} {version} FAILED at DB transaction "
                    f"after deploy. DB rolled back; filesystem may have partial "
                    f"changes. Re-run 'pkm install {name}' to recover. Error: {e}"
                )

            # Generate text manifest reflecting the supersede outcome.
            self._write_manifest(
                name, version, file_list,
                hashes=hashes_for_db,
                supersedes=superseded_names,
            )

            file_count = len([f for f in file_list if not f.endswith("/")])
            extra = ""
            if superseded_names:
                extra = f" — superseded {', '.join(superseded_names)}"
            return True, f"Installed {name} {version} ({file_count} files){extra}"

        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers — manifest reading + predecessor validation + hash building
    # ------------------------------------------------------------------

    def _read_staged_manifest(self, staging, name):
        """Read SUPERSEDES + file list + per-file hashes from the staged manifest.

        Returns (supersedes_list_or_None, file_list, hashes_dict).
        Tolerates absent manifest, missing SUPERSEDES line, and absent
        sha256 columns (legacy format) — the install path falls back
        gracefully in each case.
        """
        manifest_dir = staging / "var" / "lib" / "igos" / "packages"
        if not manifest_dir.exists():
            return None, [], {}
        candidates = sorted(manifest_dir.glob(f"{name}-*"))
        if not candidates:
            return None, [], {}
        try:
            content = candidates[0].read_text()
        except (OSError, UnicodeDecodeError):
            return None, [], {}

        supersedes = None
        files = []
        hashes = {}
        in_files = False
        for line in content.splitlines():
            if line.startswith("SUPERSEDES:"):
                value = line.split(":", 1)[1].strip()
                parsed = [s.strip() for s in value.split(",") if s.strip()]
                supersedes = parsed if parsed else None
            elif line.strip() == "FILE LIST:":
                in_files = True
            elif in_files and line.strip():
                # _parse_manifest_line handles paths with whitespace correctly
                # (anchors hash suffix at end-of-line via regex).
                path, h = _parse_manifest_line(line)
                files.append(path)
                if h is not None:
                    hashes[path.rstrip("/")] = h
        return supersedes, files, hashes

    def _validate_predecessors(self, name, supersedes_decl, queue):
        """Validate each predecessor named in supersedes_decl.

        Returns:
            list of installed-record dicts for predecessors to supersede, OR
            None if the install-order invariant was violated (predecessor
            named appears LATER than this package in the install queue —
            manifest inversion would result, regardless of whether the
            predecessor is currently installed from a prior batch).

        Missing-predecessor and already-superseded predecessor cases
        surface warnings on stderr and are skipped (RFC §11).
        """
        predecessors = []
        queue_list = list(queue) if queue is not None else None

        for pred_name in supersedes_decl:
            if (
                queue_list is not None
                and name in queue_list
                and pred_name in queue_list
            ):
                my_pos = queue_list.index(name)
                pred_pos = queue_list.index(pred_name)
                if pred_pos > my_pos:
                    return None

            pred = self.db.get_installed(pred_name)
            if pred is None:
                if queue_list is not None and pred_name in queue_list:
                    print(
                        f"  WARNING: {pred_name} is earlier in install queue "
                        f"than {name} but is not yet registered. Proceeding "
                        f"with missing-supersedee semantics for this entry.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  WARNING: {name} declares supersedes:[{pred_name}] but "
                        f"{pred_name} is not installed. Proceeding as a standard "
                        f"install (no ownership transfer).",
                        file=sys.stderr,
                    )
                continue
            if pred.get("superseded_by"):
                print(
                    f"  WARNING: {name} declares supersedes:[{pred_name}] but "
                    f"{pred_name} was already superseded by "
                    f"{pred['superseded_by']}. Skipping ownership transfer "
                    f"for this predecessor.",
                    file=sys.stderr,
                )
                continue
            predecessors.append(pred)
        return predecessors

    def _build_hash_map(self, staging, file_list, manifest_hashes):
        """Return a dict {path: sha256_hex} for every regular file in file_list.

        Primary source: the per-file hashes carried in the staged manifest
        (Phase 2 tracker write-through).
        Fallback source: hash the staged tree directly (RFC v2 §2g — applies
        to legacy archives produced before Phase 2). Either way, every
        regular file ends up with a hash recorded in pkm SQLite, closing
        the NULL-checksum hole.
        """
        hashes = {}
        for entry in file_list:
            if entry.endswith("/"):
                continue  # directories don't carry hashes
            normalized = entry.rstrip("/")
            existing = manifest_hashes.get(normalized)
            if existing:
                hashes[normalized] = existing
                continue
            staged_file = staging / normalized
            if not staged_file.is_file() or staged_file.is_symlink():
                continue
            try:
                hashes[normalized] = _sha256(str(staged_file))
            except (OSError, PermissionError):
                # Best-effort — failed-to-read leaves checksum NULL for this
                # path, surfaced later by `pkm verify --strict`.
                pass
        return hashes

    def _paths_owned_by(self, predecessor_name, file_list):
        """Return the subset of file_list paths currently owned by predecessor.

        Used during atomic supersede to scope the file-ownership transfer to
        only the paths the successor actually overwrote (per RFC §3b — the
        successor's manifest must not annex predecessor paths the successor
        didn't touch).
        """
        pred = self.db.get_installed(predecessor_name)
        if not pred:
            return []
        rows = self.db.conn.execute(
            "SELECT path FROM files WHERE package_id = ?",
            (pred["id"],),
        ).fetchall()
        owned = {r[0] for r in rows}
        overlap = []
        for entry in file_list:
            if entry.endswith("/"):
                continue
            normalized = entry.rstrip("/")
            if normalized in owned:
                overlap.append(normalized)
        return overlap

    # ------------------------------------------------------------------
    # Helpers — archive discovery + version parsing
    # ------------------------------------------------------------------

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

    def _find_helper(self, name):
        """Check if an install helper script exists for this package.

        Proprietary software (Chrome, VS Code, Claude Code) can't be
        pre-built into archives. Instead, helper scripts handle the
        download and installation. pkm runs them transparently so the
        user doesn't need to remember separate commands.
        """
        helper = Path(f"/usr/bin/igos-install-{name}")
        if helper.exists() and os.access(str(helper), os.X_OK):
            return helper
        return None

    def _run_helper(self, name, helper_path):
        """Run an install helper script with transparent output.

        The user sees exactly what the helper is doing — no hidden steps.
        """
        print(f"  No local archive for '{name}' — using install helper")
        print(f"  Running: {helper_path}")
        print(f"  {'─' * 50}")

        result = subprocess.run(
            [str(helper_path)],
            env=os.environ.copy(),
        )

        print(f"  {'─' * 50}")

        if result.returncode == 0:
            self.db.add_installed(
                name=name,
                version="latest",
                install_method="helper",
                archive_path=str(helper_path),
            )
            self.db.log_operation("install", name, new_version="latest", method="helper")
            return True, f"Installed {name} via helper ({helper_path.name})"
        else:
            return False, f"Install helper failed (exit {result.returncode})"

    # ------------------------------------------------------------------
    # Helpers — text manifest write-out
    # ------------------------------------------------------------------

    def _write_manifest(self, name, version, file_list, hashes=None, supersedes=None):
        """Write a text manifest alongside the SQLite entry.

        The text manifest is a transparency artifact — pkm SQLite's
        files.checksum is the authoritative source. The manifest mirrors
        the post-install ownership state including any SUPERSEDES header
        and per-file sha256 columns.
        """
        manifest_dir = self.root / "var" / "lib" / "igos" / "packages"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{name}-{version}"

        total_size = sum(
            os.path.getsize(str(self.root / f)) for f in file_list
            if not f.endswith("/") and os.path.isfile(str(self.root / f))
        )
        if total_size > 1024 * 1024:
            human_size = f"{total_size / 1024 / 1024:.1f}M"
        else:
            human_size = f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        lines = [
            f"PACKAGE NAME: {name}-{version}",
            f"PACKAGE VERSION: {version}",
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)",
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"BUILD SYSTEM: InterGenOS pkm",
        ]
        if supersedes:
            lines.append(f"SUPERSEDES: {', '.join(supersedes)}")
        lines.extend([
            "DESCRIPTION:",
            f"{name}: (installed via pkm)",
            "",
            "FILE LIST:",
        ])
        for entry in file_list:
            if entry.endswith("/"):
                lines.append(entry)
            else:
                normalized = entry.rstrip("/")
                h = (hashes or {}).get(normalized)
                lines.append(f"{entry} sha256:{h}" if h else entry)

        manifest_path.write_text("\n".join(lines) + "\n")
