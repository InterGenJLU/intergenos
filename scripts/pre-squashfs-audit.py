#!/usr/bin/env python3
"""Pre-squashfs audit — verify every declared package landed on disk.

For each packages/<tier>/<name>/package.yml that declares `verify_paths:`,
check that each declared path exists in the chroot. Fail loudly if any
declared file is missing — that's the linux-firmware-class regression
signal (package claimed to install, files didn't actually appear).

Exemption: packages with a top-level `pending_acquisition:` string field
are deliberately deferred (e.g., shim-signed waiting on Microsoft UEFI CA
sponsorship). These are skipped without warning.

Packages without `verify_paths` get a WARNING (audit is blind to them).
With --strict, the warning becomes an error.

Designed to run inside the chroot (where /usr/bin/X resolves directly) OR
against a chroot rooted at --chroot PATH from outside (paths are then
prefixed: e.g., `--chroot /mnt/igos` makes /usr/bin/bash resolve to
/mnt/igos/usr/bin/bash).

Exit codes:
  0 — clean
  1 — missing path(s) found
  2 — argument or environment error
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML required. Install with: pip3 install PyYAML\n")
    sys.exit(2)


SIDECAR_NAME = 'auto-verify-paths.json'


def chroot_path(path, chroot_root):
    """Resolve a package-declared path against the chroot."""
    if chroot_root in (None, '', '/'):
        return Path(path)
    return Path(chroot_root) / path.lstrip('/')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--chroot', default='/',
                    help='Chroot root to resolve verify_paths against '
                         '(default: / — assume running inside chroot)')
    ap.add_argument('--packages-dir', default='packages',
                    help='Path to packages/ tree (default: packages)')
    ap.add_argument('--strict', action='store_true',
                    help='Promote "missing verify_paths" warnings to errors')
    ap.add_argument('--quiet', action='store_true',
                    help='Suppress per-package PASS lines; only report failures')
    args = ap.parse_args()

    pkgs_dir = Path(args.packages_dir)
    if not pkgs_dir.is_dir():
        sys.stderr.write(f"ERROR: packages dir not found: {pkgs_dir}\n")
        sys.exit(2)

    total = 0
    checked = 0
    passed = 0
    failed_pkgs = []   # [(pkg_id, [missing_paths])]
    exempt = []
    missing_field = []

    for tier_dir in sorted(pkgs_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        if tier_dir.name == 'toolchain':
            continue  # LFS Ch5-7 — discarded post-bootstrap, no chroot presence
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml = pkg_dir / 'package.yml'
            if not yml.exists():
                continue
            total += 1
            pkg_id = f"{tier_dir.name}/{pkg_dir.name}"
            try:
                data = yaml.safe_load(yml.read_text()) or {}
            except Exception as e:
                sys.stderr.write(f"YAML PARSE FAIL [{pkg_id}]: {e}\n")
                failed_pkgs.append((pkg_id, [f'<yaml parse error: {e}>']))
                continue

            # Exemption: deliberately-deferred packages
            if data.get('pending_acquisition'):
                exempt.append(pkg_id)
                continue

            paths = data.get('verify_paths')
            path_source = 'package.yml'
            if not paths:
                # Fall back to auto-derived sidecar if present
                sidecar = pkg_dir / SIDECAR_NAME
                if sidecar.exists():
                    try:
                        side_data = json.loads(sidecar.read_text())
                        paths = side_data.get('verify_paths') or None
                        if paths:
                            path_source = 'auto-derived sidecar'
                    except Exception:
                        paths = None
            if not paths:
                missing_field.append(pkg_id)
                continue

            checked += 1
            missing = []
            for p in paths:
                if not isinstance(p, str) or not p.startswith('/'):
                    missing.append(f'{p} <invalid-shape>')
                    continue
                full = chroot_path(p, args.chroot)
                # os.path.lexists is symlink-tolerant + doesn't follow symlinks,
                # so it doesn't raise EACCES on inaccessible targets the way
                # Path.exists() does on root-owned chroot files.
                try:
                    if not os.path.lexists(str(full)):
                        missing.append(p)
                except OSError:
                    missing.append(p)
            if missing:
                failed_pkgs.append((pkg_id, missing))
            else:
                passed += 1
                if not args.quiet:
                    src_tag = '' if path_source == 'package.yml' else f' [{path_source}]'
                    print(f"  PASS [{pkg_id}] ({len(paths)} paths){src_tag}")

    print()
    print(f"=== pre-squashfs-audit summary ===")
    print(f"  Total packages (non-toolchain): {total}")
    print(f"  Checked (have verify_paths):    {checked}")
    print(f"  Passed:                         {passed}")
    print(f"  Failed (missing paths on disk): {len(failed_pkgs)}")
    print(f"  Exempt (pending_acquisition):   {len(exempt)}")
    print(f"  Missing verify_paths field:     {len(missing_field)} "
          f"({'STRICT FAIL' if args.strict else 'warning only'})")

    if failed_pkgs:
        print()
        print(f"=== {len(failed_pkgs)} FAILURE(S) — missing paths on disk ===")
        for pkg_id, missing in failed_pkgs:
            print(f"  [{pkg_id}]")
            for m in missing:
                print(f"    - MISSING: {m}")
        print()
        print(f"  Audit failed. The above packages declared paths that don't")
        print(f"  exist on the chroot at {args.chroot!r}. Either:")
        print(f"    (a) the package wasn't actually built/installed, OR")
        print(f"    (b) the declared verify_paths are wrong (correct them).")
        print(f"  This is the linux-firmware-class regression signal.")

    if missing_field:
        kind = 'ERROR' if args.strict else 'WARN'
        print()
        print(f"=== {len(missing_field)} package(s) without verify_paths ({kind}) ===")
        if args.strict:
            print(f"  --strict promotes this to a build-blocker.")
        else:
            print(f"  These packages are blind to this audit. Add verify_paths.")
        if not args.quiet:
            for pkg_id in missing_field[:20]:
                print(f"    {pkg_id}")
            if len(missing_field) > 20:
                print(f"    ... +{len(missing_field)-20} more")

    if failed_pkgs:
        sys.exit(1)
    if args.strict and missing_field:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
