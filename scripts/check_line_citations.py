#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check_line_citations.py — implementation backing
# check-line-citations.sh. See that wrapper's header for context.
"""Line-citation validator (S-D 5; extended per the 2026-08-16 decision).

The 2026-08-16 audit proved the v1 scope vacuous against the corpus it was
built for: the default sweep pointed at a directory that does not exist,
only markdown-LINK citations were recognized while the security documents
write backticked plain text, kernel .config fragments — the most-cited
files — were outside the extension list, and a citation 7,000 lines wrong
inside a 9,000-line file passed because only bounds were checked. The gate
printed green having validated zero citations.

Extended scope, each part decided 2026-08-16:
  * BOTH citation forms: markdown links `[file:N](href)` AND backticked
    plain text (a path with a recognized extension, a colon, a line
    number, inside backticks).
  * `.config` joins the extension list.
  * SEMANTIC anchor check: when the citing line also carries a backticked
    symbol (an identifier, optionally `=value`), the cited line range must
    CONTAIN that identifier — otherwise the citation points somewhere
    unrelated and is reported as SEMANTIC-MISMATCH even when in bounds.
    A citation with no symbol on its line keeps the bounds-only check.
  * A run that validated ZERO citations never prints the green all-clear;
    it says plainly that nothing was checked.

REPO_ROOT may be overridden with IGOS_CITE_REPO_ROOT (test harnesses run
the scanner against throwaway trees). The hooks never set it; pointing it
elsewhere is the same effect class as --no-verify, which every committer
already holds.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOS_CITE_REPO_ROOT")
                 or Path(__file__).resolve().parents[1])

_EXTS = (r"py|sh|md|yml|yaml|c|h|cpp|hpp|conf|toml|json|txt|in|rs|go|js|ts|tsx"
         r"|html|css|service|preset|policy|target|rules|nft|nix|pl|rb|config")

# Markdown link with citation-shaped text:
#   [some/path/file.ext:42](relative/path#L42)
#   [some/path/file.ext:42-51](relative/path#L42-L51)
# The path inside [] is the SoT for the citation; the href is treated
# as a navigation helper.
CITE_RE = re.compile(
    r"\[([^\]\s]+?\.(?:" + _EXTS + r")):(\d+)(?:-(\d+))?\]\([^)]*\)"
)

# Backticked plain-text citation: `some/path/file.ext:42` or `...:42-51`.
# The shim-review submission and the security documents cite this way.
BACKTICK_CITE_RE = re.compile(
    r"`([^`\s]+?\.(?:" + _EXTS + r")):(\d+)(?:-(\d+))?`"
)

# A backticked symbol on the citing line: an identifier of >=4 chars,
# optionally carrying an =value tail (`CONFIG_MODULE_SIG=y`). The character
# class excludes dots and slashes, so paths never qualify as symbols.
ANCHOR_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})(?:=[^`]*)?`")

# Cache the basename → list-of-paths map for bare-filename resolution.
_BASENAME_INDEX: dict[str, list[Path]] | None = None


def _build_basename_index() -> dict[str, list[Path]]:
    """Walk repo tree once and bucket every file by basename."""
    index: dict[str, list[Path]] = {}
    # Single path components excluded anywhere in a path.
    exclude_parts = {".git", "build", "node_modules", "__pycache__", ".venv"}
    # Repo-relative subtree prefixes. These need their own check: a
    # multi-component string can never equal a single path component, so
    # listing "docs/lfs-13.0" in the parts set silently excluded nothing.
    exclude_prefixes = ("docs/lfs-13.0",)
    for path in REPO_ROOT.rglob("*"):
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            continue
        parts = rel.parts
        if any(p in exclude_parts for p in parts):
            continue
        rel_posix = rel.as_posix()
        if any(rel_posix == pfx or rel_posix.startswith(pfx + "/")
               for pfx in exclude_prefixes):
            continue
        if not path.is_file():
            continue
        index.setdefault(path.name, []).append(rel)
    return index


def _basename_index() -> dict[str, list[Path]]:
    global _BASENAME_INDEX
    if _BASENAME_INDEX is None:
        _BASENAME_INDEX = _build_basename_index()
    return _BASENAME_INDEX


def _line_count(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return -1


def _read_lines(path: Path, start: int, end: int) -> str:
    """The cited line range's text, joined (1-based, inclusive)."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[start - 1:end])


def resolve_citation(cited: str, doc_path: Path) -> tuple[str, Path | None, list[Path]]:
    """Resolve a cited file reference. Returns (status, path, candidates)."""
    cited_clean = cited.lstrip("/")
    candidate = REPO_ROOT / cited_clean
    if candidate.exists() and candidate.is_file():
        return ("OK", candidate, [])
    alt = (doc_path.parent / cited_clean).resolve()
    if alt.exists() and alt.is_file() and alt.is_relative_to(REPO_ROOT):
        return ("OK", alt, [])
    name = Path(cited_clean).name
    if name == cited_clean:
        matches = _basename_index().get(name, [])
        if len(matches) == 1:
            return ("OK", REPO_ROOT / matches[0], [])
        if len(matches) > 1:
            return ("AMBIGUOUS", None, matches)
    return ("MISSING", None, [])


def _iter_citations(line: str):
    """Yield (cited_file, start, end, span) for BOTH citation forms."""
    for m in CITE_RE.finditer(line):
        yield (m.group(1), int(m.group(2)),
               int(m.group(3)) if m.group(3) else int(m.group(2)), m.span())
    for m in BACKTICK_CITE_RE.finditer(line):
        yield (m.group(1), int(m.group(2)),
               int(m.group(3)) if m.group(3) else int(m.group(2)), m.span())


