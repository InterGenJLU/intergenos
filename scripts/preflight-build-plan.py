#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Print the exact build-vs-skip plan for a targeted build, before launch.

A targeted build off a golden substrate rebuilds "the delta". Deciding what the
delta IS has been done by reading a git range and by eye, and both have shipped
wrong answers — a stale package rode a candidate image because nothing at build
time was going to catch it. This prints the decision the build is going to make,
per package, per tier, while it is still cheap to disagree with.

The two tiers decide differently, and the difference is the whole point.

PYTHON TIERS (desktop, extra, compute, ai) — the builder's --skip-built layer
answers this itself. This tool reproduces that answer rather than inventing a
parallel one: a package with no tracked manifest builds; a package whose
template hash no longer matches the TEMPLATE_HASH marker recorded in its
manifest builds; everything else is skipped. The hash is computed with the
builder's own igos-build.content_hash.template_hash, not a local reimplementation,
so this tool cannot drift from the decision it reports. Note that the hash folds
first-party source content as well as package.yml and build.sh, so a source-only
edit does flip it for packages whose source lives outside the recipe.

BASH TIERS (core, base — the chroot-build-ch8 / core-extra / ch10 / base drivers)
— there is NO skip-built layer here at all. Nothing at build time compares a
recipe to what was built from it, so a bash-tier package whose recipe advanced
before the delta base is invisible to a git range AND unenforced by the build.
This encodes the framework's mandatory sweep as code, and it makes two separate
checks because currency and deployment are separate facts:

  CURRENCY   — the tree's (version, release) from package.yml against the
               (pkgver, pkgrel) recorded in the sealed archive's .PKGINFO.
               The archive header is the only honest instrument here. The pkm
               database's release column is NOT used and must not be: it was
               found reset to 1 corpus-wide on a live substrate, so a
               DB-based sweep reads "current" off corrupted data. Version
               ordering is pkm.version.compare, the same comparison pkm itself
               uses for upgrades.

  DEPLOYMENT — whether the package is actually ON the substrate: an installed
               text manifest, a database row, and its files on disk. Archive
               currency proves nothing about deployment. A package can hold a
               perfectly current banked archive and be entirely absent from the
               chroot — 15 core-tier packages were in exactly that state on a
               live substrate, current archives banked, zero files, zero rows,
               and a currency-only sweep called them fine right up until a
               dependent package failed to configure.

Verdicts, bash tiers: CURRENT, REBUILD (the tree is ahead of the archive),
DEPLOY (the archive is current but the package is not installed), MISSING (no
archive at all).

READ-ONLY. Recipes, archive headers, text manifests and the filesystem are
opened for reading; the database is opened through an immutable read-only URI
with query_only set, so this cannot take a write lock on a substrate a build may
be using. It is a planning instrument, not a gate: it exits 0 once it has
produced a plan, whatever that plan says, and exits 2 only when it could not
produce one. A tool that refuses a build is a different tool, and conflating the
two would make people stop running this one.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tarfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "igos-build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The builder's own hash and pkm's own version ordering. Imported, never
# reimplemented: a second copy of either would drift from the decision this
# tool exists to predict, and would do it silently.
import importlib

_parser_mod = importlib.import_module("igos-build.parser")
_content_hash_mod = importlib.import_module("igos-build.content_hash")
from pkm.version import VersionParseError, compare as version_compare  # noqa: E402

load_all_packages = _parser_mod.load_all_packages
template_hash = _content_hash_mod.template_hash

# Tiers the Python builder walks, where --skip-built applies.
PYTHON_TIERS = ("desktop", "extra", "compute", "ai")
# Tiers the bash drivers walk, where no skip-built layer exists.
BASH_TIERS = ("core", "base")
# toolchain is built before the chroot has a package database at all, so neither
# decision applies to it. It is reported as EXCLUDED with its count rather than
# filtered away — a package that silently vanishes from a plan is the failure
# mode this tool exists to prevent.
EXCLUDED_TIERS = ("toolchain",)

ARCHIVE_SUFFIX = ".igos.tar.gz"


# ---------------------------------------------------------------------------
# Substrate readers — all read-only
# ---------------------------------------------------------------------------

def read_pkginfo(archive: Path) -> dict | None:
    """Return the archive's .PKGINFO as a dict, or None if it carries none.

    Streams and stops at the header. The payload is deliberately not read: this
    tool answers "which build is banked here", and hashing hundreds of archives'
    contents to learn a version would make a preflight check cost more than the
    question is worth. The metadata/payload sync gate at squashfs time is what
    reads payloads.
    """
    try:
        with tarfile.open(archive, mode="r|gz") as tf:
            for member in tf:
                name = member.name[2:] if member.name.startswith("./") else member.name
                if name == ".PKGINFO":
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        return None
                    info: dict[str, str] = {}
                    text = fobj.read().decode("utf-8", errors="replace")
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        info.setdefault(key.strip(), value.strip())
                    return info
    except (tarfile.TarError, OSError):
        return None
    return None


