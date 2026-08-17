#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""ISO-closure preflight gate — fail-closed host-side runtime-edge check.

Turns a one-off ``iso_include`` sweep into a standing pre-launch gate
(decided "run those gates prior to launching"). Runs in the ~1s
class off the package.yml tree alone — no chroot, no sources — so it guards
every window between now and any launch.

What it catches (all three HARD, exit 1):

  ISO-CLOSURE-VIOLATION
      A package whose EFFECTIVE iso_include is True declares a
      ``dependencies.runtime`` edge on a package whose EFFECTIVE iso_include
      is False. The shipped package would be missing a runtime dependency
      on the live ISO (the GE-01 L28 mpv→libvdpau class — an evicted
      MIRROR-only dep behind a shipped app). ``pkm iso-prep`` aborts on this
      at squashfs time; this gate surfaces it host-side, before the burn.

  ISO-CLOSURE-DANGLING-DEP
      A ``dependencies.runtime`` name that resolves to no package.yml in the
      tree — a typo or a removed package. Dangling regardless of tier.

  ISO-CLOSURE-NONBOOLEAN
      An explicit ``iso_include:`` whose YAML value is not a real boolean.
      The parser coerces it with ``bool(...)`` (parser.py:575), and
      ``bool("false") is True`` — so a quoted ``"false"`` (or any non-bool)
      silently SHIPS a package that was meant to be MIRROR-only. The parser
      accepts it; this gate refuses it loudly. (Contrast ``installer_hooks``,
      which the parser type-checks — ``iso_include`` does not.)

EFFECTIVE iso_include semantics come from the PARSER, not a re-derivation:
this gate imports ``igos-build/parser.py`` and reads ``Package.iso_include``,
which parser.py:571-575 computes as "explicit override wins, otherwise
``tier != 'extra'``". Importing (rather than replicating) is deliberate — the
gate cannot drift from build-time semantics. Same import pattern + same
effective-value read as ``scripts/derive-iso-exclusions.py``.

Exit codes:
  0 — clean (no HARD findings); prints a one-line summary
  1 — HARD findings present (build launch must halt)
  2 — environment problem (packages dir absent, etc.)

Usage:
  scripts/preflight-iso-closure.py                 # gate mode
  scripts/preflight-iso-closure.py --verbose       # list every finding
  scripts/preflight-iso-closure.py --report        # + JSON artifact to build/
  scripts/preflight-iso-closure.py --root /alt     # override repo root
  scripts/preflight-iso-closure.py --packages DIR  # override packages/ dir

Environment:
  INTERGENOS_ROOT   repo autodetection override
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Repo + parser import
# ---------------------------------------------------------------------------
# Import the parser from the in-tree igos-build package so EFFECTIVE
# iso_include matches build-time semantics EXACTLY (parser.py:571-575).
# The package directory uses a hyphen, so we resolve it via importlib with
# the repo root on sys.path — same pattern as derive-iso-exclusions.py and
# igos-build.py.

_MISSING = object()


def discover_repo_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def load_parser(repo: Path):
    """Return the igos-build.parser module (parse_template + friends)."""
    sys.path.insert(0, str(repo))
    return importlib.import_module("igos-build.parser")


# ---------------------------------------------------------------------------
# Raw-YAML footgun probe (the parser can't tell us — it coerces)
# ---------------------------------------------------------------------------

