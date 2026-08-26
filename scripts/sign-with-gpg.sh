#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# sign-with-gpg.sh — produce a detached OpenPGP signature over an arbitrary
# file using the operator's hardware-rooted master signing key. Pure
# signing primitive: produces a .asc sidecar file and verifies it.
#
# Phases:
#   0. Pre-flight: target file present + sha256 matches expected (if given).
#   0b. Lock pre-flight: classify the lock files in the GnuPG home before any
#      daemon is touched, and refuse the shapes that would leave gpg waiting
#      with the token in the operator's hand (see lib-gnupg-lock-preflight.sh).
#   1. Daemon setup via sourced library (Class A/B detection: pcscd-based
#      vs scdaemon built-in CCID; pinentry-tty config; pubkey import + own-
#      ertrust + secret-key stub via gpg --card-status).
#   2. gpg --detach-sign --armor over the target file -> sidecar .asc file.
#   3. gpg --verify --status-fd=1 the produced sig against the primary key
#      fingerprint (matches the VALIDSIG line's last field).
#   4. Summary: print sig path + size + sha256.
#
# Usage:
#   bash sign-with-gpg.sh --file <path> [--sha256 <hex>] [--key <fpr>]
#                         [--out <path>] [--clear-stale-locks] [--dry-run]
#                         [--debug] [--help]
#
# --file <path>     REQUIRED. File to sign.
# --sha256 <hex>    Optional. Expected SHA256 of the file; pre-flight rejects
#                   if mismatched. Skip the check by omitting this arg.
# --key <fpr>       Optional. Signing-key fingerprint (40 hex chars; no
#                   spaces). Default: 5597A3E0587B253006D0DD7B8C50826182083050
#                   (project master per docs/signing-key.md).
# --out <path>      Optional. Output sig file path. Default: <file>.asc.
# --clear-stale-locks
#                   Optional. Remove lock files in the GnuPG home whose
#                   owning process is dead AND which were recorded on this
#                   same machine. Nothing else is ever removed: a lock held
#                   by a running process, and a lock naming another machine,
#                   are refused and left exactly where they are.
# --dry-run         Pre-flight + library setup-init in DRY mode. No signing.
#                   Use FIRST to validate before the real run.
# --debug           Verbose tracing to ~/tmp/sign-with-gpg.debug.log
#                   (script-side) + ~/tmp/scdaemon.log (library-side, when
#                   the library is in Class A mode).
# --help            This usage text.
#
# Pre-conditions (script refuses to run if any fail):
#   - --file points at a readable regular file.
#   - If --sha256 is given, it matches the file's sha256.
#   - No lock in the GnuPG home is held by a live process other than that
#     home's own GnuPG daemons, and none names a different machine.
#   - Nitrokey (or other smartcard) present + master signing subkey in keyring
#     (the library will fetch the pubkey + create the secret-key stub if not).
#
# Pairs with lib-gpg-card-setup.sh in the same directory.
# Companion doc: docs/signing-with-gpg.md.

set -euo pipefail

# ============================================================
# CONSTANTS + DEFAULTS
# ============================================================
DEFAULT_KEY_FINGERPRINT="5597A3E0587B253006D0DD7B8C50826182083050"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PATH="${SCRIPT_DIR}/lib-gpg-card-setup.sh"
LOCK_LIB_PATH="${SCRIPT_DIR}/lib-gnupg-lock-preflight.sh"
# The home gpg will actually use, resolved the way gpg resolves it.
GNUPG_HOME_PATH="${GNUPGHOME:-${HOME}/.gnupg}"
DEBUG_LOG="${HOME}/tmp/sign-with-gpg.debug.log"

# Per-invocation args (set during parsing)
FILE_PATH=""
EXPECTED_SHA256=""
KEY_FINGERPRINT="${DEFAULT_KEY_FINGERPRINT}"
OUT_PATH=""

# Flags
DRY_RUN=0
DEBUG=0
CLEAR_STALE_LOCKS=0

