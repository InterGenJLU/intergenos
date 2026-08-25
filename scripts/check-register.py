#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Push-time writing-register gate — two tiers over the lines a push adds.

Decided 2026-08-16: public-bound prose is held to a plain engineering
register. This gate scans ONLY the text a push introduces — the added lines
of the push range that sit in prose zones (documentation files, code
comments, Python docstrings) plus the range's non-merge commit messages —
and classifies matches into two tiers:

  BLOCK  — machine-assistant self-narration phrasing. High-precision by
           measurement (2026-08-16 density run: every tree hit of this class
           was either quoted transcript evidence or the detector that hunts
           the class, both covered by the exclusion file). A hit refuses the
           push.
  WARN   — the broad advisory classes from the same measurement run
           (promotional adjectives, filler phrasing, narration-style
           comments, emphasis ceremony, emoji). Printed for the author's
           judgment; never refuses on its own.

Range-scoped by design: text already in the tree was measured, dispositioned,
and where kept, labeled (decided 2026-08-16) — a per-push gate guards new
text, it does not re-litigate history.

Joined across a wrap (decided 2026-08-25): the added prose-zone lines of a file
are scanned in runs of CONSECUTIVE lines, each stripped of its leading and
trailing whitespace and joined by a single space, and each
commit message is scanned as one run, so a tier pattern spelled as two or more
words is matched when a wrap falls between its words. The line reported is the
one the match STARTS on, and a match that begins and ends inside one line
reports as it always did. Lines outside the prose zone are dropped before the
run is formed, so the drop breaks the run instead of joining two prose lines
that have code between them.

