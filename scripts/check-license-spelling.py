#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed gate: the project spells its license word one way in its own voice.

WHAT THIS IS FOR
----------------
This project writes for people who read what it says and check it against their
own machine. Two spellings of the same word across the tree, the commit history
and the pages it ships is a small thing that reads as carelessness in exactly
the surfaces where carelessness is expensive: a security document, a release
note, a package description. The project's own voice uses the American spelling,
noun and verb.

The word also appears in names this project does NOT own — an upstream file
called LICENCE, a Python package-metadata directory called licence-files, a
bundled data file from SPDX, a vendored archive. Those are identifiers. Changing
one breaks a match against somebody else's bytes, so every one of them is
exempted HERE, BY NAME, with the reason beside it — never by a pattern that
would also swallow a sentence somebody writes next year. An exemption that
cannot name the file and the exact text it covers is not an exemption; it is a
hole.

WHAT IT SCANS
-------------
Everything this project says in its own voice, which is three surfaces:
  * the tree's text — every tracked file git reports as text, which includes
    the pages the tree ships (documentation, the site content, the recipe
    descriptions that reach a package manager's output);
  * the commit messages in a push range, when one is given, because a commit
    message is published the moment the branch is;
  * nothing else. A file git calls binary is skipped and named in the report.

FAIL-CLOSED
-----------
A file that cannot be read is a failure, not a skip. An exemption whose named
file no longer exists is a failure too: a stale exemption silently widens into
nothing, and the next person cannot tell a rule that is doing its job from one
that has quietly stopped.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# The spelling this project does not use in its own voice. Case-insensitive,
# whole word, so "Licence", "LICENCE" and "licences" are all hits, and a longer
# word that merely contains the letters is not.
BRITISH = re.compile(r"\blicenc(e|es|ed|ing)\b", re.IGNORECASE)


class Exemption:
    """One named piece of somebody else's text, and why it may keep its spelling.

    `path` is exact and relative to the repository root. `marker` is the exact
    text on the line that makes the line theirs rather than ours — an upstream
    file name, a metadata directory name, a quoted line. A line in that file is
    exempt only when it contains that marker, so a new sentence in the same file
    is still gated.

    `whole_file` names a file whose every line is somebody else's (a bundled
    data file); it still has to exist.
    """

    def __init__(self, name, path, reason, marker=None, whole_file=False):
        self.name = name
        self.path = path
        self.reason = reason
        self.marker = marker
        self.whole_file = whole_file

    def covers(self, path, line):
        if path != self.path:
            return False
        if self.whole_file:
            return True
        return self.marker in line


EXEMPTIONS = [
    Exemption(
        name="sof-firmware-upstream-licence-files",
        path="packages/core/sof-firmware/build.sh",
        marker="LICENCE.Intel",
        reason="the file names inside Intel's own firmware tarball; the recipe "
               "installs them by name and a corrected spelling installs nothing"),
    Exemption(
        name="python-metadata-licence-files-directory",
        path="igos-build/license_bundle.py",
        marker="licence-files",
        reason="a directory name Python packaging itself writes into wheels; "
               "this set is matched against real directories on disk"),
    Exemption(
        name="upstream-licence-file-name-pattern",
        path="igos-build/license_bundle.py",
        marker="LICENCE",
        reason="the alternation and the comment above it match upstream files "
               "literally called LICENCE, and the comment says which"),
    Exemption(
        name="package-extract-licence-file-names",
        path="scripts/pkg-functions.sh",
        marker="LICENCE",
        reason="the find expression matches upstream files called LICENCE, and "
               "the comment that documents it lists the same names"),
    Exemption(
        name="wxwidgets-upstream-licence-txt",
        path="packages/extra/wxwidgets/package.yml",
        marker="docs/licence.txt",
        reason="the path of the license file inside the pinned wxWidgets "
               "tarball, quoted so a reader can find it there"),
    Exemption(
        name="linux-firmware-own-licence-file",
        path="docs/research/packaging/nvidia_driver_open_packaging_2026-04-20.md",
        marker="its own LICENCE",
        reason="quotes the name of the file linux-firmware ships its terms in"),
    Exemption(
        name="firmware-licence-glob-in-the-policy",
        path="docs/governance/license-policy.md",
        marker="/lib/firmware/LICENCE.*",
        reason="names the files linux-firmware installs on an installed system, "
               "so a reader can list them; the path is theirs, not ours"),
    Exemption(
        name="this-gate-and-its-own-data",
        path="scripts/check-license-spelling.py",
        reason="this file IS the list of other people's names plus the pattern "
               "that finds them, so the spelling it refuses has to appear in it; "
               "the canary test beside it counts those lines, so a NEW one has "
               "to be recorded deliberately rather than slipped in",
        whole_file=True),
    Exemption(
        name="this-gates-test-fixtures",
        path="tests/preflight/test_license_spelling_gate.py",
        reason="its fixtures are this gate's inputs: the upstream names it must "
               "accept and the sentences it must refuse, which cannot be "
               "written in the spelling the gate accepts without testing "
               "nothing; the same canary test counts these lines too",
        whole_file=True),
    Exemption(
        name="spdx-bundled-list-data",
        path="config/spdx-license-list.json",
        reason="SPDX's own published data, bundled verbatim so the identifier "
               "gate can validate offline; its bytes are not ours to edit",
        whole_file=True),
]

# Named because a reader should not have to guess why a vendored archive never
# appears in this gate's report. git reports it as binary and the scan skips
# binaries, and this is the one whose contents are known to carry the spelling.
KNOWN_BINARY_HOLDERS = [
    ("vendored-extension-archive",
     "assets/theming/extensions/AlphabeticalAppGrid@stuarthayhurst.zip",
     "a vendored upstream extension archive, shipped byte-for-byte"),
]


def git(args, root):
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise SystemExit(f"[license-spelling] git {' '.join(args)} failed: "
                         f"{proc.stderr.strip()}")
    return proc.stdout


def tracked_text_files(root):
    """Every tracked file git does not call binary, and the binaries it does."""
    names = [n for n in git(["ls-files", "-z"], root).split("\0") if n]
    text, binary = [], []
    for name in names:
        path = root / name
        if not path.is_file():
            continue
        try:
            chunk = path.open("rb").read(8192)
        except OSError as e:
            raise SystemExit(f"[license-spelling] cannot read {name}: {e}")
        if b"\0" in chunk:
            binary.append(name)
        else:
            text.append(name)
    return text, binary


def scan_tree(root):
    findings = []
    text, binary = tracked_text_files(root)
    for name in text:
        try:
            content = (root / name).read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as e:
            raise SystemExit(f"[license-spelling] cannot read {name}: {e}")
        for number, line in enumerate(content.splitlines(), start=1):
            if not BRITISH.search(line):
                continue
            if any(x.covers(name, line) for x in EXEMPTIONS):
                continue
            findings.append((name, number, line.strip()))
    return findings, binary


def scan_commit_messages(rng, root):
    out = git(["log", "--format=%H%x00%B%x00%x00", rng], root)
    findings = []
    for record in out.split("\0\0"):
        if not record.strip():
            continue
        sha, _, body = record.partition("\0")
        for number, line in enumerate(body.splitlines(), start=1):
            if BRITISH.search(line):
                findings.append((f"commit {sha.strip()[:12]}", number,
                                 line.strip()))
    return findings


def check_exemptions_are_live(root):
    """A stale exemption is a failure: it means the rule stopped covering what
    it says it covers, and nothing would ever say so."""
    dead = []
    for x in EXEMPTIONS:
        path = root / x.path
        if not path.exists():
            dead.append((x.name, x.path, "the named file is gone"))
            continue
        if x.whole_file:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if x.marker not in text:
            dead.append((x.name, x.path,
                         f"the named text {x.marker!r} is no longer in it"))
    for name, path, _reason in KNOWN_BINARY_HOLDERS:
        if not (root / path).exists():
            dead.append((name, path, "the named file is gone"))
    return dead


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="repository root to scan")
    ap.add_argument("--range", dest="rng",
                    help="also scan the commit messages in this range")
    ap.add_argument("--list-exemptions", action="store_true",
                    help="print the named exemptions and their reasons")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    if args.list_exemptions:
        for x in EXEMPTIONS:
            scope = "whole file" if x.whole_file else f"lines containing {x.marker!r}"
            print(f"{x.name}: {x.path} ({scope})\n    {x.reason}")
        for name, path, reason in KNOWN_BINARY_HOLDERS:
            print(f"{name}: {path} (binary, not scanned)\n    {reason}")
        return 0

    dead = check_exemptions_are_live(root)
    if dead:
        print("[license-spelling] SETUP ERROR — an exemption no longer covers "
              "anything:")
        for name, path, why in dead:
            print(f"  {name}: {path} — {why}")
        print("  An exemption is a statement about somebody else's bytes. When "
              "those bytes move, the statement is re-made deliberately or "
              "dropped; it is never left to rot.")
        return 2

    findings, binary = scan_tree(root)
    if args.rng:
        findings += scan_commit_messages(args.rng, root)

    if not findings:
        print(f"[license-spelling] OK — the project's own voice spells it one "
              f"way. {len(EXEMPTIONS)} named exemptions, "
              f"{len(binary)} binary files not scanned.")
        return 0

    print("[license-spelling] REFUSED — the British spelling in this project's "
          "own voice:")
    for where, number, line in findings:
        print(f"  {where}:{number}: {line[:160]}")
    print("\n  This project writes 'license', noun and verb. If the word here "
          "is part of a name somebody else owns — an upstream file, a metadata "
          "directory, bundled data — add it to EXEMPTIONS in "
          "scripts/check-license-spelling.py by name, with the reason.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
