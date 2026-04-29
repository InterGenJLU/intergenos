"""Package tracking — manifest generation, archive creation, deployment, verification.

Extracted from builder.py to reduce the BuildExecutor class size.
These methods handle everything after a successful build:
  1. Generate Slackware-style text manifest
  2. Create .igos.tar.gz archive
  3. Deploy staged files to the live filesystem
  4. Verify deployment against manifest
"""

import os
import shutil
import subprocess
from pathlib import Path

from .parser import Package


class PackageTracker:
    """Mixin class providing package tracking methods.

    Requires self.logger, self.pkg_db, self.pkg_archives, self.pkg_staging
    to be set by the host class (BuildExecutor).
    """

    def pkg_manifest(self, pkg: Package, staging_dir: Path) -> bool:
        """Generate a Slackware-style manifest from staged files.

        Writes: /var/lib/igos/packages/<name>-<version>
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []
        for root, dirs, files in os.walk(staging_dir):
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), staging_dir)
                file_list.append(rel + "/")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), staging_dir)
                file_list.append(rel)

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
        # Compute template hash for skip-built change detection
        template_hash = ""
        if pkg.template_path:
            import hashlib
            hasher = hashlib.sha256()
            for tpl_file in [pkg.template_path, pkg.template_path.parent / "build.sh"]:
                if tpl_file.exists():
                    hasher.update(tpl_file.read_bytes())
            template_hash = hasher.hexdigest()[:16]

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"TEMPLATE_HASH: {template_hash}\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest: {manifest_path} ({len(file_list)} entries)")
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

        # B4: validate staging directory — reject any path that escapes staging
        # before archiving for deployment to / (root filesystem)
        staging_root = staging_dir.resolve()
        for root, dirs, files in os.walk(str(staging_dir)):
            for name in files + dirs:
                resolved = (Path(root) / name).resolve()
                if not str(resolved).startswith(str(staging_root)):
                    self.logger.error(
                        f"SECURITY: staging path '{resolved}' escapes staging "
                        f"root '{staging_root}' — rejecting package deployment"
                    )
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
                filepath = "/" + line
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

    def fs_snapshot(self, dirs: list[str] | None = None) -> set[str]:
        """Snapshot all files and symlinks under key system directories."""
        if dirs is None:
            dirs = ["/usr", "/etc", "/opt", "/var/lib", "/lib"]
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
        return snapshot

    def pkg_manifest_from_diff(self, pkg: Package, before: set[str], after: set[str]) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages)."""
        new_files = sorted(after - before)

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

        from datetime import datetime, timezone
        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"INSTALL MODE: direct (filesystem diff)\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest (diff): {manifest_path} ({len(new_files)} files, {len(dirs_seen)} dirs)")
        return True

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
