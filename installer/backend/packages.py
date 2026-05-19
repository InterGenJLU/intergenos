"""Package installation for InterGenOS installer — wraps pkm."""

import os
import sys
from pathlib import Path

# Add project root so we can import pkm
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


# Package groups for installation
GROUPS = {
    "core": {
        "description": "Essential system (kernel, shell, coreutils, systemd, SSH)",
        "tiers": ["core"],
        "required": True,
    },
    "base": {
        "description": "CLI tools (htop, rsync, strace, screen)",
        "tiers": ["base"],
        "required": False,
        "default": True,
    },
    "desktop-gnome": {
        "description": "GNOME desktop environment on Wayland",
        "tiers": ["desktop"],
        "required": False,
        "default": True,
    },
    "extra": {
        "description": "Node.js, Chrome/VS Code/Claude Code helpers",
        "tiers": ["extra"],
        "required": False,
        "default": False,
    },
    # C-007: "ai" was selectable in TUI PACKAGE_GROUP_CHOICES (frontend/tui.py:221)
    # but had no entry here, so get_group_packages did GROUPS.get("ai") → None →
    # tiers stayed empty → user-checked AI group installed zero packages silently.
    # packages/ai/ ships intergen + llama-cpp (text-only per D-008 InterGen scope).
    "ai": {
        "description": "Local AI runtime (intergen + llama-cpp; text-only)",
        "tiers": ["ai"],
        "required": False,
        "default": False,
    },
}


def get_archives(archive_dir):
    """Scan archive directory and return dict of {name: (version, path)}.

    Archives are named: <name>-<version>.igos.tar.gz
    """
    archive_dir = Path(archive_dir)
    archives = {}

    if not archive_dir.exists():
        return archives

    for f in sorted(archive_dir.iterdir()):
        if not f.name.endswith(".igos.tar.gz"):
            continue
        stem = f.name.replace(".igos.tar.gz", "")
        # Split on last hyphen before version number
        import re
        match = re.match(r'^(.+?)-(\d.*)$', stem)
        if match:
            name = match.group(1)
            version = match.group(2)
            archives[name] = (version, f)

    return archives


def get_group_packages(groups, archive_dir, package_dir=None):
    """Get the list of archives to install for selected groups.

    Args:
        groups: list of group names (e.g., ["core", "base", "desktop-gnome"])
        archive_dir: path to archive directory
        package_dir: path to packages/ directory (for tier mapping)

    Returns:
        list of (name, version, archive_path) tuples in install order
    """
    # Determine which tiers we need
    tiers = set()
    for group_name in groups:
        group = GROUPS.get(group_name)
        if group:
            tiers.update(group["tiers"])

    # Get available archives
    archives = get_archives(archive_dir)

    # If we have a packages directory, filter by tier
    if package_dir:
        package_dir = Path(package_dir)
        tier_packages = set()
        for tier in tiers:
            tier_dir = package_dir / tier
            if tier_dir.exists():
                for pkg_dir in tier_dir.iterdir():
                    if pkg_dir.is_dir():
                        tier_packages.add(pkg_dir.name)

        # Filter archives to only include packages in requested tiers
        filtered = []
        for name, (version, path) in sorted(archives.items()):
            if name in tier_packages:
                filtered.append((name, version, path))
        return filtered
    else:
        # No tier filtering — return all archives
        return [(name, ver, path) for name, (ver, path) in sorted(archives.items())]


def install_packages(target, archive_dir, groups, package_dir=None,
                     progress_callback=None):
    """Install packages to a target root filesystem.

    Args:
        target: target root path (e.g., /mnt/target)
        archive_dir: path to .igos.tar.gz archives
        groups: list of group names to install
        package_dir: path to packages/ for tier mapping
        progress_callback: fn(current, total, name) called per package

    Returns:
        (success_count, fail_count, failed_packages)
    """
    packages = get_group_packages(groups, archive_dir, package_dir)
    total = len(packages)

    if total == 0:
        return 0, 0, []

    # Create pkm database on the target. Context-manager use guarantees
    # close() runs even if an installer.install() call raises mid-loop —
    # otherwise an exception leaks the SQLite handle (FD + WAL state).
    db_path = Path(target) / "var" / "lib" / "igos" / "pkm.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = []

    with PackageDB(str(db_path), root=target) as db:
        installer = PackageInstaller(db, root=target)

        # Build the install queue once so the per-package install() call can
        # enforce the supersede install-order invariant: a successor declaring
        # supersedes:[predecessor] must install AFTER its predecessor when both
        # are in the queue. Without this, ad-hoc ordering could let a successor
        # install first as a standard package, then the predecessor overwrites
        # the same paths, leaving pkm with a manifest inversion the user cannot
        # see. See pkm/installer.py install() and the Phase 4 RFC §4 design.
        queue_names = [pkg[0] for pkg in packages]

        for i, (name, version, archive_path) in enumerate(packages, 1):
            if progress_callback:
                progress_callback(i, total, name)

            ok, msg = installer.install(
                name, archive_path=str(archive_path), queue=queue_names
            )
            if ok:
                success += 1
            else:
                failed.append((name, msg))

    return success, len(failed), failed
