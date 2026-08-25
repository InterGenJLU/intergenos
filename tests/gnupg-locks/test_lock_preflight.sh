#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# test_lock_preflight.sh — the signing scripts' GnuPG lock pre-flight.
#
# WHAT IS BEING PROTECTED. The signing ceremony runs with the hardware token
# plugged in and the operator waiting at a PIN prompt. A lock file GnuPG will
# not break does not make gpg fail — it makes gpg WAIT, printing "waiting for
# lock" and making no progress, with nothing for the operator to act on. The
# pre-flight exists to say, before any of that, which locks are present and
# which of them actually block the run.
#
# THE FOUR STATES ARE NOT THE SAME AND ARE NOT ANSWERED THE SAME WAY. They were
# measured against gpg 2.5.17 in an isolated throwaway home before this test was
# written, and the measurements are what the cases below encode:
#   - a lock held by this home's own GnuPG daemon is NORMAL (keyboxd holds
#     public-keys.d/pubring.db.lock for its whole life) and must not refuse;
#   - a lock whose owning pid is dead, on this host, is cleared by gpg itself
#     within a second, so it is reported and must not refuse;
#   - a lock held by a live process that is not one of this home's daemons
#     makes gpg wait with no progress, so it must refuse and name the pid;
#   - a lock naming another machine cannot be judged from here at all, gpg
#     waits on it, and it must refuse and must never be removed automatically.
#
# The pre-flight is pure file-and-pid logic, so this suite drives it directly
# with planted lock files and real processes. No gpg is invoked, no key is
# generated, no real GnuPG home is read or written.
#
# Output discipline: tool output is captured to files, never echoed to stdout.
# The canonical shell-suite runner (tests/test_shell_suites.py) treats certain
# words appearing on a suite's stdout as a sign the instrument is degrading, so
# this suite prints only its own PASS/FAIL lines.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB="${REPO_ROOT}/scripts/lib-gnupg-lock-preflight.sh"

PASS=0
FAIL=0
WORK=""

cleanup() {
    # Stop anything this suite started, then remove its scratch tree.
    if [[ -n "${WORK}" && -f "${WORK}/pids" ]]; then
        local p
        while read -r p; do
            [[ -n "${p}" ]] && kill "${p}" 2>/dev/null
        done < "${WORK}/pids"
        wait 2>/dev/null
    fi
    [[ -n "${WORK}" && -d "${WORK}" ]] && rm -rf "${WORK}"
}
trap cleanup EXIT

ok()   { PASS=$((PASS + 1)); echo "PASS: $*"; }
bad()  { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }

check_rc() {  # $1 expected rc, $2 actual rc, $3 description
    if [[ "$1" == "$2" ]]; then ok "$3 (rc=$2)"; else bad "$3 — expected rc=$1, got rc=$2"; fi
}

# ------------------------------------------------------------
# The library must exist and must be sourceable without side effects.
# ------------------------------------------------------------
if [[ ! -f "${LIB}" ]]; then
    echo "FAIL: pre-flight library not found at ${LIB}"
    echo "SUMMARY: 0 passed, 1 failed"
    exit 1
fi

# shellcheck disable=SC1090
source "${LIB}"

if ! declare -F gnupg_lock_preflight >/dev/null; then
    echo "FAIL: sourcing ${LIB} did not define gnupg_lock_preflight"
    echo "SUMMARY: 0 passed, 1 failed"
    exit 1
fi
ok "library sources and defines gnupg_lock_preflight"

WORK="$(mktemp -d)"
: > "${WORK}/pids"
NODE="$(uname -n)"

# ------------------------------------------------------------
# Helpers: build a throwaway GnuPG home and plant dotlock pairs in it.
# A dotlock is a <store>.lock file hard-linked to a sibling
# .#lk<hex>.<nodename>.<pid> file whose two lines are the pid and the node.
# ------------------------------------------------------------
new_home() {
    local h="${WORK}/$1/.gnupg"
    mkdir -p "${h}"
    chmod 700 "${h}"
    : > "${h}/pubring.kbx"
    echo "${h}"
}