# ============================================================
# USAGE
# ============================================================
usage() {
    cat <<EOF
Usage: $(basename "$0") --file <path> [--sha256 <hex>] [--key <fpr>]
                       [--out <path>] [--clear-stale-locks] [--dry-run]
                       [--debug] [--help]

Produces a detached OpenPGP signature over an arbitrary file using the
operator's hardware-rooted master signing key.

  --file <path>     REQUIRED. File to sign.
  --sha256 <hex>    Optional. Expected SHA256 of the file (byte-fidelity
                    check before signing). Skip by omitting.
  --key <fpr>       Optional. Signing-key fingerprint (40 hex chars).
                    Default: ${DEFAULT_KEY_FINGERPRINT}
                    (project master per docs/signing-key.md)
  --out <path>      Optional. Output sig file path. Default: <file>.asc
  --clear-stale-locks
                    Remove locks in the GnuPG home whose owning process is
                    dead and which were recorded on this machine. A live
                    lock, and another machine's lock, are never removed.
  --dry-run         Pre-flight + library setup-init in DRY mode. No signing.
  --debug           Verbose tracing to ${DEBUG_LOG}.
  --help            This usage text.

Run --dry-run first to validate before the real sign.
See docs/signing-with-gpg.md for the full workflow + examples.
EOF
}

