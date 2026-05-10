#!/usr/bin/env bash
# installer/smoke/smoke-test.sh — InterGenOS post-install smoke-test framework.
#
# Runs INSIDE a freshly-installed InterGenOS to confirm pkm sanity,
# signing-chain validation, boot integrity, and service health. Implements
# RFC v1 phase 7 of the supersedes ship-gate sequence.
#
# Design doc reference: bus proposal 2026-05-08T18:06:40Z (ratified
# 2026-05-08T18:18:04Z). Categories, output format, and pass-fail criteria
# are codified there.
#
# Installed locations (post-install):
#   /usr/lib/intergenos/smoke-test.sh        # this file
#   /usr/lib/intergenos/lib.sh               # shared helpers
#   /usr/lib/intergenos/checks/{pkm,signing,boot,services}.sh
#   /usr/bin/intergenos-smoke-test           # symlink to smoke-test.sh
#
# Exit codes:
#   0 — all PASS or only WARN/SKIP
#   1 — at least one FAIL
#   2 — unable to run (env probe failure)

set -uo pipefail

# Self-locate so this works whether run from the in-repo path during
# development or from the installed /usr/lib/intergenos location.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: ${0##*/} [OPTIONS]

InterGenOS post-install smoke-test framework.

Options:
  -v, --verbose    Per-check diagnostic output to stderr
      --strict     Use 'pkm verify --strict' (10-15s) instead of --fast
      --json       Emit structured JSON to stdout (for CI/scripts)
  -h, --help       Show this message

Exit codes:
  0   All checks PASS or only WARN/SKIP
  1   At least one FAIL
  2   Environment probe failure (cannot run)
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
SMOKE_VERBOSE=0
SMOKE_JSON=0
SMOKE_STRICT=0
export SMOKE_VERBOSE SMOKE_JSON SMOKE_STRICT

while [ $# -gt 0 ]; do
    case "$1" in
        -v|--verbose) SMOKE_VERBOSE=1 ;;
        --strict)     SMOKE_STRICT=1 ;;
        --json)       SMOKE_JSON=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# Sanity probe — fail-fast if not on a recognizable system at all.
# Distinct exit code 2 (vs FAIL=1) so wrappers can distinguish "your
# system is unhealthy" from "this isn't even Linux".
# ---------------------------------------------------------------------------
if [ ! -d /proc/1 ] || ! command -v bash >/dev/null 2>&1; then
    echo "smoke-test: not a recognizable Linux runtime — aborting" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Source helpers + check modules
# ---------------------------------------------------------------------------
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
# shellcheck source=checks/pkm.sh
. "${SCRIPT_DIR}/checks/pkm.sh"
# shellcheck source=checks/signing.sh
. "${SCRIPT_DIR}/checks/signing.sh"
# shellcheck source=checks/boot.sh
. "${SCRIPT_DIR}/checks/boot.sh"
# shellcheck source=checks/services.sh
. "${SCRIPT_DIR}/checks/services.sh"

# ---------------------------------------------------------------------------
# Run the four categories. Each run_*_checks function emits its own
# check_pass/check_fail/check_warn/check_skip lines (captured into the
# SMOKE_RESULTS tally by lib.sh).
# ---------------------------------------------------------------------------
[ "$SMOKE_JSON" = "1" ] || echo "InterGenOS smoke-test running..."
[ "$SMOKE_JSON" = "1" ] || echo

run_pkm_checks
run_signing_checks
run_boot_checks
run_services_checks

# ---------------------------------------------------------------------------
# Tally + emit summary. summary() returns 0 if no FAIL, 1 otherwise.
# ---------------------------------------------------------------------------
if summary; then
    exit 0
else
    exit 1
fi
