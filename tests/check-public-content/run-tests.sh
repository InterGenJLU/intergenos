#!/bin/bash
# Test runner for check-public-content.py
# Asserts that should-fail fixtures produce violations and should-pass produce none.

set -euo pipefail

SCRIPT="$(dirname "$0")/../../scripts/check-public-content.py"
SHOULD_FAIL_DIR="$(dirname "$0")/should-fail"
SHOULD_PASS_DIR="$(dirname "$0")/should-pass"

PASS=0
FAIL=0

echo "=== Testing should-fail fixtures ==="
for fixture in "$SHOULD_FAIL_DIR"/*; do
    if [ ! -f "$fixture" ]; then
        continue
    fi
    basename="$(basename "$fixture")"
    if python3 "$SCRIPT" --file "$fixture" --require-fail > /dev/null 2>&1; then
        echo "  PASS: $basename (violations detected as expected)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $basename (expected violations, got clean)"
        python3 "$SCRIPT" --file "$fixture" 2>&1 || true
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== Testing should-pass fixtures ==="
for fixture in "$SHOULD_PASS_DIR"/*; do
    if [ ! -f "$fixture" ]; then
        continue
    fi
    basename="$(basename "$fixture")"
    if python3 "$SCRIPT" --file "$fixture" --require-clean > /dev/null 2>&1; then
        echo "  PASS: $basename (clean as expected)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $basename (expected clean, got violations)"
        python3 "$SCRIPT" --file "$fixture" 2>&1 || true
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
