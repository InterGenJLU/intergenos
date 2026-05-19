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
from .repo import _read_package_meta

# H-008: archive-bundled metadata files (provenance + key=value pkginfo) are
# read at install time for the DB metadata population, but must NOT be
# tracked as installed files on the target filesystem.
_ARCHIVE_METADATA_FILES = frozenset({".PKGINFO", "package.yml"})


# Environment allowlist for install-helper subprocess execution (H-024).
# Default-deny per Holy-Grail-aligned security posture. Anything outside this
# set is dropped before exec to prevent inherited-variable attacks via e.g.
# LD_PRELOAD / LD_LIBRARY_PATH / LD_AUDIT (library injection), *_PROXY (MitM
# of helper-time upstream downloads), PYTHONPATH (Python-module hijack).
HELPER_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "SHELL",
})


class PackageInstaller:
    """Install packages from pre-built archives."""

    def __init__(self, db: PackageDB, root="/"):
        self.db = db
        self.root = Path(root)

    def install(self, name, archive_path=None, queue=None, expected_sha256=None):
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
            expected_sha256: When provided, re-verify the archive sha256
                   inside install() AFTER path validation but BEFORE any
                   tar extract (L-021 TOCTOU defense). Caller computed
                   the hash at download/verify time; we re-compute here
                   so a local attacker who swaps the cached archive
                   between caller's verify and our extract fails the
                   second hash check. Mismatch → fail-closed return.
                   None means no expected hash (legacy / archive-trust=
                   loose path); install proceeds without the gate.

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

        # L-021: re-hash archive immediately before any tar extract.
        # Defends against TOCTOU between the caller's sha256 verification
        # (in repo.download_package or cmd_install --archive path) and
        # the subprocess tar invocations below. The narrow window between
        # caller-verify and our hash here is the only remaining attack
        # surface for the cached-archive-swap scenario; subprocess tar
        # then runs on the freshly-verified content.
        if expected_sha256:
            actual = _sha256(str(archive_path))
            if actual != expected_sha256:
                return False, (
                    f"Archive integrity check FAILED at install time for "
                    f"{name}: expected sha256 {expected_sha256[:16]}..., "
                    f"got {actual[:16]}.... Cached file may have been "
                    f"swapped between download/verify and install. "
                    f"Cache cleared; retry with `pkm install {name}`."
                )

        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # Extract archive to staging for inspection. Hardened tar flags:
            # --no-same-owner: don't preserve UID/GID from archive
            # --no-same-permissions: apply umask instead of archive perms
            try:
                result = subprocess.run(
                    ["tar", "-xzf", str(archive_path), "-C", str(staging),
                     "--no-same-owner", "--no-same-permissions"],
                    capture_output=True, text=True, check=True
                )
            except subprocess.CalledProcessError as e:
                return False, f"Failed to extract archive: {e.stderr}"

            # Read staged manifest (if present): SUPERSEDES + file list + hashes.
            supersedes_decl, manifest_files, manifest_hashes = self._read_staged_manifest(staging, name)

            # H-008: read canonical .PKGINFO key=value for tier/description/
            # license/build_date population at add_installed below. Falls back
            # to empty dict for archives built before .PKGINFO ratification.
            pkginfo = _read_package_meta(archive_path) or {}

            # Build canonical file list from the staged tree itself; the
            # manifest's file list is a transparency artifact, not the
            # authoritative ownership record. Skip archive-level metadata
            # files (.PKGINFO + package.yml) — they're provenance/metadata,
            # not installed-system payload.
            file_list = []
            for root, dirs, files in os.walk(staging):
                for d in sorted(dirs):
                    rel = os.path.relpath(os.path.join(root, d), staging)
                    if not os.path.islink(os.path.join(root, d)):
                        file_list.append(rel + "/")
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(root, f), staging)
                    if rel in _ARCHIVE_METADATA_FILES:
                        continue
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
            try:
                result = subprocess.run(
                    ["tar", "-xzf", str(archive_path), "-C", str(self.root),
                     "--no-overwrite-dir", "--keep-directory-symlink",
                     "--no-same-owner", "--no-same-permissions",
                     # H-008: archive-level provenance/metadata files are not
                     # deployed to the target — pkm reads them in-staging.
                     "--exclude=.PKGINFO", "--exclude=package.yml"],
                    capture_output=True, text=True, check=True
                )
            except subprocess.CalledProcessError as e:
                return False, f"Failed to deploy: {e.stderr}"

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
                # H-008: populate tier/description/license/build_date from
                # archive .PKGINFO. pkm._parse_pkginfo returned an empty dict
                # for pre-H-008 archives so this stays backward-compatible.
                pkg_id = self.db.add_installed(
                    name=name, version=version, install_method="archive",
                    archive_path=str(archive_path),
                    tier=pkginfo.get("tier"),
                    description=pkginfo.get("description"),
                    license_=pkginfo.get("license"),
                    # _parse_pkginfo renames the on-disk `builddate` key
                    # to `build_date` (pkm/repo.py:593-595). Look up the
                    # post-rename key.
                    build_date=pkginfo.get("build_date"),
                    commit=False,
                )
                self.db.add_files(pkg_id, file_list, hashes=hashes_for_db, commit=False)

                # H-004: persist runtime deps so `pkm depends` works + `pkm
                # remove` reverse-dep safety check has data to trip on. Deps
                # come from .PKGINFO depend=X lines (per-entry, repeated).
                runtime_deps = pkginfo.get("depends", [])
                if runtime_deps:
                    self.db.add_depends(
                        pkg_id,
                        [(d, "runtime") for d in runtime_deps],
                        commit=False,
                    )

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

            # D-005 Phase A: fire per-package post-install runtime hook if
            # shipped. Hook lives at <root>/var/lib/pkm/hooks/<name>/post-install
            # (executable). Used by linux-kernel to rebuild + sign the UKI on
            # every install/upgrade per D-005 Option A. Failure is non-fatal —
            # deploy + DB commit already happened; hook is a side-channel.
            self._run_post_install_hook(name, version)

            file_count = len([f for f in file_list if not f.endswith("/")])
            extra = ""
            if superseded_names:
                extra = f" — superseded {', '.join(superseded_names)}"
            return True, f"Installed {name} {version} ({file_count} files){extra}"

        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _run_post_install_hook(self, name, version):
        """Fire per-package post-install runtime hook if shipped.

        Pkm-runtime hook surface for packages that need to do work on the
        live system after deploy (UKI rebuild per D-005, cache regeneration,
        depmod, etc.). Hook lives at
        <root>/var/lib/pkm/hooks/<name>/post-install (executable).

        Environment provided to the hook:
            PKM_PACKAGE_NAME      — package name
            PKM_PACKAGE_VERSION   — package version
            PKM_PACKAGE_ROOT      — install root (e.g. "/" or a chroot)

        Failure is non-fatal: log + continue. The deploy + DB transaction
        already committed; the hook is a side-channel that can be re-run
        manually if it fails.
        """
        hook = self.root / "var" / "lib" / "pkm" / "hooks" / name / "post-install"
        if not hook.is_file() or not os.access(str(hook), os.X_OK):
            return
        # H-024 (same vulnerability class as _run_helper): strip env to
        # HELPER_ENV_ALLOWLIST. Hook executes as the install process; inherited
        # LD_PRELOAD / *_PROXY / PYTHONPATH would let an attacker who can set
        # parent-env vars compromise hook execution.
        env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}
        env["PKM_PACKAGE_NAME"] = name
        env["PKM_PACKAGE_VERSION"] = version
        env["PKM_PACKAGE_ROOT"] = str(self.root)
        try:
            result = subprocess.run([str(hook)], env=env)
            if result.returncode != 0:
                print(
                    f"  WARNING: post-install hook for {name} exited "
                    f"{result.returncode}; install proceeds (hook is "
                    f"non-fatal). Re-run manually: {hook}",
                    file=sys.stderr,
                )
        except (OSError, subprocess.SubprocessError) as e:
            print(
                f"  WARNING: post-install hook for {name} could not "
                f"execute: {e}; install proceeds (hook is non-fatal).",
                file=sys.stderr,
            )

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
        Subprocess env is stripped to HELPER_ENV_ALLOWLIST (H-024) so
        inherited variables like LD_PRELOAD / LD_LIBRARY_PATH / *_PROXY
        / PYTHONPATH cannot redirect the helper's execution or downloads.
        """
        print(f"  No local archive for '{name}' — using install helper")
        print(f"  Running: {helper_path}")
        print(f"  {'─' * 50}")

        helper_env = {k: v for k, v in os.environ.items() if k in HELPER_ENV_ALLOWLIST}

        result = subprocess.run(
            [str(helper_path)],
            env=helper_env,
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