def _line_anchors(line: str) -> list[str]:
    """Backticked symbol identifiers on the citing line that are not
    themselves citations."""
    cite_spans = [span for *_ignored, span in _iter_citations(line)]
    anchors = []
    for m in ANCHOR_RE.finditer(line):
        if any(s <= m.start() and m.end() <= e for s, e in cite_spans):
            continue
        anchors.append(m.group(1))
    return anchors


def _check_one(label: str, cited_file: str, start: int, end: int,
               citing_line: str, doc_path: Path) -> tuple[int, str | None]:
    """Validate one citation. Returns (ok_increment, violation_or_None)."""
    if end < start:
        return 0, f"  {label}: INVERTED RANGE — {cited_file}:{start}-{end}"
    status, path, candidates = resolve_citation(cited_file, doc_path)
    line_repr = f"{start}" if end == start else f"{start}-{end}"
    if status == "MISSING":
        return 0, f"  {label}: MISSING — {cited_file}:{line_repr} (no matching file in tree)"
    if status == "AMBIGUOUS":
        candidate_str = ", ".join(str(c) for c in candidates[:3])
        extra = f" (+{len(candidates) - 3} more)" if len(candidates) > 3 else ""
        return 0, f"  {label}: AMBIGUOUS — {cited_file}:{line_repr} matches: {candidate_str}{extra}"
    assert path is not None
    total = _line_count(path)
    if total < 0:
        return 0, f"  {label}: UNREADABLE — {cited_file}:{line_repr}"
    if start < 1 or end < 1 or start > total or end > total:
        return 0, f"  {label}: OUT-OF-BOUNDS — {cited_file}:{line_repr} (file has {total} lines)"
    anchors = _line_anchors(citing_line)
    if anchors:
        segment = _read_lines(path, start, end)
        if not any(a in segment for a in anchors):
            shown = ", ".join(anchors[:3])
            return 0, (f"  {label}: SEMANTIC-MISMATCH — {cited_file}:{line_repr} "
                       f"does not contain the cited symbol(s): {shown}")
    return 1, None


def scan_doc(doc_path: Path) -> tuple[int, list[str]]:
    """Scan one doc line by line. Returns (ok_count, violations)."""
    text = doc_path.read_text(errors="replace")
    ok = 0
    violations: list[str] = []
    seen: set[tuple[str, int, int]] = set()
    for line in text.splitlines():
        for cited_file, start, end, _span in _iter_citations(line):
            key = (cited_file, start, end)
            if key in seen:
                continue
            seen.add(key)
            inc, vio = _check_one(str(doc_path), cited_file, start, end, line, doc_path)
            ok += inc
            if vio:
                violations.append(vio)
    return ok, violations


def scan_diff_added_lines() -> tuple[int, list[str]]:
    """Diff-only mode: validate citations in lines newly added by the
    staged diff. Pre-existing citations are ignored — only NEW ones must
    be valid, so commits are never blocked by baseline drift.
    """
    import subprocess

    # errors="replace" is load-bearing, not defensive decoration. A tracked
    # markdown file can carry bytes that are not valid UTF-8 — the generated
    # THIRD-PARTY-NOTICES.md carried 180 stray cp1252 bytes — and a strict
    # decode raised UnicodeDecodeError out of this call, reporting a citation
    # failure for a check that never ran. A byte that is not valid UTF-8
    # cannot be part of a citation, which is all this function reads.
    proc = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--", "*.md"],
        capture_output=True, text=True, errors="replace", cwd=REPO_ROOT,
    )
    if proc.returncode != 0:
        return 0, [f"  git diff failed: {proc.stderr.strip()}"]

    ok = 0
    violations: list[str] = []
    current_file: Path | None = None
    seen: set[tuple[Path, str, int, int]] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_file = Path(line[6:])
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if not line.startswith("+") or current_file is None:
            continue
        added = line[1:]
        for cited_file, start, end, _span in _iter_citations(added):
            key = (current_file, cited_file, start, end)
            if key in seen:
                continue
            seen.add(key)
            inc, vio = _check_one(str(current_file), cited_file, start, end,
                                  added, current_file)
            ok += inc
            if vio:
                violations.append(vio)
    return ok, violations


def _report_zero() -> None:
    print("[check-line-citations] validated ZERO citations — nothing was "
          "checked (the scanned set carries no recognizable citations).")


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--diff-only":
        ok, vios = scan_diff_added_lines()
        for v in vios:
            print(v)
        print()
        print(f"[check-line-citations] (diff-only) new citations OK: {ok}")
        if vios:
            print(f"\033[31m[check-line-citations] new citations DRIFTED: {len(vios)}\033[0m")
            print("\033[33mFix the cited line numbers (or paths) and re-stage.\033[0m")
            return 1
        if ok == 0:
            _report_zero()
            return 0
        print("\033[32m[check-line-citations] all newly-added citations resolve cleanly\033[0m")
        return 0

    files = [Path(p) for p in argv[1:] if Path(p).is_file()]
    if not files:
        print("[check-line-citations] no markdown files matched")
        _report_zero()
        return 0

    total_ok = 0
    total_bad = 0
    files_scanned = 0
    for doc in files:
        files_scanned += 1
        ok, vios = scan_doc(doc)
        total_ok += ok
        total_bad += len(vios)
        for v in vios:
            print(v)

    print()
    print(f"[check-line-citations] {files_scanned} file(s) scanned")
    print(f"[check-line-citations] citations OK: {total_ok}")
    if total_bad > 0:
        print(f"\033[31m[check-line-citations] citations DRIFTED: {total_bad}\033[0m")
        print("\033[33mFix the cited line numbers (or the file paths) and re-run.\033[0m")
        return 1
    if total_ok == 0:
        _report_zero()
        return 0
    print("\033[32m[check-line-citations] all citations resolve cleanly\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
