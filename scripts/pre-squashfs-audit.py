#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
import importlib.util
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

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_parser():
    """Import igos-build/parser.py (hyphenated dir → load by file path).

    Hard-fails instead of falling back to a local copy of the rule. A copy is
    exactly what this import exists to delete: the audit's hand-mirrored
    iso_include rule drifted from the parser's twice. An audit that cannot
    reach the authority must stop, not guess.
    """
    path = _REPO_ROOT / "igos-build" / "parser.py"
    spec = importlib.util.spec_from_file_location("igos_build_parser", path)
    if spec is None or spec.loader is None:
        sys.stderr.write(f"ERROR: cannot load the package parser at {path}\n")
        sys.exit(2)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        sys.stderr.write(f"ERROR: cannot load the package parser at {path}: {e}\n")
        sys.exit(2)
    return module


_parser = _load_parser()


def chroot_path(path, chroot_root):
    """Resolve a package-declared path against the chroot."""
    if chroot_root in (None, '', '/'):
        return Path(path)
    return Path(chroot_root) / path.lstrip('/')


def effective_iso_include(data):
    """Return a package's EFFECTIVE iso_include as a bool.

    CALLS the parser's rule (igos-build/parser.effective_iso_include) rather
    than restating it, so this audit's mirror-only exemption cannot drift from
    build-time semantics. An explicit boolean wins; otherwise every tier in
    parser.NON_ISO_DEFAULT_TIERS — `extra`, `compute` and `toolchain` — is
    mirror-only/not-shipped and every other tier ships.

    A hand-mirrored tuple used to live here and drifted from the parser TWICE:
    `compute` was added to the parser and not here (2026-07-18, surfaced as a
    mint Step-4.5 halt), then `toolchain` repeated it (2026-08-06, found by
    cross-review 2026-08-13 while the divergence was still latent). Importing
    the authority was the recorded follow-on; this is it. There is no second
    copy of the rule left — do not reintroduce one.

    A non-boolean explicit value raises ValueError, exactly as it does at parse
    time: bool() coercion made a quoted "false" truthy, and the wrong answer
    here is a SILENT SKIP of a package the audit should have checked. The
    caller reports it against the package instead of guessing.
    """
    return _parser.effective_iso_include(data.get('iso_include', None),
                                         data.get('tier', 'core'))


def assert_root_traversal(chroot_root):
    """Refuse to run unprivileged.

    os.path.lexists silently returns False when the calling user lacks +x
    on a parent dir — which the audit then misclassifies as "file missing."
    The chroot at /mnt/igos contains root-only-traversable dirs like
    /etc/caddy (0750), /etc/sudoers.d, /root, and ssh host-key dirs;
    every file beneath them would report as missing to a non-root audit.
    Categorical refusal is the simplest correct fix.
    """
    if os.geteuid() == 0:
        return
    sys.stderr.write(
        "ERROR: pre-squashfs-audit must run as root.\n"
        "  Reason: chroot contains 0750/0700 dirs (e.g. /etc/caddy) whose\n"
        "  contents silently report as missing to an unprivileged audit\n"
        "  (os.path.lexists returns False when parent lacks +x). Without\n"
        "  root, the audit emits false positives that look identical to\n"
        "  the linux-firmware-class regression signal.\n"
        "  Re-invoke with sudo.\n"
    )
    sys.exit(2)


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

    assert_root_traversal(args.chroot)

    total = 0
    checked = 0
    passed = 0
    failed_pkgs = []   # [(pkg_id, [missing_paths])]
    exempt = []
    missing_field = []

    for tier_dir in sorted(pkgs_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        # NOTE there is deliberately no skip on the DIRECTORY name here.
        # `packages/toolchain/` used to be skipped whole ("LFS Ch5-7 —
        # discarded post-bootstrap, no chroot presence"), which was a second,
        # separate statement of "toolchain does not ship" keyed on where a
        # recipe SITS rather than on what it DECLARES. The tier exemption
        # below now covers it through the parser's own rule, so every recipe
        # is read and judged by its declared tier — the same field the build
        # uses. A toolchain recipe therefore appears in the summary as EXEMPT
        # instead of never being looked at, and a recipe filed in the wrong
        # directory is judged by what it says it is.
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

            # Exemption: mirror-only packages (EFFECTIVE iso_include False).
            # Excluded from the live ISO by derive-iso-exclusions; their
            # payload installs on-target via the mirror, so no live-ISO
            # presence is expected. Top-level mirror-only apps assert their
            # installer-hook via verify_paths; this covers transitive lib-deps
            # (e.g. cairomm1) that have no hook and no live-ISO artifact.
            #
            # EFFECTIVE, not just explicit — and answered by the parser's own
            # rule (effective_iso_include above delegates to it): an explicit
            # boolean wins, otherwise the tier decides. The prior test read
            # EXPLICIT `iso_include is False` only, so a `tier: extra` package
            # with no explicit override (the common MIRROR case) was NOT
            # exempted here even though derive-iso-exclusions evicts it from
            # the chroot before squashfs. Harmless to date (those packages
            # passed anyway) but a real divergence from build-time semantics —
            # closed at the mechanism.
            #
            # A malformed iso_include is REPORTED, never guessed at: coercing
            # it could exempt a package that should have been audited, and a
            # silent skip is the one outcome this audit exists to prevent.
            try:
                mirror_only = effective_iso_include(data) is False
            except ValueError as e:
                failed_pkgs.append((pkg_id, [f'<invalid iso_include: {e}>']))
                continue
            if mirror_only:
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
                # os.path.lexists is symlink-tolerant and doesn't follow
                # symlinks. It still requires +x on every parent dir to
                # reach the entry — when a parent lacks +x, lexists
                # silently returns False (no exception). That false-
                # negative is why assert_root_traversal() above refuses
                # to run unprivileged: as root we have CAP_DAC_READ_SEARCH
                # equivalent, so traversal can't fail this way.
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
    print(f"  Total packages read:            {total}")
    print(f"  Checked (have verify_paths):    {checked}")
    print(f"  Passed:                         {passed}")
    print(f"  Failed (missing paths on disk): {len(failed_pkgs)}")
    print(f"  Exempt (pending_acquisition or not shipped): {len(exempt)}")
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