def raw_iso_include(yml_path: Path):
    """Return the RAW ``iso_include`` value from a package.yml.

    Returns ``_MISSING`` if the key is absent (or the file can't be read as a
    mapping). The parser applies ``bool(...)`` to whatever is here; we need
    the pre-coercion value to catch the non-boolean footgun that the parser
    silently accepts.
    """
    try:
        raw = yaml.safe_load(yml_path.read_text())
    except Exception:
        return _MISSING
    if not isinstance(raw, dict):
        return _MISSING
    return raw.get("iso_include", _MISSING)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan(repo: Path, packages_dir: Path) -> tuple[list[dict], dict]:
    """Return (findings, stats). findings carry a HARD ``type``; stats is a
    summary dict for the clean-pass one-liner."""
    parser_mod = load_parser(repo)
    parse_template = parser_mod.parse_template

    findings: list[dict] = []
    packages = []            # (name, pkg, rel_id)
    parse_notes: list[dict] = []

    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml = pkg_dir / "package.yml"
            if not yml.is_file():
                continue
            rel_id = f"{tier_dir.name}/{pkg_dir.name}"

            # Footgun probe FIRST — reads the raw value the parser coerces.
            raw_val = raw_iso_include(yml)
            if raw_val is not _MISSING and raw_val is not None \
                    and not isinstance(raw_val, bool):
                findings.append({
                    "type": "ISO-CLOSURE-NONBOOLEAN",
                    "package": rel_id,
                    "detail": (
                        f"iso_include: {raw_val!r} "
                        f"(type {type(raw_val).__name__}) is not a boolean — "
                        f"parser coerces via bool(), and bool() of a non-empty "
                        f"value is True, so this SHIPS a package that may be "
                        f"meant MIRROR-only. Use an unquoted true/false."
                    ),
                    "file": str(yml),
                })

            try:
                pkg = parse_template(yml)
            except Exception as e:
                # A package whose template fails to parse has NO analyzed
                # runtime edges — closure cannot be certified over a partial
                # inventory, so this is a HARD finding, not a note. (Template
                # validity is also owned by sibling gates, but "someone else
                # reports it" left THIS gate's PASS meaning "closure holds
                # over whatever subset happened to parse".)
                parse_notes.append({"package": rel_id, "error": str(e)})
                findings.append({
                    "type": "ISO-CLOSURE-PARSE-FAILURE",
                    "package": rel_id,
                    "detail": (
                        f"package.yml failed to parse ({e}) — its runtime "
                        f"edges were NOT analyzed; closure cannot be "
                        f"certified over an incomplete inventory"),
                    "file": str(yml),
                })
                continue
            packages.append((pkg.name, pkg, rel_id))

    if not packages:
        findings.append({
            "type": "ISO-CLOSURE-EMPTY-INVENTORY",
            "package": "(tree)",
            "detail": (f"zero package templates parsed under {packages_dir} — "
                       f"an empty scan certifies nothing (wrong packages dir "
                       f"or mass parse failure)"),
            "file": str(packages_dir),
        })

    by_name = {name: pkg for name, pkg, _ in packages}

    # Ship-name provider map (ships_as, F25 namespace wave 2026-07-21):
    # runtime deps are user-side contracts, so a dep may name a package by
    # its SHIPPED name (recipe gcc-core ships as gcc). Resolution mirrors
    # igos-build/graph.py resolve(): shipped provider wins.
    ship_providers = {}
    for name, pkg, rel_id in packages:
        sa = getattr(pkg, "ships_as", None)
        if sa:
            if sa in ship_providers:
                findings.append({
                    "type": "ISO-CLOSURE-DUPLICATE-SHIPS-AS",
                    "package": rel_id,
                    "detail": (
                        f"ships_as {sa!r} declared by both "
                        f"{ship_providers[sa]!r} and {name!r} — two shipped "
                        f"providers cannot share one user-namespace name"),
                })
                continue
            ship_providers[sa] = name

    # Runtime-edge closure.
    for name, pkg, rel_id in packages:
        for dep in pkg.dependencies.runtime:
            if dep in ship_providers:
                resolved = by_name[ship_providers[dep]]
            elif dep in by_name:
                resolved = by_name[dep]
                # F25 gate (namespace check, 2026-07-21): a runtime dep is a
                # USER-side contract — it lands verbatim in the archive's
                # .PKGINFO depend= lines (gen-pkginfo.py does not translate)
                # and must resolve in the published index. A dep naming a
                # RECIPE that ships under a different name (ships_as) exists
                # only in the build chroot's namespace: it resolves here in
                # the tree, then fails on every user system ("<dep> not
                # found in any repository"). Build-block twin refs are
                # unaffected (this loop reads runtime edges only).
                sa = getattr(resolved, "ships_as", None)
                if sa:
                    findings.append({
                        "type": "ISO-CLOSURE-BUILD-NAME-DEP",
                        "package": rel_id,
                        "detail": (
                            f"runtime dep {dep!r} names a build-namespace "
                            f"recipe that ships as {sa!r} — the published "
                            f"depend= entry will not resolve on user "
                            f"systems; declare the shipped name {sa!r}"
                        ),
                        "edge": f"{pkg.name} -> {dep}",
                    })
            else:
                findings.append({
                    "type": "ISO-CLOSURE-DANGLING-DEP",
                    "package": rel_id,
                    "detail": (
                        f"runtime dep {dep!r} resolves to no package.yml "
                        f"name or ships_as in the tree (typo or removed "
                        f"package)"
                    ),
                    "edge": f"{pkg.name} -> {dep}",
                })
                continue
            if pkg.iso_include and not resolved.iso_include:
                findings.append({
                    "type": "ISO-CLOSURE-VIOLATION",
                    "package": rel_id,
                    "detail": (
                        f"shipped package (effective iso_include=True) "
                        f"runtime-depends {dep!r} which is MIRROR-only "
                        f"(effective iso_include=False) — the live ISO would "
                        f"be missing a runtime dependency"
                    ),
                    "edge": f"{pkg.name} -> {dep}",
                })

    stats = {
        "packages": len(packages),
        "iso_true": sum(1 for _, p, _ in packages if p.iso_include),
        "iso_false": sum(1 for _, p, _ in packages if not p.iso_include),
        "parse_notes": parse_notes,
    }
    return findings, stats


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

