#!/usr/bin/env python3
"""Build-order ordering-bug preflight gate.

Promoted from prototype (Scan A) used during the Build #8 → Build #9
remediation arc. Catches the class of bug where a chroot-build-*.sh script
invokes ``run_package "<consumer>"`` before its declared ``dependencies.build``
have been built, which historically results in:

* Configure-time hard failures ("checking for X.h... no") that halt the
  build mid-stream;
* Silent feature loss when the consumer's configure script defaults the
  missing dep to a soft-disable instead of erroring out.

Both have surfaced in real builds (Halt #3, mitkrb's libgcrypt halt @
Build #8 → #9). Surfacing these at ``phase_validate`` time blocks build
kickoff before any package compile burns CPU.

Method:
  1. Parse ``run_package "name" "dir" "version"`` lines from every
     ``scripts/chroot-build-<phase>.sh`` to build {pkg: (phase, line_no, pos)}.
  2. Walk every ``packages/<tier>/<dir>/package.yml`` to build
     {yaml_name: [(tier, dir), ...]} for the cross-tier name-collision check.
  3. For each package with a ``package.yml`` ``dependencies.build`` list,
     check each declared dep against the build-order index.
  4. Findings:
       - ``SAME-SCRIPT-VIOLATION``: dep wired AFTER consumer in same script
       - ``CROSS-PHASE-VIOLATION``: dep is in a LATER phase script (or
         tier-defaulted into a later phase by the Python DAG builder)
       - ``DEP-NOT-FOUND``: declared dep has no package.yml in tree
       - ``DEP-TIER-UNKNOWN``: dep's tier has no default-phase mapping
       - ``PACKAGE-YML-MISSING``: consumer has no package.yml (shouldn't
         happen post-tier-validator; surfaced for completeness)
       - ``DUPLICATE-PACKAGE-NAME``: two or more package.yml files declare
         the same top-level ``name:`` field. The orchestrator's graph
         loader rejects this at phase entry with
         ``ValueError: duplicate package 'X'``; catching it here blocks
         the cherry-pick before push. Surfaced after the 2026-05-12
         protobuf incident (core/protobuf v33.5 + extra/protobuf v29.6
         collided at phase_ai entry on Build #9 r#19).

Exit codes:
  0 — clean (no findings)
  1 — findings present (build kickoff should halt)
  2 — environment problem (repo not found, etc.)

Usage:
  scripts/preflight-build-order.py             # gate mode (terse pass/fail)
  scripts/preflight-build-order.py --report    # verbose: emit JSON + TSV summaries
  scripts/preflight-build-order.py --root /alt/repo  # override repo location

Environment:
  INTERGENOS_ROOT — if set, overrides repo autodetection (script's parent dir).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# Phase order per scripts/build-intergenos.sh
PHASE_ORDER = [
    "ch8",
    "core-extra",
    "base",
    "ch10",
    "desktop",
    "ai",
    "extra",
    "bootloader",
]
PHASE_INDEX = {name: i for i, name in enumerate(PHASE_ORDER)}

# Tier directory -> default phase (when pkg isn't in any run_package line,
# the Python DAG builder runs it in this tier's phase).
TIER_TO_DEFAULT_PHASE = {
    "toolchain": "ch8",
    "core": "core-extra",
    "base": "base",
    "desktop": "desktop",
    "ai": "ai",
    "extra": "extra",
}

RUN_PACKAGE_RE = re.compile(r'^\s*run_package\s+"([^"]+)"')


def discover_repo_root(arg_root: str | None) -> Path:
    """Find the repo root.

    Priority: CLI arg > INTERGENOS_ROOT env > script's parent's parent
    (canonical install: ${REPO}/scripts/preflight-build-order.py).
    """
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def parse_script(script_path: Path) -> list[tuple[str, int]]:
    """Return [(pkg_name, line_no)] in script order."""
    pkgs = []
    with script_path.open() as fp:
        for i, line in enumerate(fp, start=1):
            m = RUN_PACKAGE_RE.match(line)
            if m:
                pkgs.append((m.group(1), i))
    return pkgs


def parse_deps_build(pkg_yml_path: Path) -> list[str]:
    """Stdlib YAML parse of ``dependencies.build`` for one package.yml.

    Handles both indent styles (PyYAML default vs deeper-indent list).
    """
    if not pkg_yml_path.is_file():
        return []
    deps: list[str] = []
    in_deps = False
    in_build = False
    deps_indent = -1
    build_indent = -1
    with pkg_yml_path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0:
                in_deps = stripped.startswith("dependencies:")
                in_build = False
                deps_indent = 0 if in_deps else -1
                continue
            if not in_deps:
                continue
            if not in_build:
                if stripped.startswith("build:") and indent > deps_indent:
                    in_build = True
                    build_indent = indent
                continue
            if indent < build_indent:
                in_build = False
                continue
            if indent == build_indent:
                if stripped.startswith("- "):
                    item = stripped[2:].strip().strip('"').strip("'")
                    if "#" in item:
                        item = item.split("#", 1)[0].strip()
                    if item:
                        deps.append(item)
                else:
                    in_build = False
                continue
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                if item:
                    deps.append(item)
    return deps


def find_package_yml(packages_dir: Path, pkg_name: str) -> Path | None:
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        candidate = tier_dir / pkg_name / "package.yml"
        if candidate.is_file():
            return candidate
    return None


def parse_yaml_name(pkg_yml_path: Path) -> str | None:
    """Extract the top-level ``name:`` field from a package.yml.

    Stdlib-only parse matching the script's existing parse_deps_build style
    (no PyYAML dependency). Returns the name string, or None if no top-level
    ``name:`` field is present.
    """
    if not pkg_yml_path.is_file():
        return None
    with pkg_yml_path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0 and stripped.startswith("name:"):
                value = stripped[len("name:"):].strip()
                value = value.strip('"').strip("'")
                if "#" in value:
                    value = value.split("#", 1)[0].strip()
                return value or None
    return None


def scan_yaml_name_collisions(packages_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Walk all ``packages/<tier>/<dir>/package.yml`` and return collisions.

    Returns ``{yaml_name: [(tier, dir_name), ...]}`` for ``name:`` fields
    that appear in 2+ package.yml files. Each entry is a hard error: the
    orchestrator's graph loader rejects duplicates at phase entry with
    ``ValueError: duplicate package 'X'``.

    Note: the dir_name is the on-disk directory, which is typically (but
    not always) equal to the YAML name field. The collision is keyed on the
    YAML name because that's what the graph loader keys on.
    """
    by_name: dict[str, list[tuple[str, str]]] = {}
    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue
            yml = pkg_dir / "package.yml"
            if not yml.is_file():
                continue
            name = parse_yaml_name(yml)
            if name is None:
                continue
            by_name.setdefault(name, []).append((tier_dir.name, pkg_dir.name))
    return {n: locs for n, locs in by_name.items() if len(locs) > 1}


