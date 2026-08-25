#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# test_config_save_restore.sh — the signing setup library's config save and
# restore, and specifically the promise that a run which changes nothing leaves
# nothing changed.
#
# WHAT WENT WRONG. scripts/lib-gpg-card-setup.sh borrows two files for the
# duration of a signing run — ~/.gnupg/gpg-agent.conf and ~/.gnupg/scdaemon.conf
# — by moving the existing file to a backup name, writing its own content, and
# putting the original back from an exit handler. The exit handler decides what
# to do by looking for the backup:
#
#   backup present  -> put it back
#   no backup, but a file is there -> assume this run created it, and remove it
#
# That second branch is only true if this run really did write the file. A
# dry run does not: the swap returns early after printing what it would have
# done, so no backup is ever created. The exit handler then finds a config with
# no backup beside it, concludes it must have written it, and deletes a file it
# never touched.
#
# Measured on this project's own machine: `sign-with-gpg.sh --dry-run` removed
# an operator's ~/.gnupg/gpg-agent.conf that had been in place since July. The
# script's own documentation tells the operator to run --dry-run first, so the
# documented procedure was the one that destroyed the file.
#
# The same branch fires whenever a swap is skipped rather than performed, which
# also happens on a REAL run: when pinentry-tty is not installed the library
# warns and skips the gpg-agent.conf swap, and the exit handler then deletes the
# operator's own gpg-agent.conf on the way out.
#
# So the property under test is not "dry runs are special". It is: the exit
# handler restores exactly the files this run actually swapped, and touches no
# others.
#
# This suite drives the library's own helpers against a throwaway HOME. gpgconf
# is shadowed by a stub for the duration, so nothing here can reach a real
# GnuPG agent — the library's exit handler calls it unconditionally.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB="${REPO_ROOT}/scripts/lib-gpg-card-setup.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "PASS: $*"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }

if [[ ! -f "${LIB}" ]]; then
    echo "FAIL: setup library not found at ${LIB}"
    echo "SUMMARY: 0 passed, 1 failed"
    exit 1
fi

WORK="$(mktemp -d)"
cleanup() { [[ -n "${WORK}" && -d "${WORK}" ]] && rm -rf "${WORK}"; }
trap cleanup EXIT

# A stub gpgconf, so the library's exit handler cannot reach any real agent.
STUB_BIN="${WORK}/bin"
mkdir -p "${STUB_BIN}"
printf '#!/bin/sh\nexit 0\n' > "${STUB_BIN}/gpgconf"
chmod +x "${STUB_BIN}/gpgconf"
PATH="${STUB_BIN}:${PATH}"
export PATH

ORIGINAL_CONTENT='pinentry-program /usr/bin/pinentry-gnome3'

# Each case runs in a subshell with its own throwaway HOME, because the library
# reads ${HOME} directly.
run_case() {  # $1 label, $2 dry_run, $3 do_swap(0|1), $4 seed_config(0|1)
    local label="$1" dry="$2" do_swap="$3" seed="$4"
    local home="${WORK}/${label}"
    mkdir -p "${home}/.gnupg"
    if [[ "${seed}" == "1" ]]; then
        printf '%s\n' "${ORIGINAL_CONTENT}" > "${home}/.gnupg/gpg-agent.conf"
        chmod 600 "${home}/.gnupg/gpg-agent.conf"
    fi
    (
        export HOME="${home}"
        export GPG_CARD_DRY_RUN="${dry}"
        export GPG_CARD_DEBUG=0
        export GPG_CARD_LOG=""
        # shellcheck disable=SC1090
        source "${LIB}"
        if [[ "${do_swap}" == "1" ]]; then
            __gpg_card_swap_in_config "${home}/.gnupg/gpg-agent.conf" "temporary content for this run"
        fi
        __gpg_card_cleanup_on_exit
    ) > "${WORK}/${label}.out" 2>&1
    echo "${home}"
}

# ------------------------------------------------------------
# CASE 1 — a DRY RUN must leave an existing config exactly as it found it.
# This is the case that destroyed a real file.
# ------------------------------------------------------------
H="$(run_case dryrun-existing 1 1 1)"
if [[ -f "${H}/.gnupg/gpg-agent.conf" ]]; then
    ok "a dry run leaves an existing gpg-agent.conf on disk"
    if [[ "$(cat "${H}/.gnupg/gpg-agent.conf")" == "${ORIGINAL_CONTENT}" ]]; then
        ok "a dry run leaves the config's bytes unchanged"
    else
        bad "a dry run changed the config's bytes"
    fi
else
    bad "a dry run DELETED an existing gpg-agent.conf"
fi

# ------------------------------------------------------------
# CASE 2 — a run in which the swap was SKIPPED must also leave the config
# alone. This is the same branch, on the real signing path: when pinentry-tty
# is absent the library skips the swap and the exit handler still runs.
# ------------------------------------------------------------
H="$(run_case skipped-swap 0 0 1)"
if [[ -f "${H}/.gnupg/gpg-agent.conf" ]]; then
    ok "a run that skipped the swap leaves the config on disk"
    if [[ "$(cat "${H}/.gnupg/gpg-agent.conf")" == "${ORIGINAL_CONTENT}" ]]; then
        ok "a run that skipped the swap leaves the bytes unchanged"
    else
        bad "a run that skipped the swap changed the bytes"
    fi
else
    bad "a run that skipped the swap DELETED the config"
fi

# ------------------------------------------------------------
# CASE 3 — a real swap must still be undone: the borrowed file goes back with
# its original bytes. The fix must not turn restore into a no-op.
# ------------------------------------------------------------
H="$(run_case real-swap 0 1 1)"
if [[ -f "${H}/.gnupg/gpg-agent.conf" ]]; then
    if [[ "$(cat "${H}/.gnupg/gpg-agent.conf")" == "${ORIGINAL_CONTENT}" ]]; then
        ok "a real swap is undone and the original bytes come back"
    else
        bad "a real swap left its own content behind: $(cat "${H}/.gnupg/gpg-agent.conf")"
    fi
else
    bad "a real swap left no config at all"
fi
if compgen -G "${H}/.gnupg/*.sign-with-gpg-backup" >/dev/null; then
    bad "a backup file was left behind after restore"
else
    ok "no backup file is left behind after restore"
fi

# ------------------------------------------------------------
# CASE 4 — a real swap on a host that had NO config must leave none behind.
# The file the run wrote is its own, and removing it is correct.
# ------------------------------------------------------------
H="$(run_case real-swap-no-original 0 1 0)"
if [[ -f "${H}/.gnupg/gpg-agent.conf" ]]; then
    bad "a run left its temporary config behind on a host that had none"
else
    ok "a run that wrote a config where there was none removes it again"
fi

# ------------------------------------------------------------
# CASE 5 — a dry run on a host with no config must not invent one.
# ------------------------------------------------------------
H="$(run_case dryrun-no-original 1 1 0)"
if [[ -f "${H}/.gnupg/gpg-agent.conf" ]]; then
    bad "a dry run created a config where there was none"
else
    ok "a dry run creates no config where there was none"
fi

# ------------------------------------------------------------
echo "SUMMARY: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
