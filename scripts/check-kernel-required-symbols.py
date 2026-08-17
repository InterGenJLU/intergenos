#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-kernel-required-symbols.py — fail-closed produced-config assertion gate.

Both kernel recipes build their .config by concatenating the fragments and
running `make olddefconfig`. That merge has NO conflict detection, so a symbol
can be requested and silently not appear:

  * a dependency downgrade demotes it (DM_VERITY=y becomes =m because
    BLK_DEV_DM was =m), stripping a security guarantee while the build succeeds;
  * a parent symbol was never requested, so olddefconfig discards the children
    without a word. Measured in the shipped tree: the baseline fragment asked
    for THIRTEEN CONFIG_MMC_* host-controller drivers and never asked for the
    parent `menuconfig MMC`, so the produced kernel had no MMC subsystem at all
    and nothing failed. Evaluations run in virtual machines, which have no SD
    card reader, no I2C-HID touchpad and no on-board audio codec — a kernel
    missing all of them passes every automated check and fails on a real laptop.

So the produced config is asserted, and a dropped class is a BUILD FAILURE
rather than a silent shipment.

TWO REQUIREMENT KINDS, because they are not the same claim:

  EXACT   config/kernel/required-security-symbols.txt holds literal
          `CONFIG_X=value` lines. For these, built-in-ness IS the guarantee and
          =m would be a silent downgrade, so the value is part of the
          requirement.
  ENABLED config/kernel/required-hardware-symbols.txt holds bare `CONFIG_X`
          names. For a driver, module-versus-built-in is a legitimate packaging
          choice while ABSENCE is the defect, so =y and =m both satisfy it.

It also refuses a kernel whose lockdown `choice` resolved back to
LOCK_DOWN_KERNEL_FORCE_NONE — lockdown disabled at boot, which is exactly the
failure a positive check for FORCE_INTEGRITY can miss.

WHY THIS IS A SCRIPT AND NOT TWO LOOPS IN A RECIPE (decided 2026-08-11). The
assertions lived inline in pass 1. Pass 2 supersedes pass 1 and its payload is
what lands last on an installed system, so every property pass 1 asserts has to
be asserted by pass 2 as well — and copying two long symbol lists into a second
shell script is a drift class waiting to happen, of exactly the kind this
repository has already had to write a test to police once. One list, read from
one file, by both passes.

FAIL-CLOSED IN EVERY DIRECTION. Exits 2 — refusing the build — when it cannot
measure: an unreadable config, a config with no enabled symbols at all, a
missing or malformed requirement file, or a requirement file that holds
implausibly few entries. An instrument that saw nothing must never report that
it saw nothing wrong.

Exit codes:
    0  every required symbol is present at its required strength
    1  at least one requirement is not met
    2  the gate could not measure what it was asked to measure

Usage:
    scripts/check-kernel-required-symbols.py --config <produced .config>
