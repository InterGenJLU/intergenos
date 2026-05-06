#!/usr/bin/env bash
# scripts/check-sbat-generations.sh — validate SBAT generations against upstream baselines.
#
# Background: SBAT (Secure Boot Advanced Targeting) gates loading of signed
# bootloader/kernel components on per-component generation numbers stored in
# UEFI variables and embedded in each binary's `.sbat` section. Microsoft
# updates the SBAT revocation policy by bumping baseline generation numbers
# upstream; distros must keep their vendor entries at-or-above those baselines.
#
# A vendor SBAT generation that DROPS below the upstream baseline is a
# Tails-6.5-class footgun: it would allow loading a binary that the upstream
# revocation has marked vulnerable. We block that class of regression at
# build time by enforcing the baselines below.
#
# Wired into:
#   - scripts/build-grub-standalone.sh (early; before module-set baking)
#   - scripts/sign-release.sh (early; before signing UKI/GRUB binaries)
#
# Doc reference: docs/shim-review-submission.md Q14 — describes this enforcement.
#
# Baselines (verified 2026-05-06; UPDATE when bumping upstream pins):
#   sbat:  1  (per SBAT.md spec; rhboot/shim's SBAT format version)
#   shim:  4  (per rhboot/shim 16.1 data/sbat.csv first generation line)
#   grub:  5  (per GNU GRUB 2.14 baked-in baseline)
#
# Vendor extensions (`*.intergenos`) have a floor of 1 (must be a positive
# integer); they have no upstream baseline because they are vendor-namespaced.

set -euo pipefail

# Hardcoded baselines per the comment block above. Update on upstream version bumps.
declare -A BASELINES=(
    [sbat]=1
    [shim]=4
    [grub]=5
)

# CSV file locations (overridable via env for testing)
GRUB_SBAT="${GRUB_SBAT:-packages/core/grub/sbat.csv}"
SHIM_SBAT="${SHIM_SBAT:-docker/shim-build/sbat/sbat.intergenos.csv}"

FAIL_COUNT=0
PASS_COUNT=0

# Validate one CSV file. Iterates lines, extracts (component, generation), and
# compares against either the upstream baseline (if component is known) or the
# vendor-extension floor of 1.
#
# CSV format per SBAT spec: one component per line, comma-separated:
#   <component>,<generation>,<vendor-name>,<package-name>,<version>,<url>
# Only the first two fields are load-bearing for this check.
check_csv() {
    local file="$1"

    if [ ! -f "$file" ]; then
        echo "FAIL: $file not found" >&2
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return
    fi

    local lineno=0
    while IFS=, read -r component generation rest; do
        lineno=$((lineno + 1))

        # Strip whitespace from the two load-bearing fields
        component="${component// /}"
        generation="${generation// /}"

        # Skip blank lines + comment lines
        [ -z "$component" ] && continue
        [[ "$component" == \#* ]] && continue

        # Generation must be a non-negative integer
        if ! [[ "$generation" =~ ^[0-9]+$ ]]; then
            echo "FAIL: $file:$lineno: $component generation '$generation' is not a non-negative integer" >&2
            FAIL_COUNT=$((FAIL_COUNT + 1))
            continue
        fi

        # Check against upstream baseline if component is known; else treat as vendor
        if [ -n "${BASELINES[$component]:-}" ]; then
            local baseline="${BASELINES[$component]}"
            if [ "$generation" -lt "$baseline" ]; then
                echo "FAIL: $file:$lineno: $component generation $generation < upstream baseline $baseline" >&2
                FAIL_COUNT=$((FAIL_COUNT + 1))
            else
                echo "PASS: $file:$lineno: $component,$generation (upstream baseline $baseline)"
                PASS_COUNT=$((PASS_COUNT + 1))
            fi
        else
            # Vendor extension — floor 1
            if [ "$generation" -lt 1 ]; then
                echo "FAIL: $file:$lineno: vendor entry $component generation $generation < 1" >&2
                FAIL_COUNT=$((FAIL_COUNT + 1))
            else
                echo "PASS: $file:$lineno: $component,$generation (vendor extension)"
                PASS_COUNT=$((PASS_COUNT + 1))
            fi
        fi
    done < "$file"
}

echo "[check-sbat] running SBAT generation precheck"
echo "[check-sbat] grub CSV: $GRUB_SBAT"
check_csv "$GRUB_SBAT"
echo "[check-sbat] shim CSV: $SHIM_SBAT"
check_csv "$SHIM_SBAT"

echo ""
echo "[check-sbat] summary: $PASS_COUNT entries PASS, $FAIL_COUNT entries FAIL"

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "[check-sbat] BLOCK: $FAIL_COUNT generation regression(s) detected — refusing to proceed" >&2
    echo "[check-sbat] Update the failing CSV(s) to bring generations at-or-above the documented" >&2
    echo "[check-sbat] baselines (see header comments). If an upstream baseline truly changed," >&2
    echo "[check-sbat] update the BASELINES array AND amend the comment block above." >&2
    exit 1
fi

echo "[check-sbat] PASS: all SBAT entries at-or-above baselines"
exit 0