# ============================================================
# ARG PARSING
# ============================================================
while [[ $# -gt 0 ]]; do
    case "${1}" in
        --file)
            [[ $# -ge 2 ]] || { echo "Error: --file requires a path argument." >&2; exit 2; }
            FILE_PATH="${2}"; shift 2 ;;
        --sha256)
            [[ $# -ge 2 ]] || { echo "Error: --sha256 requires a hex argument." >&2; exit 2; }
            EXPECTED_SHA256="${2}"; shift 2 ;;
        --key)
            [[ $# -ge 2 ]] || { echo "Error: --key requires a fingerprint argument." >&2; exit 2; }
            KEY_FINGERPRINT="${2}"; shift 2 ;;
        --out)
            [[ $# -ge 2 ]] || { echo "Error: --out requires a path argument." >&2; exit 2; }
            OUT_PATH="${2}"; shift 2 ;;
        --clear-stale-locks) CLEAR_STALE_LOCKS=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --debug)   DEBUG=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *)
            echo "Unknown argument: ${1}" >&2
            usage >&2
            exit 2 ;;
    esac
done

# Required-arg checks
if [[ -z "${FILE_PATH}" ]]; then
    echo "Error: --file is required." >&2
    usage >&2
    exit 2
fi

# Default --out = <file>.asc
if [[ -z "${OUT_PATH}" ]]; then
    OUT_PATH="${FILE_PATH}.asc"
fi

# Fingerprint sanity (must be 40 hex chars, no spaces)
if [[ ! "${KEY_FINGERPRINT}" =~ ^[A-Fa-f0-9]{40}$ ]]; then
    echo "Error: --key value must be a 40-hex-char fingerprint with no spaces (got: '${KEY_FINGERPRINT}')." >&2
    exit 2
fi

# ============================================================
# HELPERS
# ============================================================
die()    { echo "[sign-with-gpg] error: $*" >&2; exit 1; }
info()   { echo "[sign-with-gpg] $*"; }
ok()     { echo "[sign-with-gpg OK] $*"; }
warn()   { echo "[sign-with-gpg WARN] $*" >&2; }
debug()  {
    [[ "${DEBUG}" == "1" ]] || return 0
    local msg
    msg="[sign-with-gpg DEBUG $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${DEBUG_LOG}"
}
banner() {
    echo
    echo ">>> $*"
}

# Initialize debug log if --debug
if [[ "${DEBUG}" == "1" ]]; then
    mkdir -p "$(dirname "${DEBUG_LOG}")"
    {
        echo "=== sign-with-gpg.sh debug log started $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
        echo "DRY_RUN=${DRY_RUN} DEBUG=${DEBUG}"
        echo "FILE_PATH=${FILE_PATH}"
        echo "OUT_PATH=${OUT_PATH}"
        echo "KEY_FINGERPRINT=${KEY_FINGERPRINT}"
        echo "EXPECTED_SHA256=${EXPECTED_SHA256:-<unset>}"
        echo "GNUPG_HOME_PATH=${GNUPG_HOME_PATH}"
        echo "CLEAR_STALE_LOCKS=${CLEAR_STALE_LOCKS}"
    } > "${DEBUG_LOG}"
fi

# ============================================================
# PHASE 0/5: PRE-FLIGHT
# ============================================================
banner "Phase 0/5: Pre-flight (library presence / target file / sha256)"

# Library file presence check
[[ -f "${LIB_PATH}" ]] || die "Library missing at ${LIB_PATH}. Expected sibling of this script."
ok "Library present: ${LIB_PATH}"
[[ -f "${LOCK_LIB_PATH}" ]] || die "Lock pre-flight library missing at ${LOCK_LIB_PATH}. Expected sibling of this script."
ok "Lock pre-flight library present: ${LOCK_LIB_PATH}"

# Target file presence
[[ -f "${FILE_PATH}" ]] || die "Target file does not exist or is not a regular file: ${FILE_PATH}"
[[ -r "${FILE_PATH}" ]] || die "Target file is not readable: ${FILE_PATH}"
ok "Target file present: ${FILE_PATH} ($(stat -c %s "${FILE_PATH}") bytes)"

# Optional sha256 check
if [[ -n "${EXPECTED_SHA256}" ]]; then
    ACTUAL_SHA256="$(sha256sum "${FILE_PATH}" | awk '{print $1}')"
    debug "actual_sha256=${ACTUAL_SHA256} expected_sha256=${EXPECTED_SHA256}"
    [[ "${ACTUAL_SHA256}" == "${EXPECTED_SHA256}" ]] || die "Target file sha256 mismatch. Got ${ACTUAL_SHA256}, expected ${EXPECTED_SHA256}."
    ok "Target file sha256 matches expected"
else
    debug "no --sha256 given; skipping byte-fidelity check"
fi

# ============================================================
# PHASE 0b/5: GNUPG LOCK PRE-FLIGHT
# ============================================================
# Runs BEFORE the setup library, because that library stops and restarts
# gpg-agent and scdaemon. Whatever is holding a lock should be named while it
# is still there to name, and the run should stop here rather than at a PIN
# prompt that never arrives.
banner "Phase 0b/5: GnuPG lock pre-flight (${GNUPG_HOME_PATH})"

# shellcheck disable=SC1090
source "${LOCK_LIB_PATH}"

if ! gnupg_lock_preflight "${GNUPG_HOME_PATH}" "${CLEAR_STALE_LOCKS}"; then
    die "GnuPG lock pre-flight refused. Resolve the lock(s) named above, then run again. Nothing was signed and nothing was deleted."
fi
ok "Lock pre-flight clear"

# ============================================================
# PHASE 1/5: GPG SMARTCARD SETUP (via library)
# ============================================================
banner "Phase 1/5: GPG smartcard setup (library: lib-gpg-card-setup.sh)"

debug "sourcing library from ${LIB_PATH}"

# Export library env vars from wrapper flags
export GPG_CARD_DEBUG="${DEBUG}"
export GPG_CARD_LOG="${DEBUG_LOG}"
export GPG_CARD_DRY_RUN="${DRY_RUN}"

# shellcheck disable=SC1090
source "${LIB_PATH}"

gpg_card_setup_init
gpg_card_verify_key "${KEY_FINGERPRINT}"
ok "Library setup complete. scdaemon log: $(gpg_card_logfile_path)"

# ============================================================
# PHASE 2/5: SIGN TARGET FILE
# ============================================================
banner "Phase 2/5: gpg --detach-sign --armor over target file"

if [[ "${DRY_RUN}" == "1" ]]; then
    info "[DRY-RUN] would: gpg --detach-sign --armor --local-user ${KEY_FINGERPRINT} --output ${OUT_PATH} ${FILE_PATH}"
else
    # Pre-announce the PIN entry so the operator knows exactly what to enter.
    cat <<'EOF'

  -----------------------------------------------------------
   PIN ENTRY REQUIRED
  -----------------------------------------------------------

   Enter your Nitrokey OpenPGP User PIN at the prompt below.

   Note: you'll be required to touch the Nitrokey when it
   blinks (touch over/near the shield symbol at the top of
   the key), or the signing will timeout and you'll be
   required to re-run the script.

  -----------------------------------------------------------

EOF
    # Remove any stale sig at the output path (overwrite semantics)
    if [[ -f "${OUT_PATH}" ]]; then
        info "Removing stale ${OUT_PATH} from prior run"
        rm -f "${OUT_PATH}"
    fi

    if ! gpg --detach-sign --armor \
              --local-user "${KEY_FINGERPRINT}" \
              --output "${OUT_PATH}" \
              "${FILE_PATH}" \
              2>>"${DEBUG_LOG:-/dev/null}"; then
        die "gpg --detach-sign failed. Check ${DEBUG_LOG} + $(gpg_card_logfile_path) for diagnostics."
    fi

    [[ -s "${OUT_PATH}" ]] || die "Sig file ${OUT_PATH} not produced or empty."
    ok "Sig produced: ${OUT_PATH} ($(stat -c %s "${OUT_PATH}") bytes)"
fi

# ============================================================
# PHASE 3/5: VERIFY SIG
# ============================================================
banner "Phase 3/5: gpg --verify (sig sanity check against key fingerprint)"

if [[ "${DRY_RUN}" == "1" ]]; then
    info "[DRY-RUN] would: gpg --verify --status-fd=1 ${OUT_PATH} ${FILE_PATH}"
else
    # --status-fd=1 produces machine-parsable status lines including:
    #   [GNUPG:] GOODSIG <subkey-fpr> <name>
    #   [GNUPG:] VALIDSIG <subkey-fpr> <date> <ts> ... <primary-key-fpr>
    # We need the primary-key-fpr (last field of VALIDSIG) to match the
    # --key fingerprint. Human-readable output of gpg --verify only shows
    # the subkey that did the signing, never the primary.
    # `VAR="$(cmd)"` takes cmd's exit status as its own, so under the set -e
    # at the top of this script a failing gpg ends the run ON THE ASSIGNMENT:
    # the `VERIFY_EXIT=$?` that used to sit on the next line never ran, and the
    # message below — the one that names the step and points at the debug log —
    # could not be reached. Capturing the status through `|| VERIFY_EXIT=$?`
    # keeps the failure in this script's hands.
    VERIFY_EXIT=0
    VERIFY_STATUS="$(gpg --status-fd=1 --verify "${OUT_PATH}" "${FILE_PATH}" 2>>"${DEBUG_LOG:-/dev/null}")" || VERIFY_EXIT=$?
    debug "verify status output: ${VERIFY_STATUS}"
    if [[ "${DEBUG}" == "1" ]]; then
        {
            echo "--- gpg --verify --status-fd=1 output $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
            echo "${VERIFY_STATUS}"
            echo "--- end ---"
        } >> "${DEBUG_LOG}"
    fi

    if [[ ${VERIFY_EXIT} -ne 0 ]]; then
        die "gpg --verify exited non-zero (${VERIFY_EXIT}). See ${DEBUG_LOG}."
    fi

    if ! echo "${VERIFY_STATUS}" | grep -q "^\[GNUPG:\] GOODSIG "; then
        die "gpg --verify did not emit a GOODSIG status line. See ${DEBUG_LOG}."
    fi

    PRIMARY_FPR_IN_SIG="$(echo "${VERIFY_STATUS}" | awk '/^\[GNUPG:\] VALIDSIG/ {print $NF; exit}')"
    if [[ "${PRIMARY_FPR_IN_SIG}" != "${KEY_FINGERPRINT}" ]]; then
        die "Sig primary-key fingerprint mismatch. Got '${PRIMARY_FPR_IN_SIG:-<empty>}', expected '${KEY_FINGERPRINT}'."
    fi

    SUBKEY_FPR_IN_SIG="$(echo "${VERIFY_STATUS}" | awk '/^\[GNUPG:\] VALIDSIG/ {print $3; exit}')"
    ok "Sig verifies: subkey ${SUBKEY_FPR_IN_SIG} -> primary ${KEY_FINGERPRINT}"
fi

# ============================================================
# PHASE 4/5: SUMMARY
# ============================================================
banner "Phase 4/5: Summary"

if [[ "${DRY_RUN}" == "1" ]]; then
    info "DRY-RUN complete. Pre-flight + library setup + sign-step + verify-step checks: all PASSED."
    info "Re-run without --dry-run to produce the real sig."
    exit 0
fi

cat <<EOF

FILE SIGNED.

Target:         ${FILE_PATH}
Target size:    $(stat -c %s "${FILE_PATH}") bytes
Target sha256:  $(sha256sum "${FILE_PATH}" | awk '{print $1}')
Sig file:       ${OUT_PATH}
Sig size:       $(stat -c %s "${OUT_PATH}") bytes
Sig sha256:     $(sha256sum "${OUT_PATH}" | awk '{print $1}')
Signing key:    ${KEY_FINGERPRINT}
Debug log:      ${DEBUG_LOG}
scdaemon log:   $(gpg_card_logfile_path)

The .asc sidecar has been produced and independently verified against the
primary-key fingerprint.
EOF

# ============================================================
# PHASE 5/5: DONE
# ============================================================
banner "Phase 5/5: Complete"
ok "sign-with-gpg.sh complete. Sig produced at ${OUT_PATH}."
exit 0
