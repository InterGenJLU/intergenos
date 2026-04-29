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
            # Suggest install-helper if one exists
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

        # Extract to temp staging area for inspection
        staging = Path(tempfile.mkdtemp(prefix=f"pkm-{name}-"))
        try:
            # Extract archive with hardened flags:
            # --no-same-owner: don't preserve UID/GID from archive
            # --no-same-permissions: apply umask instead of archive perms
            result = subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(staging),
                 "--no-same-owner", "--no-same-permissions"],
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
                 "--no-overwrite-dir", "--keep-directory-symlink",
                 "--no-same-owner", "--no-same-permissions"],
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
            # Record in database so pkm knows it's installed
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
