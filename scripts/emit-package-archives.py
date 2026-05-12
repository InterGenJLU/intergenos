#!/usr/bin/env python3
"""E1.B.5 — Per-package .igos.tar.gz archive emitter.

Post-build tool that reads install manifests from a chroot and emits
transportable .igos.tar.gz archives for the binary repository pipeline.

Usage:
    python3 scripts/emit-package-archives.py /mnt/igos /
    python3 scripts/emit-package-archives.py --manifest-dir /var/lib/igos/packages --chroot /mnt/igos --output /tmp/archives

Chain (Half-B binary repo):
    1. Build #N completes → manifests in /var/lib/igos/packages/
    2. E1.B.5 (this script) emits .igos.tar.gz per package
    3. E1.B.6 (generate-repodb.py) creates+signed InterGenOS.db
    4. publish-repo.sh promotes to repo.intergenos.org
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Match pkg-functions.sh constants (scripts/pkg-functions.sh:23-24)
DEFAULT_MANIFEST_DIR = "/var/lib/igos/packages"
DEFAULT_CHROOT = "/mnt/igos"


def _read_manifest(manifest_path):
    """Parse a Slackware-style manifest file.

    Format (from pkg-functions.sh:342-353):
        PACKAGE NAME: firefox-138.0
        PACKAGE VERSION: 138.0
        UNCOMPRESSED SIZE: 215M (215000000 bytes)
        BUILD DATE: 2026-05-11T12:00:00Z
        BUILD SYSTEM: InterGenOS LFS 13.0
        DESCRIPTION:
        firefox: Mozilla Firefox web browser
        <blank line>
        FILE LIST:
        usr/bin/firefox
        ...
    """
    meta = {"files": []}
    section = None
    description_lines = []

    with open(manifest_path, "r") as f:
        for line in f:
            line = line.rstrip("\n")

            if line.startswith("PACKAGE NAME:"):
                val = line.split(":", 1)[1].strip()
                meta["name"] = val.rsplit("-", 1)[0] if "-" in val else val
                meta["full_name"] = val
            elif line.startswith("PACKAGE VERSION:"):
                meta["version"] = line.split(":", 1)[1].strip()
            elif line.startswith("UNCOMPRESSED SIZE:"):
                size_str = line.split(":", 1)[1].strip()
                meta["uncompressed_size"] = size_str
                # Extract bytes value if present
                if "(" in size_str:
                    meta["installed_size"] = int(
                        size_str.split("(")[1].split("bytes")[0].strip()
                    )
            elif line.startswith("BUILD DATE:"):
                meta["build_date"] = line.split(":", 1)[1].strip()
            elif line.startswith("DESCRIPTION:"):
                section = "description"
                continue
            elif line == "FILE LIST:":
                section = "file_list"
                continue

            if section == "description" and line.strip():
                description_lines.append(line.strip())
            elif section == "file_list" and line.strip():
                meta["files"].append(line.strip())

    if description_lines:
        meta["description"] = " ".join(description_lines)

    return meta


def _sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _resolve_package_yml(manifest_name):
    """Try to find the corresponding package.yml for a manifest."""
    candidates = [
        Path("packages") / "desktop" / manifest_name / "package.yml",
        Path("packages") / "core" / manifest_name / "package.yml",
        Path("packages") / "extra" / manifest_name / "package.yml",
        Path("packages") / "base" / manifest_name / "package.yml",
        Path("packages") / "config" / manifest_name / "package.yml",
        Path("packages") / "ai" / manifest_name / "package.yml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def emit_archive(manifest_path, chroot, output_dir):
    """Create a .igos.tar.gz archive from a manifest and chroot files."""
    manifest_path = Path(manifest_path)
    chroot = Path(chroot)
    output_dir = Path(output_dir)

    meta = _read_manifest(manifest_path)
    name = meta.get("name", manifest_path.stem.split("-")[0])
    version = meta.get("version", "unknown")
    archive_name = f"{name}-{version}.igos.tar.gz"
    archive_path = output_dir / archive_name

    # Filter to files that actually exist in chroot
    files_to_add = []
    for f in meta.get("files", []):
        fullpath = chroot / f
        if fullpath.exists():
            files_to_add.append((f, fullpath))
        elif fullpath.is_symlink():
            files_to_add.append((f, fullpath))

    if not files_to_add:
        print(f"  WARNING: {name}: no files found in chroot, skipping", file=sys.stderr)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    # Build archive using Python tarfile (no shell, per Rule #6)
    with tarfile.open(archive_path, "w:gz") as tar:
        # Add installed files at their correct paths
        for arcname, fullpath in files_to_add:
            tar.add(fullpath, arcname=arcname)

        # Add the manifest itself (as .PKGINFO for pkm compatibility)
        manifest_content = manifest_path.read_text()
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write(manifest_content)
            tmp.flush()
            tar.add(tmp.name, arcname=".PKGINFO")
            os.unlink(tmp.name)

        # Add package.yml if we can find it (provenance)
        pkg_yml = _resolve_package_yml(name)
        if pkg_yml:
            tar.add(pkg_yml, arcname="package.yml")

    # Compute SHA256 of the archive
    sha = _sha256_file(archive_path)
    archive_size = archive_path.stat().st_size

    print(f"  {archive_name}: {archive_size} bytes, sha256={sha[:16]}..., {len(files_to_add)} files", file=sys.stderr)
    return {
        "name": name,
        "version": version,
        "filename": archive_name,
        "size": archive_size,
        "sha256": sha,
        "files": len(files_to_add),
        "path": str(archive_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="E1.B.5 — Emit .igos.tar.gz from chroot manifests"
    )
    parser.add_argument(
        "chroot", nargs="?", default=DEFAULT_CHROOT,
        help=f"Path to chroot mount (default: {DEFAULT_CHROOT})",
    )
    parser.add_argument(
        "output", nargs="?", default=".",
        help="Output directory for .igos.tar.gz archives (default: .)",
    )
    parser.add_argument(
        "--manifest-dir",
        help=f"Path to manifests directory (default: chroot + {DEFAULT_MANIFEST_DIR})",
    )
    args = parser.parse_args()

    chroot = Path(args.chroot)
    output_dir = Path(args.output)
    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else chroot / DEFAULT_MANIFEST_DIR.lstrip("/")

    if not manifest_dir.is_dir():
        print(f"ERROR: manifest directory not found: {manifest_dir}", file=sys.stderr)
        sys.exit(1)

    manifests = sorted(manifest_dir.glob("*"))
    manifests = [m for m in manifests if m.is_file() and not m.name.startswith(".")]

    if not manifests:
        print(f"WARNING: no manifests found in {manifest_dir}", file=sys.stderr)
        sys.exit(0)

    print(f"Emitting archives from {len(manifests)} manifests...", file=sys.stderr)

    results = []
    for m in manifests:
        result = emit_archive(m, chroot, output_dir)
        if result:
            results.append(result)

    print(f"\nEmitted {len(results)} archives to {output_dir}", file=sys.stderr)

    # Write a summary manifest for the pipeline
    summary_path = output_dir / "emit-summary.json"
    summary_path.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_dir": str(manifest_dir),
        "chroot": str(chroot),
        "archives": results,
    }, indent=2))
    print(f"Summary written: {summary_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