The tier patterns live below in code zones, written as ADJACENT SPLIT string
literals (the established scanner-file discipline): this gate reads prose
zones only, so it never flags its own table — and the split spelling keeps
any sibling byte-level scanner from finding an intact vocabulary term in
this file either (measured live on this file's first push attempt). Keep
pattern vocabulary out of THIS docstring and out of comments in this file —
those ARE prose zones.

Exclusions: a committed, public-safe path-prefix file (--exclusions). Files
under an excluded prefix are skipped in both tiers. The file must exist —
a missing exclusion file is a loud operational failure, never a silent
wider-or-narrower scan.

House marks: the documented terminal-output marks (check / cross / bare
warning sign, per the logging design) are NOT matched; the warning sign
counts only in its emoji presentation (with variation selector).

Exit codes: 0 = clean or advisory-only · 1 = BLOCK-tier hit ·
2 = operational failure (bad range, missing exclusion file, git error).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# The join lives in one module so this gate and the public-language gate can
# never disagree about what "the same run of added lines" means. sys.path[0] is
# this script's own directory when it runs as a script; the insert keeps the
# import working when a test loads this file by path instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from joined_lines import JoinedText, consecutive_runs  # noqa: E402

DOC_EXT = {".md", ".txt", ".rst"}
CODE_EXT = {".py", ".sh", ".c", ".h", ".cpp", ".hpp", ".rs", ".js", ".css",
            ".yml", ".yaml", ".service", ".conf"}

COMMENT_RX = {
    ".py": r"^\s*#", ".sh": r"^\s*#", ".yml": r"^\s*#", ".yaml": r"^\s*#",
    ".service": r"^\s*#", ".conf": r"^\s*#",
    ".c": r"^\s*(//|\*|/\*)", ".h": r"^\s*(//|\*|/\*)",
    ".cpp": r"^\s*(//|\*|/\*)", ".hpp": r"^\s*(//|\*|/\*)",
    ".rs": r"^\s*//", ".js": r"^\s*(//|\*|/\*)", ".css": r"^\s*(/\*|\*)",
}

_SELF_NARRATION = [
    "we'?ve " + "success" "fully",
    "success" "fully" + r" (created|" + "imple" "mented" + "|added|" + "comp" "leted" + ")",
    "you can" " now",
    "as an" " ai",
    r"i'?ve (created|added|" + "imple" "mented" + ")",
]

_HYPE = [
    "ro" "bust", "compre" "hensive", "seam" "less(?:ly)?", "power" "ful",
    "ele" "gant(?:ly)?", "blaz" "ing(?:ly)?", "cutting" "-edge",
    "state-of" "-the-art", "world" "-class", "best-in" "-class",
    "revolu" "tionary", "effort" "less(?:ly)?", "beauti" "fully",
    "grace" "fully", "super" "charge(?:d|s)?", "battle" "-tested",
    "produc" "tion-ready", "delight" "ful",
]

_FILLER = [
    r"it'?s " + "worth" " noting", "it is " + "worth" " noting",
    "import" "antly,", "essen" "tially,", "basic" "ally,",
    "please" " note", "keep in" " mind", "as you" " can see",
    "needless" " to say", "of " "course,", r"let'?s" " now",
    "as we" " can see", "going " "forward,",
]

_NARRATION = [
    r"this (function|method|class|module|script) " + "(is respon" "sible for)",
    "the follow" "ing code", "here" " we", "respon" "sible for handling",
]

_EMPHASIS = [
    "!{2,}", r"\bIMPOR" "TANT!", r"\bNO" "TE!", r"\bperf" "ect!",
    r"\bgre" "at!", r"\bawe" "some\b",
]

BLOCK_CLASSES = {
    "ai-self-narration": re.compile(
        r"\b(" + "|".join(_SELF_NARRATION) + r")\b", re.I),
}

WARN_CLASSES = {
    "hype-adjective": re.compile(r"\b(" + "|".join(_HYPE) + r")\b", re.I),
    # Comma-terminated entries cannot take a trailing word boundary (comma
    # then space has no boundary transition — a defect inherited from the
    # measurement instrument, caught by this gate's own pinned checks); a
    # not-a-word-character lookahead terminates both comma- and word-ended
    # entries correctly.
    "filler-phrase": re.compile(r"\b(" + "|".join(_FILLER) + r")(?=\W|$)", re.I),
    "narration-comment": re.compile(r"\b(" + "|".join(_NARRATION) + r")\b", re.I),
    "emphasis-ceremony": re.compile("(" + "|".join(_EMPHASIS) + ")", re.I),
    # Emoji presentation only. The bare warning sign (U+26A0 without U+FE0F)
    # and the check/cross marks are the documented terminal-output design and
    # are deliberately not matched.
    "emoji": re.compile(
        "[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F✨✅❌⭐]"
        "|⚠️"),
}


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def load_exclusions(path: Path) -> list[str]:
    if not path.is_file():
        print(f"[register] OPERATIONAL FAILURE: exclusion file not found: {path}")
        print("[register] the gate refuses to run with an undefined exclusion set.")
        sys.exit(2)
    prefixes = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            prefixes.append(line.rstrip("/"))
    return prefixes


def excluded(relpath: str, prefixes: list[str]) -> bool:
    return any(relpath == p or relpath.startswith(p + "/") for p in prefixes)


def prose_line_numbers(text: str, ext: str) -> set[int]:
    """Line numbers (1-based) of this file's prose zones."""
    lines = text.splitlines()
    if ext in DOC_EXT:
        return set(range(1, len(lines) + 1))
    crx = re.compile(COMMENT_RX[ext]) if ext in COMMENT_RX else None
    zone: set[int] = set()
    in_docstring = False
    for i, line in enumerate(lines, 1):
        if ext == ".py":
            flips = line.count('"""') + line.count("'''")
            if in_docstring:
                zone.add(i)
            elif flips:
                zone.add(i)
            if flips % 2 == 1:
                in_docstring = not in_docstring
            if i in zone:
                continue
        if crx and crx.match(line):
            zone.add(i)
    return zone


def added_lines(rng: str, cwd: Path) -> dict[str, list[tuple[int, str]]]:
    """Map changed-file relpath -> [(new-file line number, line text), ...]."""
    out: dict[str, list[tuple[int, str]]] = {}
    diff = _git(["diff", "-U0", "--no-color", rng, "--"], cwd)
    current = None
    new_ln = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            out.setdefault(current, [])
        elif line.startswith("+++ /dev/null"):
            current = None
        elif line.startswith("@@") and current is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            new_ln = int(m.group(1)) if m else 0
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            out[current].append((new_ln, line[1:]))
            new_ln += 1
    return out


def run_hits(run, classes):
    """[(lineno, line_text, class_name)] for each class match in one run.

    `run` is [(lineno, text), ...] with consecutive line numbers. The lines are
    joined by one space and each tier pattern is matched against the JOINED
    text, so a pattern spelled as two or more words is found even when the
    author's editor wrapped the line between them. The line reported is THE ONE
    THE MATCH STARTS ON, which is where the author has to look.

    At most one report per (starting line, class), which is what per-line
    scanning produced before this changed: a line that hit a class once was
    reported once. A match that begins and ends inside a single line therefore
    yields exactly what it yielded before, and only a match crossing a join is
    new.
    """
    joined = JoinedText(run)
    found = []
    for name, rx in classes.items():
        for m in rx.finditer(joined.text):
            lineno, text = joined.locate(m.start())
            found.append((m.start(), lineno, text, name))
    found.sort(key=lambda f: (f[0], f[3]))
    out = []
    seen = set()
    for _offset, lineno, text, name in found:
        if (lineno, name) in seen:
            continue
        seen.add((lineno, name))
        out.append((lineno, text, name))
    return out


def scan(rng: str, cwd: Path, prefixes: list[str]) -> tuple[int, int]:
    tip = rng.split("..")[-1]
    blocks = warns = 0
    for relpath, additions in added_lines(rng, cwd).items():
        ext = Path(relpath).suffix.lower()
        if ext not in DOC_EXT and ext not in CODE_EXT:
            continue
        if excluded(relpath, prefixes):
            continue
        try:
            blob = _git(["show", f"{tip}:{relpath}"], cwd)
        except RuntimeError:
            continue
        zone = prose_line_numbers(blob, ext)
        # Lines outside the prose zone are dropped BEFORE the run is formed, so
        # the drop shows up as a break in the line numbers instead of joining
        # two prose lines that have code between them. A blank line is dropped
        # the same way: a paragraph break is not a line wrap.
        in_zone = [(ln, text) for ln, text in additions
                   if ln in zone and text.strip()]
        for run in consecutive_runs(in_zone):
            for ln, text, name in run_hits(run, BLOCK_CLASSES):
                print(f"[register] BLOCK[{name}] {relpath}:{ln}: {text.strip()[:160]}")
                blocks += 1
            for ln, text, name in run_hits(run, WARN_CLASSES):
                print(f"[register] WARN[{name}] {relpath}:{ln}: {text.strip()[:160]}")
                warns += 1
    # Non-merge commit messages the range introduces.
    try:
        raw = _git(["log", "--no-merges", "--format=%H%x00%B%x01", rng], cwd)
    except RuntimeError as exc:
        print(f"[register] OPERATIONAL FAILURE reading commit messages: {exc}")
        sys.exit(2)
    for entry in raw.split("\x01"):
        entry = entry.strip()
        if not entry:
            continue
        sha, _, body = entry.partition("\x00")
        # A commit message wraps for the same reason a paragraph does, so the
        # message is scanned as one run of its own lines — its blank lines
        # dropped, which breaks the run between the subject and the body and
        # between paragraphs.
        msg_lines = [(i, t) for i, t in enumerate(body.splitlines(), 1) if t.strip()]
        for run in consecutive_runs(msg_lines):
            for _ln, text, name in run_hits(run, BLOCK_CLASSES):
                print(f"[register] BLOCK[{name}] commit {sha[:12]}: {text.strip()[:160]}")
                blocks += 1
            for _ln, text, name in run_hits(run, WARN_CLASSES):
                print(f"[register] WARN[{name}] commit {sha[:12]}: {text.strip()[:160]}")
                warns += 1
    return blocks, warns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--range", required=True, help="git range OLD..NEW to scan")
    ap.add_argument("--exclusions", default="config/register-gate-exclusions.txt",
                    help="committed path-prefix exclusion file (must exist)")
    ap.add_argument("--repo", default=".", help="repository to operate in")
    args = ap.parse_args()
    cwd = Path(args.repo).resolve()
    prefixes = load_exclusions(cwd / args.exclusions)
    try:
        blocks, warns = scan(args.range, cwd, prefixes)
    except RuntimeError as exc:
        print(f"[register] OPERATIONAL FAILURE: {exc}")
        return 2
    if blocks:
        print(f"[register] BLOCK: {blocks} hit(s) in the tier that refuses. "
              f"Rewrite the flagged text to a plain engineering register.")
        return 1
    if warns:
        print(f"[register] advisory: {warns} WARN-tier hit(s) above — author's judgment call.")
    else:
        print("[register] PASS: no register hits in the push range.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
