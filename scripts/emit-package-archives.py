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


def _read_pkg_yml_fields(yml_path):
    """Extract flat top-level scalar fields + depends.runtime list from a
    package.yml for .PKGINFO emission.

    Top-level scalar targets: tier, license, release, description, name, version.
    Block target: depends.runtime (list of dep names).

    Hand-parses the YAML rather than depending on PyYAML; only the field
    set we care about is recognized. Lines starting with `#` and blank
    lines are skipped. Inside a `depends:` block we track sub-blocks
    `build:`/`host:`/`runtime:` and collect runtime entries.

    Returns: dict with present-keys-only (no defaults); 'runtime' key holds
    a list of strings (may be empty).
    """
    fields = {"runtime": []}
    if not yml_path or not yml_path.exists():
        return fields
    targets = {"tier", "license", "release", "description", "name", "version"}
    in_depends = False
    in_runtime = False
    try:
        for raw in yml_path.read_text().splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                # Top-level key — closes any open block.
                in_depends = False
                in_runtime = False
                if ":" not in stripped:
                    continue
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key == "depends" and not value:
                    in_depends = True
                    continue
                if not value:
                    continue
                if key in targets:
                    fields[key] = value
            elif in_depends and indent == 2 and stripped.endswith(":"):
                # Sub-block under depends:
                subkey = stripped[:-1].strip()
                in_runtime = (subkey == "runtime")
            elif in_depends and in_runtime and stripped.startswith("- "):
                dep = stripped[2:].strip().strip('"').strip("'")
                if dep:
                    fields["runtime"].append(dep)
    except OSError:
        pass
    return fields


def _render_pkginfo(meta, yml_fields):
    """Compose canonical lowercase Arch-style .PKGINFO key=value content.

    H-008 + Path A (ratified 2026-05-19 cross-coordinator). pkm._parse_pkginfo
    at pkm/repo.py:575 already expects this format with builddate→build_date
    and size→installed_size mappings.
    """
    name = meta.get("name", "unknown")
    version = meta.get("version", "unknown")
    description = (
        yml_fields.get("description") or meta.get("description", "")
    )
    license_ = yml_fields.get("license", "")
    tier = yml_fields.get("tier", "")
    release = yml_fields.get("release", "1")
    build_date = meta.get("build_date", "")
    installed_size = meta.get("installed_size", 0)
    file_count = len(meta.get("files", []))
    lines = [
        f"pkgname={name}",
        f"pkgver={version}",
        f"pkgrel={release}",
        f"pkgdesc={description}",
        f"license={license_}",
        f"tier={tier}",
        f"builddate={build_date}",
        f"size={installed_size}",
        f"filecount={file_count}",
    ]
    # H-004: emit one depend= line per runtime dep (Arch convention).
    for dep in yml_fields.get("runtime", []):
        lines.append(f"depend={dep}")
    return "\n".join(lines) + "\n"


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


def _resolve_root():
    """Get project root from env var or autodetect.

    Priority: INTERGENOS_ROOT env → script location
    Pattern matches scripts/preflight-build-order.py (merged to master @ 13defc1).
    """
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parent.parent


def _resolve_package_yml(manifest_name):
    """Try to find the corresponding package.yml for a manifest name.

    Searches all known tiers under packages/. Tier list is derived
    dynamically from directory listing rather than hardcoded.
    """
    root = _resolve_root()
    packages_root = root / "packages"
    if not packages_root.is_dir():
        return None

    # Dynamically discover all tier directories under packages/
    for tier_dir in sorted(packages_root.iterdir()):
        if not tier_dir.is_dir():
            continue
        candidate = tier_dir / manifest_name / "package.yml"
        if candidate.exists():
            return candidate
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

    # H-008: source tier/license/release/description fields from package.yml
    # (since the prose manifest doesn't carry these). pkm._parse_pkginfo at
    # pkm/repo.py:575 reads the resulting .PKGINFO at install time and
    # populates the installed table's tier/description/license/build_date
    # columns. Path A — lowercase Arch-style keys ratified 2026-05-19.
    pkg_yml = _resolve_package_yml(name)
    yml_fields = _read_pkg_yml_fields(pkg_yml)
    pkginfo_content = _render_pkginfo(meta, yml_fields)

    # Build archive using Python tarfile (no shell, per Rule #6)
    with tarfile.open(archive_path, "w:gz") as tar:
        # Add installed files at their correct paths
        for arcname, fullpath in files_to_add:
            tar.add(fullpath, arcname=arcname)

        # Add canonical .PKGINFO (H-008 Path A: key=value, not the prose
        # manifest_content this used to write; pkm consumes via _parse_pkginfo)
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write(pkginfo_content)
            tmp.flush()
            tar.add(tmp.name, arcname=".PKGINFO")
            os.unlink(tmp.name)

        # Add package.yml if we can find it (provenance)
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
