#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""scripts/add-spdx-headers.py — K21.D SPDX header sweep tool.

Idempotent authoring tool that inserts a 2-line SPDX header into all
InterGenOS-authored source files in the decided scope.

Scope (per K21.D dispatch 2026-05-21):

  pkm/                       (*.py, *.sh)
  installer/                 (*.py, *.sh)
  intergen/                  (*.py, *.sh)
  igos-build/                (*.py, *.sh)
  scripts/                   (*.py, *.sh; recursive)
  .github/workflows/         (*.yml)

Explicitly excluded: packages/, docs/, build/, tests/ at repo root.

Header (verbatim):

    # SPDX-License-Identifier: GPL-3.0-or-later
    # Copyright (C) 2015-2016, 2026 InterGenJLU

Comment style: `# ` prefix works for all three extensions (.py, .sh, .yml).

Behavior:

  - Walks scope dirs recursively for files matching extension.
  - For each file:
    - If `SPDX-License-Identifier:` already present anywhere -> skip
      (idempotent on re-run).
    - If first line starts with `#!` (shebang) -> preserve as line 1,
      insert header on lines 2-3, original content shifts down by 2.
    - Else -> insert header on lines 1-2, original content shifts down.
  - Reports per-file action: added | skipped-already-present.
  - Prints summary counts at end.

Usage:

    scripts/add-spdx-headers.py             # walk repo (must run from repo root)
    scripts/add-spdx-headers.py --dry-run   # show what would change without writing
    scripts/add-spdx-headers.py --verbose   # per-file action lines
"""

import sys
from pathlib import Path

SCOPE = [
    ("pkm", (".py", ".sh")),
    ("installer", (".py", ".sh")),
    ("intergen", (".py", ".sh")),
    ("igos-build", (".py", ".sh")),
    ("scripts", (".py", ".sh")),
    (".github/workflows", (".yml",)),
]

SPDX_LINE = "# SPDX-License-Identifier: GPL-3.0-or-later"
COPYRIGHT_LINE = "# Copyright (C) 2015-2016, 2026 InterGenJLU"
SPDX_MARKER = "SPDX-License-Identifier:"


def enumerate_corpus(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for sub, exts in SCOPE:
        scope_dir = repo_root / sub
        if not scope_dir.exists():
            continue
        for ext in exts:
            files.extend(scope_dir.rglob(f"*{ext}"))
    return sorted(set(files))


def insert_header(content: str) -> str:
    if not content:
        return SPDX_LINE + "\n" + COPYRIGHT_LINE + "\n"
    lines = content.splitlines(keepends=True)
    first = lines[0]
    if first.startswith("#!"):
        return first + SPDX_LINE + "\n" + COPYRIGHT_LINE + "\n" + "".join(lines[1:])
    return SPDX_LINE + "\n" + COPYRIGHT_LINE + "\n" + content


def process_file(path: Path, dry_run: bool) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "skipped-binary-or-encoding"
    if SPDX_MARKER in content:
        return "skipped-already-present"
    new_content = insert_header(content)
    if not dry_run:
        path.write_text(new_content, encoding="utf-8")
    return "added"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    verbose = "--verbose" in argv or "-v" in argv

    repo_root = Path.cwd()
    if not any((repo_root / d).exists() for d, _ in SCOPE):
        sys.stderr.write(
            f"error: none of the K21.D scope dirs found under {repo_root}; "
            "run from the repo root.\n"
        )
        return 2

    files = enumerate_corpus(repo_root)
    counts: dict[str, int] = {}
    for f in files:
        action = process_file(f, dry_run=dry_run)
        counts[action] = counts.get(action, 0) + 1
        if verbose:
            print(f"{action:30s} {f.relative_to(repo_root)}")

    mode = "DRY-RUN (no writes)" if dry_run else "APPLIED"
    print(f"K21.D SPDX header sweep -- {mode}")
    print(f"  scope dirs: {', '.join(d for d, _ in SCOPE)}")
    print(f"  total files scanned: {len(files)}")
    for action, n in sorted(counts.items()):
        print(f"  {action}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