def get_pkg_tier(packages_dir: Path, pkg_name: str) -> str | None:
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        if (tier_dir / pkg_name / "package.yml").is_file():
            return tier_dir.name
    return None


def scan(repo: Path) -> tuple[dict, list[dict], dict]:
    """Return (script_pkgs, findings, duplicates).

    script_pkgs: {phase: [(pkg_name, line_no), ...]}
    findings:    [{type, consumer, consumer_phase, ...}, ...]
    duplicates:  {pkg_name: [(phase, line_no, pos), ...]} for pkgs in >1 script
    """
    scripts_dir = repo / "scripts"
    packages_dir = repo / "packages"

    pkg_index: dict[str, list[tuple[str, int, int]]] = {}
    script_pkgs: dict[str, list[tuple[str, int]]] = {}

    for phase in PHASE_ORDER:
        script = scripts_dir / f"chroot-build-{phase}.sh"
        if not script.is_file():
            # Phase script may legitimately not exist (e.g., bootloader is
            # assembled by build-intergenos.sh directly). Skip without warn.
            continue
        pkgs = parse_script(script)
        script_pkgs[phase] = pkgs
        for pos, (name, line_no) in enumerate(pkgs):
            pkg_index.setdefault(name, []).append((phase, line_no, pos))

    duplicates = {n: locs for n, locs in pkg_index.items() if len(locs) > 1}

    findings: list[dict] = []

    # YAML name-collision sweep across all package.yml files. Each
    # collision surfaces as a DUPLICATE-PACKAGE-NAME finding so the
    # gate exit-code reflects it. Sorted location lists for stable
    # output.
    yaml_collisions = scan_yaml_name_collisions(packages_dir)
    for name, locs in sorted(yaml_collisions.items()):
        findings.append({
            "type": "DUPLICATE-PACKAGE-NAME",
            "name": name,
            "locations": sorted(locs),
        })

    for phase in PHASE_ORDER:
        for pos, (consumer, consumer_line) in enumerate(script_pkgs.get(phase, [])):
            yml = find_package_yml(packages_dir, consumer)
            if yml is None:
                findings.append({
                    "type": "PACKAGE-YML-MISSING",
                    "consumer": consumer,
                    "consumer_phase": phase,
                    "consumer_line": consumer_line,
                })
                continue
            deps = parse_deps_build(yml)
            for dep in deps:
                dep_locs = pkg_index.get(dep)
                if dep_locs:
                    dep_phase, dep_line, _ = dep_locs[0]
                    dep_source = "run_package"
                else:
                    dep_tier = get_pkg_tier(packages_dir, dep)
                    if dep_tier is None:
                        findings.append({
                            "type": "DEP-NOT-FOUND",
                            "consumer": consumer,
                            "consumer_phase": phase,
                            "consumer_line": consumer_line,
                            "dep": dep,
                        })
                        continue
                    dep_phase = TIER_TO_DEFAULT_PHASE.get(dep_tier)
                    if dep_phase is None:
                        findings.append({
                            "type": "DEP-TIER-UNKNOWN",
                            "consumer": consumer,
                            "consumer_phase": phase,
                            "consumer_line": consumer_line,
                            "dep": dep,
                            "dep_tier": dep_tier,
                        })
                        continue
                    dep_line = -1
                    dep_source = f"tier:{dep_tier}"

                consumer_phase_idx = PHASE_INDEX[phase]
                dep_phase_idx = PHASE_INDEX[dep_phase]
                if dep_phase_idx > consumer_phase_idx:
                    findings.append({
                        "type": "CROSS-PHASE-VIOLATION",
                        "consumer": consumer,
                        "consumer_phase": phase,
                        "consumer_line": consumer_line,
                        "dep": dep,
                        "dep_phase": dep_phase,
                        "dep_line": dep_line,
                        "dep_source": dep_source,
                    })
                elif dep_phase_idx == consumer_phase_idx and dep_line > consumer_line:
                    findings.append({
                        "type": "SAME-SCRIPT-VIOLATION",
                        "consumer": consumer,
                        "consumer_phase": phase,
                        "consumer_line": consumer_line,
                        "dep": dep,
                        "dep_phase": dep_phase,
                        "dep_line": dep_line,
                        "dep_source": dep_source,
                    })
    return script_pkgs, findings, duplicates


