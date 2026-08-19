#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Test runner for scripts/check-aspirational-stubs.py.
# Asserts:
#   1. The script imports + runs cleanly (no Python errors).
#   2. The should-pass-fixture (synthetic clean tree) exits 0.
#   3. The should-fail-fixture (synthetic stub-bearing tree) exits 1.
#
# Mirrors the pattern of tests/check-public-content/run-tests.sh.

set -uo pipefail

SCRIPT="$(dirname "$0")/../../scripts/check-aspirational-stubs.py"
PASS_FIXTURE="$(dirname "$0")/should-pass-fixture"
FAIL_FIXTURE="$(dirname "$0")/should-fail-fixture"

PASS=0
FAIL=0

echo "=== Test 1: script imports cleanly ==="
if python3 -c "import ast; ast.parse(open('$SCRIPT').read())" 2>&1; then
    echo "  PASS: script parses as valid Python"
    PASS=$((PASS + 1))
else
    echo "  FAIL: script has syntax errors"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Test 2: --help runs without crashing ==="
if python3 "$SCRIPT" --help > /dev/null 2>&1; then
    echo "  PASS: --help succeeds"
    PASS=$((PASS + 1))
else
    echo "  FAIL: --help crashes"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Test 3: should-pass-fixture exits 0 ==="
if python3 "$SCRIPT" --project "$PASS_FIXTURE" --summary-only > /dev/null 2>&1; then
    echo "  PASS: clean synthetic fixture exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL: clean synthetic fixture should exit 0"
    python3 "$SCRIPT" --project "$PASS_FIXTURE" 2>&1 || true
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Test 4: should-fail-fixture exits EXACTLY 1 ==="
# Exactly 1, not merely non-zero: exit 2 means the gate refused to run, and a
# refusal reports nothing about the content. A test that accepts any non-zero
# reads a refusal as a detection — the failure this script's own exit-code
# contract exists to prevent.
python3 "$SCRIPT" --project "$FAIL_FIXTURE" --summary-only > /dev/null 2>&1
FAIL_RC=$?
if [ "$FAIL_RC" -eq 1 ]; then
    echo "  PASS: stub-bearing synthetic fixture exits 1 (findings)"
    PASS=$((PASS + 1))
else
    echo "  FAIL: expected exit 1 (findings), got $FAIL_RC"
    python3 "$SCRIPT" --project "$FAIL_FIXTURE" 2>&1 || true
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Test 5: a missing hook allowlist is refused with exit 2 ==="
python3 "$SCRIPT" --project "$PASS_FIXTURE" \
    --hook-allowlist /nonexistent/aspirational-stub-hook-allowlist.txt \
    > /dev/null 2>&1
MISSING_RC=$?
if [ "$MISSING_RC" -eq 2 ]; then
    echo "  PASS: unreadable allowlist refuses (exit 2), never a silent pass"
    PASS=$((PASS + 1))
else
    echo "  FAIL: expected exit 2 (refused), got $MISSING_RC"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
