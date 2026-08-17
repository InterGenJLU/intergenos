#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed preflight gate: hand-written kernel-release paths must match the
release they are derived from.

The kernel's identity is computed, not chosen. packages/core/linux-kernel/build.sh
reads `release:` out of linux-kernel's own package.yml, stamps it into
CONFIG_LOCALVERSION as -igos-<release>, and the resulting KERNELRELEASE
<version>-igos-<release> is what names the image in /boot, the module tree under
/usr/lib/modules, the UKI and the boot-menu entry. packages/core/linux-kernel-pass2
reads the same field for the same reason: it ships the final kernel, so it must
produce the release the user sees from pkm.

Recipes then also state those paths by hand, in verify_paths, so the build can
assert the files landed. Those literals have no derivation behind them — someone
has to remember to edit them whenever the kernel's release moves. That has now
been missed three times: 4 -> 6 and 6 -> 7 were both caught at squashfs Step 4.5,
hours into a build, and the r8 bump left the pass-2 recipe naming a kernel that
no build will ever produce. The recipe's own comment records the first two and
calls a build-time derivation a tracked work item.

This gate does that derivation. It computes the expected KERNELRELEASE from
linux-kernel's recipe — the same single source of truth build.sh reads — and
refuses the build if any recipe states a different one. Static, no chroot
dependency, so the answer arrives at preflight instead of at squashfs, and the
recipe rather than the build log names what is wrong.

Step 4.5 stays exactly as it is. This gate makes the drift cheap to find; 4.5
remains the proof that the files actually landed, which no static check can
replace.

The gate reports and refuses; it does not rewrite recipes. A release-coupled
path is a claim about what the build produces, and correcting it silently under
a build would hide the very drift being detected.

Exit 0 clean, 1 on violations, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

KERNEL_RECIPE = "packages/core/linux-kernel/package.yml"

# Top-level `release:` / `version:`, anchored at column 0 so a key nested inside
# another mapping cannot be mistaken for the package's own. Both recipes' build
# scripts read the release with the same column-0 anchoring.
RELEASE_RE = re.compile(r'^release:[^\S\n]*"?(\d+)"?', re.M)
VERSION_RE = re.compile(r'^version:[^\S\n]*"?([0-9][^"\s#]*)"?', re.M)

# A KERNELRELEASE-shaped literal: a dotted version followed by -igos-<n>.
KERNELRELEASE_RE = re.compile(r'(\d+(?:\.\d+)+)-igos-(\d+)')


def kernel_release(repo_root: Path) -> str:
    """Derive <version>-igos-<release> from linux-kernel's recipe.

    Fails loudly on an unparseable field rather than assuming a default, exactly
    as build.sh does: the release is an identity value, and a guessed one would
    make this gate certify a kernel name nothing builds.
    """
    recipe = repo_root / KERNEL_RECIPE
    if not recipe.is_file():
        raise FileNotFoundError(
            f"{KERNEL_RECIPE} is missing — the kernel release cannot be derived")
    text = recipe.read_text()
    rel = RELEASE_RE.search(text)
    ver = VERSION_RE.search(text)
    if not rel:
        raise ValueError(
            f"cannot parse 'release:' from {KERNEL_RECIPE} — refusing to check "
            f"against a guessed kernel release")
    if not ver:
        raise ValueError(
            f"cannot parse 'version:' from {KERNEL_RECIPE} — refusing to check "
            f"against a guessed kernel version")
    return f"{ver.group(1)}-igos-{rel.group(1)}"


def scan(repo_root: Path) -> tuple[str, list[dict]]:
    """Return (expected_kernelrelease, violations).

    Every package.yml is scanned, not just the kernel's: a release-coupled path
    can be stated by any recipe that verifies a kernel artifact, and a gate that
    only looked where the problem has appeared before would miss the next one.
    """
    expected = kernel_release(repo_root)
    packages = repo_root / "packages"
    if not packages.is_dir():
        raise FileNotFoundError(
            f"{packages} is missing — a scan that finds nothing must not "
            f"report clean")

    violations: list[dict] = []
    for recipe in sorted(packages.glob("*/*/package.yml")):
        for lineno, line in enumerate(recipe.read_text().splitlines(), 1):
            stripped = line.lstrip()
            # The release: line's own changelog comment cites historical
            # kernel names on purpose — a record of what WAS, not a claim
            # about what this build produces.
            if stripped.startswith("#") or stripped.startswith("release:"):
                continue
            for m in KERNELRELEASE_RE.finditer(line):
                if m.group(0) == expected:
                    continue
                violations.append({
                    "recipe": str(recipe.relative_to(repo_root)),
                    "line": lineno,
                    "found": m.group(0),
                    "text": line.strip(),
                })
    return expected, violations


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=str(REPO_ROOT),
                    help="repository root to scan (default: this checkout)")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        expected, violations = scan(repo_root)
    except (FileNotFoundError, ValueError) as e:
        print(f"[kernel-release-lockstep] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    if not violations:
        print(f"[kernel-release-lockstep] PASS: every release-coupled path "
              f"names {expected}")
        return 0

    print(f"[kernel-release-lockstep] HALT: {len(violations)} path(s) name a "
          f"kernel release this build will not produce.", file=sys.stderr)
    print(f"  derived from {KERNEL_RECIPE}: {expected}", file=sys.stderr)
    for v in violations:
        print(f"  {v['recipe']}:{v['line']}", file=sys.stderr)
        print(f"    states : {v['found']}", file=sys.stderr)
        print(f"    in     : {v['text']}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  CONFIG_LOCALVERSION is stamped from linux-kernel's release "
          "(packages/core/linux-kernel/build.sh), so a bump there moves every "
          "kernel-named path. Update these to the derived value in the same "
          "change as the release bump.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