def emit_summary(script_pkgs: dict, findings: list[dict], duplicates: dict, verbose: bool) -> None:
    """Stdout summary."""
    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    print("=== preflight-build-order ===")
    print(f"Scripts scanned: {', '.join(script_pkgs.keys())}")
    print(f"Packages parsed: {sum(len(v) for v in script_pkgs.values())}")
    if duplicates:
        print(f"Duplicates (pkg in >1 script): {len(duplicates)}")
        if verbose:
            for n, locs in duplicates.items():
                print(f"  {n}: " + ", ".join(f"{p}:{l}" for p, l, _ in locs))
    print(f"TOTAL FINDINGS: {len(findings)}")
    if not findings:
        print()
        print("PASS — no build-order violations against current package.yml deps.")
        return

    for t in ("DUPLICATE-PACKAGE-NAME", "SAME-SCRIPT-VIOLATION",
              "CROSS-PHASE-VIOLATION", "DEP-NOT-FOUND", "DEP-TIER-UNKNOWN",
              "PACKAGE-YML-MISSING"):
        items = by_type.get(t, [])
        if items:
            print(f"  {t}: {len(items)}")

    print()
    print("=== First 5 of each finding type ===")
    for t, items in by_type.items():
        if not items:
            continue
        print(f"\n[{t}]")
        for f in items[:5]:
            if t == "SAME-SCRIPT-VIOLATION":
                print(f"  [{f['consumer_phase']}:{f['consumer_line']}] "
                      f"{f['consumer']} needs {f['dep']} @ line {f['dep_line']} (later in same script)")
            elif t == "CROSS-PHASE-VIOLATION":
                dep_loc = (f"{f['dep_phase']}@{f['dep_line']}" if f['dep_line'] > 0
                           else f"{f['dep_phase']} (Python DAG)")
                print(f"  [{f['consumer_phase']}@{f['consumer_line']}] "
                      f"{f['consumer']} needs {f['dep']} (in {dep_loc}, source={f.get('dep_source','?')})")
            elif t == "DEP-NOT-FOUND":
                print(f"  [{f['consumer_phase']}] {f['consumer']} needs {f['dep']} (no package.yml in tree)")
            elif t == "DEP-TIER-UNKNOWN":
                print(f"  [{f['consumer_phase']}] {f['consumer']} needs {f['dep']} (tier {f['dep_tier']} has no default-phase mapping)")
            elif t == "PACKAGE-YML-MISSING":
                print(f"  [{f['consumer_phase']}:{f['consumer_line']}] {f['consumer']} (no package.yml)")
            elif t == "DUPLICATE-PACKAGE-NAME":
                locs = ", ".join(f"{tier}/{d}" for tier, d in f["locations"])
                print(f"  name='{f['name']}' in {len(f['locations'])} package.yml files: {locs}")
        if len(items) > 5:
            print(f"  ... ({len(items) - 5} more)")

    print()
    print("FAIL — build-order violations found. Resolve by reordering "
          "run_package lines (move dep earlier or retier dep into a "
          "pre-consumer phase) or by removing the spurious declared-dep "
          "from package.yml if it was a misclassification.")