def index_archives(archives_dir: Path) -> dict[str, list[tuple[str, int, Path]]]:
    """Map package name -> [(pkgver, pkgrel, path), ...] from archive headers.

    Keyed on the .PKGINFO pkgname rather than on the filename. The filename
    embeds a version, so a name-with-hyphens plus a version is ambiguous to
    parse and would mis-attribute archives between sibling packages; the header
    states the name outright.
    """
    index: dict[str, list[tuple[str, int, Path]]] = {}
    if not archives_dir.is_dir():
        return index
    for archive in sorted(archives_dir.iterdir()):
        if not archive.name.endswith(ARCHIVE_SUFFIX) or not archive.is_file():
            continue
        info = read_pkginfo(archive)
        if not info or "pkgname" not in info:
            # A metadata-less archive is reported by the caller, never counted
            # as evidence of currency.
            index.setdefault("", []).append(("", 0, archive))
            continue
        try:
            rel = int(info.get("pkgrel", 1))
        except (TypeError, ValueError):
            rel = 1
        index.setdefault(info["pkgname"], []).append(
            (info.get("pkgver", ""), rel, archive))
    return index


def open_db_readonly(db_path: Path) -> sqlite3.Connection | None:
    """Open pkm.db so that this tool cannot possibly write to it.

    immutable=1 additionally tells SQLite the file will not change underneath
    us, which means no locking of any kind — the point being that running this
    against a substrate a build is touching must never be able to block it.
    """
    if not db_path.is_file():
        return None
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = 1")
    return conn


def installed_names(conn: sqlite3.Connection | None) -> set[str]:
    """Names with a row in `installed`. Only the NAME is taken from the DB.

    The release column is deliberately not read anywhere in this tool. It was
    found reset to 1 across an entire substrate, so it cannot be used to decide
    currency; presence of a row is a different and still-trustworthy fact.
    """
    if conn is None:
        return set()
    try:
        return {r[0] for r in conn.execute("SELECT name FROM installed")}
    except sqlite3.Error:
        return set()


def manifest_paths(manifest: Path) -> list[str]:
    """Root-relative file paths from a text manifest's FILE LIST.

    Directory entries (trailing /) are skipped; a directory existing proves
    nothing about the payload. The ' sha256:<hex>' suffix is stripped from the
    right so paths containing spaces survive intact.
    """
    paths: list[str] = []
    in_files = False
    try:
        text = manifest.read_text(errors="surrogateescape")
    except OSError:
        return paths
    for line in text.splitlines():
        if line.strip() == "FILE LIST:":
            in_files = True
            continue
        if not in_files or not line.strip():
            continue
        entry = line
        marker = " sha256:"
        idx = entry.rfind(marker)
        if idx != -1 and len(entry) - idx == len(marker) + 64:
            entry = entry[:idx]
        if entry.endswith("/"):
            continue
        paths.append(entry)
    return paths


def files_present(chroot: Path, paths: list[str], limit: int = 0) -> tuple[int, int]:
    """Return (checked, missing) for manifest paths under the chroot.

    lstat only — existence, not content. Content verification belongs to
    `pkm verify`, which is a different question asked at a different time; this
    is answering "is this package on the substrate at all", the state in which
    15 packages were found with zero files present.

    limit > 0 stops after that many paths. The default is 0, meaning check
    every path: a sampled answer to "is anything here" would be a guess, and a
    guess that says CURRENT is exactly the failure this replaces.
    """
    checked = missing = 0
    for rel in paths:
        if limit and checked >= limit:
            break
        checked += 1
        if not os.path.lexists(str(chroot / rel.lstrip("/"))):
            missing += 1
    return checked, missing


# ---------------------------------------------------------------------------
# The two decisions
# ---------------------------------------------------------------------------

