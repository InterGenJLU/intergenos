#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""
preflight-tier-coverage.py — Build Development Rulebook Rule 17 enforcement.

Walks packages/*/*/package.yml, collects every tier declaration, and asserts
every package is reachable from its phase's build invocation. HALTs the build
if any package is unreachable (silent-skip).

Reachability rules per tier:

  tier:toolchain — built inline by scripts/toolchain-build.sh and
                   scripts/temp-tools-build.sh. Treated as out-of-scope for
                   this scan (their inline-build pattern doesn't surface a
                   `run_package` line we can grep).

  tier:core      — must appear as a `run_package "<name>"` call in either
                   scripts/chroot-build-ch8.sh OR
                   scripts/chroot-build-core-extra.sh.
                   Exception: 'linux-kernel' is built by phase_kernel via
                   scripts/chroot-build-ch10.sh's build_ch10_package call.

  tier:base      — must appear as `run_package "<name>"` in
                   scripts/chroot-build-base.sh.

  For both hardcoded-list tiers the `run_package` line's own literals are
  held to the recipe it names: `run_package "<dir>" "<name>" "<version>"
  "<tarball>"` must state the version the recipe at packages/<tier>/<dir>/
  package.yml declares, and a tarball the recipe's source entries resolve to
  (the same substitution scripts/download-sources.py stages with). The
  drivers verify the tarball against the RECIPE's pinned sha256, so a recipe
  bump that leaves the driver's line behind halts the build at that package
  with a checksum mismatch (gzip 1.14 vs the recipe's 1.15, 2026-10-12) —
  a reachability defect of the wrong source, caught here before the build.

  tier:desktop   — reachable via `python3 igos-build.py --tier desktop` in
                   scripts/chroot-build-desktop.sh; the Python builder filters
                   all packages whose `tier:` matches and builds the whole
                   topological closure.

  tier:extra     — reachable via `--tier extra` in chroot-build-extra.sh.

  tier:ai        — reachable via `--tier ai` in chroot-build-ai.sh.

Exit status:
  0  All tier-declared packages are reachable.
  1  One or more orphans found. Prints the orphan list and the fix path.

Run from anywhere; resolves repo root via the script's own location.
"""

import re
import sys
import os
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"

# Scripts that explicitly invoke run_package "<name>"
HARDCODED_LIST_SCRIPTS = {
    "core": [REPO_ROOT / "scripts" / "chroot-build-ch8.sh",
             REPO_ROOT / "scripts" / "chroot-build-core-extra.sh"],
    "base": [REPO_ROOT / "scripts" / "chroot-build-base.sh"],
}

# Phases that build all tier:<name> packages via the Python builder's --tier filter
TIER_FILTER_SCRIPTS = {
    "desktop": REPO_ROOT / "scripts" / "chroot-build-desktop.sh",
    "extra":   REPO_ROOT / "scripts" / "chroot-build-extra.sh",
    "ai":      REPO_ROOT / "scripts" / "chroot-build-ai.sh",
    "compute": REPO_ROOT / "scripts" / "chroot-build-compute.sh",
}

# Packages whose build is handled by a specific phase rather than a generic
# script. Each maps to (description, script, required word) — the wiring is
# VERIFIED at scan time, never trusted by name: a special-case whose script
# stopped referencing it is an orphan like any other.
SPECIAL_CASE_PACKAGES = {
    "linux-kernel": ("phase_kernel via chroot-build-ch10.sh",
                     REPO_ROOT / "scripts" / "chroot-build-ch10.sh",
                     "linux-kernel"),
}

# Toolchain tier is built inline by these scripts via per-package
# ./configure/make sequences (host cross-toolchain, host temp tools, and the
# in-chroot Ch7 temp tools respectively), not by `run_package` lines. A
# toolchain package is reachable when its name — or its base name with the
# -tmp / -passN intermediate suffix stripped (bash-tmp builds as 'bash' in
# the Ch7 script) — appears word-bounded in one of them. The tier was
# previously SKIPPED wholesale, so an unwired toolchain package was
# invisible to this gate.
TOOLCHAIN_INLINE_SCRIPTS = [
    REPO_ROOT / "scripts" / "toolchain-build.sh",
    REPO_ROOT / "scripts" / "temp-tools-build.sh",
    REPO_ROOT / "scripts" / "chroot-build.sh",
]


def collect_packages(malformed: list | None = None):
    """Walk packages/*/*/package.yml and return {name -> (tier, pending_reason_or_None)}.

    A package may carry an explicit `pending_acquisition: <reason>` field at the
    top level. Such packages are tier-declared but intentionally not yet wired
    because something external (typically an upstream binary acquisition) is
    blocking. The reason MUST be a non-empty string explaining what unblocks it.

    A manifest that cannot be parsed (or lacks name:/tier:) is appended to
    `malformed` as (path, reason) — main() FAILS on any entry. The old
    warn-on-stderr + continue silently SHRANK the inventory, and a gate that
    certifies only the subset it could see proves nothing about coverage.
    """
    if malformed is None:
        malformed = []
    pkgs = {}
    for yml in PACKAGES_DIR.rglob("package.yml"):
        try:
            data = yaml.safe_load(yml.read_text())
        except Exception as e:
            malformed.append((str(yml), f"parse error: {e}"))
            continue
        if not isinstance(data, dict):
            malformed.append((str(yml),
                              f"top level is {type(data).__name__}, expected a mapping"))
            continue
        name = data.get("name")
        tier = data.get("tier")
        if not name or not tier:
            malformed.append((str(yml), "missing required name:/tier: fields"))
            continue
        if name in pkgs:
            print(f"warning: duplicate package name '{name}' at {yml}", file=sys.stderr)
        pending = data.get("pending_acquisition")
        if pending is not None and (not isinstance(pending, str) or not pending.strip()):
            print(f"ERROR: {yml} has `pending_acquisition` but the reason is empty.\n"
                  "  Pending entries MUST have a non-empty string reason explaining\n"
                  "  what unblocks the wiring. Refusing to treat as pending.",
                  file=sys.stderr)
            sys.exit(1)
        pkgs[name] = (tier, pending)
    return pkgs


def collect_run_package_calls(script_paths):
    """Scan each script for `run_package "<name>"` calls; return a set of names."""
    pattern = re.compile(r'^\s*run_package\s+"([^"]+)"', re.MULTILINE)
    found = set()
    for sp in script_paths:
        if not sp.exists():
            continue
        text = sp.read_text(errors="replace")
        for m in pattern.finditer(text):
            found.add(m.group(1))
    return found


# A run_package call as the core and base drivers write it: the recipe
# directory, the ship name, the version and the tarball, each quoted, on one
# line or continued with a backslash at the line end (the description that
# follows is not read). A call with fewer literals yields None for the rest;
# an empty tarball literal ("" — a first-party package the build generates
# in place, with no source entry) is returned as None as well.
RUN_PACKAGE_LITERAL = re.compile(
    r'^[ \t]*run_package[ \t]+"(?P<pkg_dir>[^"]+)"[ \t]+"(?P<name>[^"]+)"'
    r'(?:[ \t]*(?:\\\n)?[ \t]*"(?P<version>[^"]+)")?'
    r'(?:[ \t]*(?:\\\n)?[ \t]*"(?P<tarball>[^"]*)")?',
    re.MULTILINE)


def collect_run_package_literals(script_paths):
    """Scan each script for run_package calls; return a list of
    (script_path, line_number, pkg_dir, name, version_or_None, tarball_or_None)."""
    found = []
    for sp in script_paths:
        if not sp.exists():
            continue
        text = sp.read_text(errors="replace")
        for m in RUN_PACKAGE_LITERAL.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            found.append((sp, line_no, m.group("pkg_dir"), m.group("name"),
                          m.group("version"), m.group("tarball") or None))
    return found


def _recipe_source_filenames(data: dict) -> list[str]:
    """The local filenames the recipe's source entries stage to, resolved
    exactly as scripts/download-sources.py resolves them (imported by path:
    one resolution, not a second copy of it)."""
    import importlib.util
    ds_path = REPO_ROOT / "scripts" / "download-sources.py"
    spec = importlib.util.spec_from_file_location("igos_download_sources", ds_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [src["filename"] for src in mod.get_source_info(data)]


def check_run_package_literals(script_paths, tier: str) -> list[tuple]:
    """Hold every run_package literal to its recipe. Returns a list of
    (name, tier, reason) mismatches: a missing recipe, a version that differs
    from the recipe's, or a tarball none of the recipe's source entries
    resolve to. A recipe with no source entries is checked on version only
    (a first-party package the build generates in place; its driver line
    carries an empty tarball literal), and a recipe WITH source entries
    whose driver line extracts nothing is a mismatch."""
    mismatches = []
    for sp, line_no, pkg_dir, name, version, tarball in \
            collect_run_package_literals(script_paths):
        where = f"{sp.name}:{line_no}"
        yml = PACKAGES_DIR / tier / pkg_dir / "package.yml"
        if not yml.exists():
            mismatches.append((name, tier,
                               f"{where} names recipe directory '{pkg_dir}' "
                               f"but {yml} does not exist"))
            continue
        try:
            data = yaml.safe_load(yml.read_text())
        except Exception as e:  # the inventory pass already failed on this
            mismatches.append((name, tier, f"{where}: recipe unreadable ({e})"))
            continue
        if not isinstance(data, dict):
            mismatches.append((name, tier, f"{where}: recipe is not a mapping"))
            continue
        recipe_version = data.get("version")
        if recipe_version is None:
            mismatches.append((name, tier,
                               f"{where}: recipe declares no version: to hold "
                               f"the driver's literal '{version}' to"))
            continue
        recipe_version = str(recipe_version)
        if version is not None and version != recipe_version:
            mismatches.append((name, tier,
                               f"{where} builds version {version} but the recipe "
                               f"pins {recipe_version} (and the driver verifies the "
                               f"tarball against the recipe's sha256)"))
            continue
        filenames = _recipe_source_filenames(data)
        if filenames and tarball not in filenames:
            mismatches.append((name, tier,
                               f"{where} extracts {tarball or 'no tarball'} but the "
                               f"recipe's source entries stage "
                               f"{', '.join(filenames)}"))
    return mismatches


def toolchain_reachable(name: str, script_texts: list[str]) -> bool:
    """A toolchain package is reachable when its name (or intermediate base
    name — bash-tmp builds as 'bash', binutils-pass1 as 'binutils') appears
    word-bounded in one of the inline toolchain build scripts."""
    base = re.sub(r"-(tmp|pass\d+)$", "", name)
    for text in script_texts:
        for candidate in {name, base}:
            if re.search(rf"\b{re.escape(candidate)}\b", text):
                return True
    return False


def special_case_wired(name: str) -> tuple[bool, str]:
    """Verify a SPECIAL_CASE_PACKAGES entry's wiring actually exists."""
    desc, script, word = SPECIAL_CASE_PACKAGES[name]
    if not script.exists():
        return False, f"special-case script {script.name} does not exist"
    if not re.search(rf"\b{re.escape(word)}\b",
                     script.read_text(errors="replace")):
        return False, (f"special-case script {script.name} no longer "
                       f"references '{word}'")
    return True, desc


def script_uses_tier_filter(script_path, tier_name):
    """Check whether the script invokes `igos-build.py --tier <tier_name>`."""
    if not script_path.exists():
        return False
    text = script_path.read_text(errors="replace")
    pattern = re.compile(r'igos-build\.py.*?--tier\s+' + re.escape(tier_name),
                         re.MULTILINE | re.DOTALL)
    return bool(pattern.search(text))


def main():
    if not PACKAGES_DIR.is_dir():
        print(f"ERROR: packages dir not found at {PACKAGES_DIR}", file=sys.stderr)
        return 1

    malformed: list = []
    pkgs = collect_packages(malformed)
    print(f"[preflight] scanned {len(pkgs)} packages across all tiers")

    # Completeness contract: the gate certifies only what it POSITIVELY
    # scanned. A malformed manifest shrank the inventory; an empty inventory
    # certifies nothing (wrong dir / mass parse failure) — both are FAIL,
    # never a quieter pass.
    if malformed:
        print()
        print(f"[preflight] FAIL: {len(malformed)} package manifest(s) could not "
              f"be inventoried — coverage cannot be certified:", file=sys.stderr)
        for path, reason in malformed:
            print(f"  - {path}: {reason}", file=sys.stderr)
        return 1
    if not pkgs:
        print(f"[preflight] FAIL: zero packages inventoried under {PACKAGES_DIR} — "
              f"an empty scan certifies nothing (wrong packages dir?)",
              file=sys.stderr)
        return 1

    # Group by tier
    by_tier = {}
    pending_packages = {}  # name -> reason
    for name, (tier, pending) in pkgs.items():
        by_tier.setdefault(tier, []).append(name)
        if pending:
            pending_packages[name] = pending

    orphans = []  # list of (name, tier, reason)
    literals_checked = 0
    literal_mismatches = []  # list of (name, tier, reason): a driver line not matching its recipe

    for tier, names in sorted(by_tier.items()):
        if tier == "toolchain":
            texts = [s.read_text(errors="replace")
                     for s in TOOLCHAIN_INLINE_SCRIPTS if s.exists()]
            if not texts:
                for n in names:
                    orphans.append((n, tier,
                                    "no toolchain inline build script found"))
                continue
            unreachable = [n for n in names
                           if n not in pending_packages
                           and not toolchain_reachable(n, texts)]
            print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — "
                  f"{len(names) - len(unreachable)} reachable via inline "
                  f"toolchain scripts, {len(unreachable)} unreachable")
            for n in unreachable:
                orphans.append((n, tier,
                                "not referenced by any inline toolchain "
                                "build script (toolchain-build.sh, "
                                "temp-tools-build.sh, chroot-build.sh)"))
            continue

        if tier in HARDCODED_LIST_SCRIPTS:
            wired = collect_run_package_calls(HARDCODED_LIST_SCRIPTS[tier])
            tier_literals = collect_run_package_literals(HARDCODED_LIST_SCRIPTS[tier])
            tier_mismatches = check_run_package_literals(HARDCODED_LIST_SCRIPTS[tier], tier)
            literals_checked += len(tier_literals)
            literal_mismatches.extend(tier_mismatches)
            unreachable = []
            for name in names:
                if name in wired:
                    continue
                if name in SPECIAL_CASE_PACKAGES:
                    ok, why = special_case_wired(name)
                    if ok:
                        continue
                    orphans.append((name, tier, why))
                    continue
                if name in pending_packages:
                    continue
                unreachable.append(name)
            wired_count = len(names) - len(unreachable)
            pending_in_tier = sum(1 for n in names if n in pending_packages)
            print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — "
                  f"{wired_count} reachable via run_package, "
                  f"{len(unreachable)} unreachable, "
                  f"{pending_in_tier} pending acquisition")
            for n in unreachable:
                orphans.append((n, tier,
                                f"not in run_package list of "
                                f"{', '.join(s.name for s in HARDCODED_LIST_SCRIPTS[tier])}"))

        elif tier in TIER_FILTER_SCRIPTS:
            script = TIER_FILTER_SCRIPTS[tier]
            if script_uses_tier_filter(script, tier):
                print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — "
                      f"reachable via --tier {tier} in {script.name}")
            else:
                # The phase script doesn't have --tier <name>; every package is orphan
                print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — "
                      f"** ERROR: {script.name} does NOT invoke --tier {tier}")
                for n in names:
                    orphans.append((n, tier,
                                    f"phase script {script.name} does not invoke "
                                    f"`igos-build.py --tier {tier}`"))

        else:
            print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — "
                  f"** UNKNOWN TIER (no rule registered)")
            for n in names:
                orphans.append((n, tier, f"tier '{tier}' has no reachability rule"))

    print(f"[preflight] run_package literals held to their recipes: "
          f"{literals_checked} checked, {len(literal_mismatches)} mismatched")
    if literal_mismatches:
        print()
        print("=" * 70)
        print("[preflight] FAIL: driver run_package line(s) do not match their recipe")
        print("=" * 70)
        print(f"\n{len(literal_mismatches)} run_package line(s) build a version or tarball "
              "other than the one the recipe pins. The driver verifies the tarball\n"
              "against the RECIPE's sha256, so the build halts at that package.\n")
        print(f"{'Package':<28} {'Tier':<12} Reason")
        print("-" * 70)
        for name, tier, reason in sorted(literal_mismatches):
            print(f"{name:<28} {tier:<12} {reason}")
        print("\nResolution: bring the driver's run_package line to the recipe's")
        print("  version and tarball — a version bump of a core or base package")
        print("  updates its driver line in the same change (the sed 4.10 and")
        print("  ncurses 6.6-20261010 bumps are the shape). Never edit the recipe")
        print("  back to the driver's version to make this pass.")
        if not orphans:
            return 1

    if orphans:
        print()
        print("=" * 70)
        print("[preflight] FAIL: silent-skip orphans found")
        print("=" * 70)
        print(f"\n{len(orphans)} package(s) declare a tier but are not reachable from\n"
              "their phase's build invocation. Per Build Development Rulebook Rule 2,\n"
              "every tier-declared package MUST be wired into its phase.\n")
        print(f"{'Package':<28} {'Tier':<12} Reason")
        print("-" * 70)
        for name, tier, reason in sorted(orphans):
            print(f"{name:<28} {tier:<12} {reason}")
        print("\nResolution per Rule 1+2:")
        print("  - For tier:core orphans: add `run_package \"<name>\" ...` to")
        print("    scripts/chroot-build-ch8.sh OR scripts/chroot-build-core-extra.sh.")
        print("    NEVER fix by changing tier — tier reflects what the package IS.")
        print("  - For tier:base orphans: add to scripts/chroot-build-base.sh.")
        print("  - For tier:desktop/extra/ai orphans: verify the phase script invokes")
        print("    `igos-build.py --tier <name>`.")
        return 1

    if pending_packages:
        print()
        print(f"[preflight] {len(pending_packages)} package(s) marked pending acquisition (informational):")
        for name in sorted(pending_packages):
            tier = pkgs[name][0]
            reason = pending_packages[name][:120]
            print(f"  {name} (tier:{tier}): {reason}")

    print()
    print("[preflight] PASS: all tier-declared packages are reachable or explicitly pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
