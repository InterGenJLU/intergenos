#!/usr/bin/env python3
"""Fail-closed gate: a push that changes the shipped package set must move
CHANGELOG.md with it, or say in the commit why it does not.

CHANGELOG.md was absent for R001 entirely and its frame still read
v1.0-Unreleased afterwards, because nothing connected the act of changing
what ships to the act of recording it. Release day is the worst moment to
reconstruct a release's contents from a commit log, and a changelog
assembled that way is a curated account rather than a record.

This gate makes the connection structural, at the cheapest point: the push.

WHAT FIRES IT. A commit that either
  * changes the upstream `version:` of a packages/*/*/package.yml — the
    version a user actually receives, or
  * ADDS a packages/*/*/package.yml that the range did not have before (a
    package joining the shipped set), or
  * DELETES one (a package leaving it).

WHAT DELIBERATELY DOES NOT FIRE IT: a `release:` bump that leaves the
upstream version alone. That is a rebuild or a metadata change, and this
project ALREADY records those, in the recipe's own `# rNN:` note chain
which scripts/check-release-notes.py enforces on the same commit. Firing
here as well would duplicate an existing record in a user-facing document
that should not carry it. MEASURED, and this is why the trigger is drawn
here: over the last 40 commits on the development branch, keying on any
release bump would have blocked 21 of them — mostly hand-bumps,
landing-collision re-derivations and comment-register changes. A gate that
demands an exemption on more than half of all pushes teaches people to
paste the exemption reflexively, which is how a required field stops being
a requirement.

The narrowed trigger measured on the same 40 commits blocks NONE of them,
and a null result on its own would only mean the gate never fires — so it
was checked against a true positive from real history in the same pass:
commit c7ee46374 (the R001.1 package wave) adds ethtool, logrotate,
nvme-cli, nmap, openvpn, tcpdump, wireguard-tools and thirteen more, and
the gate names all twenty and blocks. Silent on churn, loud on exactly the
class a user-facing changelog exists to carry.

WHAT SATISFIES IT. Either of:
  * CHANGELOG.md is touched anywhere in the push range. Range-level, not
    per-commit, on purpose: bumping in one commit and writing the entry in
    the next is ordinary, honest work and a gate that forbade it would only
    teach people to squash for the gate's benefit.
  * the triggering commit carries a REASONED exemption trailer (below).

THE TRAILER, and why it is not `NO-GATE:`. The key is
`Changelog-Exempt: <reason>`. A distinct key rather than the house-wide
blanket override, because "this change does not belong in a user-facing
changelog" is a specific editorial judgment worth recording in its own
words next to the change it describes — a reader of the log later can see
WHY a bump went unrecorded without having to guess.

THE REASONED-VALUE RULE. The value must be a real sentence of reason. A
required field that accepts "n/a" is not a requirement, it is a formality,
and this project has already been bitten by exactly that shape elsewhere
(a test policy whose mandatory `reason` was never checked against what
actually failed). So the value must be at least MIN_REASON_CHARS
characters after stripping, must contain a space (one word is a label, not
a reason), and must not be one of the recognised non-answers.

WHAT IT DOES NOT DO. It does not read the changelog's CONTENT or judge
whether the entry describes the bump — a gate cannot referee prose. It
checks that the record moved when the shipped set moved, and leaves the
quality of the entry to review, which is where judgment belongs.

STATED LIMITATION: the house-wide `NO-GATE:` override is honored here as
it is everywhere else in the pre-push chain. That means the reasoned-value
rule above can be bypassed by a blanket override. Honoring it is the
deliberate choice — a gate that could not be overridden at all would be
the first in this chain that cannot, and consistency in the escape hatch
matters more than sealing this one. A NO-GATE override is visible in the
commit message forever, which is the actual control.

Exit codes: 0 = clean, 1 = blocked (a violation, or a fail-closed error).
"""

import argparse
import re
import subprocess
import sys

# Shared shape with scripts/check-release-notes.py and
# bump-changed-releases.py — kept in lockstep by
# tests/preflight/test_changelog_enforcement.py, which asserts this module
# and check-release-notes.py agree on what a release line looks like.
RELEASE_RE = re.compile(
    r'^(release:[^\S\n]*)(\d+)([^\S\n]*(?:#[^\n]*)?)$', re.M)
# The UPSTREAM version — what a user actually receives. Quoted or bare.
VERSION_RE = re.compile(
    r'^version:[^\S\n]*[\'"]?([^\'"\n#]+?)[\'"]?[^\S\n]*(?:#[^\n]*)?$', re.M)

PKG_YML_RE = re.compile(r'^packages/[^/]+/[^/]+/package\.yml$')
CHANGELOG_PATH = "CHANGELOG.md"

