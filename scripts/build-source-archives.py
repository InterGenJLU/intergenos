#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""build-source-archives.py — Emit .igos.src.tar.gz source archives.

Delivers on the corresponding-source commitment in SOURCES.md §2 by
bundling every InterGenOS-built package's source artifacts into a
single archive named to match its binary counterpart:

    binary:  <name>-<version>-<release>.igos.tar.gz
    source:  <name>-<version>-<release>.igos.src.tar.gz

Per SOURCES.md §2 and the §3 layout, each source archive contains:
    1. EVERY declared upstream source (unmodified; each filename + sha256
       matches its entry in the package.yml source: list). A package that
       declares several inputs — a library plus the two companion tarballs it
       is built against, say — gets all of them, because the corresponding
       source of what was built is all of what it was built from. Bundling
       only the first entry would understate the build.
    2. A `<tarball>.sha256` hash file beside every bundled tarball
       (sha256sum -c compatible). The value emitted is VERIFIED at emission:
       the staged file is hashed and compared against the package.yml pin,
       and a mismatch refuses the archive — a hash file that disagrees with
       the bytes beside it is the lie this member exists to preclude.
       Pin-less inputs (`generated: true` first-party tarballs carry no pin)
       get the computed hash, so the archive stays self-verifying.
    3. Every patch the InterGenOS build applies (packages/<t>/<p>/patches/*.patch)
    4. The build script (packages/<t>/<p>/build.sh)
    5. The package metadata (packages/<t>/<p>/package.yml)
    6. Any sidecar artifacts the build composes into the final binary,
       listed via the optional `sources_extra:` field in package.yml
       (e.g., config/kernel/fragments/*.config for the kernel package)
    7. README.SOURCES — the build-reproduction instructions SOURCES.md §3
       promises, rendered per-archive from the template below.

Packages with no upstream source (intergenos-keyring, intergenos-legal,
etc. with `source: []`) are skipped — they have no upstream corresponding
source obligation; their build.sh + package.yml are already in the
shipped repository's git tree.

NON-REDISTRIBUTABLE INPUTS. A source entry may declare
`redistributable: false` (parser.py Source.redistributable). Such an input
is fetched and used to build, but its bytes are never placed in a source
archive — republishing them is exactly what we have no right to do. This
generator refuses to bundle one and writes NON-REDISTRIBUTABLE.txt in its
place, naming the file, the vendor URL it came from, and its sha256, so the
corresponding-source archive states plainly what is absent and where the
vendor publishes it. Writing that note is not optional: the omission is
always STATED, never silent, because an unstated hole in a
corresponding-source archive is worse than a stated one. First user: the
NVIDIA CUDA toolkit runfile that compute/llama-cpp-cuda compiles against
(nvcc is not redistributable).

Usage:
    scripts/build-source-archives.py
    scripts/build-source-archives.py --package openssl
    scripts/build-source-archives.py --output-dir /tmp/sources

Exit non-zero on any per-package failure that isn't a clean skip.
"""

import argparse
import gzip
import hashlib
import os
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
    """Derive the upstream tarball filename from a source: list entry.

    Honors the optional `filename:` field (with ${name}/${version}
    substitution) — that is the canonical stored name the builder caches the
    download under (and verify-sources checks the sha against), e.g. a github
    archive URL ending in `v${version}.tar.gz` is stored as
    `${name}-${version}.tar.gz`. Only when no `filename:` override is declared
    does the URL basename match the stored name.
    """
    if src_entry.get("filename"):
        return substitute_url(src_entry["filename"], name, version)
    url = substitute_url(src_entry["url"], name, version)
    return url.split("/")[-1]


def _withheld_note(name: str, version: str, withheld: list[dict]) -> str:
    """Text placed in a source archive in place of inputs we may not republish.

    The point is that the archive never lies by omission: it names the file,
    says who publishes it, and gives the sha256 the build verified, so anyone
    reproducing the build can obtain exactly the bytes we used.
    """
    lines = [
        f"Corresponding source note for {name} {version}",
        "",
        "One or more inputs this package builds against are NOT included in",
        "this archive. They are proprietary vendor artifacts whose licences do",
        "not grant us redistribution rights, so InterGenOS fetches them from",
        "the vendor at build time and does not republish them.",
        "",
        "Everything InterGenOS itself wrote — the build script, the package",
        "metadata, and any patches — is included, and each withheld input is",
        "identified below by its vendor URL and by the SHA-256 the build",
        "verified before using it. Fetching that URL and checking that hash",
        "reproduces exactly the bytes this package was built against.",
        "",
    ]
    for entry in withheld:
        lines.append(f"  file:   {upstream_tarball_name(entry, name, version)}")
        lines.append(f"  from:   {substitute_url(entry['url'], name, version)}")
        lines.append(f"  sha256: {entry.get('sha256', '(unpinned — declaration error)')}")
        lines.append("")
    return "\n".join(lines)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _readme_sources(
    name: str,
    version: str,
    release,
    bundled: list[str],
    has_patches: bool,
    has_extras: bool,
    has_withheld: bool,
) -> str:
    """Render README.SOURCES for one archive.

    The prose is the ratified compliance text (decided 2026-08-04); only the
    <...> placeholders are filled per archive — the package identity and the
    member list. The class lines (patches/, extras/, NON-REDISTRIBUTABLE.txt)
    appear only when the member is present, so CONTENTS lists what the archive
    actually holds rather than what an archive may hold.
    """
    nvr = f"{name} {version}-{release}"
    binary = f"{name}-{version}-{release}.igos.tar.gz"

    # CONTENTS column: member names padded to one deterministic width so the
    # annotations align regardless of filename length.
    members: list[tuple[str, list[str]]] = []
    first = True
    for fname in bundled:
        if first:
            members.append((fname, [
                "every source input package.yml declares",
                "(one per declared entry, not only the first)",
            ]))
            members.append((f"{fname}.sha256", [
                "the SHA256 recorded for that input, one file",
                "per tarball, sha256sum -c compatible",
            ]))
            first = False
        else:
            members.append((fname, []))
            members.append((f"{fname}.sha256", []))
    if has_patches:
        members.append(("patches/", ["patches the build applies via build.sh"]))
    members.append(("build.sh", ["the build script — the recipe's phases as run"]))
    members.append(("package.yml", [
        "package metadata: version, declared source",
        "inputs with their SHA256 pins, dependencies,",
        "and installed-file declarations",
    ]))
    if has_extras:
        members.append(("extras/", ["sidecar inputs the build composes in"]))
    if has_withheld:
        members.append(("NON-REDISTRIBUTABLE.txt", [
            "a declared input cannot be republished: this",
            "names the file, the vendor URL, and the",
            "SHA256 the build verified",
        ]))
    members.append(("README.SOURCES", ["this file"]))

    width = max(len(m) for m, _ in members) + 3
    contents_lines = []
    for member, notes in members:
        if notes:
            contents_lines.append(f"  {member:<{width}}{notes[0]}")
            contents_lines.extend(f"  {'':<{width}}{note}" for note in notes[1:])
        else:
            contents_lines.append(f"  {member}")
    contents = "\n".join(contents_lines)

    return f"""InterGenOS corresponding source — {nvr}

This archive is the complete corresponding source for the binary package
{binary} as distributed at
https://repo.intergenos.org/.

CONTENTS
{contents}

VERIFYING THIS ARCHIVE
  1. This archive's own SHA256 is recorded in the signed package index
     (InterGenOS.db, signature InterGenOS.db.sig) alongside the binary
     archive's hash — one signature covers both.
  2. Each upstream tarball here can be checked with
     `sha256sum -c <upstream-tarball>.sha256`; the same value is pinned in
     package.yml's source entry.

REPRODUCING THE BINARY
  The binary was produced by the InterGenOS build system from exactly these
  inputs, inside the InterGenOS build environment:
  1. Obtain the InterGenOS source tree at the release this package was cut
     from: https://github.com/InterGenJLU/intergenos — it provides the
     build orchestrator and the build-environment definitions.
  2. Stage the upstream tarball(s) from this archive into build/sources/.
  3. Build the package with the tree's builder — the build.sh and
     package.yml in this archive are the recipe the tree carries at that
     release. Python-tier packages:
         python3 igos-build.py --build --tracked --only {name} \\
             --sources-dir <sources-path>
     Bash-tier (core/base) packages build through the tree's
     scripts/chroot-build-<tier>.sh driver, which runs the same build.sh.
  4. The toolchain that compiled the shipped binary is itself distributed
     as InterGenOS packages under this same corresponding-source policy;
     building inside the InterGenOS build chroot (docs/operations/ in the
     source tree) reproduces the shipped configuration.
"""


def build_source_archive(
    pkg_dir: Path,
    meta: dict,
    sources_dir: Path,
    output_dir: Path,
    repo_root: Path,
) -> tuple[str, str]:
    """Build a single .igos.src.tar.gz. Returns ("ok"|"skip"|"fail", message)."""
    name = meta.get("name")
    # A dual-name twin (ships_as:) publishes its binary under the ship name, so
    # its corresponding-source archive must carry the same identity — the
    # correspondence gate matches the staged .PKGINFO name, never the recipe
    # dir. The recipe name still governs upstream tarball lookup below: the
    # builder caches downloads under ${name} substituted from the RECIPE.
    ship_name = meta.get("ships_as") or name
    version = str(meta.get("version", ""))
    release = meta.get("release", 1)
    src_field = meta.get("source", []) or []

    if not src_field:
        return ("skip", "no upstream source (pure-data package)")

    # Every entry is inspected, so a malformed one is named by its index rather
    # than hiding behind whichever entry happened to be read.
    for i, entry in enumerate(src_field):
        if not isinstance(entry, dict):
            return ("fail", f"source[{i}] is not a mapping (got {type(entry).__name__})")
        if "url" not in entry:
            return ("fail", f"source[{i}] has no url key")

    # Inputs the vendor's terms do not let us republish. They are fetched and
    # built against, never bundled here; the omission is stated instead, in
    # NON-REDISTRIBUTABLE.txt. Both lists are taken across EVERY declared entry:
    # the exclusion is a property of the entry's declaration, not of its index.
    withheld = [e for e in src_field if e.get("redistributable", True) is False]
    to_bundle = [e for e in src_field if e.get("redistributable", True) is not False]

    # Two entries resolving to one stored filename would silently overwrite each
    # other in the staging directory, and the archive would claim to carry both
    # while carrying one. Refuse instead: a corresponding-source archive that
    # quietly drops an input is the defect this generator exists to prevent.
    staged: dict[str, int] = {}
    for i, entry in enumerate(to_bundle):
        fname = upstream_tarball_name(entry, name, version)
        if fname in staged:
            return ("fail",
                    f"two source entries resolve to the same stored filename "
                    f"{fname!r} (entries {staged[fname]} and {i} of the "
                    f"republishable set) — give one an explicit filename:")
        staged[fname] = i

    # Emit nothing rather than emit an archive that is short a declared input we
    # DO have the right to publish. Every absent file is named, so the message
    # says what to fetch instead of only that something was wrong.
    absent = sorted(f for f in staged if not (sources_dir / f).exists())
    if absent:
        return ("skip",
                f"upstream tarball(s) not in {sources_dir.name}/: "
                f"{', '.join(absent)}")

    archive_name = f"{ship_name}-{version}-{release}.igos.src.tar.gz"
    archive_path = output_dir / archive_name

    # Verify every republishable input's bytes BEFORE anything is emitted. The
    # .sha256 member states "these are the bytes"; emitting it unverified would
    # let a corrupted or stale staging copy ship under a hash that contradicts
    # it. A pinned entry must hash to its pin (mismatch = refusal naming both
    # values); a pin-less entry (generated: true first-party tarballs carry no
    # pin) gets its computed hash, so the archive stays self-verifying.
    emitted_sha: dict[str, str] = {}
    for fname, idx in sorted(staged.items()):
        actual = _file_sha256(sources_dir / fname)
        pin = to_bundle[idx].get("sha256")
        if pin and pin != actual:
            return ("fail",
                    f"{fname}: staged bytes hash {actual} but package.yml "
                    f"pins {pin} — refusing to publish a corresponding-source "
                    f"archive whose input contradicts its declared hash")
        emitted_sha[fname] = actual

    with tempfile.TemporaryDirectory() as tmp:
        # SOURCES.md §3 names the archive's top directory
        # <name>-<version>-<release>/ — the layout promise, matched here.
        stage = Path(tmp) / f"{ship_name}-{version}-{release}"
        stage.mkdir()

        for fname in sorted(staged):
            shutil.copy(sources_dir / fname, stage / fname)
            (stage / f"{fname}.sha256").write_text(
                f"{emitted_sha[fname]}  {fname}\n", encoding="utf-8")

        if withheld:
            (stage / "NON-REDISTRIBUTABLE.txt").write_text(
                _withheld_note(ship_name, version, withheld), encoding="utf-8")

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

        extras_field = meta.get("sources_extra", []) or []
        for extra in extras_field:
            extra_src = repo_root / extra
            if not extra_src.exists():
                return ("fail", f"sources_extra entry not found: {extra}")
            extra_dst = stage / "extras" / extra
            extra_dst.parent.mkdir(parents=True, exist_ok=True)
            if extra_src.is_file():
                shutil.copy(extra_src, extra_dst)
            else:
                shutil.copytree(extra_src, extra_dst, dirs_exist_ok=True)

        (stage / "README.SOURCES").write_text(
            _readme_sources(
                ship_name, version, release,
                bundled=sorted(staged),
                has_patches=(stage / "patches").is_dir(),
                has_extras=bool(extras_field),
                has_withheld=bool(withheld),
            ),
            encoding="utf-8")

        write_deterministic_archive(archive_path, stage)

    # The counts are in the line so a publish log shows completeness per package
    # instead of only that something was written.
    detail = f"{len(staged)} of {len(src_field)} declared source(s)"
    if withheld:
        detail += f", {len(withheld)} withheld and stated"
    return ("ok",
            f"{archive_name} ({archive_path.stat().st_size} bytes, {detail})")


def write_deterministic_archive(archive_path: Path, stage: Path) -> None:
    """Write stage/ to archive_path as a BYTE-REPRODUCIBLE .tar.gz.

    Identical inputs must yield identical bytes. The mirror publish hardlinks
    unchanged archives instead of re-uploading them (rsync --checksum
    --link-dest), so a nondeterministic writer silently forces a full
    re-upload of the entire corresponding-source corpus on every publish —
    ~24 G of wire and disk for content that never changed.

    Four independent sources of nondeterminism are pinned here; each one alone
    is enough to change every byte downstream of it:

      1. The gzip header carries a timestamp and the origin filename.
         -> GzipFile(mtime=epoch, filename="").
      2. ``tarfile.add(recursive=True)`` walks in ``os.listdir`` order, which
         is filesystem order, not sorted. -> explicit sorted walk.
      3. Member mtimes. ``shutil.copy`` does NOT preserve mtime, so every
         staged file was stamped "now" on each run. -> pinned to the epoch.
      4. uid/gid/uname/gname of whoever ran the build. -> normalised to root.

    ``SOURCE_DATE_EPOCH`` is honoured (the same knob build-iso.sh uses);
    it defaults to 0 rather than the current time, because defaulting to
    "now" is exactly the bug.
    """
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))

    def normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.mtime = epoch
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        if info.isdir():
            info.mode = 0o755
        else:
            # Preserve only the executable bit; drop per-host umask variation.
            info.mode = 0o755 if info.mode & 0o100 else 0o644
        return info

    members = [stage]
    for root, dirs, files in os.walk(stage):
        dirs.sort()
        root_path = Path(root)
        members.extend(root_path / d for d in dirs)
        members.extend(root_path / f for f in sorted(files))
    # Sort the flattened list so ordering does not depend on walk mechanics.
    members = sorted(set(members), key=str)

    with open(archive_path, "wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=epoch
        ) as gz:
            # Pin the tar format explicitly — tarfile's default has changed
            # across Python versions, and the format is part of the bytes.
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for member in members:
                    if member == stage:
                        arcname = stage.name
                    else:
                        arcname = str(Path(stage.name) / member.relative_to(stage))
                    tar.add(member, arcname=arcname, recursive=False, filter=normalise)


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