HARD_TYPES = (
    "ISO-CLOSURE-VIOLATION",
    "ISO-CLOSURE-DANGLING-DEP",
    "ISO-CLOSURE-NONBOOLEAN",
    "ISO-CLOSURE-DUPLICATE-SHIPS-AS",
    "ISO-CLOSURE-BUILD-NAME-DEP",
    "ISO-CLOSURE-PARSE-FAILURE",
    "ISO-CLOSURE-EMPTY-INVENTORY",
)


def emit_summary(findings: list[dict], stats: dict, verbose: bool) -> None:
    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    print("=== preflight-iso-closure ===")
    print(f"PACKAGES SCANNED: {stats['packages']} "
          f"(iso_include True {stats['iso_true']} / False {stats['iso_false']})")
    print(f"HARD FINDINGS:    {len(findings)}")

    if stats["parse_notes"]:
        print(f"PARSE NOTES:      {len(stats['parse_notes'])} "
              f"(template not analyzed here — owned by the validate gates)")

    if not findings:
        print()
        print(f"PASS — ISO closure holds: every shipped package's runtime deps "
              f"ship too, no dangling deps, no non-boolean iso_include.")
        return

    print()
    for t in HARD_TYPES:
        items = by_type.get(t, [])
        if items:
            print(f"  [HARD] {t}: {len(items)}")

    for t in HARD_TYPES:
        items = by_type.get(t, [])
        if not items:
            continue
        print(f"\n[{t}]")
        show = items if verbose else items[:20]
        for f in show:
            edge = f.get("edge")
            loc = f" ({edge})" if edge else ""
            print(f"  {f['package']}{loc}: {f['detail']}")
        if not verbose and len(items) > 20:
            print(f"  ... ({len(items) - 20} more — rerun with --verbose)")

    print()
    print("FAIL — ISO closure broken. Resolve each: declare the MIRROR-only "
          "dep as iso_include (or the shipped consumer as MIRROR); fix the "
          "dangling runtime-dep name; replace a non-boolean iso_include with "
          "an unquoted true/false.")


def write_artifact(repo: Path, findings: list[dict], stats: dict) -> Path:
    build_dir = repo / "build"
    build_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = build_dir / f"preflight-iso-closure-{ts}.json"
    path.write_text(json.dumps({
        "timestamp": ts,
        "stats": {k: v for k, v in stats.items() if k != "parse_notes"},
        "parse_notes": stats["parse_notes"],
        "findings": findings,
    }, indent=2))
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fail-closed host-side ISO-closure preflight gate.",
        epilog="Exit 0 clean, 1 on HARD findings, 2 on env problem.",
    )
    ap.add_argument("--root", help="repo root (overrides INTERGENOS_ROOT)")
    ap.add_argument("--packages", help="packages/ dir (default: <repo>/packages)")
    ap.add_argument("--verbose", action="store_true",
                    help="list every finding (default: first 20 per type)")
    ap.add_argument("--report", action="store_true",
                    help="also write a JSON artifact to <repo>/build/")
    args = ap.parse_args()

    repo = discover_repo_root(args.root)
    packages_dir = (Path(args.packages).resolve() if args.packages
                    else repo / "packages")

    if not packages_dir.is_dir():
        print(f"ERROR: packages dir {packages_dir} not found", file=sys.stderr)
        return 2
    if not (repo / "igos-build" / "parser.py").is_file():
        print(f"ERROR: parser not found under {repo}/igos-build", file=sys.stderr)
        return 2

    findings, stats = scan(repo, packages_dir)
    emit_summary(findings, stats, args.verbose)

    if args.report:
        path = write_artifact(repo, findings, stats)
        print()
        print(f"Report artifact: {path}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
