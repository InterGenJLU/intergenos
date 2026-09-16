#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""check-setuid-inventory.py — fail-closed setuid/setgid + ownership gate.

Born from the GE-01 setuid-strip regression (L29, 2026-07-05): the staging
chokepoint's blanket `chown -R root:root` cleared suid/sgid on every
privileged binary in the corpus — the kernel clears those bits on any chown
of a regular file, even by root — and nothing between the archive and the
installed system refused. sudo/su/passwd/pkexec all shipped inert, on the
live ISO and on installed targets. This gate makes that class impossible to
seal again, in BOTH directions:

  1. STRIPPED-BIT / FLATTENED-OWNERSHIP arm — every inventory entry present
     on the chroot must carry exactly the declared mode, owner, and group.
  2. UNEXPECTED-PRIVILEGE arm — any suid/sgid regular file on the chroot
     NOT matched by the inventory refuses the seal (a setuid injection or
     an undeclared upstream addition must be triaged, never ridden).

  3. RECIPE-DECLARATION arm (--recipes, added 2026-09-16) — every recipe
     line that SETS a setuid or setgid bit must name a path this inventory
     already declares. Arms 1 and 2 fire at the seal, which is the last
     moment before an image is written and a long way from the commit that
     introduced the privilege; this arm answers the same question at
     authoring time, so a recipe cannot add a privileged program unless the
     declared list changes in the same commit. It reads the SAME list — no
     second declaration exists to drift.

     Population: the arm's verdict covers the SHIPPED set only, resolved
     through the builder's own parser (iso_include, tier defaults included),
     because the inventory declares what the image ships. Privileged modes in
     mirror-only recipes are REPORTED in their own section with their paths
     and never narrowed away into silence — they are outside the shipped
     population, not outside the reader's view.

An inventory path absent from the chroot is skipped — package presence is
owned by verify_paths (4.5) and the eviction dep rules, not this gate.

Owner/group names resolve against the CHROOT's etc/passwd + etc/group.

Usage: check-setuid-inventory.py --chroot /mnt/igos \
           [--inventory <repo>/config/setuid-inventory.txt]
       check-setuid-inventory.py --recipes <repo>/packages \
           [--inventory <repo>/config/setuid-inventory.txt]
Exit 0 = PASS; exit 1 = violations (each named); exit 2 = usage/inventory
error; exit 3 = empty audit (nothing found to audit — a gate that cannot see
must halt, not wave through: on --chroot, zero suid/sgid files AND zero
inventory entries present; on --recipes, zero privileged-mode lines found in
the whole recipe tree, which means the scanner stopped matching reality).
"""

import argparse
import fnmatch
import importlib
import os
import re
import stat
import sys
from pathlib import Path

# Pseudo-fs / volatile trees never audited (mirror of needclosure.py's skips).
SKIP_PREFIXES = ("proc", "sys", "dev", "run", "tmp", "mnt", "sources", "build")


def parse_inventory(path: Path):
    entries = []
    for ln, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            print(f"[setuid-gate] inventory syntax error at line {ln}: {raw!r}")
            sys.exit(2)
        glob, mode_s, owner, group = parts
        try:
            mode = int(mode_s, 8)
        except ValueError:
            print(f"[setuid-gate] bad octal mode at line {ln}: {mode_s!r}")
            sys.exit(2)
        entries.append((glob, mode, owner, group))
    if not entries:
        print(f"[setuid-gate] inventory {path} declares nothing — refusing")
        sys.exit(2)
    return entries


def load_ids(chroot: Path):
    """uid->name and gid->name maps from the chroot's own passwd/group."""
    users, groups = {}, {}
    for fname, table in (("etc/passwd", users), ("etc/group", groups)):
        f = chroot / fname
        if not f.is_file():
            print(f"[setuid-gate] {f} missing — cannot resolve names, refusing")
            sys.exit(2)
        for line in f.read_text().splitlines():
            bits = line.split(":")
            if len(bits) >= 3 and bits[2].isdigit():
                table[int(bits[2])] = bits[0]
    return users, groups



