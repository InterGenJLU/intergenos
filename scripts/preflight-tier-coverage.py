#!/usr/bin/env python3
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
}

# Packages whose build is handled by a specific phase rather than a generic
# script. These are treated as reachable.
SPECIAL_CASE_PACKAGES = {
    "linux-kernel": "phase_kernel via chroot-build-ch10.sh",
}

# Toolchain tier is built inline by toolchain-build.sh and temp-tools-build.sh
# via a sequence of ./configure/make calls per package, not by `run_package`
# discrete invocations. Skip from the scan; tracked separately if needed.
SKIP_TIERS = {"toolchain", "bootloader"}


def collect_packages():
    """Walk packages/*/*/package.yml and return {name -> (tier, pending_reason_or_None)}.

    A package may carry an explicit `pending_acquisition: <reason>` field at the
    top level. Such packages are tier-declared but intentionally not yet wired
    because something external (typically an upstream binary acquisition) is
    blocking. The reason MUST be a non-empty string explaining what unblocks it.
    """
    pkgs = {}
    for yml in PACKAGES_DIR.rglob("package.yml"):
        try:
            data = yaml.safe_load(yml.read_text())
        except Exception as e:
            print(f"warning: could not parse {yml}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        tier = data.get("tier")
        if not name or not tier:
            print(f"warning: {yml} missing name or tier", file=sys.stderr)
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

    pkgs = collect_packages()
    print(f"[preflight] scanned {len(pkgs)} packages across all tiers")

    # Group by tier
    by_tier = {}
    pending_packages = {}  # name -> reason
    for name, (tier, pending) in pkgs.items():
        by_tier.setdefault(tier, []).append(name)
        if pending:
            pending_packages[name] = pending

    orphans = []  # list of (name, tier, reason)

    for tier, names in sorted(by_tier.items()):
        if tier in SKIP_TIERS:
            print(f"[preflight] tier:{tier:<10} {len(names):>4} packages — skip "
                  f"(handled by inline build pattern, out of scope)")
            continue

        if tier in HARDCODED_LIST_SCRIPTS:
            wired = collect_run_package_calls(HARDCODED_LIST_SCRIPTS[tier])
            unreachable = []
            for name in names:
                if name in wired:
                    continue
                if name in SPECIAL_CASE_PACKAGES:
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
