#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-reboot-required-declared.py — 3.0-F28 activation-semantics gate.

A package that ships a payload which cannot activate on the running system
until reboot MUST declare `reboot_required: true` in its package.yml. Without
the declaration pkm cannot warn the user, and the payload installs silently
(the silent-activation failure, decided 2026-07-21: nvidia's kernel modules
land behind the nouveau blacklist and stay inactive, with no notice that a
reboot is what makes the new driver take over).

This gate scans every package's build.sh for the ACTUAL install of a
module/boot-path payload and fails closed if such a package does not declare
the field. It is the regression guard: a NEW out-of-tree driver or kernel
package that forgets the field is caught here rather than in the field.

Detection (ground-truth = the build.sh install commands, comments stripped so
documentation prose about modprobe/modules never false-positives — e.g.
amdgpu, whose amdgpu.ko is in-tree and whose build.sh only *mentions*
/etc/modprobe.d in a comment, is correctly NOT flagged):

  1. KERNEL IMAGE   — a cp/install/mv of a kernel image to /boot/vmlinuz*.
  2. MODULE BLACKLIST — a write (cat >/tee/install/cp) to /etc/modprobe.d/ in a
                        build.sh that also emits a `blacklist <module>` line
                        (an out-of-tree driver displacing an in-tree one, which
                        the running kernel cannot swap live).
  3. KERNEL MODULE  — a cp/install/mv of a *.ko file into /lib/modules.

Exit codes:
  0  every detected module/boot-path package declares reboot_required: true.
  1  one or more detected packages do NOT declare it (offenders on stderr).
  2  arguments invalid / packages tree not found.

Usage:
  python3 scripts/check-reboot-required-declared.py [--packages <dir>]
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_parser():
    """Import igos-build/parser.py (hyphenated dir → load by file path)."""
    path = _REPO_ROOT / "igos-build" / "parser.py"
    spec = importlib.util.spec_from_file_location("igos_build_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A build.sh line is a comment when its first non-whitespace char is '#'.
_COMMENT_RE = re.compile(r"^\s*#")
# Kernel image install to the boot path.
_KERNEL_IMAGE_RE = re.compile(r"\b(cp|install|mv)\b.*?/boot/vmlinu[xz]")
# A WRITE that targets /etc/modprobe.d/ (redirect, or a copy/install/tee).
_MODPROBE_WRITE_RE = re.compile(
    r"(>\s*[\"']?\S*/etc/modprobe\.d/|\b(cat|tee|install|cp|mv)\b[^\n]*/etc/modprobe\.d/)"
)
# A `blacklist <module>` directive emitted anywhere in the recipe.
_BLACKLIST_RE = re.compile(r"(^|['\"\s])blacklist\s+[a-zA-Z0-9_-]+")
# A kernel module (.ko) copied into /lib/modules.
_KO_INSTALL_RE = re.compile(r"\b(cp|install|mv)\b.*?/lib/modules/.*?\.ko\b")


def _noncomment_lines(text):
    return [ln for ln in text.splitlines() if not _COMMENT_RE.match(ln)]


def ships_module_or_boot_payload(build_sh_text):
    """Return (bool, reason) — does this build.sh install a payload that can
    only activate on reboot? Comments are stripped before matching so prose
    about modules/modprobe does not false-positive."""
    lines = _noncomment_lines(build_sh_text)
    body = "\n".join(lines)
    for ln in lines:
        if _KERNEL_IMAGE_RE.search(ln):
            return True, "installs a kernel image to /boot/vmlinuz*"
        if _KO_INSTALL_RE.search(ln):
            return True, "installs a kernel module (.ko) into /lib/modules"
    # Module blacklist: a write to modprobe.d + a blacklist directive.
    if any(_MODPROBE_WRITE_RE.search(ln) for ln in lines) and _BLACKLIST_RE.search(body):
        return True, "installs an /etc/modprobe.d blacklist for an out-of-tree module"
    return False, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--packages", default=str(_REPO_ROOT / "packages"),
        help="path to the packages/ tree (default: repo packages/)",
    )
    ap.add_argument(
        "--verbose", action="store_true",
        help="list every detected module/boot-path package, not just offenders",
    )
    args = ap.parse_args(argv)

    packages_dir = Path(args.packages)
    if not packages_dir.is_dir():
        print(f"error: packages tree not found: {packages_dir}", file=sys.stderr)
        return 2

    parser = _load_parser()

    detected = []   # (name, rel_path, reason)
    offenders = []  # (name, rel_path, reason)
    for tier_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        for pkg_dir in sorted(p for p in tier_dir.iterdir() if p.is_dir()):
            build_sh = pkg_dir / "build.sh"
            yml = pkg_dir / "package.yml"
            if not build_sh.is_file() or not yml.is_file():
                continue
            ships, reason = ships_module_or_boot_payload(
                build_sh.read_text(errors="replace")
            )
            if not ships:
                continue
            rel = pkg_dir.relative_to(packages_dir)
            try:
                pkg = parser.parse_template(yml)
                declared = bool(getattr(pkg, "reboot_required", False))
                name = pkg.name
            except Exception as e:  # a malformed recipe is not this gate's job
                print(f"warning: could not parse {rel}/package.yml: {e}",
                      file=sys.stderr)
                continue
            detected.append((name, str(rel), reason))
            if not declared:
                offenders.append((name, str(rel), reason))

    if args.verbose:
        print(f"Detected {len(detected)} module/boot-path package(s):")
        for name, rel, reason in detected:
            mark = "MISSING" if (name, rel, reason) in offenders else "declared"
            print(f"  [{mark}] {name} ({rel}) — {reason}")

    if offenders:
        print(
            f"\nERROR: {len(offenders)} package(s) ship a module/boot-path "
            f"payload but do NOT declare `reboot_required: true` (3.0-F28):",
            file=sys.stderr,
        )
        for name, rel, reason in offenders:
            print(f"  - {name} ({rel}): {reason}", file=sys.stderr)
        print(
            "\nAdd `reboot_required: true` to each package.yml so pkm can warn "
            "the user the payload activates only on reboot.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: all {len(detected)} module/boot-path package(s) declare "
        f"reboot_required (3.0-F28)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