plant_lock() {  # $1 home, $2 store basename, $3 pid, $4 nodename
    local h="$1" store="$2" pid="$3" node="$4"
    local magic="${h}/.#lk0x00007f0000000000.${node}.${pid}"
    printf '%10d\n%s\n' "${pid}" "${node}" > "${magic}"
    ln -f "${magic}" "${h}/${store}.lock"
}

start_bg() {  # runs "$@" in the background, records the pid, prints it
    "$@" >/dev/null 2>&1 &
    local p=$!
    echo "${p}" >> "${WORK}/pids"
    echo "${p}"
}

dead_pid() {  # a pid number that is certainly not running
    local p
    ( true ) & p=$!
    wait "${p}" 2>/dev/null
    echo "${p}"
}

run_preflight() {  # $1 home, $2 clear_stale — output captured, never echoed
    gnupg_lock_preflight "$1" "$2" > "${WORK}/out.txt" 2> "${WORK}/err.txt"
    echo $?
}

# ------------------------------------------------------------
# CASE 0 — a home with no locks at all proceeds.
# ------------------------------------------------------------
H="$(new_home case0)"
RC="$(run_preflight "${H}" 0)"
check_rc 0 "${RC}" "a home with no lock files proceeds"

# ------------------------------------------------------------
# CASE 1 — a lock held by this home's own GnuPG daemon proceeds.
# This is the state a healthy keyboxd host is in every minute of the day; a
# pre-flight that refused here would refuse every ceremony on that machine.
# The stand-in carries the real shape the check reads: a program named keyboxd
# whose command line names this same home.
# ------------------------------------------------------------
H="$(new_home case1)"
BIN="${WORK}/bin"
mkdir -p "${BIN}"
cp /bin/sh "${BIN}/keyboxd"
# Two commands, not one: a shell given a single command execs it and loses its
# own command line, which is exactly the thing under test here.
DPID="$(start_bg "${BIN}/keyboxd" -c 'sleep 900; true' keyboxd --homedir "${H}" --daemon)"
sleep 0.3
plant_lock "${H}" pubring.kbx "${DPID}" "${NODE}"
RC="$(run_preflight "${H}" 0)"
check_rc 0 "${RC}" "a lock held by this home's own GnuPG daemon proceeds"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "the daemon's live lock was left in place"
else
    bad "the daemon's live lock was removed — a live lock must never be touched"
fi

# ------------------------------------------------------------
# CASE 2 — a lock held by a LIVE process that is not one of this home's
# daemons refuses, and names the pid so the operator can act on it.
# ------------------------------------------------------------
H="$(new_home case2)"
OPID="$(start_bg sleep 900)"
sleep 0.3
plant_lock "${H}" pubring.kbx "${OPID}" "${NODE}"
RC="$(run_preflight "${H}" 0)"
check_rc 1 "${RC}" "a lock held by an unrelated live process refuses"
if grep -q "${OPID}" "${WORK}/err.txt"; then
    ok "the refusal names the holding pid"
else
    bad "the refusal does not name the holding pid ${OPID}"
fi
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "the live lock was left in place"
else
    bad "the live lock was removed — a live lock must never be touched"
fi

# ------------------------------------------------------------
# CASE 3 — a lock whose owner is dead, on this host, is reported and proceeds.
# gpg clears this shape itself, so refusing would add a failure the ceremony
# does not otherwise have. Without the clearing flag the file stays put.
# ------------------------------------------------------------
H="$(new_home case3)"
XPID="$(dead_pid)"
plant_lock "${H}" pubring.kbx "${XPID}" "${NODE}"
RC="$(run_preflight "${H}" 0)"
check_rc 0 "${RC}" "a stale lock from a dead run on this host proceeds"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "the stale lock was reported and left alone without the clearing flag"
else
    bad "the stale lock was deleted without being asked to"
