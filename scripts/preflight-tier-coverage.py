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