def python_verdict(pkg, manifests_dir: Path, sources_dir: Path,
                   has_archive: bool) -> tuple[str, str]:
    """Reproduce igos-build's --skip-built decision for one package.

    Mirrors builder.py's tracked-package branch exactly, including its quiet
    edge: when the package has no template_path the hash is empty and the
    builder skips. Reproducing that rather than "improving" it is the
    requirement — this tool is only useful if it says what the build will do.
    """
    manifest = manifests_dir / f"{pkg.name}-{pkg.version}"
    if not manifest.exists():
        return "BUILD", "manifest-less" if has_archive else "never-built"
    current = template_hash(pkg, sources_dir)
    if current:
        try:
            manifest_text = manifest.read_text(errors="surrogateescape")
        except OSError:
            return "BUILD", "manifest-unreadable"
        if f"TEMPLATE_HASH: {current}" not in manifest_text:
            return "BUILD", "hash-changed"
    return "SKIP", "already-tracked"


def bash_verdict(pkg, archives: list[tuple[str, int, Path]], chroot: Path,
                 manifests_dir: Path, db_names: set[str],
                 file_limit: int) -> tuple[str, str]:
    """Currency then deployment, in that order, for one bash-tier package."""
    if not archives:
        return "MISSING", "no archive banked"

    tree = (pkg.version, pkg.release)
    best = None
    for pkgver, pkgrel, path in archives:
        cand = (pkgver, pkgrel, path)
        if best is None:
            best = cand
            continue
        try:
            if version_compare((pkgver, pkgrel), (best[0], best[1])) > 0:
                best = cand
        except VersionParseError:
            continue
    pkgver, pkgrel, path = best

    try:
        cmp = version_compare(tree, (pkgver, pkgrel))
    except VersionParseError as e:
        # An unorderable version is stated, never assumed current. Guessing
        # either way here would be inventing the answer the tool was asked for.
        return "REBUILD", f"version unorderable ({e})"

    if cmp > 0:
        return "REBUILD", (f"tree {pkg.version}-{pkg.release} ahead of archive "
                           f"{pkgver}-{pkgrel}")

    reasons = []
    manifest = manifests_dir / f"{pkg.name}-{pkg.version}"
    if not manifest.exists():
        reasons.append("no installed manifest")
    if pkg.name not in db_names:
        reasons.append("no database row")
    if manifest.exists():
        checked, missing = files_present(chroot, manifest_paths(manifest),
                                         file_limit)
        if missing:
            reasons.append(f"{missing}/{checked} files absent")

    if reasons:
        return "DEPLOY", (f"archive {pkgver}-{pkgrel} current but "
                          + "; ".join(reasons))

    note = ""
    if cmp < 0:
        # Not a verdict of its own — the substrate is not stale — but an
        # archive newer than the recipe means someone's tree is behind, and
        # saying CURRENT without saying that would hide it.
        note = (f" (archive {pkgver}-{pkgrel} AHEAD of tree "
                f"{pkg.version}-{pkg.release} — tree may be stale)")
    return "CURRENT", f"archive {pkgver}-{pkgrel} banked and deployed{note}"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def build_plan(tree: Path, chroot: Path, archives_dir: Path,
               manifests_dir: Path, db_path: Path, sources_dir: Path,
               tiers: tuple[str, ...] | None = None,
               file_limit: int = 0) -> dict:
    packages_dir = tree / "packages"
    if not packages_dir.is_dir():
        raise FileNotFoundError(f"{packages_dir} is not a directory")

    pkgs = load_all_packages(packages_dir)
    archive_index = index_archives(archives_dir)
    conn = open_db_readonly(db_path)
    try:
        db_names = installed_names(conn)
    finally:
        if conn is not None:
            conn.close()

    rows = []
    for pkg in sorted(pkgs, key=lambda p: (p.tier or "", p.name)):
        tier = pkg.tier or ""
        if tiers and tier not in tiers:
            continue
        archives = archive_index.get(pkg.name, [])
        if tier in EXCLUDED_TIERS:
            verdict, reason, lane = "EXCLUDED", "built before the package database exists", "n/a"
        elif tier in PYTHON_TIERS:
            verdict, reason = python_verdict(pkg, manifests_dir, sources_dir,
                                             bool(archives))
            lane = "python"
        elif tier in BASH_TIERS:
            verdict, reason = bash_verdict(pkg, archives, chroot, manifests_dir,
                                           db_names, file_limit)
            lane = "bash"
        else:
            # An unrecognized tier is reported, not dropped. Silence here would
            # mean a package with a new tier quietly leaves every future plan.
            verdict, reason, lane = "UNCLASSIFIED", f"unrecognized tier {tier!r}", "unknown"
        rows.append({
            "name": pkg.name, "version": pkg.version, "release": pkg.release,
            "tier": tier, "lane": lane, "verdict": verdict, "reason": reason,
        })

    orphan_archives = [str(p) for _v, _r, p in archive_index.get("", [])]
    return {
        "tree": str(tree), "chroot": str(chroot),
        "archives_dir": str(archives_dir), "manifests_dir": str(manifests_dir),
        "db": str(db_path), "db_present": db_path.is_file(),
        "sources_dir": str(sources_dir),
        "rows": rows, "metadata_less_archives": orphan_archives,
    }


