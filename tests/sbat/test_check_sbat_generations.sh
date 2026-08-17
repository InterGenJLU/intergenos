#!/usr/bin/env bash
# tests/sbat/test_check_sbat_generations.sh — exercise scripts/check-sbat-generations.sh
#
# Three test cases:
#   1. PASS — current master CSV state should pass cleanly
#   2. REJECT — manufactured grub-generation downgrade (5 → 3) should fail
#   3. REJECT — manufactured shim-vendor downgrade (1 → 0) should fail
#
# Run: bash tests/sbat/test_check_sbat_generations.sh
# Exit 0 = all 3 cases behaved as expected; exit 1 = at least one regression.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/check-sbat-generations.sh"

[ -x "$SCRIPT" ] || { echo "FAIL: $SCRIPT not found or not executable" >&2; exit 1; }

WORK=$(mktemp -d -t sbat-test-XXXXXX)
trap 'rm -rf "$WORK"' EXIT

FAIL_COUNT=0

# ---- Case 1: current master CSVs (expect exit 0) ----
echo "=== test 1: current-master CSVs (expect PASS, exit 0) ==="
if (cd "$REPO_ROOT" && bash "$SCRIPT") >/dev/null 2>&1; then
    echo "  PASS: current master CSVs pass"
else
    echo "  FAIL: current master CSVs unexpectedly rejected" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---- Case 2: manufactured grub-generation downgrade (expect exit 1) ----
echo ""
echo "=== test 2: manufactured grub-generation downgrade (expect REJECT, exit 1) ==="
cat > "$WORK/grub-downgrade.csv" <<'EOF'
sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
grub,3,Free Software Foundation,grub,2.14,https://www.gnu.org/software/grub/
grub.intergenos,1,InterGenOS Project,grub2,2.14-1.igos,https://github.com/InterGenJLU/intergenos
EOF
echo "shim.intergenos,1,InterGenOS,shim,16.1,https://github.com/InterGenJLU/intergenos" > "$WORK/shim-ok.csv"

if GRUB_SBAT="$WORK/grub-downgrade.csv" SHIM_SBAT="$WORK/shim-ok.csv" bash "$SCRIPT" >/tmp/sbat-test-out 2>&1; then
    echo "  FAIL: manufactured grub downgrade (5→3) was NOT rejected" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    if grep -q "grub generation 3 < upstream baseline 5" /tmp/sbat-test-out; then
        echo "  PASS: grub downgrade correctly rejected with expected diagnostic"
    else
        echo "  FAIL: grub downgrade rejected but diagnostic text missing" >&2
        cat /tmp/sbat-test-out >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi

# ---- Case 3: manufactured shim-vendor downgrade to 0 (expect exit 1) ----
echo ""
echo "=== test 3: manufactured shim-vendor downgrade (expect REJECT, exit 1) ==="
cat > "$WORK/grub-ok.csv" <<'EOF'
sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
grub,5,Free Software Foundation,grub,2.14,https://www.gnu.org/software/grub/
grub.intergenos,1,InterGenOS Project,grub2,2.14-1.igos,https://github.com/InterGenJLU/intergenos
EOF
echo "shim.intergenos,0,InterGenOS,shim,16.1,https://github.com/InterGenJLU/intergenos" > "$WORK/shim-downgrade.csv"

if GRUB_SBAT="$WORK/grub-ok.csv" SHIM_SBAT="$WORK/shim-downgrade.csv" bash "$SCRIPT" >/tmp/sbat-test-out 2>&1; then
    echo "  FAIL: manufactured shim-vendor downgrade (1→0) was NOT rejected" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    if grep -q "shim.intergenos generation 0 < 1" /tmp/sbat-test-out; then
        echo "  PASS: shim-vendor downgrade correctly rejected with expected diagnostic"
    else
        echo "  FAIL: shim-vendor downgrade rejected but diagnostic text missing" >&2
        cat /tmp/sbat-test-out >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi

echo ""
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "=== test summary: 3/3 PASS ==="
    exit 0
fi

echo "=== test summary: $FAIL_COUNT case(s) FAILED ===" >&2
exit 1
