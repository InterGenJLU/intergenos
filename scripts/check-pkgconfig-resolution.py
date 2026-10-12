#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""
check-pkgconfig-resolution.py — every installed pkg-config file resolves.

Walks the built root's pkg-config directories (usr/lib/pkgconfig,
usr/lib32/pkgconfig, usr/share/pkgconfig) and asks pkg-config itself whether
each module resolves against that root alone (`pkg-config --exists
--print-errors <module>` with PKG_CONFIG_LIBDIR pointing into the root and
PKG_CONFIG_SYSROOT_DIR set to it). A module whose Requires / Requires.private
name a package the root does not carry is a finding: the next package that
asks pkg-config for it fails to configure, and the build halts there.

The class this guards: a recipe supplies a library NAME without its
pkg-config name. ncurses r2 shipped libtinfo.so.6 / libtinfo.so as links onto
the wide library and no tinfo.pc; readline 8.3, configured after that ncurses
was installed, found -ltinfo and wrote `Requires.private: tinfo` into
readline.pc; libxml2's meson then failed its pkg-config lookup of readline and
the core-extra phase halted at its ninth package (R001.3 build, 2026-10-12).

Dispositions:
  1. the owning recipe supplies the missing pkg-config name (ncurses-core r4:
     tinfo.pc -> ncursesw.pc), or declares the dependency it really has;
  2. a reviewed allowlist entry with a mandatory reason, for a module that is
     unresolvable BY ITS OWN PACKAGING and that nothing in the tree consumes
     (a test-support .pc whose requirement is a test library the package does
     not install; an internal library whose bundled dependency ships no .pc).

Allowlist format (config/pkgconfig-resolution-allowlist.txt): one module name
per line, then a TAB or two-plus spaces, then the reason; `#` comments and
blank lines ignored. An entry without a reason refuses the gate. An entry
whose module now resolves, or no longer exists in the root, is STALE and
fails the gate: the list states reality or it states nothing.

Exit status:
  0  every module resolves, or is allowlisted with a reason (counts printed)
  1  one or more modules unresolved and not allowlisted, a stale allowlist
     entry, or zero modules scanned (an empty scan certifies nothing)
  2  usage / fatal: root or allowlist missing, pkg-config unavailable
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PKGCONFIG_DIRS = (
    ("usr/lib/pkgconfig", "usr/share/pkgconfig"),
    ("usr/lib32/pkgconfig", "usr/share/pkgconfig"),
    ("usr/share/pkgconfig", "usr/lib/pkgconfig"),
)


def load_allowlist(path: Path) -> dict[str, str]:
    """{module: reason}; a line without a reason is fatal (exit 2)."""
    entries: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\S+)(?:\t+| {2,})(.+)$", line)
        if not m or not m.group(2).strip():
            print(f"FATAL: {path}:{n}: allowlist entry without a reason: {line!r}",
                  file=sys.stderr)
            sys.exit(2)
        entries[m.group(1)] = m.group(2).strip()
    return entries


def resolve(pkg_config: str, root: Path, libdir: str, sharedir: str, module: str):
    """(ok, first_error_line) for one module against the root alone."""
    env = dict(os.environ)
    env["PKG_CONFIG_LIBDIR"] = f"{root / libdir}:{root / sharedir}"
    env["PKG_CONFIG_SYSROOT_DIR"] = str(root)
    env.pop("PKG_CONFIG_PATH", None)
    r = subprocess.run([pkg_config, "--exists", "--print-errors", module],
                       env=env, capture_output=True, text=True)
    if r.returncode == 0:
        return True, ""
    err = (r.stderr or r.stdout).strip().splitlines()
    first = next((l for l in err if "not found" in l or "Package" in l), err[0] if err else "")
    return False, first.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("/mnt/igos"),
                    help="the built root (default /mnt/igos)")
    ap.add_argument("--allowlist", type=Path, required=True)
    ap.add_argument("--pkg-config", default="pkg-config",
                    help="the pkg-config binary to ask (default: pkg-config on PATH)")
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"FATAL: root not found: {args.root}", file=sys.stderr)
        return 2
    if not args.allowlist.is_file():
        print(f"FATAL: allowlist not found: {args.allowlist}", file=sys.stderr)
        return 2
    pkg_config = shutil.which(args.pkg_config) or args.pkg_config
    if not Path(pkg_config).is_file():
        print(f"FATAL: pkg-config not available: {args.pkg_config}", file=sys.stderr)
        return 2
    allow = load_allowlist(args.allowlist)

    scanned = 0
    unresolved: list[tuple[str, str, str]] = []   # (dir, module, error)
    allowlisted: list[tuple[str, str, str]] = []  # (dir, module, reason)
    seen: set[str] = set()
    for libdir, sharedir in PKGCONFIG_DIRS:
        d = args.root / libdir
        if not d.is_dir():
            continue
        files = sorted(p for p in d.glob("*.pc"))
        n_dir = 0
        for pc in files:
            module = pc.name[:-3]
            n_dir += 1
            scanned += 1
            seen.add(module)
            ok, err = resolve(pkg_config, args.root, libdir, sharedir, module)
            if ok:
                continue
            if module in allow:
                allowlisted.append((libdir, module, allow[module]))
            else:
                unresolved.append((libdir, module, err))
        print(f"[pkgconfig-resolution] {libdir}: {n_dir} module(s)")

    stale = sorted(m for m in allow
                   if m not in {x[1] for x in allowlisted})
    print(f"[pkgconfig-resolution] scanned {scanned} module(s) in {args.root}: "
          f"{scanned - len(unresolved) - len(allowlisted)} resolve, "
          f"{len(allowlisted)} allowlisted (reasoned), {len(unresolved)} unresolved, "
          f"{len(stale)} stale allowlist entr{'y' if len(stale) == 1 else 'ies'}")
    for libdir, module, reason in allowlisted:
        print(f"  allowlisted: {module} ({libdir}) — {reason}")

    rc = 0
    if scanned == 0:
        print(f"[pkgconfig-resolution] FAIL — zero pkg-config files found under "
              f"{args.root}; an empty scan certifies nothing (wrong root?)")
        rc = 1
    if unresolved:
        print(f"[pkgconfig-resolution] FAIL — {len(unresolved)} module(s) do not resolve:")
        for libdir, module, err in unresolved:
            print(f"  {module} ({libdir}/{module}.pc): {err}")
        print("  Disposition: the owning recipe supplies the missing pkg-config name or")
        print("  declares the dependency it has; or a REASONED allowlist entry for a module")
        print("  unresolvable by its own packaging that nothing in the tree consumes.")
        rc = 1
    if stale:
        print(f"[pkgconfig-resolution] FAIL — {len(stale)} allowlist entr"
              f"{'y' if len(stale) == 1 else 'ies'} no longer describe"
              f"{'s' if len(stale) == 1 else ''} reality (the module resolves, or is not "
              f"installed): {', '.join(stale)} — remove or correct the entry")
        rc = 1
    if rc == 0:
        print("[pkgconfig-resolution] PASS — every installed pkg-config file resolves "
              "against the root, or is allowlisted with a reason")
    return rc


if __name__ == "__main__":
    sys.exit(main())
