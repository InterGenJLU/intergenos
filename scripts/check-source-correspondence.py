#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-source-correspondence.py — fail-closed binary↔source publish gate.

SOURCES.md commits that every published binary package has its corresponding
source archive published beside it. This gate turns that commitment from a
process step someone must remember into a checked refusal: a publish that
would ship a binary archive without its matching source archive stops before
anything is uploaded or signed.

For every staged binary archive `<name>-<version>-<release>.igos.tar.gz`
(identity read from the archive's own `.PKGINFO`, never parsed from the
filename), exactly one of the following must hold:

  1. The matching `<name>-<version>-<release>.igos.src.tar.gz` exists in the
     source-archive directory; or
  2. The package's recipe in the packages tree declares NO upstream source
     (`source: []` or absent) — the legitimately source-less class (pure-data
     packages such as keyrings and legal texts, whose build.sh + package.yml
     already live in the shipped repository's git tree). The class is DERIVED
     from the tree at gate time and every exempted name is printed, so the
     exemption is auditable, never a hidden allowlist; or
  3. The binary has no recipe of its own but is one of the minimal-core
     packages built directly from the LFS Chapter-8 sequence (binutils, gcc,
     coreutils, ...), whose upstream source is published under a toolchain
     TWIN archive (`<name>-pass*` or `<name>-tmp`). Acceptance is PROVEN, not
     assumed (decided 2026-08-04): a twin recipe must exist at the SAME
     upstream version, its primary source entry must carry a sha256 pin, the
     twin's source archive must be staged, and the bundled tarball inside
     that staged archive must HASH to the recipe pin — the gate hashes the
     member itself. Every accepted mapping is printed name by name.

Anything else is a refusal that names every shortfall:
  - a binary whose source archive is absent and whose recipe declares sources;
  - a binary whose name has no recipe in the packages tree and no
    hash-proven toolchain twin (its source-less status cannot be proven,
    so it fails closed);
  - a binary whose `.PKGINFO` cannot be read (unprovable identity).

Exit 0 only when every staged binary is accounted for.

Usage:
    scripts/check-source-correspondence.py \
        --archive-dir /var/lib/igos/archives \
        --sources-archive-dir build/sources-archives \
        --packages-root packages
"""

import argparse
import hashlib
import sys
import tarfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)


def read_pkginfo(archive: Path) -> dict:
    """Read the archive's own .PKGINFO. Empty dict = unreadable (fail-closed)."""
    try:
        with tarfile.open(archive, "r:gz") as t:
            for cand in ("./.PKGINFO", ".PKGINFO"):
                try:
                    member = t.getmember(cand)
                except KeyError:
                    continue
                kv = {}
                for line in t.extractfile(member).read().decode().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        kv[k.strip()] = v.strip()
                return kv
    except (tarfile.TarError, OSError):
        pass
    return {}


def load_recipes(packages_root: Path) -> dict[str, dict]:
    """Map package name -> its parsed recipe metadata."""
    recipes: dict[str, dict] = {}
    for pkg_yml in sorted(packages_root.glob("*/*/package.yml")):
        try:
            with open(pkg_yml) as f:
                meta = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            # An unreadable recipe cannot prove anything about its package;
            # leaving it out makes a staged archive of that name fail closed
            # as "no recipe", which is the honest outcome.
            continue
        name = meta.get("name")
        if name:
            recipes[name] = meta
    return recipes


def _substitute(text: str, name: str, version: str) -> str:
    return text.replace("${name}", name).replace("${version}", version)


def _primary_tarball_name(meta: dict) -> str | None:
    """Stored filename of a recipe's primary (source[0]) upstream tarball.

    Mirrors build-source-archives.py: an explicit `filename:` wins; otherwise
    the URL basename, both with ${name}/${version} substitution.
    """
    sources = meta.get("source") or []
    if not sources or not isinstance(sources[0], dict):
        return None
    entry = sources[0]
    name, version = meta.get("name", ""), str(meta.get("version", ""))
    if entry.get("filename"):
        return _substitute(entry["filename"], name, version)
    if entry.get("url"):
        return _substitute(entry["url"], name, version).split("/")[-1]
    return None


def resolve_toolchain_twin(name: str, ver: str, recipes: dict[str, dict],
                           sources_dir: Path) -> str | None:
    """Prove a recipe-less minimal-core binary's source via a toolchain twin.

    A twin is a recipe named `<name>-tmp` or `<name>-pass*` at the SAME
    upstream version whose primary source entry carries a sha256 pin. The
    proof is the gate's own hash of the tarball bundled inside the twin's
    staged source archive equalling that pin — presence alone is never
    accepted. Returns a printable proof line, or None (fail closed).
    """
    candidates = sorted(
        n for n in recipes
        if n == f"{name}-tmp" or n.startswith(f"{name}-pass"))
    for cand in candidates:
        meta = recipes[cand]
        if str(meta.get("version", "")) != ver:
            continue
        pin = None
        sources = meta.get("source") or []
        if sources and isinstance(sources[0], dict):
            pin = sources[0].get("sha256")
        tarball = _primary_tarball_name(meta)
        if not (pin and tarball):
            continue
        rel = str(meta.get("release", 1))
        twin_archive = sources_dir / f"{cand}-{meta['version']}-{rel}.igos.src.tar.gz"
        if not twin_archive.is_file():
            continue
        member_path = f"{cand}-{meta['version']}-{rel}/{tarball}"
        try:
            with tarfile.open(twin_archive, "r:gz") as t:
                member = t.getmember(member_path)
                fh = t.extractfile(member)
                if fh is None:
                    continue
                digest = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
        except (KeyError, tarfile.TarError, OSError):
            continue
        if digest.hexdigest() == pin:
            return (f"{name}-{ver} → {twin_archive.name} "
                    f"({tarball} sha256 verified against the twin recipe pin)")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive-dir", required=True,
                        help="Directory holding the staged *.igos.tar.gz binaries")
    parser.add_argument("--sources-archive-dir", required=True,
                        help="Directory holding the *.igos.src.tar.gz source archives")
    parser.add_argument("--packages-root", default="packages",
                        help="Packages tree the source-less class is derived from")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    sources_dir = Path(args.sources_archive_dir)
    packages_root = Path(args.packages_root)

    if not archive_dir.is_dir():
        print(f"ERROR: archive dir does not exist: {archive_dir}", file=sys.stderr)
        return 2
    if not packages_root.is_dir():
        print(f"ERROR: packages root does not exist: {packages_root}", file=sys.stderr)
        return 2

    recipes = load_recipes(packages_root)
    binaries = sorted(archive_dir.glob("*.igos.tar.gz"))
    if not binaries:
        print(f"ERROR: no *.igos.tar.gz staged in {archive_dir}", file=sys.stderr)
        return 2

    matched = 0
    exempt: list[str] = []
    twin_proven: list[str] = []
    shortfalls: list[str] = []

    for binary in binaries:
        info = read_pkginfo(binary)
        name = info.get("pkgname")
        ver = info.get("pkgver")
        rel = info.get("pkgrel")
        if not (name and ver and rel):
            shortfalls.append(
                f"{binary.name}: .PKGINFO unreadable or incomplete — identity "
                f"unprovable, cannot establish source correspondence")
            continue
        expected = f"{name}-{ver}-{rel}.igos.src.tar.gz"
        if (sources_dir / expected).is_file():
            matched += 1
            continue
        if name not in recipes:
            proof = resolve_toolchain_twin(name, ver, recipes, sources_dir)
            if proof:
                twin_proven.append(proof)
                continue
            shortfalls.append(
                f"{binary.name}: no recipe named {name!r} in "
                f"{packages_root}/ and no same-version toolchain twin "
                f"hash-proves its source — source-less status cannot be proven")
            continue
        if not (recipes[name].get("source") or []):
            exempt.append(f"{name}-{ver}-{rel}")
            continue
        shortfalls.append(
            f"{binary.name}: recipe declares upstream source(s) but "
            f"{expected} is absent from {sources_dir}/")

    if exempt:
        print(f"  source-less class ({len(exempt)} packages, derived from "
              f"{packages_root}/, recipe declares no upstream source):")
        for entry in exempt:
            print(f"    {entry}")

    if twin_proven:
        print(f"  recipe-less minimal-core class ({len(twin_proven)} binaries, "
              f"upstream source published under a toolchain-twin archive, "
              f"bundled tarball hash-verified by this gate):")
        for entry in twin_proven:
            print(f"    {entry}")

    if shortfalls:
        print(f"ERROR: {len(shortfalls)} staged binary archive(s) lack their "
              f"corresponding source archive:", file=sys.stderr)
        for s in shortfalls:
            print(f"  {s}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  Publishing a binary without its corresponding source violates the",
              file=sys.stderr)
        print("  SOURCES.md commitment. Run scripts/build-source-archives.py to",
              file=sys.stderr)
        print("  generate the missing archives, then re-run the publish.",
              file=sys.stderr)
        return 1

    print(f"  OK — {matched} binary archives have their corresponding source; "
          f"{len(exempt)} legitimately source-less (named above); "
          f"{len(twin_proven)} recipe-less minimal-core proven via toolchain "
          f"twins (named above); "
          f"{len(binaries)} total, every one accounted for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
