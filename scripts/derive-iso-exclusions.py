#!/usr/bin/env python3
"""Derive mksquashfs exclusion list for packages NOT shipping in the ISO.

Walks every package.yml under packages/<tier>/<name>/. Identifies packages
where iso_include resolves to False (explicit `iso_include: false` OR the
tier-based default for `tier: extra` packages). For each such package,
reads the on-disk pkm manifest at $CHROOT/var/lib/igos/packages/<name>-<version>
and emits its file list to the output exclusion file, one path per line.

The output file is consumed by mksquashfs via `-ef <file>` to skip those
paths during squashfs assembly. Per IGOSC's classification doc
(`docs/extra-tier-classification.md`), v1.0 ships 11 ISO + 91 MIRROR
packages from the `tier: extra` set. The MIRROR set is reachable via
`pkm install <name>` after install, fetched from the InterGenOS mirror.

Usage:
    derive-iso-exclusions.py [--chroot /mnt/igos]
                             [--packages /mnt/intergenos/packages]
                             [--output /tmp/iso-exclusions.txt]

Exit 0 on success. Prints summary to stderr.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

# Import the parser from the in-tree igos-build package so default-resolution
# matches build-time semantics exactly. Avoids duplicating the
# "tier:extra defaults to iso_include:False" rule. The package directory
# uses a hyphen so we go through importlib (same pattern as igos-build.py).
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
_parser_mod = importlib.import_module("igos-build.parser")
parse_template = _parser_mod.parse_template


def find_manifest(chroot: Path, name: str, version: str) -> Path | None:
    """Look up pkm manifest for <name>-<version> in chroot."""
    candidate = chroot / "var/lib/igos/packages" / f"{name}-{version}"
    if candidate.is_file():
        return candidate
    # Fallback: glob for name-* in case version mismatch
    pkg_dir = chroot / "var/lib/igos/packages"
    if pkg_dir.is_dir():
        for entry in pkg_dir.iterdir():
            if entry.is_file() and entry.name.startswith(f"{name}-"):
                return entry
    return None


def extract_file_list(manifest: Path) -> list[str]:
    """Parse a pkm manifest's FILE LIST section into a list of paths.

    Manifest entries are "<relative-path> sha256:<hex>" — split on
    whitespace and take only the path. Strip any leading slash so
    paths are relative to the chroot root (mksquashfs -ef expects
    paths relative to its source-tree root).
    """
    paths: list[str] = []
    in_file_list = False
    for line in manifest.read_text(errors="replace").splitlines():
        if line == "FILE LIST:":
            in_file_list = True
            continue
        if not in_file_list:
            continue
        line = line.strip()
        if not line:
            continue
        # First whitespace-delimited token is the path; remainder is the
        # `sha256:<hex>` annotation that we don't need here.
        parts = line.split(None, 1)
        if not parts:
            continue
        path = parts[0]
        # mksquashfs -ef paths are relative to the source root; strip
        # any leading slash. Also skip trailing-slash directory markers
        # (mksquashfs handles parent dirs implicitly when contents are
        # excluded, and explicitly excluding the dir would also drop
        # other packages' files under shared dirs like /etc/, /usr/bin/).
        path = path.lstrip("/")
        if not path or path.endswith("/"):
            continue
        paths.append(path)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chroot", type=Path, default=Path("/mnt/igos"))
    ap.add_argument(
        "--packages",
        type=Path,
        default=Path("/mnt/intergenos/packages"),
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/iso-exclusions.txt"),
    )
    args = ap.parse_args()

    if not args.packages.is_dir():
        print(f"FATAL: packages dir not found: {args.packages}", file=sys.stderr)
        return 1
    if not args.chroot.is_dir():
        print(f"FATAL: chroot not found: {args.chroot}", file=sys.stderr)
        return 1

    excluded_paths: list[str] = []
    mirror_packages: list[str] = []
    iso_packages: list[str] = []
    missing_manifests: list[str] = []

    for tier_dir in sorted(args.packages.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml = pkg_dir / "package.yml"
            if not yml.is_file():
                continue
            try:
                pkg = parse_template(yml)
            except Exception as e:
                print(f"WARN: parse failed {yml}: {e}", file=sys.stderr)
                continue
            if pkg.iso_include:
                iso_packages.append(pkg.name)
                continue
            mirror_packages.append(pkg.name)
            manifest = find_manifest(args.chroot, pkg.name, pkg.version)
            if manifest is None:
                missing_manifests.append(f"{pkg.name}-{pkg.version}")
                continue
            excluded_paths.extend(extract_file_list(manifest))

    # Dedupe + sort for determinism
    excluded_paths_sorted = sorted(set(excluded_paths))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(excluded_paths_sorted) + "\n")

    print(
        f"[derive-iso-exclusions] ISO packages:     {len(iso_packages)}",
        file=sys.stderr,
    )
    print(
        f"[derive-iso-exclusions] MIRROR packages:  {len(mirror_packages)}",
        file=sys.stderr,
    )
    print(
        f"[derive-iso-exclusions] Excluded paths:   {len(excluded_paths_sorted)}",
        file=sys.stderr,
    )
    print(
        f"[derive-iso-exclusions] Missing manifest: {len(missing_manifests)}",
        file=sys.stderr,
    )
    if missing_manifests:
        print(
            "[derive-iso-exclusions]   (these MIRROR packages have no manifest in the chroot,",
            file=sys.stderr,
        )
        print(
            "[derive-iso-exclusions]    so nothing is excluded for them — either not built or DB-gap):",
            file=sys.stderr,
        )
        for m in missing_manifests[:10]:
            print(f"[derive-iso-exclusions]     - {m}", file=sys.stderr)
        if len(missing_manifests) > 10:
            print(
                f"[derive-iso-exclusions]     ... and {len(missing_manifests) - 10} more",
                file=sys.stderr,
            )
    print(
        f"[derive-iso-exclusions] Output:           {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
