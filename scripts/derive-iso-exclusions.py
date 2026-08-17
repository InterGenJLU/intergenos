#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Derive the ISO-exclusion list for packages NOT shipping in the ISO.

Walks every package.yml under packages/<tier>/<name>/ and identifies
packages where iso_include resolves to False (explicit `iso_include:
false` OR the tier-based default — `tier: extra` and `tier: compute`
default to iso_include:False, all other tiers default to True; the
resolution itself is the parser's, imported, never replicated). Per the
2026-05-28 ISO-curation walk amendment to docs/extra-tier-
classification.md, the current state is 22 ISO + 81 MIRROR from extra
plus 6 MIRROR from desktop (build-time-only / generator tooling).

Two output modes:

  --mode=paths (Path-a, historical)
    Emit a mksquashfs exclusion file containing every file path owned
    by a MIRROR package. The build pipeline consumes it via
    `mksquashfs -ef <file>`. The chroot still contains the MIRROR
    files; mksquashfs just skips them when writing the squashfs.

  --mode=names (Path-b, DEFAULT, operator-picked 2026-05-28)
    Emit a names list, one package per line, for
    `pkm iso-prep --packages-from <file>`. The build pipeline calls
    pkm iso-prep BEFORE mksquashfs, removing the MIRROR packages from
    the chroot entirely; mksquashfs then runs without `-ef`. Path-b
    lets pkm's runtime-dep graph enforce safety + makes the chroot
    state IS the final ISO state.

  --mode=archive-excludes (F41, decided 2026-07-22)
    Emit `var/lib/igos/archives/<name>-<version>.igos.tar.gz` lines —
    the EXACT archive basenames of every MIRROR package, built from
    the parsed package.yml name + version (the same fields pkg_archive
    names archives from), never from filename-splitting heuristics.
    Consumed by build-squashfs as per-file mksquashfs `-e` entries so
    iso_include:false ARCHIVES stop shipping inside the squashfs
    (pkm iso-prep removes installed payloads only; it never touched
    /var/lib/igos/archives, and every prior ISO carried the full
    archive corpus — mirror-only included). Exclusion, not deletion:
    the chroot's archive corpus stays intact as the mirror-publish
    source and the snapshot's banked state.

Usage:
    derive-iso-exclusions.py [--mode {paths,names,archive-excludes}]
                             [--chroot /mnt/igos]
                             [--packages <tree>/packages]
                             [--output /tmp/iso-exclusions.txt]

--packages defaults to the packages/ tree THIS script lives in, so the
copy that runs always classifies its own tree (inside the build VM that
is /mnt/intergenos/packages exactly as before).

Exit 0 on success. Exit 1 REFUSED if any package.yml fails to parse:
a package that cannot be classified is neither shipped nor excluded,
and continuing would let it ship silently — no output is written.
Prints summary to stderr.
"""

from __future__ import annotations

import argparse
import importlib
import re
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


_SHA256_SUFFIX_RE = re.compile(r" sha256:[0-9a-f]{64}$")


def extract_file_list(manifest: Path) -> list[str]:
    """Parse a pkm manifest's FILE LIST section into a list of paths.

    Manifest entries are "<path>", "<path>/" or "<path> sha256:<64 hex>".
    The hash annotation is stripped from the RIGHT, anchored at end of line —
    never by splitting on whitespace. Manifest paths may contain spaces
    (linux-firmware ships several, e.g. "brcmfmac43455-sdio.Raspberry Pi
    Foundation-Raspberry Pi 4 Model B.txt.xz"), and a first-token split
    truncates those to "brcmfmac43455-sdio.Raspberry", which is not a path
    that exists — the exclusion silently misses the real file. Anchoring is
    the same rule pkm's own _parse_manifest_line uses, and it is what makes
    the annotation safe to add to a manifest at all. Leading slashes are
    stripped so paths are relative to the chroot root (mksquashfs -ef expects
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
        path = _SHA256_SUFFIX_RE.sub("", line)
        if not path:
            continue
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
    ap.add_argument(
        "--mode",
        choices=["paths", "names", "archive-excludes"],
        default="names",
        help=(
            "Output mode. 'names' (DEFAULT, Path-b 2026-05-28): emit one "
            "package name per line for `pkm iso-prep --packages-from`. "
            "'archive-excludes' (F41 2026-07-22): emit exact "
            "var/lib/igos/archives/<name>-<version>.igos.tar.gz basenames "
            "of every MIRROR package for mksquashfs -e. "
            "'paths' (Path-a, historical): emit file paths for "
            "`mksquashfs -ef`."
        ),
    )
    ap.add_argument("--chroot", type=Path, default=Path("/mnt/igos"))
    ap.add_argument(
        "--packages",
        type=Path,
        default=_project_root / "packages",
        help="packages/ tree to classify (default: this script's own tree)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output file. Default: /tmp/iso-mirror-packages.txt for "
            "--mode=names, /tmp/iso-exclusions.txt for --mode=paths."
        ),
    )
    args = ap.parse_args()

    if args.output is None:
        args.output = {
            "names": Path("/tmp/iso-mirror-packages.txt"),
            "archive-excludes": Path("/tmp/iso-mirror-archive-excludes.txt"),
            "paths": Path("/tmp/iso-exclusions.txt"),
        }[args.mode]

    if not args.packages.is_dir():
        print(f"FATAL: packages dir not found: {args.packages}", file=sys.stderr)
        return 1
    # --mode=names doesn't need a chroot at all (works purely off package.yml
    # files in the source tree). Only --mode=paths needs the chroot manifests.
    if args.mode == "paths" and not args.chroot.is_dir():
        print(f"FATAL: chroot not found: {args.chroot}", file=sys.stderr)
        return 1

    excluded_paths: list[str] = []
    mirror_packages: list[str] = []
    mirror_versions: dict[str, str] = {}
    iso_packages: list[str] = []
    missing_manifests: list[str] = []
    parse_failures: list[str] = []

    # ships_as-first resolution (same rule the SBOM generator applies): a
    # ship-name claimed by a `ships_as:` declarer takes THAT recipe's
    # iso_include — a bare-named twin recipe (toolchain glibc/m4/ncurses)
    # must never contribute its tier default to the SHIP name. Without
    # this, adding `toolchain` to NON_ISO_DEFAULT_TIERS put the ship-names
    # glibc/m4/ncurses into the prune + archive-exclude sets, and iso-prep
    # deleted the real installed payloads (RC001 squashfs leg, 2026-08-15).
    templates = []
    ships_declarers: dict[str, object] = {}
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
                parse_failures.append(f"{yml}: {e}")
                continue
            templates.append(pkg)
            if getattr(pkg, "ships_as", None):
                ships_declarers[pkg.ships_as] = pkg

    for pkg in templates:
        ship_name = pkg.ships_as if getattr(pkg, "ships_as", None) else pkg.name
        declarer = ships_declarers.get(pkg.name)
        if declarer is not None and declarer is not pkg:
            # Same-named twin of a ships_as ship-name: the declarer decides
            # this name's classification; the twin contributes nothing.
            continue
        if pkg.iso_include:
            iso_packages.append(ship_name)
            continue
        mirror_packages.append(ship_name)
        mirror_versions[ship_name] = pkg.version
        if args.mode == "paths":
            manifest = find_manifest(args.chroot, ship_name, pkg.version)
            if manifest is None:
                missing_manifests.append(f"{ship_name}-{pkg.version}")
                continue
            excluded_paths.extend(extract_file_list(manifest))

    if parse_failures:
        print(
            f"[derive-iso-exclusions] REFUSED: {len(parse_failures)} "
            "package.yml file(s) failed to parse. An unparseable package "
            "joins neither the ISO list nor the exclusion list and would "
            "ship silently — refusing to emit any output (fail-closed):",
            file=sys.stderr,
        )
        for pf in parse_failures:
            print(f"[derive-iso-exclusions]   - {pf}", file=sys.stderr)
        return 1

    # Write output per mode
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "names":
        names_sorted = sorted(set(mirror_packages))
        header = (
            "# MIRROR package names — packages with iso_include resolving False.\n"
            "# Consumed by `pkm iso-prep --packages-from <this-file>` to evict\n"
            "# them from the chroot before mksquashfs assembles the squashfs.\n"
            "# Regenerated by scripts/derive-iso-exclusions.py --mode=names.\n"
        )
        args.output.write_text(header + "\n".join(names_sorted) + "\n")
        print(
            f"[derive-iso-exclusions] ISO packages:     {len(iso_packages)}",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] MIRROR packages:  {len(mirror_packages)}",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] Mode:             names "
            f"(Path-b — feeds pkm iso-prep)",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] Output:           {args.output}",
            file=sys.stderr,
        )
        return 0

    if args.mode == "archive-excludes":
        # Exact basenames from the parsed (name, version) — the same two
        # fields pkg_archive composes archive filenames from. No filename
        # splitting, so a mirror name that prefixes a shipped package's
        # name (the go / go-md2man class) can never over-match.
        lines = sorted(
            f"var/lib/igos/archives/{n}-{mirror_versions[n]}.igos.tar.gz"
            for n in set(mirror_packages)
        )
        args.output.write_text("\n".join(lines) + "\n")
        print(
            f"[derive-iso-exclusions] ISO packages:     {len(iso_packages)}",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] MIRROR packages:  {len(mirror_packages)}",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] Mode:             archive-excludes "
            f"(F41 — mirror-only archives kept OFF the squashfs)",
            file=sys.stderr,
        )
        print(
            f"[derive-iso-exclusions] Output:           {args.output}",
            file=sys.stderr,
        )
        return 0

    # --mode=paths (historical)
    excluded_paths_sorted = sorted(set(excluded_paths))
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
        f"[derive-iso-exclusions] Mode:             paths "
        f"(Path-a, historical — feeds mksquashfs -ef)",
        file=sys.stderr,
    )
    print(
        f"[derive-iso-exclusions] Output:           {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