# --- RECIPE-DECLARATION arm ------------------------------------------------
#
# A recipe sets a privileged bit in one of three shapes, and all three are
# matched against the text of the recipe rather than by running it: an octal
# `chmod`, a symbolic `chmod` that adds s, and `install -m <octal>`. Both the
# DESTDIR form (staged payload) and the bare-path form (a post_install hook
# acting on the installed system) count — either one puts a privileged program
# on a machine.
_OCTAL_CHMOD_RE = re.compile(r'\bchmod\s+(?:-[A-Za-z-]+\s+)*([0-7]{3,4})\s+([^\n;&|]+)')
_SYMBOLIC_CHMOD_RE = re.compile(r'\bchmod\s+(?:-[A-Za-z-]+\s+)*([ugoa]*\+[rwxXst]*s[rwxXst]*)\s+([^\n;&|]+)')
_INSTALL_MODE_RE = re.compile(r'\binstall\b[^\n;&|]*?-m\s*([0-7]{3,4})([^\n;&|]*)')
_VAR_RE = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*')


def _targets(blob: str):
    """Absolute install paths named in one command's argument text.

    A shell variable inside a path becomes `*`: the inventory already declares
    version-suffixed programs as globs (`/usr/sbin/exim-*`), so a recipe's
    `${version}` and the list's glob meet on the same shape instead of being
    compared as literals that can never be equal.
    """
    out = []
    for raw in blob.split():
        tok = raw.strip().strip('"').strip("'")
        for prefix in ("${DESTDIR}", "$DESTDIR", "${pkgdir}", "$pkgdir"):
            if tok.startswith(prefix):
                tok = tok[len(prefix):]
        if not tok.startswith("/"):
            continue
        tok = _VAR_RE.sub("*", tok)
        tok = tok.rstrip('\\')
        if tok not in out:
            out.append(tok)
    return out


