#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Self-policing gate for source-aware change detection.

A first-party package whose build.sh reads from an EXTERNAL in-tree dir
(/mnt/intergenos/{intergen,pkm,installer} or assets/...) must declare that dir
in its `source_tree:` so the content is folded into the skip-built fingerprint
(igos-build/content_hash.py). Otherwise an edit to that external source does
NOT flip the package's fingerprint and a targeted build silently ships the
STALE binary — the exact class that bit intergen-welcome and cost us days.

This gate keeps the fix COMPLETE as the tree grows: it fails the build's
validate phase if any package reads an external source dir not covered by its
source_tree (or by the package's own dir, which is hashed automatically). The
remedy is always harmless — add the dir to source_tree (over-declaring only
hashes a bit more; it can never ship stale).

Detection is conservative: it scans real source reads on non-comment lines for
the two external-source path shapes, normalizes them repo-relative, and checks
each against the package's declared source_tree (prefix match). A package's own
package-dir content is covered by content_hash arm (b) and never needs listing.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import parse_template, discover_templates, TemplateError  # noqa: E402

# A build.sh reads repo content via a recognized REPO-ROOT prefix (all of which
# expand to the repo root at build time). We capture the repo-relative path
# from such a prefix, then flag it only if it lives under an EXTERNAL source
# root (intergen/pkm/installer/assets) and NOT under packages/ (a package's own
# dir is hashed automatically by content_hash arm (b) and never needs listing).
#
# Requiring the prefix is what keeps this precise — it excludes the two
# false-positive shapes: `$out_dir/assets/...` (assets/ inside an UPSTREAM
# build dir, e.g. bat) and a bare cwd-relative `assets/...` (which wouldn't
# resolve to the repo root anyway). Our real repo reads always use one of these
# prefixes (verified across the tree).
_ROOT_PREFIX = (
    r'(?:/mnt/intergenos'
    r'|\$\{IGOS_SOURCE_ROOT:-/mnt/intergenos\}'
    r'|\$\{IGOS_SOURCE_ROOT\}'
    r'|\$IGOS_SOURCE_ROOT)'
)
_PATH_RE = re.compile(_ROOT_PREFIX + r'/([A-Za-z0-9._+/-]+)')
# The first-party top-level source roots that count as "external" — a build
# script reading from one of these (outside its own package dir) must declare it
# in source_tree. MUST stay in sync with the source dirs the build rsyncs into
# the chroot (build-intergenos.sh ensure_sources_staged): a NEW first-party
# top-level source root added to the tree has to be added here too, or reads
# from it silently escape this gate (WC review). Kept a literal for clarity —
# this comment is the reminder the gate cannot enforce on itself.
_EXTERNAL_TOPS = ("intergen", "pkm", "installer", "assets")
# Strip a shell comment (`# ...` at start-of-token) so a path mentioned only in
# a comment never trips the gate.
_COMMENT_RE = re.compile(r'(^|\s)#.*$')


def external_reads(build_sh_text: str) -> set:
    """Set of repo-relative EXTERNAL source paths a build.sh reads from.

    External = under intergen/pkm/installer/assets. Own-dir reads (packages/...)
    are excluded — content_hash arm (b) covers them automatically.
    """
    found = set()
    for raw in build_sh_text.splitlines():
        line = _COMMENT_RE.sub("", raw)
        if not line.strip():
            continue
        for m in _PATH_RE.finditer(line):
            rel = m.group(1).rstrip("/")
            top = rel.split("/", 1)[0]
            if top == "packages":
                continue  # own package dir — hashed by content_hash arm (b)
            if top in _EXTERNAL_TOPS:
                found.add(rel)
    return found


def _covered(read_path: str, source_tree: list) -> bool:
    """A read is covered if some source_tree entry equals it or is a parent."""
    for st in source_tree:
        st = st.rstrip("/")
        if read_path == st or read_path.startswith(st + "/"):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate: external source reads must be declared in source_tree")
    ap.add_argument("--packages-dir", default=str(REPO_ROOT / "packages"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    packages_dir = Path(args.packages_dir)
    gaps = []      # (pkg, uncovered_path)
    errors = []
    checked = 0

    for yml in discover_templates(packages_dir):
        try:
            pkg = parse_template(yml)
        except TemplateError as e:
            errors.append(f"{yml}: parse error: {e}")
            continue
        # Scan build.sh AND every other shell script in the package dir — a
        # build.sh that delegates the external source read to a helper script (or
        # a sourced fragment) in its own dir would otherwise slip the gate, and
        # the undeclared source_tree is exactly the stale-ship class this gate
        # exists to prevent (WC review). Makefiles are not shell; if a package
        # ever reads external source from a Makefile, add that here too.
        pkg_scripts = sorted(yml.parent.glob("*.sh"))
        if not pkg_scripts:
            continue
        reads = set()
        for sh in pkg_scripts:
            try:
                reads |= external_reads(sh.read_text())
            except (OSError, UnicodeDecodeError):
                continue
        if not reads:
            continue
        checked += 1
        st = pkg.source_tree or []
        for r in sorted(reads):
            if not _covered(r, st):
                gaps.append((pkg.name, r))
        if args.verbose and reads:
            print(f"  {pkg.name}: reads {sorted(reads)} ; source_tree={st}")

    if errors:
        print("ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2

    if gaps:
        print("SOURCE_TREE COVERAGE GAP — these packages read an external source dir "
              "not declared in source_tree (a source edit there would ship STALE):",
              file=sys.stderr)
        for name, path in gaps:
            print(f"  {name}: reads '{path}' — add it to source_tree in package.yml", file=sys.stderr)
        return 1

    print(f"OK — {checked} package(s) with external source reads, all covered by source_tree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
