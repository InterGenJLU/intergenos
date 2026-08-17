#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed ISO metadata/payload sync gate (build-squashfs Step 2.7).

The squashfs ships three descriptions of the same package set: the chroot's
pkm database, its text manifests, and the archive corpus the installer
consumes. Nothing previously compared them, and the current release
candidate shipped a database describing a different build of 198 packages
than the archives beside it — so a live session's pkm answers were wrong
for ~24% of the corpus, one FHS skeleton directory an owner still claims
was absent from the image, and /etc/os-release shipped nearly empty. This
gate makes that split structurally unshippable.

For every archive that will ship, one streaming pass reads its .PKGINFO
(name, version, release) and hashes its payload members. Then:

  1. METADATA — the chroot database must record the package at exactly the
     archive's (version, release), and the text manifest
     var/lib/igos/packages/<name>-<version> must exist. A manifest carrying
     a PACKAGE RELEASE header must agree with the archive's pkgrel.
  2. PAYLOAD — every archive member must exist in the chroot. Regular
     files outside etc/ must content-match at least one claiming archive
     (bootstrap twins legitimately share byte-identical paths; the chroot
     carries whichever installed last). etc/ files are existence-checked
     only — the config phase legitimately rewrites them, the same
     PKM-E3/Class-2 policy pkm verify applies. Absent paths are excused
     only by pkm's own expected-absent classes (PI-E4/Component B).

The gate is CHECK-ONLY — it never mutates the chroot. Every violation
names its package and the standard remedy: redeploy the shipping archive
into the chroot (pkm install <name> --archive <path> --archive-trust
loose), which heals payload and registration through proven machinery,
then re-run the gate.

Exit 0 clean, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pkm.database import _is_expected_absent  # the PI-E4/Component-B policy

ARCHIVE_RE = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)\.igos\.tar\.gz$")
METADATA_MEMBERS = {".PKGINFO", "package.yml"}
# Archive-bundled metadata DIRECTORIES — the same contract as
# pkm/installer.py's _ARCHIVE_METADATA_DIRS: `.scripts/` carries the sealed
# lifecycle hooks (hookseal), which pkm fires from the extracted archive and
# deliberately neither deploys to the filesystem nor registers in the
# database. They are archive metadata, not payload — a disk-presence check
# against them reports a by-design absence (first observed on the ge9b-12
# hook-staging members, 45 findings on a correct image).
METADATA_DIRS = (".scripts",)
RELEASE_HEADER_RE = re.compile(r"^PACKAGE RELEASE:\s*(\d+)\s*$", re.MULTILINE)


def parse_pkginfo(text: str) -> dict:
    """Parse .PKGINFO key = value lines into a dict (first wins)."""
    info: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        info.setdefault(key.strip(), value.strip())
    return info


def stream_archive(archive: Path):
    """One streaming pass over a .igos.tar.gz.

    Returns (pkginfo_dict_or_None, entries) where entries maps a
    normalized member path to a tuple:
      ("file", sha256hex) | ("dir", None) | ("symlink", target) |
      ("hardlink", linked_path) | ("other", None)
    """
    pkginfo = None
    entries: dict[str, tuple[str, str | None]] = {}
    with tarfile.open(archive, mode="r|gz") as tf:
        for member in tf:
            name = member.name
            if name.startswith("./"):
                name = name[2:]
            name = name.rstrip("/")
            if not name or name in (".",):
                continue
            if any(name == d or name.startswith(d + "/")
                   for d in METADATA_DIRS):
                continue
            if name in METADATA_MEMBERS:
                if name == ".PKGINFO":
                    fobj = tf.extractfile(member)
                    if fobj is not None:
                        pkginfo = parse_pkginfo(
                            fobj.read().decode("utf-8", errors="replace"))
                continue
            if member.isdir():
                entries[name] = ("dir", None)
            elif member.issym():
                entries[name] = ("symlink", member.linkname)
            elif member.islnk():
                linked = member.linkname
                if linked.startswith("./"):
                    linked = linked[2:]
                entries[name] = ("hardlink", linked)
            elif member.isfile():
                fobj = tf.extractfile(member)
                h = hashlib.sha256()
                if fobj is not None:
                    for chunk in iter(lambda: fobj.read(1024 * 1024), b""):
                        h.update(chunk)
                entries[name] = ("file", h.hexdigest())
            else:
                entries[name] = ("other", None)
    return pkginfo, entries