def _privileged_recipe_targets(build_sh: Path):
    """[(path, mode-as-written, line number)] for every privileged-mode line."""
    found = []
    text = build_sh.read_text(errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in _OCTAL_CHMOD_RE.finditer(line):
            if int(m.group(1), 8) & 0o6000:
                for t in _targets(m.group(2)):
                    found.append((t, m.group(1), lineno))
        for m in _SYMBOLIC_CHMOD_RE.finditer(line):
            for t in _targets(m.group(2)):
                found.append((t, m.group(1), lineno))
        for m in _INSTALL_MODE_RE.finditer(line):
            if int(m.group(1), 8) & 0o6000:
                targets = _targets(m.group(2))
                # `install -m MODE src... dest` — the LAST absolute path is the
                # destination; earlier ones are sources being read, not files
                # being given privilege. `install -d` names directories only,
                # and a directory is not this inventory's subject.
                if targets and " -d" not in line:
                    found.append((targets[-1], m.group(1), lineno))
    return found


def _declared(path: str, entries) -> bool:
    """True when the declared list already covers this path.

    Matched in both directions: the list's globs cover a concrete recipe path,
    and a recipe path that itself became a glob (a `${version}` suffix) covers
    a concrete list entry.
    """
    for glob, _mode, _owner, _group in entries:
        if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(glob, path):
            return True
    return False


def audit_recipes(packages_dir: Path, entries, inv_path: Path) -> int:
    """Authoring-time arm. Returns the process exit code."""
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    try:
        parse_template = importlib.import_module("igos-build.parser").parse_template
    except Exception as e:  # the resolver is the builder's own; no local copy
        print(f"[setuid-gate] cannot load the recipe parser ({e}) — refusing")
        return 2

    shipped_violations, mirror_only, scanned, parse_failures = [], [], 0, []
    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml, build_sh = pkg_dir / "package.yml", pkg_dir / "build.sh"
            if not yml.is_file() or not build_sh.is_file():
                continue
            hits = _privileged_recipe_targets(build_sh)
            if not hits:
                continue
            scanned += len(hits)
            try:
                ships = bool(parse_template(yml).iso_include)
            except Exception as e:
                # A recipe whose classification cannot be read is not silently
                # treated as mirror-only — that would be the mask.
                parse_failures.append(f"{yml}: {e}")
                ships = True
            for path, mode, lineno in hits:
                if _declared(path, entries):
                    continue
                # Repo-relative where it is inside the repository, absolute
                # where it is not — a scanned tree may live anywhere (the
                # gate's own tests point it at fixtures outside the checkout).
                try:
                    shown = build_sh.relative_to(repo_root)
                except ValueError:
                    shown = build_sh
                where = f"{shown}:{lineno}"
                if ships:
                    shipped_violations.append(
                        f"{path}: recipe sets mode {mode} at {where} but "
                        f"{inv_path.name} declares no such path — declare it "
                        f"in the same commit or do not set the bit")
                else:
                    mirror_only.append(f"{path} (mode {mode}, {where})")

    print(f"[setuid-gate] audited recipes: {scanned} privileged-mode line(s) "
          f"found, {len(mirror_only)} in mirror-only packages, "
          f"{len(shipped_violations)} violation(s)")
    if scanned == 0:
        print("[setuid-gate] EMPTY AUDIT — no privileged-mode line found in "
              "any recipe; a gate that cannot see must halt")
        return 3
    if mirror_only:
        print("[setuid-gate] mirror-only privileged modes (reported, not part "
              "of the shipped-set verdict — the image does not carry these):")
        for m in mirror_only:
            print(f"[setuid-gate]   {m}")
    if parse_failures:
        for f in parse_failures:
            print(f"[setuid-gate]   UNREADABLE recipe, audited as shipped: {f}")
    if shipped_violations:
        for v in shipped_violations:
            print(f"[setuid-gate]   {v}")
        print("[setuid-gate] FAIL — an undeclared privileged program")
        return 1
    print("[setuid-gate] PASS — every privileged mode a shipped recipe sets is "
          "declared")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="setuid/setgid inventory gate")
    ap.add_argument("--chroot", help="audit a built chroot (arms 1 and 2)")
    ap.add_argument("--recipes", help="audit the recipe tree (arm 3): the "
                                      "packages/ directory to scan")
    ap.add_argument("--inventory", default=None,
                    help="default: <script-repo>/config/setuid-inventory.txt")
    args = ap.parse_args(argv)

    if bool(args.chroot) == bool(args.recipes):
        print("[setuid-gate] name exactly one of --chroot or --recipes")
        sys.exit(2)

    inv_path = Path(args.inventory) if args.inventory else \
        Path(__file__).resolve().parent.parent / "config" / "setuid-inventory.txt"
    entries = parse_inventory(inv_path)

    if args.recipes:
        sys.exit(audit_recipes(Path(args.recipes), entries, inv_path))

    chroot = Path(args.chroot)
    users, groups = load_ids(chroot)

    violations = []
    matched_entries = set()
    privileged_found = 0

    for dirpath, dirnames, filenames in os.walk(chroot):
        rel_dir = os.path.relpath(dirpath, chroot)
        if rel_dir != "." and rel_dir.split(os.sep, 1)[0] in SKIP_PREFIXES:
            dirnames[:] = []
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = "/" + os.path.relpath(full, chroot)
            mode = stat.S_IMODE(st.st_mode)
            owner = users.get(st.st_uid, str(st.st_uid))
            group = groups.get(st.st_gid, str(st.st_gid))
            hit = None
            for e in entries:
                if fnmatch.fnmatch(rel, e[0]):
                    hit = e
                    break
            if hit:
                matched_entries.add(hit[0])
                if (mode, owner, group) != (hit[1], hit[2], hit[3]):
                    violations.append(
                        f"{rel}: is {mode:o} {owner}:{group}, inventory "
                        f"declares {hit[1]:o} {hit[2]}:{hit[3]} "
                        f"(stripped bit / flattened ownership)")
                if mode & 0o6000:
                    privileged_found += 1
            elif mode & 0o6000:
                privileged_found += 1
                violations.append(
                    f"{rel}: UNEXPECTED suid/sgid ({mode:o} {owner}:{group}) "
                    f"— not in {inv_path.name}; triage before sealing")

    print(f"[setuid-gate] audited chroot: {len(matched_entries)} inventory "
          f"entries present, {privileged_found} privileged file(s) seen, "
          f"{len(violations)} violation(s)")
    if not matched_entries and privileged_found == 0:
        print("[setuid-gate] EMPTY AUDIT — no inventory entry present and no "
              "privileged file found; a gate that cannot see must halt")
        sys.exit(3)
    if violations:
        for v in violations:
            print(f"[setuid-gate]   {v}")
        print("[setuid-gate] FAIL — refusing the seal")
        sys.exit(1)
    print("[setuid-gate] PASS — every present inventory entry exact; no "
          "unexpected privileged files")
    sys.exit(0)


if __name__ == "__main__":
    main()
