#!/usr/bin/env python3
"""build-source-archives.py — Emit .igos.src.tar.gz source archives.

Delivers on the corresponding-source commitment in SOURCES.md §2 by
bundling every InterGenOS-built package's source artifacts into a
single archive named to match its binary counterpart:

    binary:  <name>-<version>-<release>.igos.tar.gz
    source:  <name>-<version>-<release>.igos.src.tar.gz

Per SOURCES.md §2, each source archive contains:
    1. The upstream source tarball (unmodified, filename + sha256 match
       package.yml source: field)
    2. Every patch the InterGenOS build applies (packages/<t>/<p>/patches/*.patch)
    3. The build script (packages/<t>/<p>/build.sh)
    4. The package metadata (packages/<t>/<p>/package.yml)
    5. Any sidecar artifacts the build composes into the final binary,
       listed via the optional `sources_extra:` field in package.yml
       (e.g., config/kernel/fragments/*.config for the kernel package)

Packages with no upstream source (intergenos-keyring, intergenos-legal,
etc. with `source: []`) are skipped — they have no upstream corresponding
source obligation; their build.sh + package.yml are already in the
shipped repository's git tree.

Usage:
    scripts/build-source-archives.py
    scripts/build-source-archives.py --package openssl
    scripts/build-source-archives.py --output-dir /tmp/sources

Exit non-zero on any per-package failure that isn't a clean skip.
"""

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


def substitute_url(url: str, name: str, version: str) -> str:
    """Resolve the ${name} and ${version} placeholders used in package.yml."""
    return url.replace("${name}", name).replace("${version}", version)


def upstream_tarball_name(src_entry: dict, name: str, version: str) -> str:
    """Derive the upstream tarball filename from a source: list entry."""
    url = substitute_url(src_entry["url"], name, version)
    return url.split("/")[-1]


def build_source_archive(
    pkg_dir: Path,
    meta: dict,
    sources_dir: Path,
    output_dir: Path,
    repo_root: Path,
) -> tuple[str, str]:
    """Build a single .igos.src.tar.gz. Returns ("ok"|"skip"|"fail", message)."""
    name = meta.get("name")
    version = str(meta.get("version", ""))
    release = meta.get("release", 1)
    src_field = meta.get("source", []) or []

    if not src_field:
        return ("skip", "no upstream source (pure-data package)")

    src_entry = src_field[0]
    if "url" not in src_entry:
        return ("fail", "source[0] has no url key")

    tarball_name = upstream_tarball_name(src_entry, name, version)
    upstream_path = sources_dir / tarball_name
    if not upstream_path.exists():
        return ("skip", f"upstream tarball not in {sources_dir.name}/: {tarball_name}")

    archive_name = f"{name}-{version}-{release}.igos.src.tar.gz"
    archive_path = output_dir / archive_name

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / f"{name}-{version}-{release}-src"
        stage.mkdir()

        shutil.copy(upstream_path, stage / tarball_name)

        patches_dir = pkg_dir / "patches"
        if patches_dir.is_dir():
            stage_patches = stage / "patches"
            stage_patches.mkdir()
            for patch in sorted(patches_dir.glob("*.patch")):
                shutil.copy(patch, stage_patches / patch.name)

        build_sh = pkg_dir / "build.sh"
        if build_sh.is_file():
            shutil.copy(build_sh, stage / "build.sh")
        shutil.copy(pkg_dir / "package.yml", stage / "package.yml")

        for extra in meta.get("sources_extra", []) or []:
            extra_src = repo_root / extra
            if not extra_src.exists():
                return ("fail", f"sources_extra entry not found: {extra}")
            extra_dst = stage / "extras" / extra
            extra_dst.parent.mkdir(parents=True, exist_ok=True)
            if extra_src.is_file():
                shutil.copy(extra_src, extra_dst)
            else:
                shutil.copytree(extra_src, extra_dst, dirs_exist_ok=True)

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(stage, arcname=stage.name)

    return ("ok", f"{archive_name} ({archive_path.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root (default: cwd)",
    )
    parser.add_argument(
        "--sources-dir",
        default="build/sources",
        help="Upstream tarball directory (default: build/sources)",
    )
    parser.add_argument(
        "--output-dir",
        default="build/sources-archives",
        help="Output directory for .igos.src.tar.gz (default: build/sources-archives)",
    )
    parser.add_argument(
        "--package",
        help="Restrict to a single package by name (default: all)",
    )
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    sources = repo / args.sources_dir
    output = repo / args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    if not sources.is_dir():
        print(f"ERROR: sources directory does not exist: {sources}", file=sys.stderr)
        return 2

    ok = skip = fail = 0
    for pkg_yml in sorted(repo.glob("packages/*/*/package.yml")):
        pkg_dir = pkg_yml.parent
        try:
            with open(pkg_yml) as f:
                meta = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            print(f"FAIL {pkg_dir}: invalid YAML: {e}", file=sys.stderr)
            fail += 1
            continue

        if args.package and meta.get("name") != args.package:
            continue

        status, msg = build_source_archive(pkg_dir, meta, sources, output, repo)
        label = f"{meta.get('name', '?')}-{meta.get('version', '?')}"
        if status == "ok":
            print(f"OK   {label}: {msg}")
            ok += 1
        elif status == "skip":
            print(f"SKIP {label}: {msg}")
            skip += 1
        else:
            print(f"FAIL {label}: {msg}", file=sys.stderr)
            fail += 1

    print(
        f"\nSummary: {ok} emitted, {skip} skipped, {fail} failed → {output}",
        file=sys.stderr,
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