fi
if grep -qi "stale" "${WORK}/out.txt"; then
    ok "the stale lock was named in the report"
else
    bad "the stale lock was not named in the report"
fi

# ------------------------------------------------------------
# CASE 4 — with the clearing flag, a stale lock is removed, and BOTH halves of
# the dotlock pair go, so no record file is left behind.
# ------------------------------------------------------------
H="$(new_home case4)"
XPID="$(dead_pid)"
plant_lock "${H}" pubring.kbx "${XPID}" "${NODE}"
RC="$(run_preflight "${H}" 1)"
check_rc 0 "${RC}" "clearing stale locks proceeds"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    bad "the stale lock is still present after being asked to clear it"
else
    ok "the stale lock was removed"
fi
if compgen -G "${H}/.#lk*" >/dev/null; then
    bad "the stale lock's record file was left behind"
else
    ok "the stale lock's record file was removed with it"
fi

# ------------------------------------------------------------
# CASE 5 — the clearing flag must NOT reach a live lock. This is the case that
# separates "clear the stale ones" from "clear the lock files".
# ------------------------------------------------------------
H="$(new_home case5)"
OPID="$(start_bg sleep 900)"
sleep 0.3
plant_lock "${H}" pubring.kbx "${OPID}" "${NODE}"
RC="$(run_preflight "${H}" 1)"
check_rc 1 "${RC}" "a live lock still refuses even with the clearing flag"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "the clearing flag did not remove a live lock"
else
    bad "the clearing flag removed a LIVE lock"
fi

# ------------------------------------------------------------
# CASE 6 — a lock naming another machine refuses and is never removed. This
# host cannot tell whether that owner is running, and gpg waits on it.
# ------------------------------------------------------------
H="$(new_home case6)"
plant_lock "${H}" pubring.kbx 999999 "some-other-host"
RC="$(run_preflight "${H}" 1)"
check_rc 1 "${RC}" "a lock naming another machine refuses"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "another machine's lock was left in place even with the clearing flag"
else
    bad "another machine's lock was removed — liveness there is unknowable"
fi
if grep -q "some-other-host" "${WORK}/err.txt"; then
    ok "the refusal names the other machine"
else
    bad "the refusal does not name the other machine"
fi

# ------------------------------------------------------------
# CASE 7 — a lock file whose record cannot be read is refused rather than
# guessed at, and is left on disk for a person to look at.
# ------------------------------------------------------------
H="$(new_home case7)"
printf 'not a lock record\n' > "${H}/pubring.kbx.lock"
RC="$(run_preflight "${H}" 1)"
check_rc 1 "${RC}" "an unreadable lock record refuses"
if [[ -e "${H}/pubring.kbx.lock" ]]; then
    ok "the unreadable lock was left on disk"
else
    bad "the unreadable lock was removed"
fi

# ------------------------------------------------------------
# CASE 8 — locks on the other stores are found too, not just pubring.
# public-keys.d/pubring.db is one directory down, which is where a keyboxd
# host's lock actually lives.
# ------------------------------------------------------------
H="$(new_home case8)"
mkdir -p "${H}/public-keys.d"
XPID="$(dead_pid)"
MAGIC="${H}/public-keys.d/.#lk0x00007f0000000000.${NODE}.${XPID}"
printf '%10d\n%s\n' "${XPID}" "${NODE}" > "${MAGIC}"
ln -f "${MAGIC}" "${H}/public-keys.d/pubring.db.lock"
RC="$(run_preflight "${H}" 0)"
check_rc 0 "${RC}" "a lock one directory down is classified too"
if grep -q "public-keys.d/pubring.db.lock" "${WORK}/out.txt"; then
    ok "the nested lock was named in the report"
else
    bad "the nested lock was not named in the report"
fi

# ------------------------------------------------------------
echo "SUMMARY: ${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
