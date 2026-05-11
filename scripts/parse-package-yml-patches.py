#!/usr/bin/env python3
"""
parse-package-yml-patches — stdlib-only minimal YAML parser for package.yml
patch declarations.

Replaces the PyYAML-based inline parser previously embedded in
pkg-functions.sh:apply_package_patches. PyYAML is not available inside
the chroot at the start of Ch 8 (chicken-and-egg: it's a Ch 8 package
itself), and Build #8 halted on this gap at man-pages 2026-05-11.

Scope: parses exactly the `patches:` block of a package.yml. Every other
top-level key is ignored. The schema is OUR schema (we define what
package.yml looks like), so a full YAML parser is overkill.

Output: one "file|sha256" line per declared patch, to stdout. No output
if no patches declared or patches block is empty. Exit 0 on success,
2 on parse error (matches prior contract).

Schema (handled):
    patches:
      - file: foo.patch
        sha256: <64-hex>
      - file: bar.patch
        sha256: <64-hex>

    patches: []          # empty inline list
    (no patches: key)    # also valid; no output

Conventions:
- Block-list indentation of 2 spaces (the project convention).
- Hyphen marker `- ` introduces each list entry.
- Comments (`#` after optional whitespace) ignored.
- The `file:` and `sha256:` fields per entry may appear in either order
  (in practice always file first, but the parser tolerates either).
- Single-quoted, double-quoted, or unquoted values all accepted; quotes
  are stripped.
- A new top-level key (line that starts non-whitespace with `key:`)
  ends the patches block.
"""

import sys
import re


def parse_patches(path):
    """Return list of (file, sha256) tuples from package.yml at `path`.

    Raises Exception on file read error. Returns empty list if no
    patches declared.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    patches = []
    in_block = False
    current = None  # dict-shaped {"file": ..., "sha256": ...} for the entry being built

    for raw in lines:
        line = raw.rstrip("\n")

        # Comment line (entire line) — ignore
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped == "":
            continue

        # List entry: `- key: val` (at any indent including 0).
        # Must check BEFORE top-level-key detection because YAML allows
        # `- file: ...` at column 0 (compact-list form), and that line
        # contains `:` so it would otherwise be misread as a top-level key.
        entry_match = re.match(r"^(\s*)-\s+(\S+)\s*:\s*(.*)$", line)
        if entry_match and in_block:
            # Flush previous entry if complete
            if current and current.get("file"):
                patches.append(current)
            current = {}
            key = entry_match.group(2)
            val = _strip_quotes(entry_match.group(3).strip())
            current[key] = val
            continue

        # New top-level key (no leading whitespace, no hyphen, contains `:`)?
        # Ends the patches block if we were in it.
        if line and not line[0].isspace() and not line.startswith("-") and ":" in line:
            key = line.split(":", 1)[0]
            if key == "patches":
                # Enter the patches block
                in_block = True
                current = None
                # Handle empty-list inline shorthand: `patches: []`
                value_part = line.split(":", 1)[1].strip()
                if value_part == "[]":
                    in_block = False
                continue
            else:
                # Different top-level key. If we were in patches block, flush + exit.
                if in_block:
                    if current and current.get("file"):
                        patches.append(current)
                    in_block = False
                    current = None
                continue

        # Inside the patches block?
        if not in_block:
            continue

        # Continuation key inside the current entry (indented, contains `:`):
        # e.g., `    sha256: HEX` after `  - file: NAME`.
        cont_match = re.match(r"^\s+(\S+)\s*:\s*(.*)$", line)
        if cont_match and current is not None:
            key = cont_match.group(1)
            val = _strip_quotes(cont_match.group(2).strip())
            current[key] = val
            continue

    # Flush trailing entry at EOF
    if in_block and current and current.get("file"):
        patches.append(current)

    return patches


def _strip_quotes(s):
    """Strip surrounding single or double quotes from a YAML scalar value."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: parse-package-yml-patches.py <package.yml>\n")
        return 2

    path = argv[1]
    try:
        patches = parse_patches(path)
    except FileNotFoundError:
        sys.stderr.write(f"[pkg] parse error: file not found: {path}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"[pkg] parse error: {e}\n")
        return 2

    for p in patches:
        pfile = p.get("file") or ""
        psha = p.get("sha256") or ""
        # Defensive: stringify so a malformed numeric sha becomes a string
        # that downstream 64-hex regex check will catch, rather than slipping
        # through (matches prior PyYAML-based code's defensive stringification).
        pfile = str(pfile)
        psha = str(psha)
        if pfile:
            print(f"{pfile}|{psha}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