"""

import argparse
import re
import sys
from pathlib import Path

# A requirement file shorter than this means the parse found something other
# than the real list, and the sweep would then pass for the wrong reason.
MIN_EXACT_REQUIREMENTS = 20
MIN_ENABLED_REQUIREMENTS = 30

RE_EXACT = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(\S+)$")
RE_NAME = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)$")

LOCKDOWN_DISABLED = "CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE=y"


class Unmeasurable(Exception):
    """The gate cannot see what it was asked to look at. Always a refusal."""


def read_requirements(path: Path, pattern: re.Pattern, minimum: int, kind: str) -> list:
    if not path.is_file():
        raise Unmeasurable(
            f"the {kind} requirement file is not readable at {path}. The gate refuses "
            "rather than assume there is nothing to require."
        )
    entries = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not pattern.match(line):
            raise Unmeasurable(
                f"{path}:{lineno}: cannot read this as a {kind} requirement: {raw!r}. "
                "A malformed requirement file is treated as unmeasurable rather than "
                "partially applied — the entries after a bad line would be silently lost."
            )
        entries.append(line)
    if len(entries) < minimum:
        raise Unmeasurable(
            f"{path} holds only {len(entries)} {kind} requirements; at least {minimum} "
            "were expected. A file that parses to almost nothing would let this gate "
            "pass for the wrong reason."
        )
    return entries


def read_config(path: Path):
    """Returns (set of literal 'CONFIG_X=value' lines, set of enabled names)."""
    if not path.is_file():
        raise Unmeasurable(f"the produced kernel config is not readable at {path}")
    literals, enabled = set(), set()
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("CONFIG_") or "=" not in line:
            continue
        literals.add(line)
        name, _, value = line.partition("=")
        if value in ("y", "m"):
            enabled.add(name)
    if not enabled:
        raise Unmeasurable(
            f"{path} contains no enabled symbols at all. That is not a kernel config "
            "this build produced, and an empty read must never certify a clean result."
        )
    return literals, enabled


def main() -> int:
    default_repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path,
                        help="the produced kernel .config to assert against")
    parser.add_argument("--repo-root", type=Path, default=default_repo_root)
    parser.add_argument("--exact-file", type=Path,
                        help="override the exact-value requirement file")
    parser.add_argument("--enabled-file", type=Path,
                        help="override the enabled-either requirement file")
    args = parser.parse_args()

    exact_path = args.exact_file or (args.repo_root / "config/kernel/required-security-symbols.txt")
    enabled_path = args.enabled_file or (args.repo_root / "config/kernel/required-hardware-symbols.txt")

    print("=" * 70)
    print("KERNEL REQUIRED-SYMBOL GATE")
    print("=" * 70)

    try:
        exact = read_requirements(exact_path, RE_EXACT, MIN_EXACT_REQUIREMENTS, "exact-value")
        enabled_req = read_requirements(enabled_path, RE_NAME, MIN_ENABLED_REQUIREMENTS, "enabled")
        literals, enabled = read_config(args.config)
    except Unmeasurable as exc:
        print("", file=sys.stderr)
        print("  REFUSING THE BUILD — the required-symbol gate cannot measure:", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print("", file=sys.stderr)
        return 2

    print(f"produced config : {args.config} ({len(enabled)} symbols enabled)")
    print(f"exact-value     : {exact_path} ({len(exact)} requirements)")
    print(f"enabled-either  : {enabled_path} ({len(enabled_req)} requirements)")
    print("")

    findings = []

    print("-" * 70)
    print("EXACT-VALUE requirements — the value IS the guarantee")
    print("-" * 70)
    missing_exact = [r for r in exact if r not in literals]
    for req in missing_exact:
        name = req.split("=", 1)[0]
        actual = next((l for l in literals if l.startswith(name + "=")), None)
        print(f"  MISSING (finding)  {req}" + (f"   [produced: {actual}]" if actual else "   [absent entirely]"))
        findings.append(
            f"{req} is not in the produced config"
            + (f" — it resolved to {actual} instead." if actual else " — the symbol is absent entirely.")
            + " A dependency downgrade or a fragment regression has stripped a guarantee "
              "this distribution requires; refusing to build a kernel that silently drops it."
        )
    print(f"  checked {len(exact)}, unmet {len(missing_exact)}")
    print("")

    print("-" * 70)
    print("ENABLED requirements — absence is the defect, =y and =m both pass")
    print("-" * 70)
    missing_enabled = [r for r in enabled_req if r not in enabled]
    for req in missing_enabled:
        print(f"  DROPPED (finding)  {req}")
        findings.append(
            f"{req} is neither =y nor =m in the merged config. The usual cause is that "
            "the symbol was requested but its PARENT symbol was not, so olddefconfig "
            "discarded it without a word — check the parent in the kernel's own Kconfig "
            "and request it in the overrides fragment. A virtual machine cannot exhibit "
            "this defect, which is why it must fail here and not on a user's laptop."
        )
    print(f"  checked {len(enabled_req)}, dropped {len(missing_enabled)}")
    print("")

    print("-" * 70)
    print("LOCKDOWN — the choice must not have resolved back to FORCE_NONE")
    print("-" * 70)
    if LOCKDOWN_DISABLED in literals:
        print(f"  PRESENT (finding)  {LOCKDOWN_DISABLED}")
        findings.append(
            "the kernel lockdown default resolved to FORCE_NONE, which disables lockdown "
            "at boot. This is the exact failure a positive check for FORCE_INTEGRITY can "
            "miss, which is why it is asserted separately."
        )
    else:
        print(f"  absent, as required  ({LOCKDOWN_DISABLED})")
    print("")

    print("=" * 70)
    if findings:
        print(f"REFUSING THE BUILD — {len(findings)} unmet requirement(s)")
        print("=" * 70)
        for finding in findings:
            print(f"  * {finding}", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    print("CLEAN — every required symbol is present at its required strength")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