def write_artifacts(repo: Path, findings: list[dict], script_pkgs: dict,
                    duplicates: dict, ts: str) -> tuple[Path, Path]:
    build_dir = repo / "build"
    build_dir.mkdir(exist_ok=True)
    json_path = build_dir / f"preflight-build-order-{ts}.json"
    tsv_path = build_dir / f"preflight-build-order-{ts}.tsv"
    json_path.write_text(json.dumps({
        "timestamp": ts,
        "phase_order": PHASE_ORDER,
        "scripts_scanned": list(script_pkgs.keys()),
        "package_count_per_script": {p: len(v) for p, v in script_pkgs.items()},
        "duplicates": duplicates,
        "findings": findings,
    }, indent=2))
    with tsv_path.open("w") as fp:
        fp.write("type\tconsumer\tconsumer_phase\tconsumer_line\tdep\tdep_phase\tdep_line\tname\tlocations\n")
        for f in findings:
            if f["type"] == "DUPLICATE-PACKAGE-NAME":
                locs = "; ".join(f"{tier}/{d}" for tier, d in f.get("locations", []))
                fp.write("\t".join([
                    f["type"], "", "", "", "", "", "",
                    f.get("name", ""), locs,
                ]) + "\n")
            else:
                fp.write("\t".join([
                    f["type"],
                    f.get("consumer", ""),
                    f.get("consumer_phase", ""),
                    str(f.get("consumer_line", "")),
                    f.get("dep", ""),
                    f.get("dep_phase", ""),
                    str(f.get("dep_line", "")),
                    "", "",
                ]) + "\n")
    return json_path, tsv_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preflight build-order ordering-bug gate.",
        epilog="Exit 0 on clean, 1 on findings, 2 on env problem.",
    )
    ap.add_argument("--root", help="repo root (overrides INTERGENOS_ROOT + autodetect)")
    ap.add_argument("--report", action="store_true",
                    help="also write JSON + TSV artifacts to <repo>/build/")
    ap.add_argument("--verbose", action="store_true",
                    help="show duplicate-package details in summary")
    args = ap.parse_args()

    repo = discover_repo_root(args.root)
    if not (repo / "scripts").is_dir() or not (repo / "packages").is_dir():
        print(f"ERROR: repo root {repo} missing scripts/ or packages/ — "
              f"is this an InterGenOS checkout?", file=sys.stderr)
        return 2

    script_pkgs, findings, duplicates = scan(repo)
    emit_summary(script_pkgs, findings, duplicates, args.verbose)

    if args.report:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path, tsv_path = write_artifacts(repo, findings, script_pkgs, duplicates, ts)
        print()
        print(f"Report artifacts:")
        print(f"  JSON: {json_path}")
        print(f"  TSV:  {tsv_path}")

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
