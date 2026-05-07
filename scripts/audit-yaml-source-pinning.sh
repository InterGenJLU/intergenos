#!/bin/bash
# audit-yaml-source-pinning.sh — find package.yml files with un-pinned upstream sources.
#
# Reports any source: list entry that has a url: but no sha256:.
# An empty source: [] (download-helpers for proprietary software) is treated as VALID.
# build_artifacts: entries are IGNORED — they're audited at the manifest phase, not here.
#
# Output: TSV to stdout; one row per un-pinned source entry.
# Exit: 0 always; piping to wc -l gives the un-pinned count.

set -euo pipefail

REPO_ROOT="${1:-/mnt/intergenos}"
PACKAGES_DIR="$REPO_ROOT/packages"

if [[ ! -d $PACKAGES_DIR ]]; then
    echo "ERROR: $PACKAGES_DIR not found" >&2
    exit 2
fi

python3 - "$PACKAGES_DIR" <<'PYEOF'
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

packages_dir = Path(sys.argv[1])
unpinned = []
parse_errors = []
total_yamls = 0
total_source_entries = 0

for yml_path in sorted(packages_dir.glob("*/*/package.yml")):
    total_yamls += 1
    try:
        with yml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        parse_errors.append((yml_path, str(e)))
        continue

    if not isinstance(data, dict):
        parse_errors.append((yml_path, "top-level not a mapping"))
        continue

    name = data.get("name", yml_path.parent.name)
    tier = yml_path.parent.parent.name

    src = data.get("source")
    if src is None:
        # No source: key at all — treat as un-pinned for visibility
        unpinned.append((tier, name, "<missing-source-key>", str(yml_path)))
        continue

    if isinstance(src, list) and len(src) == 0:
        # Empty list — download-helper convention; valid.
        continue

    if not isinstance(src, list):
        parse_errors.append((yml_path, f"source: is {type(src).__name__}, expected list"))
        continue

    for i, item in enumerate(src):
        total_source_entries += 1
        if not isinstance(item, dict):
            unpinned.append((tier, name, f"<entry-{i}-malformed>", str(yml_path)))
            continue
        url = item.get("url", "<no-url>")
        sha = item.get("sha256")
        if not sha or not isinstance(sha, str) or len(sha) != 64:
            # 64 = sha256 hex length; reject empty/truncated/bogus
            unpinned.append((tier, name, url, str(yml_path)))

# Emit TSV
print("tier\tname\turl_or_marker\tpath")
for row in unpinned:
    print("\t".join(row))

# Stderr summary
print("", file=sys.stderr)
print(f"Total package.yml scanned:    {total_yamls}", file=sys.stderr)
print(f"Total source entries scanned: {total_source_entries}", file=sys.stderr)
print(f"Un-pinned entries:            {len(unpinned)}", file=sys.stderr)
print(f"Parse errors:                 {len(parse_errors)}", file=sys.stderr)
if parse_errors:
    print("", file=sys.stderr)
    print("Parse errors:", file=sys.stderr)
    for p, msg in parse_errors:
        print(f"  {p}: {msg}", file=sys.stderr)
PYEOF
