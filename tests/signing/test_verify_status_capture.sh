#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# test_verify_status_capture.sh — the signing script's verify step must be able
# to say why it stopped.
#
# WHAT IS BEING PROTECTED. scripts/sign-with-gpg.sh runs with the hardware token
# plugged in and the operator standing at a PIN prompt. Its verify step carries
# an error message written for exactly that moment: "gpg --verify exited
# non-zero (N). See <debug log>." Under `set -euo pipefail`, which the script
# sets at line 57, that message could never be reached. The construct was
#
#     VERIFY_STATUS="$(gpg --status-fd=1 --verify ...)"
#     VERIFY_EXIT=$?
#
# and an assignment whose value comes from a command substitution takes that
# command's exit status as its own. A failing gpg therefore ended the script on
# the assignment itself: the next line never ran, VERIFY_EXIT was never set, and
# the operator got a bare non-zero exit with no line saying which step failed or
# where to look. Surfaced from the lane-18 read of this script, 2026-08-25.
#
# HOW THESE CASES MEASURE IT. They do not paraphrase the construct — they lift
# the verify block out of the real scripts/sign-with-gpg.sh by its own anchors
# and run those lines, so a change to the script reaches this suite instead of
# leaving it measuring a copy. gpg, die, debug and ok are shell functions in the
# harness, so no real gpg runs, no GnuPG home is read or written, and no token
# is required.
#
# Output discipline: tool output goes to files under the scratch tree; this
# suite prints only its own PASS/FAIL lines.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/sign-with-gpg.sh"

PASS=0
FAIL=0
WORK=""

cleanup() { [[ -n "${WORK}" && -d "${WORK}" ]] && rm -rf "${WORK}"; }
trap cleanup EXIT

ok_case()   { PASS=$((PASS + 1)); echo "PASS: $1"; }
fail_case() { FAIL=$((FAIL + 1)); echo "FAIL: $1"; }

WORK="$(mktemp -d)"

# ---------------------------------------------------------------------------
# Lift the verify block out of the real script, by anchors it owns.
# ---------------------------------------------------------------------------
extract_verify_block() {
    # Start at whichever of the two assignments the script reaches first — the
    # status capture, or the VERIFY_EXIT the fix initialises ahead of it. Taking
    # only the VERIFY_STATUS line as the anchor would leave that initialisation
    # outside the lifted block and make the cases below fail on `set -u` for a
    # reason that has nothing to do with what they measure.
    awk '
        !inblock && /VERIFY_EXIT=|VERIFY_STATUS=/ { inblock = 1 }
        inblock                                   { print }
        /^[[:space:]]*ok "Sig verifies:/          { exit }
    ' "${SCRIPT}"
}

BLOCK="${WORK}/verify-block.sh"
extract_verify_block > "${BLOCK}"

if ! grep -q 'VERIFY_STATUS=' "${BLOCK}"; then
    fail_case "the verify block could not be lifted out of ${SCRIPT}"
    echo "TOTAL: ${PASS} passed, ${FAIL} failed"
    exit 1
fi
ok_case "the verify block was lifted out of the real script ($(wc -l < "${BLOCK}") lines)"

# ---------------------------------------------------------------------------
# The harness: everything the block reaches for, and nothing else.
# ---------------------------------------------------------------------------
write_harness() {
    local out="$1" gpg_rc="$2" gpg_stdout="$3"
    {
        echo 'set -euo pipefail'
        echo 'DEBUG=0'
        echo 'DEBUG_LOG=/dev/null'
        echo 'OUT_PATH=/nonexistent.asc'
        echo 'FILE_PATH=/nonexistent'
        echo 'KEY_FINGERPRINT=5597A3E0587B253006D0DD7B8C50826182083050'
        echo 'debug() { :; }'
        echo 'ok() { echo "HARNESS-OK: $*"; }'
        echo 'die() { echo "HARNESS-DIE: $*"; exit 9; }'
        printf 'gpg() { printf %%s %s; return %s; }\n' "'${gpg_stdout}'" "${gpg_rc}"
        cat "${BLOCK}"
    } > "${out}"
}