TRAILER_RE = re.compile(r'^Changelog-Exempt:[^\S\n]*(.*)$', re.M)
NO_GATE_RE = re.compile(r'^NO-GATE:', re.M)

MIN_REASON_CHARS = 12

# Values that look like a reason and are not one. Compared case-folded
# against the whole stripped value, so a real sentence that merely contains
# one of these words is unaffected.
NON_REASONS = frozenset({
    "n/a", "na", "none", "no", "nothing", "-", "--", ".", "tbd", "todo",
    "not needed", "no changelog", "no changelog needed", "not applicable",
    "trivial", "minor", "internal", "n/a.", "none.",
})


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        check=True, capture_output=True, text=True).stdout


def _changed_paths(repo, sha):
    """(status, path) pairs for a commit, as git reports them.

    For a merge commit this is the COMBINED diff — only paths that differ
    from every parent — which is the same property check-release-notes.py
    relies on: an ordinary merge lists nothing, and a value written into a
    conflict resolution is listed and therefore evaluated.
    """
    out = _git(repo, "show", "--name-status", "--format=", sha)
    pairs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        pairs.append((parts[0], parts[-1]))
    return pairs


def _recipe_at(repo, sha, path):
    """(release, version) of `path` at `sha`; either may be None."""
    try:
        text = _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None, None
    rm = RELEASE_RE.search(text or "")
    vm = VERSION_RE.search(text or "")
    return (int(rm.group(2)) if rm else None,
            vm.group(1).strip() if vm else None)


def commit_triggers(repo, sha):
    """Why this commit needs a changelog entry — [] when it does not."""
    reasons = []
    for status, path in _changed_paths(repo, sha):
        if not PKG_YML_RE.match(path):
            continue
        pkg = path.split("/")[2]
        if status.startswith("A"):
            reasons.append(f"adds package {pkg}")
            continue
        if status.startswith("D"):
            reasons.append(f"removes package {pkg}")
            continue
        _new_rel, new_ver = _recipe_at(repo, sha, path)
        _old_rel, old_ver = _recipe_at(repo, f"{sha}^", path)
        if new_ver and old_ver and new_ver != old_ver:
            reasons.append(f"moves {pkg} {old_ver} -> {new_ver}")
    return reasons


def exemption_reason(body):
    """The trailer's reason if the commit carries a valid one.

    Returns (found, reason, problem). `found` says the trailer is present at
    all, so a malformed one is reported as malformed rather than silently
    treated as absent — an exemption that does not exempt must say so.
    """
    m = TRAILER_RE.search(body or "")
    if not m:
        return False, None, None
    value = m.group(1).strip()
    if not value:
        return True, None, "the trailer carries no reason at all"
    if value.casefold() in NON_REASONS:
        return True, None, f"{value!r} is a placeholder, not a reason"
    if len(value) < MIN_REASON_CHARS:
        return True, None, (
            f"{value!r} is {len(value)} characters; a reason needs at least "
            f"{MIN_REASON_CHARS}")
    if " " not in value:
        return True, None, f"{value!r} is one word; a reason is a sentence"
    return True, value, None


def check_range(repo, base, head):
    """Violations across base..head. Empty list = clean."""
    shas = _git(repo, "rev-list", "--reverse", f"{base}..{head}").split()
    if not shas:
        return []

    changelog_touched = any(
        path == CHANGELOG_PATH
        for sha in shas
        for _status, path in _changed_paths(repo, sha)
    )

    violations = []
    for sha in shas:
        body = _git(repo, "log", "-1", "--format=%B", sha)
        if NO_GATE_RE.search(body):
            continue
        triggers = commit_triggers(repo, sha)
        if not triggers:
            continue
        found, _reason, problem = exemption_reason(body)
        if found and problem is None:
            continue
        if found and problem:
            violations.append(
                f"{sha[:9]} ({'; '.join(triggers)}): carries a "
                f"Changelog-Exempt trailer but {problem}")
            continue
        if changelog_touched:
            continue
        violations.append(
            f"{sha[:9]} ({'; '.join(triggers)}): the push changes the "
            f"shipped package set and no commit in it touches "
            f"{CHANGELOG_PATH}")
    return violations


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    args = ap.parse_args()
    try:
        violations = check_range(args.repo, args.base, args.head)
    except subprocess.CalledProcessError as e:
        # Fail closed: a gate that cannot read its range must never pass it.
        print("changelog accumulation gate: FAIL — could not read the range")
        print(f"  {e}")
        return 1
    if violations:
        print("changelog accumulation gate: FAIL")
        for v in violations:
            print(f"  {v}")
        print(f"  Add the entry to {CHANGELOG_PATH} in this push, or put")
        print("  `Changelog-Exempt: <why this does not belong in a")
        print("  user-facing changelog>` in the commit message.")
        return 1
    print("changelog accumulation gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
