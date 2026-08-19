#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Detector: a remediation message must not quote the wording it removes.

PROPOSAL STAGE. This script is wired into no enforced gate. It is authored,
calibrated and shipped so the decision to arm it can be taken against evidence
rather than against an argument. The recommended home, once that decision is
taken, is check-public-content.py's --commit-msgs path, which already reads a
commit range and already prints repository-relative findings.

THE DEFECT IT DESCRIBES. A change that replaces disallowed wording in the public
tree has to be described by a commit message. The natural way to describe it is
to name what came out. That returns the wording to the public record, in the one
artefact a later edit cannot revise: a file can be corrected by the next commit,
a message only by rewriting history. The file ends up clean and the log does
not, which is worse than not sweeping at all, because it reads as finished.

WHY IT NEEDS BOTH HALVES. Naming a term is not the defect; naming a term THIS
CHANGE REMOVED is. The detector fires only when the diff removes an occurrence
of the term — its count falls between the removed and the added lines — AND the
message contains it. A message discussing a term the change never touched is
ordinary writing. A change that removes a term silently is the correct outcome
and produces no finding. A change that ADDS one is a different defect with a
different gate, and this detector deliberately stays quiet about it.

WHERE THE TERMS COME FROM, AND WHY THIS FILE NAMES NONE. The terms are read
from the same private list scripts/check-public-language.py already uses — same
override flag, same environment variable, same default path, loaded through that
module's own parser so the two can never drift apart. Shipping a second list in
the public tree would put every term into the tree this gate exists to keep
clean, which is the exact defect being detected, one level up. It would also
create a second place to maintain. The list is absent from the repository by
design, so this script fails closed when it cannot be read: a missing list is
never a pass.

EXIT CODES
  0  no finding (or nothing in range)
  1  at least one message names wording the same change removed
  2  refused: bad range, or the term list could not be read
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent


def _language_gate():
    """Load check-public-language.py as a module: one term list, one parser.

    It is a hyphenated filename, so it cannot be imported by name; a path load
    is the only option and is why this indirection exists.
    """
    spec = importlib.util.spec_from_file_location(
        "igos_public_language", _SCRIPTS / "check-public-language.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def commits_in_range(rng: str, repo: Path):
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--reverse", rng],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None, out.stderr.strip()
    return [c for c in out.stdout.split() if c], None


def _message(repo: Path, sha: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B", sha],
                          capture_output=True, text=True).stdout


def removed_and_added(repo: Path, sha: str, term: str) -> tuple[int, int]:
    """Occurrences of `term` on the commit's removed and added lines.

    Counted per OCCURRENCE, not per line: a line that carried the term twice and
    now carries it once is still a removal, and a line-level test would call
    that no change at all.
    """
    diff = subprocess.run(
        ["git", "-C", str(repo), "show", "--format=", "--unified=0", sha],
        capture_output=True, text=True).stdout
    low = term.lower()
    removed = added = 0
    for line in diff.split("\n"):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed += line[1:].lower().count(low)
        elif line.startswith("+"):
            added += line[1:].lower().count(low)
    return removed, added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--range", dest="rng",
                    help="git range OLD..NEW whose commits are checked")
    ap.add_argument("--message", action="append", default=[],
                    help="check a bare message instead of a range; pair with "
                         "--removed-term to state what the change removes, so a "
                         "message can be checked before the commit exists")
    ap.add_argument("--removed-term", action="append", default=[],
                    help="a term the --message change removes (repeatable)")
    ap.add_argument("--denylist", dest="denylist",
                    help="override the private term-list path")
    ap.add_argument("--repo", default=".", help="repository root")
    args = ap.parse_args()

    if bool(args.rng) == bool(args.message):
        print("FATAL: pass exactly one of --range or --message", file=sys.stderr)
        return 2

    lang = _language_gate()
    list_path = lang.resolve_list_path(args.denylist)
    try:
        terms = lang.load_terms(list_path)
    except lang.ListUnavailable as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        print("  This detector fails closed: an unreadable term list is not a pass.",
              file=sys.stderr)
        return 2

    findings = []

    if args.message:
        stated = {t.lower() for t in args.removed_term}
        for idx, msg in enumerate(args.message, 1):
            low = msg.lower()
            for term in terms:
                if term.lower() in low and term.lower() in stated:
                    findings.append((f"message {idx}", term))
    else:
        repo = Path(args.repo).resolve()
        shas, err = commits_in_range(args.rng, repo)
        if shas is None:
            print(f"FATAL: cannot read range {args.rng}: {err}", file=sys.stderr)
            return 2
        if not shas:
            print(f"[remediation-quoting] nothing in range {args.rng}")
            return 0
        for sha in shas:
            msg = _message(repo, sha).lower()
            for term in terms:
                if term.lower() not in msg:
                    continue
                removed, added = removed_and_added(repo, sha, term)
                if removed > added:
                    findings.append((sha[:12], term))
        print(f"[remediation-quoting] {len(shas)} commit(s) in {args.rng}; "
              f"{len(terms)} term(s) from the private list")

    if not findings:
        print("[remediation-quoting] PASS — no message names wording its own change removed.")
        return 0

    # The term itself is NOT printed: this output is read in build logs and
    # pasted into reports, and reproducing the wording there is the same defect
    # the detector exists to stop. The commit and the count locate it precisely
    # enough for the author, who is holding the message.
    print(f"[remediation-quoting] FAIL — {len(findings)} finding(s):")
    for subject, _term in findings:
        print(f"  {subject}: this change removes a listed term and its message names it")
    print("[remediation-quoting] Disposition: describe the change by what the text")
    print("  now says, not by what it used to say — the file records the fix, and")
    print("  the message does not need to republish the defect.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
