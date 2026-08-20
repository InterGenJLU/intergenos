#!/usr/bin/env python3
"""Draft the next CHANGELOG section from a landed range. A convenience.

THIS IS NOT A GATE and must never become one. It writes nothing, blocks
nothing, and its output is a starting point a human edits — the changelog is
a description of what a release means to the person running it, and no
script can derive that from a diff. What a script CAN do is stop the author
starting from a blank page and missing something that landed.

It reports, one line each:
  * packages ADDED in the range, with the version they arrived at;
  * packages REMOVED;
  * packages whose upstream VERSION moved, with both versions;
  * packages whose `release:` moved with the version unchanged, listed
    separately and under a heading that says they are probably not user
    facing — those are rebuilds and metadata changes, which the recipe's own
    `# rNN:` note chain already records. They are shown rather than hidden
    because "probably" is the author's call to make, not this script's.

Usage:
    scripts/draft-changelog-section.py --base <ref> --head <ref>
    scripts/draft-changelog-section.py --base R001 --head HEAD > /tmp/draft.md

The `version:` and `release:` shapes are the same ones
scripts/check-changelog-accumulation.py uses; the two are held in lockstep by
tests/preflight/test_changelog_enforcement.py so this tool cannot start
reporting a different set from the one the gate enforces.
"""

import argparse
import re
import subprocess
import sys
from collections import defaultdict

RELEASE_RE = re.compile(
    r'^(release:[^\S\n]*)(\d+)([^\S\n]*(?:#[^\n]*)?)$', re.M)
VERSION_RE = re.compile(
    r'^version:[^\S\n]*[\'"]?([^\'"\n#]+?)[\'"]?[^\S\n]*(?:#[^\n]*)?$', re.M)
PKG_YML_RE = re.compile(r'^packages/[^/]+/[^/]+/package\.yml$')


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args],
                          check=True, capture_output=True, text=True).stdout


def _recipe_at(repo, ref, path):
    try:
        text = _git(repo, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None, None
    rm = RELEASE_RE.search(text or "")
    vm = VERSION_RE.search(text or "")
    return (int(rm.group(2)) if rm else None,
            vm.group(1).strip() if vm else None)


def survey(repo, base, head):
    """What changed about the shipped package set between two refs."""
    out = _git(repo, "diff", "--name-status", f"{base}..{head}")
    added, removed, moved, rebuilt = [], [], [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not PKG_YML_RE.match(path):
            continue
        pkg = path.split("/")[2]
        tier = path.split("/")[1]
        if status.startswith("A"):
            _r, v = _recipe_at(repo, head, path)
            added.append((tier, pkg, v))
            continue
        if status.startswith("D"):
            _r, v = _recipe_at(repo, base, path)
            removed.append((tier, pkg, v))
            continue
        old_rel, old_v = _recipe_at(repo, base, path)
        new_rel, new_v = _recipe_at(repo, head, path)
        if old_v and new_v and old_v != new_v:
            moved.append((tier, pkg, old_v, new_v))
        elif old_rel is not None and new_rel is not None and old_rel != new_rel:
            rebuilt.append((tier, pkg, old_rel, new_rel))
    return added, removed, moved, rebuilt


def render(added, removed, moved, rebuilt, base, head):
    lines = []
    lines.append("## [UNRELEASED] — set the identity and date on publication day")
    lines.append("")
    lines.append(f"<!-- Drafted from {base}..{head}. Every line below is a")
    lines.append("     starting point: rewrite it to say what the change means")
    lines.append("     to someone running the system, and delete what does not")
    lines.append("     belong in a user-facing record. -->")
    lines.append("")

    if added:
        lines.append("### Added")
        for tier, pkg, v in sorted(added):
            ver = f" {v}" if v else ""
            lines.append(f"- `{pkg}`{ver} — new in the {tier} tier. "
                         "Say what it is for.")
        lines.append("")

    if moved:
        lines.append("### Changed")
        for tier, pkg, old_v, new_v in sorted(moved):
            lines.append(f"- `{pkg}` {old_v} → {new_v}. "
                         "Say what the new version brings.")
        lines.append("")

    if removed:
        lines.append("### Removed")
        for tier, pkg, v in sorted(removed):
            ver = f" {v}" if v else ""
            lines.append(f"- `{pkg}`{ver} — no longer shipped. Say why, and "
                         "what replaces it.")
        lines.append("")

    if rebuilt:
        lines.append("<!-- REBUILDS AND METADATA CHANGES — probably NOT user")
        lines.append("     facing. Each recipe's own `# rNN:` note already")
        lines.append("     records these. Keep a line here only if a user")
        lines.append("     would notice the difference.")
        for tier, pkg, old_r, new_r in sorted(rebuilt):
            lines.append(f"       {pkg}: release {old_r} -> {new_r}")
        lines.append("-->")
        lines.append("")

    if not (added or removed or moved or rebuilt):
        lines.append("<!-- Nothing in this range changed the shipped package")
        lines.append("     set. If the release carries other user-visible")
        lines.append("     work, it will not be found here — write it by")
        lines.append("     hand. -->")
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args()
    try:
        added, removed, moved, rebuilt = survey(args.repo, args.base,
                                                args.head)
    except subprocess.CalledProcessError as e:
        print(f"could not read {args.base}..{args.head}: {e}", file=sys.stderr)
        return 1
    print(render(added, removed, moved, rebuilt, args.base, args.head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
