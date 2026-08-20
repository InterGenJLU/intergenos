#!/usr/bin/env python3
"""Fail-closed gate: a release bump must move its note-chain head with it.

`bump-changed-releases.py` owns the `release:` NUMBER; the human-readable
note chain in the same line's inline comment (`# rNN: ...`) is authored by
hand. Nothing enforced the two moving together, so machine bumps shipped
with stale chain heads — found 2026-07-30 at real cost: intergen sat at
release 130 with its chain head reading r109 (20 releases undocumented),
forge likewise, plus an 87-package no-note class.

This gate makes the class structurally impossible going FORWARD without
prejudging the legacy back-fill decision: it evaluates only commits that
CHANGE a `release:` value and never fires on untouched chains, so the
existing backlog does not block pushes — but every NEW bump must carry its
note in the same commit, exactly the discipline that keeps the chain an
honest record instead of a curated one.

Per-commit evaluation: for every commit in the range whose body carries no
`NO-GATE:` override, every packages/*/*/package.yml whose `release:` value
changed must carry, on that same line, an inline comment whose chain HEAD
label is `r<new-release>:`. A brand-new package at release 1 is exempt
(nothing to chronicle yet). Merge commits are evaluated too, against their
combined diff — see check_range for why that closes a measured hole without
firing on ordinary merges.
"""

import argparse
import re
import subprocess
import sys

# Same shape as bump-changed-releases.py's RELEASE_RE (kept in lockstep by
# tests/preflight/test_check_release_notes.py): value + optional inline
# comment on the release line.
RELEASE_RE = re.compile(
    r'^(release:[^\S\n]*)(\d+)([^\S\n]*(?:#[^\n]*)?)$', re.M)
# The chain head: the FIRST `rNN:` label inside the release line's comment.
HEAD_LABEL_RE = re.compile(r'#[^\S\n]*r(\d+)[^\S\n]*:')

PKG_YML_RE = re.compile(r'^packages/[^/]+/[^/]+/package\.yml$')


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        check=True, capture_output=True, text=True).stdout


def _show(repo, sha, path):
    """File content at sha, or None when absent there."""
    try:
        return _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def parse_release_line(text):
    """(release value, inline comment) from the first release: line, or
    (None, None) when the file has no parseable release line."""
    m = RELEASE_RE.search(text or "")
    if not m:
        return None, None
    return int(m.group(2)), m.group(3)


def check_commit(repo, sha):
    """Violations this commit introduces (empty list = clean)."""
    violations = []
    changed = _git(repo, "show", "--name-status", "--format=", sha)
    for line in changed.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if status.startswith("D") or not PKG_YML_RE.match(path):
            continue
        new_text = _show(repo, sha, path)
        if new_text is None:
            continue
        new_rel, comment = parse_release_line(new_text)
        if new_rel is None:
            continue  # no release line is preflight-tier territory, not ours
        old_text = _show(repo, f"{sha}^", path)
        old_rel, _ = parse_release_line(old_text) if old_text else (None, None)
        if new_rel == old_rel:
            continue  # release untouched — legacy chains never fire the gate
        if old_rel is None and new_rel == 1:
            continue  # brand-new package at release 1: nothing to chronicle
        head = HEAD_LABEL_RE.search(comment or "")
        if head is None:
            violations.append(
                f"{path} (commit {sha[:9]}): release moved "
                f"{old_rel}->{new_rel} but the line carries no `# r{new_rel}:` "
                f"note — the chain head must ride the same commit")
        elif int(head.group(1)) != new_rel:
            violations.append(
                f"{path} (commit {sha[:9]}): release moved "
                f"{old_rel}->{new_rel} but the chain head still reads "
                f"r{head.group(1)} — write the r{new_rel} note at the head")
    return violations


def check_range(repo, base, head):
    """Violations across base..head, honoring per-commit NO-GATE overrides.

    MERGE COMMITS ARE EVALUATED (they were excluded until 2026-08-16). The
    excluded shape was real and measured: a `release:` value written into a
    merge commit's CONFLICT RESOLUTION is present in neither parent, so no
    non-merge commit in the range carries it and the gate passed a bump whose
    chain head was stale. Reproduced before this change — release 7->9 resolved
    by hand with the head still reading r7 passed the gate (exit 0), while the
    byte-identical defect as a plain commit failed it (exit 1).

    No change to check_commit was needed, because git already reports the right
    thing for a merge: `git show --name-status` on a merge is a COMBINED diff,
    listing only paths that differ from ALL parents — precisely the resolution,
    and nothing the merged branch merely carried in. A clean merge's combined
    diff is empty, so ordinary merges cannot fire this gate; bumps made on a
    branch are still evaluated on their own commits, which the range already
    walks. `sha^` remains first-parent, giving the mainline's old value.
    """
    shas = _git(repo, "rev-list", "--reverse",
                f"{base}..{head}").split()
    violations = []
    for sha in shas:
        body = _git(repo, "log", "-1", "--format=%B", sha)
        if re.search(r"^NO-GATE:", body, re.M):
            continue
        violations.extend(check_commit(repo, sha))
    return violations


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    args = ap.parse_args()
    violations = check_range(args.repo, args.base, args.head)
    if violations:
        print("release-note chain gate: FAIL")
        for v in violations:
            print(f"  {v}")
        print("  A machine bump owns the number; the note is yours — add the"
              " matching `# rNN:` label at the chain head in the SAME commit,"
              " or NO-GATE with a reason.")
        return 1
    print("release-note chain gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