def hash_file(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def load_excludes(path: Path | None) -> set[str]:
    """Basenames excluded from the squashfs (mirror-only archives)."""
    if path is None:
        return set()
    excludes = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            excludes.add(os.path.basename(line))
    return excludes


def db_installed_rows(db_path: Path) -> dict[str, dict]:
    """name -> {version, release} from the chroot database, read-only."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT name, version, release FROM installed "
            "WHERE superseded_by IS NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        # Schema predating the superseded_by column.
        rows = conn.execute(
            "SELECT name, version, release FROM installed").fetchall()
    finally:
        conn.close()
    return {r[0]: {"version": r[1], "release": r[2]} for r in rows}


def db_hook_managed_paths(db_path: Path) -> dict[str, set[str]]:
    """name -> set of root-relative paths whose content is hook-managed.

    Reads the files table's is_generated flag (pkm D-9/D-9b): a row a
    package's own sealed lifecycle hook created OR rewrote in place. Such a
    file's shipping content legitimately differs from the archive payload —
    the archive's payload plus its own hook reproduce the live state on
    every install — so the byte-compare below downgrades exactly these
    (owner, path) pairs to existence-only, the same treatment the etc/
    config class already gets. Everything undeclared-by-observation still
    byte-compares. Empty on a schema predating is_generated.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    out: dict[str, set[str]] = defaultdict(set)
    try:
        rows = conn.execute(
            "SELECT i.name, f.path FROM files f "
            "JOIN installed i ON f.package_id = i.id "
            "WHERE f.is_generated = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    for name, path in rows:
        out[name].add(path.lstrip("/"))
    return out


def load_supersedes(packages_dir: Path) -> dict[str, str]:
    """Map superseded-name -> superseding-name from recipe `supersedes:` lists.

    pkm's supersede mechanism removes the superseded twin's installed row
    while its archive legitimately keeps shipping (Forge's fresh-install
    flow consumes the archive; the superseding row owns the files in the
    live db). Without this map the no-row check misreads every declared
    twin (dbus / gobject-introspection-pass1 / libxml2 on the ge9b-10
    first firing) as the image lying about its contents.
    """
    result: dict[str, str] = {}
    if not packages_dir.is_dir():
        return result
    for yml in packages_dir.glob("*/*/package.yml"):
        try:
            text = yml.read_text()
        except OSError:
            continue
        m = re.search(r"^supersedes:\s*\n((?:\s*-\s*\S+.*\n)+)", text, re.M)
        if not m:
            continue
        superseding = yml.parent.name
        for line in m.group(1).splitlines():
            name = line.strip().lstrip("-").strip()
            if name:
                result[name] = superseding
    return result


def resolve_link(link_path: str, target: str) -> str:
    """Normalize a symlink target to its absolute resolved form.

    pkm relativizes absolute symlink targets at deploy time, so the on-disk
    text legitimately differs from the archive-recorded text while naming
    the SAME object (measured ge9b-10: 17 rows across iptables/libreoffice/
    linux-kernel/papirus — '/usr/sbin/x' vs '../sbin/x', trailing slashes,
    redundant './'). Compare resolved targets, not spellings.
    """
    if target.startswith("/"):
        return os.path.normpath(target)
    return os.path.normpath(
        os.path.join("/", os.path.dirname(link_path), target))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chroot", required=True, type=Path,
                    help="chroot root that becomes the squashfs")
    ap.add_argument("--archives-dir", type=Path, default=None,
                    help="default: <chroot>/var/lib/igos/archives")
    ap.add_argument("--db", type=Path, default=None,
                    help="default: <chroot>/var/lib/igos/pkm.db")
    ap.add_argument("--manifests-dir", type=Path, default=None,
                    help="default: <chroot>/var/lib/igos/packages")
    ap.add_argument("--archive-excludes", type=Path, default=None,
                    help="file listing mirror-only archive basenames "
                         "excluded from the squashfs (Step 2.6 output)")
    ap.add_argument("--report", type=Path, default=None,
                    help="write the full violation detail here")
    ap.add_argument("--packages-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "packages",
                    help="recipe tree for supersedes declarations "
                         "(default: <repo>/packages)")
    ap.add_argument("--progress-every", type=int, default=100)
    args = ap.parse_args()

    chroot = args.chroot
    if not chroot.is_dir():
        print(f"ERROR: chroot not a directory: {chroot}", file=sys.stderr)
        return 2
    archives_dir = args.archives_dir or chroot / "var/lib/igos/archives"
    db_path = args.db or chroot / "var/lib/igos/pkm.db"
    manifests_dir = args.manifests_dir or chroot / "var/lib/igos/packages"
    for p, what in ((archives_dir, "archives dir"), (db_path, "pkm.db"),
                    (manifests_dir, "manifests dir")):
        if not p.exists():
            print(f"ERROR: {what} not found: {p}", file=sys.stderr)
            return 2

    excludes = load_excludes(args.archive_excludes)
    installed = db_installed_rows(db_path)
    hook_managed = db_hook_managed_paths(db_path)
    supersedes_map = load_supersedes(args.packages_dir)

    archives = sorted(
        a for a in archives_dir.iterdir()
        if a.name.endswith(".igos.tar.gz")
        and a.name not in excludes
        and not a.name.endswith(".failed")
    )
    if not archives:
        print(f"ERROR: no shipping archives found in {archives_dir}",
              file=sys.stderr)
        return 2

    # Per-package violation buckets: pkg -> list of reason strings.
    violations: dict[str, list[str]] = defaultdict(list)
    # Union payload map across archives (bootstrap twins share paths):
    # path -> list of (owner, kind, detail)
    payload: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    archive_by_pkg: dict[str, str] = {}
    config_skipped = 0
    hook_skipped = 0

    print(f"[iso-metadata-sync] scanning {len(archives)} shipping archives")
    for i, archive in enumerate(archives, 1):
        if args.progress_every and i % args.progress_every == 0:
            print(f"[iso-metadata-sync]   ...{i}/{len(archives)}")
        m = ARCHIVE_RE.match(archive.name)
        try:
            pkginfo, entries = stream_archive(archive)
        except (tarfile.TarError, OSError, EOFError) as e:
            key = m.group("name") if m else archive.name
            violations[key].append(f"unreadable archive {archive.name}: {e}")
            continue
        if not pkginfo or "pkgname" not in pkginfo:
            key = m.group("name") if m else archive.name
            violations[key].append(
                f"{archive.name}: no .PKGINFO — archive is not self-describing")
            continue

        name = pkginfo["pkgname"]
        version = pkginfo.get("pkgver", "")
        release = pkginfo.get("pkgrel", "1")
        archive_by_pkg[name] = archive.name

        # 1. METADATA — database row must describe THIS archive.
        row = installed.get(name)
        if row is None:
            superseder = supersedes_map.get(name)
            if superseder and superseder in installed:
                # Declared supersede twin: row-less by pkm's own design
                # while the archive ships for Forge's fresh-install flow.
                pass
            else:
                violations[name].append(
                    f"shipping archive {archive.name} has NO installed row in "
                    f"the shipped pkm.db")
        else:
            if str(row["version"]) != version or str(row["release"]) != release:
                violations[name].append(
                    f"metadata split: shipped pkm.db records "
                    f"{row['version']}-r{row['release']}, shipping archive is "
                    f"{version}-r{release}")

        manifest = manifests_dir / f"{name}-{version}"
        if not manifest.exists():
            violations[name].append(
                f"text manifest var/lib/igos/packages/{name}-{version} "
                f"is absent from the image")
        else:
            try:
                header = RELEASE_HEADER_RE.search(manifest.read_text())
            except (OSError, UnicodeDecodeError):
                header = None
            if header and header.group(1) != release:
                violations[name].append(
                    f"text manifest carries PACKAGE RELEASE {header.group(1)}, "
                    f"shipping archive is r{release}")

        # Collect payload claims for the union pass.
        for path, (kind, detail) in entries.items():
            payload[path].append((name, kind, detail))

    # 2. PAYLOAD — one chroot walk over the union of claims.
    print(f"[iso-metadata-sync] verifying {len(payload)} claimed paths "
          f"against the chroot")
    for path, claims in payload.items():
        abs_path = chroot / path
        if not os.path.lexists(abs_path):
            excused = [
                owner for owner, _, _ in claims
                if _is_expected_absent(owner, path)
            ]
            if not excused:
                for owner, _, _ in claims:
                    violations[owner].append(f"claimed path absent: /{path}")
            continue

        kinds = {kind for _, kind, _ in claims}
        if kinds <= {"dir", "other"}:
            continue
        if os.path.isdir(abs_path) and not os.path.islink(abs_path):
            # A directory where at least one archive ships a non-directory.
            if "file" in kinds or "symlink" in kinds or "hardlink" in kinds:
                for owner, kind, _ in claims:
                    if kind != "dir":
                        violations[owner].append(
                            f"/{path} is a directory on the image but "
                            f"a {kind} in the archive")
            continue

        # etc/ = config class: existence satisfies (PKM-E3 / Class 2 —
        # the config phase and admin legitimately rewrite these).
        if path.startswith("etc/"):
            config_skipped += 1
            continue

        # Hook-managed content (pkm D-9/D-9b): the owning package's own
        # sealed lifecycle hook created or rewrote this file, observed and
        # recorded in the files table at hook time — archive payload plus
        # its own hook reproduce the live state on every install, so the
        # payload byte-compare would refuse a correct image (docbook-xml's
        # xmlcatalog-rewritten catalog.xml, the class's first live member).
        # Existence satisfies, same as the config class; every file-kind
        # claimant must be so recorded or the compare proceeds unchanged.
        file_claimants = [
            owner for owner, kind, _ in claims
            if kind in ("file", "hardlink")]
        if file_claimants and all(
                path in hook_managed.get(owner, ()) for owner in file_claimants):
            hook_skipped += 1
            continue

        if os.path.islink(abs_path):
            target = os.readlink(abs_path)
            resolved = resolve_link("/" + path, target)
            ok = any(
                kind == "symlink"
                and resolve_link("/" + path, detail) == resolved
                for _, kind, detail in claims
            ) or any(kind in ("file", "hardlink") for _, kind, _ in claims
                     ) and os.path.exists(abs_path)
            if not ok:
                for owner, kind, detail in claims:
                    if kind == "symlink":
                        violations[owner].append(
                            f"symlink /{path} points at {target!r}, archive "
                            f"records {detail!r}")
            continue

        file_hashes = {
            detail for _, kind, detail in claims if kind == "file" and detail}
        # Hardlink members carry their content at the linked path.
        for _, kind, detail in claims:
            if kind == "hardlink" and detail in payload:
                file_hashes.update(
                    d for _, k, d in payload[detail] if k == "file" and d)
        if not file_hashes:
            continue
        actual = hash_file(str(abs_path))
        if actual is None:
            for owner, _, _ in claims:
                violations[owner].append(f"unreadable on image: /{path}")
        elif actual not in file_hashes:
            for owner, _, _ in claims:
                violations[owner].append(
                    f"content differs from every claiming archive: /{path}")

    total_pkgs = len(archive_by_pkg)
    bad_pkgs = sorted(violations)
    lines = []
    for pkg in bad_pkgs:
        lines.append(f"\n  {pkg} — {len(violations[pkg])} finding(s):")
        for reason in violations[pkg][:20]:
            lines.append(f"    - {reason}")
        if len(violations[pkg]) > 20:
            lines.append(f"    ... and {len(violations[pkg]) - 20} more")
        arc = archive_by_pkg.get(pkg)
        if arc:
            # Two-step remedy, measured on the ge9b-10 first firing:
            # `pkm install` refuses a same-version already-installed package
            # and `pkm reinstall` resolves only cache/repo (not the archives
            # dir), so the working path is the framework §3.5 recovery —
            # DB-row drop (files kept) + explicit-archive install.
            lines.append(
                f"    remedy: chroot <chroot> python3 -c \"from pkm.database "
                f"import PackageDB; PackageDB().remove_installed('{pkg}')\""
                f" && chroot <chroot> pkm install {pkg} "
                f"--archive /var/lib/igos/archives/{arc} "
                f"--archive-trust loose  (then re-run this gate)")

    if args.report:
        with open(args.report, "w") as f:
            for pkg in bad_pkgs:
                for reason in violations[pkg]:
                    f.write(f"{pkg}\t{reason}\n")

    print(f"[iso-metadata-sync] {total_pkgs} archives checked, "
          f"{len(payload)} paths verified, {config_skipped} etc/ paths "
          f"existence-only, {hook_skipped} hook-managed paths "
          f"existence-only")
    if bad_pkgs:
        print(f"[iso-metadata-sync] FAIL — {len(bad_pkgs)} package(s) whose "
              f"shipped metadata or image payload does not match the "
              f"archives this ISO ships:", end="")
        print("\n".join(lines))
        print(f"\n[iso-metadata-sync] The image would lie about its own "
              f"contents. Fix by redeploying the named archives into the "
              f"chroot (remedies above), never by editing metadata by hand.")
        return 1
    print("[iso-metadata-sync] PASS — database, manifests, image payload "
          "and archives all describe the same build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
