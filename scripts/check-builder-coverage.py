#!/usr/bin/env python3
"""check-builder-coverage — find packages on disk no entry point will build.

The build orchestrator uses two patterns to enumerate packages per tier:

  1. Static-list scripts (chroot-build-{ch8,ch10,base,core-extra,
     bootloader,ai}.sh) — explicitly `run_package "<id>" "<src>" "<ver>"
     "<tarball>" "<desc>"` for each package the phase will build.

  2. Tier-driver scripts (chroot-build-{tier,desktop,extra}.sh) — call
     `igos-build.py --build --tracked --tier <X>` which lets the Python
     builder enumerate `packages/<X>/` via dep graph.

A package on disk is BUILDABLE iff:

  - Its directory name appears in some static-list run_package call, OR
  - Its tier metadata is enumerable by a tier-driver invocation that the
    orchestrator actually calls.

Anything else is an orphan: the file exists, declares verify_paths, has
a build.sh, but no entry point will ever try to build it. That's the
silent-skip class that surfaced base/at tonight (different mechanism;
same shape of "package exists but never built").

Also flags: tier-metadata mismatch (package lives in packages/A/<name>
but declares `tier: B` in its package.yml).

Exit codes:
  0 — clean
  1 — orphans or tier mismatches found
  2 — argument or env error
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML required\n")
    sys.exit(2)


# Static-list scripts: regex-grep their `run_package "<id>"` invocations.
STATIC_LIST_SCRIPTS = [
    'scripts/chroot-build-ch8.sh',
    'scripts/chroot-build-ch10.sh',
    'scripts/chroot-build-base.sh',
    'scripts/chroot-build-core-extra.sh',
    'scripts/chroot-build-bootloader.sh',
    'scripts/chroot-build-ai.sh',
]

# Tier-driver scripts that call `igos-build.py --tier <X>` to enumerate
# every package with `tier: X`. Key = script path, value = tier name.
TIER_DRIVER_SCRIPTS = {
    'scripts/chroot-build-desktop.sh': 'desktop',
    'scripts/chroot-build-extra.sh':   'extra',
    'scripts/chroot-build-ai.sh':      'ai',
}

# Tiers entirely covered by tier-driver scripts (Python builder
# enumerates all packages with that tier metadata).
TIER_DRIVER_TIERS = set(TIER_DRIVER_SCRIPTS.values())

# chroot-build-desktop.sh also pre-builds 4 base-tier deps via
# --only <name>. These are reachable via that fallback path even though
# their tier metadata isn't 'desktop'.
DESKTOP_BASE_DEPS = {'cpio', 'libtirpc', 'popt', 'which'}

# Toolchain is built OUTSIDE the chroot (LFS Ch5-7). It's excluded from
# the verify_paths audit and from this coverage check.
EXCLUDED_TIERS = {'toolchain'}

# Patterns match `run_package "<id>" ...` and the chapter-specific
# variants `build_ch10_package "<id>" ...` etc. Each chroot-build-*.sh
# script uses one such verb; the function names follow a stable suffix
# convention. Anchor on the verb suffix `_package` to catch all of them.
RUN_PKG_RE = re.compile(r'^\s*(?:run|build_[a-z0-9_]+)_package\s+"([^"]+)"')


def extract_run_package_ids(script_path):
    """Return set of pkg_id strings invoked by run/build_*_package."""
    ids = set()
    try:
        for line in script_path.read_text().splitlines():
            m = RUN_PKG_RE.match(line)
            if m:
                ids.add(m.group(1))
    except FileNotFoundError:
        pass
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo-root', default='.',
                    help='Path to repo root (default: current dir)')
    ap.add_argument('--packages-dir', default=None,
                    help='Override packages/ dir (default: <repo-root>/packages)')
    ap.add_argument('--quiet', action='store_true',
                    help='Only print summary + failures, not per-tier detail')
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    pkgs_dir = Path(args.packages_dir) if args.packages_dir else repo / 'packages'
    if not pkgs_dir.is_dir():
        sys.stderr.write(f"ERROR: packages dir not found: {pkgs_dir}\n")
        sys.exit(2)

    # Step 1: enumerate every package on disk.
    on_disk = {}  # pkg_dir_name -> dict(tier_dir, declared_tier, version)
    tier_mismatches = []
    for tier_dir in sorted(pkgs_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        tier = tier_dir.name
        if tier in EXCLUDED_TIERS:
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml = pkg_dir / 'package.yml'
            if not yml.exists():
                continue
            try:
                data = yaml.safe_load(yml.read_text()) or {}
            except Exception as e:
                sys.stderr.write(f"YAML PARSE FAIL [{tier}/{pkg_dir.name}]: {e}\n")
                continue
            declared_tier = data.get('tier')
            version = data.get('version')
            on_disk[pkg_dir.name] = {
                'tier_dir': tier,
                'declared_tier': declared_tier,
                'version': str(version) if version else None,
                'path': pkg_dir,
                'pending': bool(data.get('pending_acquisition')),
            }
            if declared_tier and declared_tier != tier:
                tier_mismatches.append((pkg_dir.name, tier, declared_tier))

    # Step 2: enumerate every package reachable from a static-list script.
    static_reachable = set()
    static_by_script = {}
    for rel in STATIC_LIST_SCRIPTS:
        ids = extract_run_package_ids(repo / rel)
        static_by_script[rel] = ids
        static_reachable |= ids

    # Step 3: enumerate every package reachable from a tier-driver call.
    # The Python builder reads packages/<tier>/ and builds every package
    # there. So tier-driver coverage = "every package whose tier_dir is
    # in TIER_DRIVER_TIERS". We don't need to invoke the builder to know
    # that; trust the script invocation list.
    driver_reachable = {n for n, info in on_disk.items()
                        if info['tier_dir'] in TIER_DRIVER_TIERS}

    # Step 4: chroot-build-desktop.sh's BASE_DEPS pre-build.
    base_dep_reachable = DESKTOP_BASE_DEPS & set(on_disk.keys())

    # Step 5: form total reachable + find orphans. Packages with
    # `pending_acquisition:` are deliberately deferred (e.g. shim-signed
    # awaiting Microsoft UEFI CA sponsorship) and are not orphans — they
    # are explicitly NOT built; the squashfs audit's same exemption applies.
    reachable = static_reachable | driver_reachable | base_dep_reachable
    orphans = []
    exempt_pending = []
    for name, info in on_disk.items():
        if name in reachable:
            continue
        if info['pending']:
            exempt_pending.append((name, info))
            continue
        orphans.append((name, info))

    # Output.
    print(f"=== check-builder-coverage ===")
    print(f"  Packages on disk (non-toolchain): {len(on_disk)}")
    print(f"  Static-list reachable:            {len(static_reachable & set(on_disk.keys()))}")
    print(f"  Tier-driver reachable:            {len(driver_reachable)}")
    print(f"  BASE_DEPS pre-build reachable:    {len(base_dep_reachable)}")
    print(f"  Total reachable:                  {len(reachable & set(on_disk.keys()))}")
    print(f"  Exempt (pending_acquisition):     {len(exempt_pending)}")
    print(f"  Orphans (unreachable on disk):    {len(orphans)}")
    print(f"  Tier-metadata mismatches:         {len(tier_mismatches)}")
    print()

    if not args.quiet:
        print(f"=== Per-script static lists ===")
        for rel, ids in static_by_script.items():
            existing = ids & set(on_disk.keys())
            ghost = ids - set(on_disk.keys())
            print(f"  {rel}: {len(ids)} ids ({len(existing)} on disk, {len(ghost)} ghost)")
            if ghost:
                for g in sorted(ghost):
                    print(f"    GHOST (script names a pkg dir that doesn't exist): {g}")
        print()

    if orphans:
        print(f"=== {len(orphans)} ORPHAN(S) — on disk but unreachable from any entry point ===")
        for name, info in sorted(orphans):
            dt = info['declared_tier'] or '<unset>'
            print(f"  [{info['tier_dir']}/{name}] tier-in-yaml={dt} version={info['version']}")
        print()
        print(f"  An orphan package has a package.yml + build.sh but no")
        print(f"  orchestrator phase will ever build it. Either:")
        print(f"    (a) wire it into the appropriate chroot-build-*.sh, OR")
        print(f"    (b) declare it under a tier the tier-driver enumerates")
        print(f"        (currently: {sorted(TIER_DRIVER_TIERS)}), OR")
        print(f"    (c) delete the package if it's no longer wanted.")
        print()

    if tier_mismatches:
        print(f"=== {len(tier_mismatches)} TIER MISMATCH(es) — dir != declared ===")
        for name, tier_dir, declared in sorted(tier_mismatches):
            print(f"  [{tier_dir}/{name}] yml declares tier={declared}, "
                  f"lives in packages/{tier_dir}/")
        print(f"  Tier mismatches cause confusion at best, silent-skip at")
        print(f"  worst. Move the package or fix the declaration.")
        print()

    if orphans or tier_mismatches:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
