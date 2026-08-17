#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""scripts/add-spdx-headers-to-packages.py — K21.D extension: SPDX header sweep
across packages/*/build.sh.

Sibling to `scripts/add-spdx-headers.py` (K21.D canonical tool). Splits out for
two reasons:

  1. K21.D scope was decided as pkm/installer/intergen/igos-build/
     scripts/.github-workflows; packages/ was explicitly excluded. The
     packages/*/build.sh layer is a separate authorized extension
     scope (USA-1 S-W1 surfaced gap; authorization received 2026-05-21).

  2. K21.D format inserts the 2-line SPDX header directly above the
     pre-existing content. The packages/*/build.sh sweep targets the
     ff33e611 canonical format, which inserts a `#` separator line between
     the SPDX header and the pre-existing comment block. Matches the format
     decided during the USA-1 walkthrough.

Target scope:

    packages/{toolchain,core,base,desktop,extra,ai}/*/build.sh

Header format (verbatim, including the `#` separator):

    #!/bin/bash
    # SPDX-License-Identifier: GPL-3.0-or-later
    # Copyright (C) 2015-2016, 2026 InterGenJLU
    #
    <existing comment block / rest of file>

Behavior:

  - Walks each tier subdirectory for `*/build.sh` (one level deep — per-package
    build.sh, not nested helper scripts).
  - For each file:
    - If `SPDX-License-Identifier:` appears in the first 5 lines of the file
      -> skip (idempotent on re-run; tight window avoids false positives
      from later in-content mentions, e.g. liburing/build.sh:16 which is a
      comment ABOUT upstream liburing's own SPDX headers, not its own header).
    - If file does NOT start with a shebang -> error and skip (build.sh files
      are bash scripts that need to be executable; missing shebang is a
      pre-existing bug that should surface, not be silently masked).
    - Else -> insert 2 SPDX lines + 1 `#` separator line directly after the
      shebang, shifting original content down by 3 lines.
  - Preserves file mode bits (executable permissions are critical for build.sh).
  - Reports per-file action (with --verbose) + aggregate counts.

Usage:

    scripts/add-spdx-headers-to-packages.py             # apply (must run from repo root)
    scripts/add-spdx-headers-to-packages.py --dry-run   # preview without writing
    scripts/add-spdx-headers-to-packages.py --verbose   # per-file action lines
"""

import os
import sys
from pathlib import Path

TIERS = ("toolchain", "core", "base", "desktop", "extra", "ai", "compute")

SPDX_LINE = "# SPDX-License-Identifier: GPL-3.0-or-later"
COPYRIGHT_LINE = "# Copyright (C) 2015-2016, 2026 InterGenJLU"
SEPARATOR_LINE = "#"
SPDX_MARKER = "SPDX-License-Identifier:"
SPDX_HEAD_WINDOW = 5  # lines from top of file to consider for "already present"


def enumerate_corpus(repo_root: Path) -> list[Path]:
    """Find all packages/<tier>/*/build.sh files."""
    files: list[Path] = []
    for tier in TIERS:
        tier_dir = repo_root / "packages" / tier
        if not tier_dir.exists():
            continue
        # One level deep: packages/<tier>/<pkg>/build.sh
        for pkg_dir in sorted(tier_dir.iterdir()):
            if not pkg_dir.is_dir():
                continue
            build_sh = pkg_dir / "build.sh"
            if build_sh.is_file():
                files.append(build_sh)
    return sorted(set(files))


def has_spdx_header(content: str) -> bool:
    """True if SPDX-License-Identifier appears in the first SPDX_HEAD_WINDOW lines.

    Tight head-window check avoids false positives like liburing/build.sh:16
    where SPDX-License-Identifier appears in an in-content comment ABOUT
    upstream's own SPDX headers, not as this file's own header.
    """
    head = content.splitlines()[:SPDX_HEAD_WINDOW]
    return any(SPDX_MARKER in line for line in head)


def insert_header(content: str) -> tuple[str, str]:
    """Insert canonical 4-line block (2 SPDX + 1 separator) after shebang.

    Returns (new_content, action) where action is 'added' or 'no-shebang-error'.
    """
    if not content:
        return content, "no-shebang-error"
    lines = content.splitlines(keepends=True)
    first = lines[0]
    if not first.startswith("#!"):
        return content, "no-shebang-error"
    # Insert after shebang
    header = SPDX_LINE + "\n" + COPYRIGHT_LINE + "\n" + SEPARATOR_LINE + "\n"
    new_content = first + header + "".join(lines[1:])
    return new_content, "added"


def process_file(path: Path, dry_run: bool) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "skipped-binary-or-encoding"
    if has_spdx_header(content):
        return "skipped-already-present"
    new_content, action = insert_header(content)
    if action != "added":
        return action
    if not dry_run:
        # Preserve mode bits explicitly (write_text would preserve, but be defensive)
        mode = path.stat().st_mode
        path.write_text(new_content, encoding="utf-8")
        os.chmod(path, mode)
    return "added"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    verbose = "--verbose" in argv or "-v" in argv

    repo_root = Path.cwd()
    if not (repo_root / "packages").exists():
        sys.stderr.write(
            f"error: packages/ not found under {repo_root}; run from repo root.\n"
        )
        return 2

    files = enumerate_corpus(repo_root)
    if not files:
        sys.stderr.write("error: no packages/<tier>/*/build.sh files found.\n")
        return 2

    counts: dict[str, int] = {}
    errors: list[Path] = []
    for f in files:
        action = process_file(f, dry_run=dry_run)
        counts[action] = counts.get(action, 0) + 1
        if action == "no-shebang-error":
            errors.append(f)
        if verbose:
            print(f"{action:30s} {f.relative_to(repo_root)}")

    mode = "DRY-RUN (no writes)" if dry_run else "APPLIED"
    print(f"K21.D extension SPDX header sweep -- {mode}")
    print(f"  scope: packages/{{{','.join(TIERS)}}}/*/build.sh")
    print(f"  total files scanned: {len(files)}")
    for action, n in sorted(counts.items()):
        print(f"  {action}: {n}")
    if errors:
        print("\n  files needing manual inspection (no shebang on line 1):")
        for e in errors:
            print(f"    {e.relative_to(repo_root)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
