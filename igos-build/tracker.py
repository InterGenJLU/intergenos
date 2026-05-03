"""Package tracking — manifest generation, archive creation, deployment, verification.

Extracted from builder.py to reduce the BuildExecutor class size.
These methods handle everything after a successful build:
  1. Generate Slackware-style text manifest with per-file SHA-256
  2. Create .igos.tar.gz archive
  3. Deploy staged files to the live filesystem
  4. Verify deployment against manifest
  5. Populate pkm SQLite database (durable hash record at build time)

Supersedes-aware (RFC v1, ratified 2026-05-01): when a package declares
`supersedes:`, the pre-build snapshot excludes the supersedee's tracked
paths so FS-diff sees writes that overlap as net-new. The manifest then
claims only paths the package actually writes — paths the supersedee
owned but the new package didn't touch stay retired with the supersedee.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

# Reuse pkm's hash function to guarantee tracker/verifier parity
# (GP review nit, RFC ratification 2026-05-01).
from pkm.database import _sha256, PackageDB, _parse_manifest_line

from .parser import Package


class PackageTracker:
    """Mixin class providing package tracking methods.

    Requires self.logger, self.pkg_db, self.pkg_archives, self.pkg_staging
    to be set by the host class (BuildExecutor).
    """

    def _compute_template_hash(self, pkg: Package) -> str:
        """Compute the 16-char hex hash of pkg's package.yml + build.sh.

        Used by both manifest paths (regular DESTDIR and direct-install/
        filesystem-diff). The hash is embedded in the manifest as
        TEMPLATE_HASH: <hex> and read back by builder.py's skip-built check
        to detect when a package's recipe has changed since last build.

        Returns empty string if pkg has no template_path (in which case
        the skip-built check defaults to skip — same as today).
        """
        if not pkg.template_path:
            return ""
        import hashlib
        hasher = hashlib.sha256()
        for tpl_file in [pkg.template_path, pkg.template_path.parent / "build.sh"]:
            if tpl_file.exists():
                hasher.update(tpl_file.read_bytes())
        return hasher.hexdigest()[:16]

    def pkg_manifest(self, pkg: Package, staging_dir: Path) -> bool:
        """Generate a Slackware-style manifest from staged files.

        Writes: /var/lib/igos/packages/<name>-<version>
        Also populates pkm SQLite (RFC §3d) with the package record + per-file
        SHA-256 hashes computed from staging, so the database is durable at
        build time rather than deferred to first install. Supersede semantics
        are wired via SUPERSEDES: header and atomic ownership transfer.
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []  # path strings (with trailing / for dirs); manifest format
        file_paths = []  # path strings for files only; used for hashing + DB
        for root, dirs, files in os.walk(staging_dir):
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), staging_dir)
                file_list.append(rel + "/")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), staging_dir)
                file_list.append(rel)
                file_paths.append(rel)

        if not file_list:
            self.logger.error(f"Staging produced no files for {pkg.name}-{pkg.version}")
            return False

        # Calculate size
        total_size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(staging_dir)
            for f in files
            if os.path.isfile(os.path.join(root, f))
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        template_hash = self._compute_template_hash(pkg)

        # Per-file SHA-256 from staging contents (RFC §3c). Same _sha256 as
        # pkm/verifier — imported at module top for byte-exact parity.
        file_hashes = self._compute_file_hashes(file_paths, staging_root=staging_dir)

        supersedes_header = ""
        if pkg.supersedes:
            supersedes_header = "SUPERSEDES: " + ", ".join(pkg.supersedes) + "\n"

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"TEMPLATE_HASH: {template_hash}\n"
            f"{supersedes_header}"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        # Render each entry; files get sha256 annotation, dirs do not.
        rendered_lines = []
        for entry in file_list:
            if entry.endswith("/"):
                rendered_lines.append(entry)
            else:
                h = file_hashes.get(entry)
                rendered_lines.append(f"{entry} sha256:{h}" if h else entry)
        manifest_content += "\n".join(rendered_lines) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(
            f"Manifest: {manifest_path} ({len(file_list)} entries, "
            f"{len(file_hashes)} hashed)"
        )

        # Stash for pkg_register_pkm_db, which the builder calls at gate-3
        # (after pkg_deploy succeeds). Writing the DB here would violate
        # RFC §4a — a deploy failure would leave pkm with a record for an
        # undeployed package.
        self._pending_pkm_paths = file_paths
        self._pending_pkm_hashes = file_hashes

        return True

    def pkg_archive(self, pkg: Package, staging_dir: Path) -> bool:
        """Create a .igos.tar.gz archive from staged files.

        Creates: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
        """
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-czf", str(archive_path), "."],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        return True

    def _validate_staging_paths(self, pkg: Package, staging_dir: Path) -> bool:
        """B4: validate staging-dir paths before deploy to /.

        The check distinguishes:
          - Real file/dir whose .resolve() escapes staging_root: actual escape
            attempt (REJECT).
          - Symlink with absolute target inside THIS package's manifest:
            legitimate intra-package compat symlink (e.g., xkeyboard-config's
            ``/usr/share/X11/xkb -> /usr/share/xkeyboard-config-2``). ALLOW.
          - Symlink with absolute target NOT in this package's manifest:
            cross-package or unknown owner. WARN but allow under current
            policy (cross-package validation against the live package db is
            a future enhancement; for now we trust the build to have produced
            valid symlinks).
          - Symlink with relative target that resolves within staging: ALLOW.
          - Symlink with relative target that escapes staging but resolves
            (post-deploy) to a path in this package's manifest: ALLOW
            (intra-package via complex relative path).
          - Symlink with relative target that escapes staging AND post-deploy
            target is not in this package's manifest: REJECT.

        Returns True if all paths pass; False (with logged error) on first
        failure.
        """
        staging_root = staging_dir.resolve()

        # Pass 1: enumerate all paths this package will install (files, dirs,
        # symlinks), expressed as absolute paths AS THEY WILL APPEAR after
        # deploy to /. os.walk's followlinks=False (default) is required so we
        # don't descend into symlinked dirs.
        package_paths: set[str] = set()
        for root, dirs, files in os.walk(str(staging_dir), followlinks=False):
            for name in files + dirs:
                full = Path(root) / name
                try:
                    rel = full.relative_to(staging_dir)
                except ValueError:
                    continue
                package_paths.add('/' + str(rel))

        # Pass 2: validate each entry against the path set.
        staging_root_str = str(staging_root)
        for root, dirs, files in os.walk(str(staging_dir), followlinks=False):
            for name in files + dirs:
                full = Path(root) / name
                if full.is_symlink():
                    target = os.readlink(full)
                    if os.path.isabs(target):
                        # Absolute symlink — allow if target is owned by this
                        # package's manifest. Otherwise warn-but-allow under
                        # current cross-package policy.
                        if target in package_paths:
                            continue
                        self.logger.warning(
                            f"{pkg.name}-{pkg.version}: absolute symlink "
                            f"{full.relative_to(staging_dir)} -> {target} "
                            f"target not in this package's manifest "
                            f"(cross-package validation deferred)"
                        )
                        continue
                    # Relative symlink — resolve and check whether it stays
                    # within staging or, if not, whether the post-deploy
                    # target is in this package's manifest.
                    resolved_abs = (full.parent / target).resolve()
                    if str(resolved_abs).startswith(staging_root_str):
                        continue  # stays within staging — safe
                    # Escapes staging via relative path. Compute what it would
                    # resolve to AFTER deploy to / and check intra-package.
                    relative_within_staging = str(full)[len(staging_root_str):]
                    deploy_target = os.path.normpath(
                        '/' + os.path.dirname(relative_within_staging)
                        + '/' + target
                    )
                    if deploy_target in package_paths:
                        continue  # intra-package via complex relative path
                    self.logger.error(
                        f"SECURITY: symlink {full} -> {target} (would resolve "
                        f"to {deploy_target} after deploy) escapes staging "
                        f"and target is not in this package's manifest — "
                        f"rejecting package deployment"
                    )
                    return False
                # Non-symlink (regular file or dir) — original escape check.
                # With followlinks=False this is belt-and-suspenders since
                # os.walk won't descend into symlinked dirs.
                resolved = full.resolve()
                if not str(resolved).startswith(staging_root_str):
                    self.logger.error(
                        f"SECURITY: staging path '{resolved}' escapes "
                        f"staging root '{staging_root}' — rejecting "
                        f"package deployment"
                    )
                    return False

        return True

    def pkg_deploy(self, pkg: Package, staging_dir: Path) -> bool:
        """Deploy staged files to the live filesystem using tar.

        Safety:
          - Pre-checks for top-level entries that would collide with
            root-level symlinks (lib -> usr/lib, bin -> usr/bin, etc.).
          - Pre-checks that the live filesystem has enough free space to
            accommodate the staged content + 10% headroom; refuses to start
            rather than leaving a partial extraction on disk-full.
          - On extract failure, logs the archive path so the user has a
            durable recovery artifact to re-deploy from or inspect.
        """
        dangerous = []
        for entry in ("lib", "lib64", "bin", "sbin"):
            staged = staging_dir / entry
            root_path = Path("/") / entry
            if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                dangerous.append(entry)

        if dangerous:
            self.logger.error(
                f"DANGEROUS: {pkg.name}-{pkg.version} staging contains top-level "
                f"dirs that would collide with root symlinks: {' '.join(dangerous)}\n"
                f"  Fix the package build.sh to install to usr/ paths instead"
            )
            return False

        # Pre-check free space. A mid-deploy ENOSPC crash leaves the live
        # filesystem with partial files — better to refuse than to partially
        # deploy.
        staging_bytes = 0
        for root, _dirs, files in os.walk(staging_dir):
            for f in files:
                try:
                    staging_bytes += (Path(root) / f).stat().st_size
                except OSError:
                    pass
        required_bytes = int(staging_bytes * 1.1)
        free_bytes = shutil.disk_usage("/").free
        if free_bytes < required_bytes:
            self.logger.error(
                f"Insufficient free space for {pkg.name}-{pkg.version} deploy:\n"
                f"  required (+10% headroom): {required_bytes:,} bytes\n"
                f"  free on /: {free_bytes:,} bytes"
            )
            return False

        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        # B4: validate staging paths before archiving for deploy to /.
        if not self._validate_staging_paths(pkg, staging_dir):
            return False

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-cf", "-", "."],
            capture_output=True,
        )
        if result.returncode != 0:
            self.logger.error(
                f"Deploy tar-create failed: {result.stderr.decode()}\n"
                f"  Staging dir: {staging_dir}\n"
                f"  Archive for manual recovery: {archive_path}"
            )
            return False

        result2 = subprocess.run(
            ["tar", "-C", "/", "-xf", "-",
             "--no-overwrite-dir", "--keep-directory-symlink"],
            input=result.stdout,
            capture_output=True,
        )
        if result2.returncode != 0:
            self.logger.error(
                f"Deploy tar-extract failed: {result2.stderr.decode()}\n"
                f"  Partial files may exist on live filesystem.\n"
                f"  Archive for manual recovery / re-deploy: {archive_path}\n"
                f"  To retry the deploy manually:\n"
                f"    sudo tar -C / -xf {archive_path} --no-overwrite-dir --keep-directory-symlink"
            )
            return False

        self.logger.info(f"Deployed {pkg.name}-{pkg.version} to live filesystem")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return True

    def pkg_verify(self, pkg: Package) -> bool:
        """Verify every file in the manifest exists on the live filesystem."""
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"
        if not manifest_path.exists():
            self.logger.error(f"Manifest not found: {manifest_path}")
            return False

        content = manifest_path.read_text()
        in_file_list = False
        missing = []

        for line in content.splitlines():
            if line == "FILE LIST:":
                in_file_list = True
                continue
            if in_file_list and line.strip():
                if line.endswith("/"):
                    continue
                # _parse_manifest_line handles paths with whitespace correctly
                # (anchors hash suffix at end-of-line via regex). Linux-firmware
                # surfaced this — files like "brcmfmac43455-sdio.Raspberry Pi
                # Foundation-Raspberry Pi 4 Model B.txt.xz" have spaces.
                path, _h = _parse_manifest_line(line)
                filepath = "/" + path
                if not os.path.lexists(filepath):
                    missing.append(filepath)

        if missing:
            self.logger.error(
                f"Manifest verification FAILED for {pkg.name}-{pkg.version}:\n"
                + "\n".join(f"  MISSING: {f}" for f in missing[:20])
            )
            if len(missing) > 20:
                self.logger.error(f"  ... and {len(missing) - 20} more")
            return False

        self.logger.info("Manifest verified: all files present on live filesystem")
        return True

    # ------------------------------------------------------------------
    # Direct install tracking (filesystem diff)
    # ------------------------------------------------------------------

    def fs_snapshot(self, dirs: list[str] | None = None,
                    exclude_paths: set[str] | None = None) -> set[str]:
        """Snapshot all files and symlinks under key system directories.

        Args:
            dirs: directories to walk. Defaults to a baseline system set.
            exclude_paths: absolute paths to remove from the snapshot. Used
                to support supersedes semantics — when package P declares
                supersedes for predecessor Q, Q's tracked paths are excluded
                so FS-diff treats P's writes over those paths as net-new.

        Returns:
            Set of absolute paths to regular files and symlinked directories.
        """
        # Default also includes /boot since kernel packages install vmlinuz
        # + System.map there; without it, the supersedes-aware diff misses
        # the kernel artifacts entirely.
        if dirs is None:
            dirs = ["/usr", "/etc", "/opt", "/var/lib", "/lib", "/boot"]
        snapshot = set()
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, dirnames, files in os.walk(d, followlinks=False):
                for f in files:
                    snapshot.add(os.path.join(root, f))
                for dn in dirnames:
                    path = os.path.join(root, dn)
                    if os.path.islink(path):
                        snapshot.add(path)
        if exclude_paths:
            snapshot -= exclude_paths
        return snapshot

    def _get_supersedee_paths(self, pkg: Package) -> set[str]:
        """Read tracked paths from each supersedee's text manifest.

        Used to drive snapshot-exclusion (RFC §3a). Returns absolute paths
        from every package this one declares as superseded. Missing
        manifests are silently skipped — corresponds to the
        missing-supersedee allow-with-warn case (RFC §11).
        """
        if not pkg.supersedes:
            return set()
        paths: set[str] = set()
        for predecessor_name in pkg.supersedes:
            for manifest_file in sorted(self.pkg_db.iterdir()) if self.pkg_db.exists() else []:
                if not manifest_file.is_file():
                    continue
                if not manifest_file.name.startswith(f"{predecessor_name}-"):
                    continue
                paths.update(self._parse_manifest_paths(manifest_file))
        return paths

    @staticmethod
    def _parse_manifest_paths(manifest_file: Path) -> set[str]:
        """Extract absolute file paths from a text manifest's FILE LIST.

        Tolerates both the original format and the supersedes-extended
        format (entries may carry an "<path> sha256:<hex>" annotation).
        Directory entries (trailing slash) are excluded.
        """
        paths: set[str] = set()
        in_files = False
        try:
            content = manifest_file.read_text()
        except (OSError, UnicodeDecodeError):
            return paths
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "FILE LIST:":
                in_files = True
                continue
            if not in_files or not stripped:
                continue
            # _parse_manifest_line handles paths with whitespace correctly.
            entry, _h = _parse_manifest_line(line)
            if entry.endswith("/"):
                continue
            paths.add("/" + entry.lstrip("/"))
        return paths

    def _detect_overwrites(self, pkg: Package, build_start_time: float) -> set[str]:
        """Identify supersedee paths the build modified during this run.

        Per DS critique on RFC §3b: pass2's manifest must include only paths
        pass2 actually wrote, never paths the supersedee owned that pass2
        didn't touch. mtime change vs build_start_time is the signal — files
        modules_install / cp / etc. all bump mtime past the build start.

        Returns: subset of supersedee_paths whose mtime is at or after the
        build started (i.e. were rewritten during this build).
        """
        overwrites: set[str] = set()
        for path in self._get_supersedee_paths(pkg):
            try:
                if os.path.lexists(path) and os.path.getmtime(path) >= build_start_time:
                    overwrites.add(path)
            except OSError:
                continue
        return overwrites

    @staticmethod
    def _compute_file_hashes(paths: list[str], staging_root: Path | None = None) -> dict[str, str]:
        """Compute SHA-256 for each file path. Returns {relpath: hex_digest}.

        If staging_root is given, paths are interpreted relative to it
        (DESTDIR-staged install). Otherwise paths are absolute (direct
        install on the live filesystem).
        """
        hashes: dict[str, str] = {}
        for path in paths:
            try:
                if staging_root is not None:
                    abs_path = staging_root / path
                else:
                    abs_path = Path(path)
                if abs_path.is_file() and not abs_path.is_symlink():
                    rel = path if staging_root is None else str(path)
                    hashes[rel] = _sha256(str(abs_path))
            except (OSError, PermissionError):
                continue
        return hashes

    def pkg_manifest_from_diff(self, pkg: Package, before: set[str], after: set[str],
                                build_start_time: float | None = None) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages).

        With supersedes semantics (RFC §3a-§3b):
          - net-new = paths present in `after` but not in `before` — caller
            already excluded supersedee paths from `before` so writes that
            overlap appear net-new.
          - overwrites = subset of supersedee paths whose mtime is at or
            after build_start_time (i.e. the build modified them). DS critique
            tightening: pass2's manifest claims only paths pass2 actually
            wrote. Paths the supersedee owned but pass2 didn't touch stay
            retired with the supersedee.

        Per-file SHA-256 is computed from the live filesystem and written
        to both the text manifest and pkm SQLite (RFC §3c-§3d).
        """
        net_new = set(after) - set(before)
        overwrites: set[str] = set()
        if pkg.supersedes and build_start_time is not None:
            overwrites = self._detect_overwrites(pkg, build_start_time)
        new_files = sorted(net_new | overwrites)

        if not new_files:
            self.logger.error(f"No new files detected for {pkg.name}-{pkg.version}")
            return False

        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []
        dirs_seen = set()
        for filepath in new_files:
            parts = Path(filepath).relative_to("/")
            for i in range(1, len(parts.parts)):
                parent = str(Path(*parts.parts[:i]))
                if parent not in dirs_seen:
                    dirs_seen.add(parent)
                    file_list.append(parent + "/")
            file_list.append(str(parts))

        file_list = sorted(set(file_list))

        total_size = sum(
            os.path.getsize(f) for f in new_files if os.path.isfile(f)
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        # Compute hashes from live filesystem (direct_install already deployed)
        file_hashes: dict[str, str] = {}
        for abs_path in new_files:
            try:
                if os.path.isfile(abs_path) and not os.path.islink(abs_path):
                    file_hashes[abs_path.lstrip("/")] = _sha256(abs_path)
            except (OSError, PermissionError):
                continue

        from datetime import datetime, timezone
        template_hash = self._compute_template_hash(pkg)

        supersedes_header = ""
        if pkg.supersedes:
            supersedes_header = "SUPERSEDES: " + ", ".join(pkg.supersedes) + "\n"

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"INSTALL MODE: direct (filesystem diff)\n"
            f"TEMPLATE_HASH: {template_hash}\n"
            f"{supersedes_header}"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        rendered_lines = []
        for entry in file_list:
            if entry.endswith("/"):
                rendered_lines.append(entry)
            else:
                h = file_hashes.get(entry)
                rendered_lines.append(f"{entry} sha256:{h}" if h else entry)
        manifest_content += "\n".join(rendered_lines) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(
            f"Manifest (diff): {manifest_path} "
            f"({len(new_files)} files, {len(dirs_seen)} dirs, "
            f"{len(overwrites)} overwrites of supersedee, "
            f"{len(file_hashes)} hashed)"
        )

        # Stash for pkg_register_pkm_db. For direct_install packages the
        # install already happened (gate-3 effectively), so registration
        # could run here — but builder calls pkg_register_pkm_db
        # uniformly for both flows so reviewers see one gate.
        rel_paths = [p.lstrip("/") for p in new_files]
        self._pending_pkm_paths = rel_paths
        self._pending_pkm_hashes = file_hashes

        return True

    def pkg_register_pkm_db(self, pkg: Package) -> bool:
        """Final TRACK-phase step: write pkm SQLite at gate-3 (post-deploy).

        Reads file paths + per-file SHA-256 hashes that pkg_manifest /
        pkg_manifest_from_diff stashed on the executor. Per RFC §4a, this
        runs ONLY after the deploy step has succeeded — a deploy failure
        leaves pkm DB untouched, so the package state is honest: no
        record claims files that aren't on disk.

        For supersede packages, runs the atomic ownership transfer and
        predecessor retirement inside a single SQLite transaction. A
        failure here rolls the entire supersede back and surfaces an
        error; the deployed files are on disk but pkm has not yet
        accounted for them, so a re-run can complete the registration
        cleanly.
        """
        rel_paths = getattr(self, "_pending_pkm_paths", None)
        file_hashes = getattr(self, "_pending_pkm_hashes", None)
        if rel_paths is None or file_hashes is None:
            self.logger.error(
                f"pkg_register_pkm_db called without pending paths/hashes — "
                f"pkg_manifest or pkg_manifest_from_diff must run first"
            )
            return False
        result = self._write_pkm_db(pkg, rel_paths, file_hashes)
        # Clear the stash so a subsequent package can't accidentally inherit
        self._pending_pkm_paths = None
        self._pending_pkm_hashes = None
        return result

    def _write_pkm_db(self, pkg: Package, rel_paths: list[str],
                       file_hashes: dict[str, str]) -> bool:
        """Populate pkm SQLite with this package's record + files (RFC §3d).

        For supersede packages, also runs the atomic ownership transfer
        and predecessor retirement inside a single SQLite transaction.
        Caller should treat False as a hard failure — the package is on
        disk but pkm cannot account for it.
        """
        try:
            db = PackageDB()
        except Exception as e:
            self.logger.error(f"pkm DB open failed: {e}")
            return False

        try:
            pkg_id = db.add_installed(
                name=pkg.name,
                version=pkg.version,
                release=pkg.release,
                tier=pkg.tier,
                description=pkg.description,
                license_=pkg.license,
                install_method="source-build",
            )
            db.add_files(pkg_id, rel_paths)

            # Supersede transition: transfer ownership of overlap paths from
            # each predecessor to this package, then mark each predecessor as
            # superseded. Caller-managed transaction wraps this so a failure
            # rolls the whole thing back without leaving pkm in a half-state.
            if pkg.supersedes:
                db.conn.execute("BEGIN")
                try:
                    pkg_set = set(rel_paths)
                    for predecessor_name in pkg.supersedes:
                        pred = db.get_installed(predecessor_name)
                        if pred is None:
                            self.logger.info(
                                f"  supersedes target '{predecessor_name}' not "
                                f"in pkm DB — supersede is a no-op (RFC §11)"
                            )
                            continue
                        pred_paths = {
                            f["path"] for f in db.get_files(predecessor_name)
                        }
                        overlap = pred_paths & pkg_set
                        if overlap:
                            overlap_hashes = {
                                p: file_hashes.get(p) for p in overlap
                            }
                            db.transfer_file_ownership(
                                predecessor_name, pkg_id,
                                list(overlap), overlap_hashes
                            )
                        db.mark_superseded(predecessor_name, pkg.name)
                        self.logger.info(
                            f"  superseded '{predecessor_name}' "
                            f"({len(overlap)} overlap paths transferred)"
                        )
                    db.conn.execute("COMMIT")
                except Exception:
                    db.conn.execute("ROLLBACK")
                    raise

            return True
        except Exception as e:
            self.logger.error(f"pkm DB write failed for {pkg.name}-{pkg.version}: {e}")
            return False
        finally:
            db.close()

    def pkg_archive_from_files(self, pkg: Package, new_files: list[str]) -> bool:
        """Create .igos.tar.gz archive from a list of files on the live filesystem."""
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            for filepath in new_files:
                f.write(filepath.lstrip("/") + "\n")
            filelist_path = f.name

        result = subprocess.run(
            ["tar", "-C", "/", "-czf", str(archive_path), "-T", filelist_path],
            capture_output=True, text=True,
        )
        os.unlink(filelist_path)

        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        return True
