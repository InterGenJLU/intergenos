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
CHANGE a `release:` value or its inline comment and never fires on
untouched chains. Every new bump must carry its note in the same commit;
every changed line must retain its previous note chain as a byte-exact
suffix of the inline comment. Older labels and text stay on that line.

Per-commit evaluation: for every commit in the range whose body carries no
`NO-GATE:` override, every packages/*/*/package.yml whose `release:` value
changed must carry, on that same line, an inline comment whose chain HEAD
label is `r<new-release>:`. A brand-new package at release 1 is exempt
(nothing to chronicle yet). Merge commits are evaluated too, against their
combined diff — see check_range for why that closes a measured hole without
firing on ordinary merges. Comment-only edits also preserve the old chain;
NO-GATE remains the explicit override for a deliberate history correction.
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
NOTE_LABEL_RE = re.compile(r'(?<!\S)r\d+(?: \(\d+\))?:')


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



def changed_older_label(old_comment, new_comment):
    """First changed historical label, or None for an intact inline suffix.

    The comment introducer and its padding are not part of a note. Keep
    all bytes after that introducer, including separators and trailing space.
    """
    old = (old_comment or '').partition('#')[2].lstrip(' \t')
    new = (new_comment or '').partition('#')[2].lstrip(' \t')
    if not old:
        return None
    before = list(NOTE_LABEL_RE.finditer(old))
    after = list(NOTE_LABEL_RE.finditer(new))
    old_labels = [m.group() for m in before]
    new_labels = [m.group() for m in after]
    if new.endswith(old) and (not old_labels or new_labels[-len(old_labels):] == old_labels):
        return None
    if not before:
        return "unlabelled note"
    try:
        start = new_labels.index(old_labels[0])
    except ValueError:
        return old_labels[0].removesuffix(':')
    for i, match in enumerate(before):
        j = start + i
        label = match.group().removesuffix(':')
        if j >= len(after) or after[j].group() != match.group():
            return label
        old_end = before[i + 1].start() if i + 1 < len(before) else len(old)
        new_end = after[j + 1].start() if j + 1 < len(after) else len(new)
        if old[match.start():old_end] != new[after[j].start():new_end]:
            return label
    return old_labels[-1].removesuffix(':')


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
        old_rel, old_comment = parse_release_line(old_text) if old_text else (None, None)
        if new_rel == old_rel and comment == old_comment:
            continue  # value and inline comment untouched: leave legacy chains alone
        if old_rel is None and new_rel == 1:
            continue  # brand-new package at release 1: nothing to chronicle
        previous_count = len(violations)
        if new_rel != old_rel:
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
        label = changed_older_label(old_comment, comment)
        if label is not None:
            detail = (f"older notes must remain a byte-exact suffix of the release "
                      f"line's inline comment; first differing label {label}")
            if len(violations) > previous_count:
                violations[-1] += "; " + detail
            else:
                violations.append(f"{path} (commit {sha[:9]}): {detail}")
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
              " keep every older note unchanged on that line, or NO-GATE with a reason.")
        return 1
    print("release-note chain gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
