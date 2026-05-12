#!/bin/bash
# dry-run-first-publish.sh — Safe rehearsal for post-Build #9 first publish
#
# Runs the full sign + emit + index chain using a TEST GPG key (clearly
# named TESTING-DO-NOT-TRUST) against a candidate set of .igos.tar.gz
# archives. Stages everything to a local dry-run-output/ dir. NO rsync,
# NO origin push, never touches pkm/release-keys.json or the real
# signing keys.
#
# Usage:
#   scripts/dry-run-first-publish.sh --archives-dir /path/to/archives/
#   scripts/dry-run-first-publish.sh --synthetic  # create test fixture
#
# Existing tools called (read-only; never modified):
#   scripts/emit-package-archives.py
#   scripts/generate-repodb.py
#   pkm/repo.py
set -e -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="dry-run-output"
TEST_KEY_NAME="TESTING-DO-NOT-TRUST-First-Publish-$(date +%Y%m%d)"
ARCHIVES_DIR=""
SYNTHETIC=false
PASS_COUNT=0
FAIL_COUNT=0

usage() {
    echo "Usage: $0 --archives-dir DIR | --synthetic"
    echo "  --archives-dir DIR   Path to .igos.tar.gz archives"
    echo "  --synthetic           Create synthetic test fixture"
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --archives-dir) ARCHIVES_DIR="$2"; shift 2 ;;
        --synthetic)    SYNTHETIC=true; shift ;;
        -h|--help)      usage ;;
        *) echo "Unknown: $1"; usage ;;
    esac
done

if [ -z "$ARCHIVES_DIR" ] && ! $SYNTHETIC; then
    echo "ERROR: --archives-dir or --synthetic required" >&2
    usage
fi

report_pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
report_fail() { echo "  [FAIL] $1" >&2; FAIL_COUNT=$((FAIL_COUNT + 1)); }

rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

echo "=== Dry Run: First Publish Rehearsal ==="
echo "Output dir: $OUTPUT_DIR"
echo ""

# Step 1: Setup test GPG keyring
echo "[1/6] Setting up test GPG keyring..."
TEST_GPG_HOME="$OUTPUT_DIR/gpg-home"
rm -rf "$TEST_GPG_HOME"
mkdir -p "$TEST_GPG_HOME"
chmod 700 "$TEST_GPG_HOME"

# Generate throwaway test key (batch mode, no passphrase, no pinentry)
gpg --homedir "$TEST_GPG_HOME" --batch --yes --pinentry-mode loopback \
    --passphrase '' --quick-gen-key "$TEST_KEY_NAME" default default never 2>&1 || {
    report_fail "Failed to generate test GPG key"
    exit 1
}

TEST_FP=$(gpg --homedir "$TEST_GPG_HOME" --list-keys --with-colons "$TEST_KEY_NAME" | grep '^fpr:' | head -1 | cut -d: -f10)

if [ -z "$TEST_FP" ]; then
    report_fail "Failed to generate test GPG key"
    exit 1
fi
report_pass "Test GPG key created: $TEST_FP"

# Step 2: Generate synthetic archives if requested
if $SYNTHETIC; then
    echo "[2/6] Creating synthetic test fixture..."
    OUTPUT_ABS="$(cd "$OUTPUT_DIR" && pwd)"
    SYNTH_DIR="$OUTPUT_ABS/synthetic-archives"
    mkdir -p "$SYNTH_DIR"

    # Create 2 synthetic .igos.tar.gz files with valid .PKGINFO
    for data in "testpkg:1.0:Test Package A" "testpkg2:2.0:Test Package B"; do
        IFS=':' read -r pkg ver desc <<< "$data"
        archive="$SYNTH_DIR/${pkg}-${ver}-1.igos.tar.gz"
        TMPDIR="$(mktemp -d)"
        (
            cd "$TMPDIR"
            echo "pkgname = $pkg" > .PKGINFO
            echo "pkgver = ${ver}-1" >> .PKGINFO
            echo "pkgdesc = $desc" >> .PKGINFO
            echo "tier = core" >> .PKGINFO
            echo "license = MIT" >> .PKGINFO
            echo "builddate = 2026-05-12T00:00:00Z" >> .PKGINFO
            echo "size = 1000" >> .PKGINFO
            tar -czf "$archive" .PKGINFO
        )
        rm -rf "$TMPDIR"
    done
    ARCHIVES_DIR="$SYNTH_DIR"
    report_pass "Created synthetic archives in $SYNTH_DIR"
else
    echo "[2/6] Using provided archives: $ARCHIVES_DIR"
    report_pass "Archives directory exists"
fi

