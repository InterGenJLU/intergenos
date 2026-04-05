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