def render(plan: dict, show_all: bool) -> str:
    out = []
    out.append("PREFLIGHT BUILD PLAN")
    out.append(f"  tree      : {plan['tree']}")
    out.append(f"  chroot    : {plan['chroot']}")
    out.append(f"  archives  : {plan['archives_dir']}")
    out.append(f"  manifests : {plan['manifests_dir']}")
    out.append(f"  database  : {plan['db']}"
               + ("" if plan["db_present"] else "  [ABSENT — deployment rows unknown]"))
    out.append("")

    actionable = {"BUILD", "REBUILD", "DEPLOY", "MISSING", "UNCLASSIFIED"}
    by_tier: dict[str, list[dict]] = {}
    for row in plan["rows"]:
        by_tier.setdefault(row["tier"], []).append(row)

    for tier in sorted(by_tier):
        rows = by_tier[tier]
        counts = Counter(r["verdict"] for r in rows)
        lane = rows[0]["lane"]
        out.append(f"[{tier}]  lane={lane}  packages={len(rows)}")
        shown = [r for r in rows if show_all or r["verdict"] in actionable]
        for r in sorted(shown, key=lambda r: (r["verdict"], r["name"])):
            out.append(f"    {r['verdict']:<12} {r['name']}-{r['version']}-{r['release']}"
                       f"  — {r['reason']}")
        if not shown:
            out.append("    (nothing to do)")
        out.append("    " + "  ".join(f"{v}={n}" for v, n in sorted(counts.items())))
        out.append("")

    if plan["metadata_less_archives"]:
        out.append("ARCHIVES WITHOUT .PKGINFO — currency cannot be read from these:")
        for p in plan["metadata_less_archives"]:
            out.append(f"    {p}")
        out.append("")

    totals = Counter(r["verdict"] for r in plan["rows"])
    work = sum(n for v, n in totals.items() if v in actionable)
    out.append("TOTALS  " + "  ".join(f"{v}={n}" for v, n in sorted(totals.items())))
    out.append(f"TOTAL PACKAGES REQUIRING ACTION: {work} of {len(plan['rows'])}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Print the exact build-vs-skip plan for a targeted build.")
    ap.add_argument("--tree", default=str(REPO_ROOT),
                    help="recipe tree to plan from (default: this checkout)")
    ap.add_argument("--chroot", required=True,
                    help="substrate root to plan against (e.g. /mnt/igos)")
    ap.add_argument("--archives-dir", default=None,
                    help="default <chroot>/var/lib/igos/archives")
    ap.add_argument("--manifests-dir", default=None,
                    help="default <chroot>/var/lib/igos/packages")
    ap.add_argument("--db", default=None,
                    help="default <chroot>/var/lib/igos/pkm.db")
    ap.add_argument("--sources-dir", default=None,
                    help="source tarball dir for the template hash "
                         "(default <chroot>/sources)")
    ap.add_argument("--tier", action="append", dest="tiers", default=None,
                    help="limit to a tier; repeatable")
    ap.add_argument("--max-files", type=int, default=0,
                    help="cap per-package file existence checks (0 = check all, "
                         "the default; a cap makes the deployment answer a sample)")
    ap.add_argument("--all", action="store_true",
                    help="list every package, including ones needing no action")
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = ap.parse_args(argv)

    tree = Path(args.tree).resolve()
    chroot = Path(args.chroot).resolve()
    igos = chroot / "var" / "lib" / "igos"
    archives_dir = Path(args.archives_dir) if args.archives_dir else igos / "archives"
    manifests_dir = Path(args.manifests_dir) if args.manifests_dir else igos / "packages"
    db_path = Path(args.db) if args.db else igos / "pkm.db"
    sources_dir = Path(args.sources_dir) if args.sources_dir else chroot / "sources"

    if not chroot.is_dir():
        print(f"[build-plan] SETUP ERROR: chroot {chroot} is not a directory",
              file=sys.stderr)
        return 2

    try:
        plan = build_plan(tree, chroot, archives_dir, manifests_dir, db_path,
                          sources_dir, tuple(args.tiers) if args.tiers else None,
                          args.max_files)
    except (FileNotFoundError, OSError, sqlite3.Error) as e:
        print(f"[build-plan] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    if args.json:
        import json
        print(json.dumps(plan, indent=2))
    else:
        print(render(plan, args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