# Verify archives exist
ARCHIVE_COUNT=$(ls "$ARCHIVES_DIR"/*.igos.tar.gz 2>/dev/null | wc -l)
if [ "$ARCHIVE_COUNT" -eq 0 ]; then
    report_fail "No .igos.tar.gz files in $ARCHIVES_DIR"
    exit 1
fi
echo "  Archives found: $ARCHIVE_COUNT"

# Step 3: Generate InterGenOS.db index
echo "[3/6] Generating InterGenOS.db..."
INDEX_OUTPUT="$OUTPUT_DIR/InterGenOS.db"

python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from pkm.repo import generate_index
path = generate_index('$ARCHIVES_DIR', arch='x86_64', output='$INDEX_OUTPUT')
print(f'Index written: {path}')
" 2>&1

if [ -f "$INDEX_OUTPUT" ] && [ "$(stat -c%s "$INDEX_OUTPUT")" -gt 0 ]; then
    report_pass "InterGenOS.db generated ($(stat -c%s "$INDEX_OUTPUT") bytes)"
else
    report_fail "InterGenOS.db not generated"
    exit 1
fi

# Step 4: Sign the index with test key
echo "[4/6] Signing InterGenOS.db with test key..."
SIG_OUTPUT="$OUTPUT_DIR/InterGenOS.db.sig"

gpg --homedir "$TEST_GPG_HOME" --batch --yes --pinentry-mode loopback \
    --passphrase '' --detach-sign --armor \
    --local-user "$TEST_FP" \
    --output "$SIG_OUTPUT" \
    "$INDEX_OUTPUT" 2>&1 || {
    report_fail "Signing failed"
    exit 1
}

if [ -f "$SIG_OUTPUT" ] && [ "$(stat -c%s "$SIG_OUTPUT")" -gt 0 ]; then
    report_pass "InterGenOS.db.sig created ($(stat -c%s "$SIG_OUTPUT") bytes)"
else
    report_fail "Signing failed"
    exit 1
fi

# Step 5: Verify signature roundtrip
echo "[5/6] Verifying signature roundtrip..."
if gpg --homedir "$TEST_GPG_HOME" --batch --yes --verify "$SIG_OUTPUT" "$INDEX_OUTPUT" 2>&1 | grep -q "Good signature"; then
    report_pass "GPG signature verified"
else
    report_fail "GPG signature verification failed"
fi

# Step 6: Roundtrip parse via pkm/repo.py
echo "[6/6] Roundtrip parse with pkm/repo.py..."
python3 -c "
import gzip, json, sys
sys.path.insert(0, '$PROJECT_ROOT')
from pkm.repo import RepoIndex

with gzip.open('$INDEX_OUTPUT', 'rt') as f:
    data = json.load(f)

index = RepoIndex('test', 'https://example.com/x86_64', data)
assert index.version == 1, f'Expected version 1, got {index.version}'
assert index.arch == 'x86_64', f'Expected arch x86_64, got {index.arch}'
assert index.package_count == $ARCHIVE_COUNT, f'Expected $ARCHIVE_COUNT packages, got {index.package_count}'
for name, pkg in index.packages.items():
    assert 'sha256' in pkg, f'{name}: missing sha256'
    assert 'filename' in pkg, f'{name}: missing filename'
    assert 'size' in pkg, f'{name}: missing size'
    print(f'  {name}: sha256={pkg[\"sha256\"][:16]}... size={pkg[\"size\"]} filename={pkg[\"filename\"]}')
print('OK')
" 2>&1

if [ $? -eq 0 ]; then
    report_pass "pkm.repo.RepoIndex roundtrip parse OK"
else
    report_fail "pkm.repo.RepoIndex roundtrip parse failed"
fi

# Summary
echo ""
echo "=== Dry Run Report ==="
echo "Total: $((PASS_COUNT + FAIL_COUNT)) checks, $PASS_COUNT pass, $FAIL_COUNT fail"
echo "Output: $OUTPUT_DIR/"
echo "  InterGenOS.db     — gzipped JSON index"
echo "  InterGenOS.db.sig — GPG detached signature"
echo "  gpg-home/         — test keyring ($TEST_KEY_NAME)"
echo ""

# Write summary file
cat > "$OUTPUT_DIR/dry-run-report.md" << REPORTEOF
# Dry Run Report — First Publish Rehearsal
Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
Test key: $TEST_FP
Archives: $ARCHIVE_COUNT from $ARCHIVES_DIR

## Results
- **Checks:** $((PASS_COUNT + FAIL_COUNT))
- **Passed:** $PASS_COUNT
- **Failed:** $FAIL_COUNT

## Artifacts
- \`$OUTPUT_DIR/InterGenOS.db\` ($(stat -c%s "$INDEX_OUTPUT") bytes, gzipped JSON)
- \`$OUTPUT_DIR/InterGenOS.db.sig\` ($(stat -c%s "$SIG_OUTPUT") bytes, GPG signature)
- \`$OUTPUT_DIR/gpg-home/\` — test keyring (DO NOT use for real signing)

## Notes
- Test key is throwaway — NEVER deployed, not in pkm/release-keys.json
- No rsync performed — artifacts staged locally only
- No existing tools modified (read-only wrapper)
- InterGenOS.db format matches pkm/repo.py RepoIndex parser
- GPG signature verified with test key
REPORTEOF

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "*** DRY RUN FAILED ***" >&2
    exit 1
fi

echo "*** DRY RUN PASSED ***"
exit 0