GOOD_STATUS='[GNUPG:] GOODSIG DEADBEEF Test Key
[GNUPG:] VALIDSIG AAAA 2026-08-25 0 0 0 0 0 0 5597A3E0587B253006D0DD7B8C50826182083050'

# --- case 1: a failing gpg --verify must reach the block's own message -------
H1="${WORK}/fail.sh"
write_harness "${H1}" 2 ""
OUT1="${WORK}/fail.out"
bash "${H1}" > "${OUT1}" 2>&1
RC1=$?
if grep -q "HARNESS-DIE: gpg --verify exited non-zero" "${OUT1}"; then
    ok_case "a failing gpg --verify reaches the script's own error message"
else
    fail_case "a failing gpg --verify did NOT reach the script's own error message (exit ${RC1}); the run said: $(tr '\n' ' ' < "${OUT1}")"
fi
if [[ ${RC1} -eq 9 ]]; then
    ok_case "the run ends through die, not on the assignment (exit 9)"
else
    fail_case "the run ended with exit ${RC1}, not through die (expected 9)"
fi

# --- case 2: the reported status is gpg's own, not something invented -------
H2="${WORK}/fail3.sh"
write_harness "${H2}" 3 ""
OUT2="${WORK}/fail3.out"
bash "${H2}" > "${OUT2}" 2>&1
if grep -q "exited non-zero (3)" "${OUT2}"; then
    ok_case "the message carries gpg's real exit status"
else
    fail_case "the message did not carry gpg's exit status 3: $(tr '\n' ' ' < "${OUT2}")"
fi

# --- case 3: control — a good verify still walks the whole block ------------
H3="${WORK}/good.sh"
write_harness "${H3}" 0 "${GOOD_STATUS}"
OUT3="${WORK}/good.out"
bash "${H3}" > "${OUT3}" 2>&1
RC3=$?
if [[ ${RC3} -eq 0 ]] && grep -q "HARNESS-OK: Sig verifies:" "${OUT3}"; then
    ok_case "a good verify still reaches the end of the block"
else
    fail_case "a good verify did not complete (exit ${RC3}): $(tr '\n' ' ' < "${OUT3}")"
fi

# --- case 4: control — a GOODSIG-less success is still refused --------------
H4="${WORK}/nogood.sh"
write_harness "${H4}" 0 "[GNUPG:] NODATA 1"
OUT4="${WORK}/nogood.out"
bash "${H4}" > "${OUT4}" 2>&1
if grep -q "HARNESS-DIE: gpg --verify did not emit a GOODSIG" "${OUT4}"; then
    ok_case "a verify that exits zero without GOODSIG is still refused"
else
    fail_case "a GOODSIG-less verify was not refused: $(tr '\n' ' ' < "${OUT4}")"
fi

# --- case 5: control — a fingerprint mismatch is still refused --------------
H5="${WORK}/mismatch.sh"
write_harness "${H5}" 0 "[GNUPG:] GOODSIG DEADBEEF Test Key
[GNUPG:] VALIDSIG AAAA 2026-08-25 0 0 0 0 0 0 0000000000000000000000000000000000000000"
OUT5="${WORK}/mismatch.out"
bash "${H5}" > "${OUT5}" 2>&1
if grep -q "HARNESS-DIE: Sig primary-key fingerprint mismatch" "${OUT5}"; then
    ok_case "a primary-key fingerprint mismatch is still refused"
else
    fail_case "a fingerprint mismatch was not refused: $(tr '\n' ' ' < "${OUT5}")"
fi

echo "TOTAL: ${PASS} passed, ${FAIL} failed"
[[ ${FAIL} -eq 0 ]]
