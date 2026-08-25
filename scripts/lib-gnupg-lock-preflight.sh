#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# lib-gnupg-lock-preflight.sh — sourceable, side-effect-free pre-flight that
# classifies the OpenPGP lock files in a GnuPG home before a signing run.
#
# WHY THIS EXISTS. The signing ceremony happens with the hardware token plugged
# in and the operator waiting at a PIN prompt. GnuPG serialises access to its
# key stores with "dotlocks": a file <store>.lock hard-linked to a sibling
# .#lk<hex>.<nodename>.<pid> file whose two lines are the owning pid and the
# owning machine's node name. If a lock is present that GnuPG will not break,
# the signing invocation does not fail — it WAITS, printing "waiting for lock"
# forever, with the token in the operator's hand and nothing to act on.
#
# WHAT WAS MEASURED (gpg 2.5.17, this project's host, isolated throwaway home;
# see the lane evidence set). Four distinct states exist, and they do NOT all
# need the same answer:
#
#   1. LIVE-DAEMON — the pid is alive and is one of GnuPG's own daemons for
#      this same home (keyboxd, gpg-agent, scdaemon, dirmngr). On a host with
#      use-keyboxd this is the NORMAL, HEALTHY state: keyboxd holds
#      public-keys.d/pubring.db.lock for its entire lifetime. Refusing here
#      would refuse every ceremony on a perfectly healthy machine.
#      -> reported, never refused.
#
#   2. STALE-SAME-HOST — the pid is dead and the node name is this machine.
#      Measured: gpg breaks this itself ("removing stale lockfile (created by
#      NNN)") and proceeds, in under a second. It is evidence a previous run
#      died, and worth saying out loud, but it does not wedge anything.
#      -> reported, not refused; --clear-stale-locks removes exactly these.
#
#   3. LIVE-OTHER — the pid is alive but is not a GnuPG daemon of this home.
#      Measured: gpg prints "waiting for lock (held by NNN)" and makes no
#      progress. Another program is genuinely using the store.
#      -> REFUSED, naming the pid and its command line.
#
#   4. FOREIGN-NODE — the node name in the lock is another machine, so this
#      host cannot tell whether the owner is alive. Measured: gpg waits and
#      makes no progress. This is the shape that actually wedges a ceremony —
#      a lock left by a run on another machine, or by a run made before this
#      machine was renamed.
#      -> REFUSED, naming the file and the foreign node name, and never
#         cleared automatically: the owner may be alive elsewhere.
#
# NOTHING IS EVER DELETED SILENTLY. --clear-stale-locks removes only case 2,
# only after printing each path it is about to remove.
#
# USAGE (sourced; defines functions only, runs nothing, changes no shell
# options, registers no traps):
#
#   source "${SCRIPT_DIR}/lib-gnupg-lock-preflight.sh"
#   gnupg_lock_preflight "<gnupg-home>" <clear_stale:0|1>
#     returns 0  -> safe to proceed
#     returns 1  -> refused; a reason has been printed to stderr
#
# Used by scripts/sign-with-gpg.sh (Phase 0b, through the setup library's
# neighbourhood) and, with one source line and one call each, by
# scripts/sign-manifest.sh and scripts/sign-release.sh — both reach gpg
# directly, so they call this immediately before their own token check.

# ============================================================
# CONSTANTS
# ============================================================
# GnuPG's own long-running daemons. A lock held by one of these, for the home
# being checked, is normal operation rather than a fault.
__GNUPG_LOCK_DAEMONS="keyboxd gpg-agent scdaemon dirmngr"

# ============================================================
# INTERNAL HELPERS
# ============================================================
__gnupg_lock_info() { echo "[gnupg-lock] $*"; }
__gnupg_lock_ok()   { echo "[gnupg-lock OK] $*"; }
__gnupg_lock_note() { echo "[gnupg-lock NOTE] $*"; }
__gnupg_lock_deny() { echo "[gnupg-lock REFUSED] $*" >&2; }

# Every lock file under a GnuPG home, one per line. Both halves of the dotlock
# pair are found: the <store>.lock link and the .#lk<hex>.<node>.<pid> file it
# points at. Only the .lock side is classified; the magic file is reported with
# it so the operator sees the whole pair.
__gnupg_lock_find() {
    local home="$1"
    [[ -d "${home}" ]] || return 0
    find "${home}" -maxdepth 2 -type f -name '*.lock' 2>/dev/null | sort
}

# The pid recorded INSIDE the lock file (line 1). Read from the content rather
# than the file name: the name is a convenience, the content is the record.
__gnupg_lock_pid() {
    awk 'NR==1 {gsub(/[^0-9]/, "", $0); print $0; exit}' "$1" 2>/dev/null
}

# The node name recorded inside the lock file (line 2).
__gnupg_lock_node() {
    awk 'NR==2 {print $1; exit}' "$1" 2>/dev/null
}

# Is this pid one of GnuPG's daemons serving the home we are checking?
# The daemons carry "--homedir <path>" on their command line, so the match is
# on both the program name and the home it serves. A daemon started without an
# explicit --homedir is not claimed as ours; it falls through to LIVE-OTHER,
# which refuses rather than waves through, and that is the safe direction.
__gnupg_lock_is_daemon_for_home() {
    local pid="$1" home="$2" cmd base
    cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null)" || return 1
    [[ -n "${cmd}" ]] || return 1
    base="$(basename "$(echo "${cmd}" | awk '{print $1}')")"
    local d found=0
    for d in ${__GNUPG_LOCK_DAEMONS}; do
        [[ "${base}" == "${d}" ]] && { found=1; break; }
    done
    [[ "${found}" == "1" ]] || return 1
    # The daemon must name the same home this check is about.
    [[ "${cmd}" == *"--homedir ${home} "* || "${cmd}" == *"--homedir ${home}" ]]
}

# One-line description of a pid, for naming it in a refusal.
__gnupg_lock_describe_pid() {
    local pid="$1"
    ps -p "${pid}" -o comm=,args= 2>/dev/null | head -1 | sed 's/^ *//'
}

# ============================================================
# PUBLIC: gnupg_lock_preflight <gnupg-home> <clear_stale:0|1>
# ============================================================
# Classifies every lock in the home and decides whether signing may proceed.
# Prints one line per lock found. Returns 0 to proceed, 1 to refuse.
gnupg_lock_preflight() {
    local home="${1:-}"
    local clear_stale="${2:-0}"
    local this_node
    this_node="$(uname -n)"

    if [[ -z "${home}" ]]; then
        __gnupg_lock_deny "gnupg_lock_preflight requires a GnuPG home path."
        return 1
    fi
    if [[ ! -d "${home}" ]]; then
        # No home yet is not a lock problem; the caller's own checks own this.
        __gnupg_lock_ok "No GnuPG home at ${home} yet; no locks to check."
        return 0
    fi

    local locks
    locks="$(__gnupg_lock_find "${home}")"
    if [[ -z "${locks}" ]]; then
        __gnupg_lock_ok "No lock files under ${home}."
        return 0
    fi

    # Stale entries are collected as "<pid> <path>": clearing needs the owning
    # pid to name that lock's own record file, and nothing else's.
    local refuse=0 stale_entries=() lock pid node
    while IFS= read -r lock; do
        [[ -n "${lock}" ]] || continue
        pid="$(__gnupg_lock_pid "${lock}")"
        node="$(__gnupg_lock_node "${lock}")"

        # A lock whose content we cannot read is not a lock we may reason
        # about. Refuse rather than guess.
        if [[ -z "${pid}" || -z "${node}" ]]; then
            __gnupg_lock_deny "Unreadable lock record: ${lock} (pid='${pid:-}' node='${node:-}'). Inspect it by hand; nothing was changed."
            refuse=1
            continue
        fi

        # Case 4: another machine's lock. Liveness is unknowable from here and
        # gpg waits on it, so this refuses and is never cleared automatically.
        if [[ "${node}" != "${this_node}" ]]; then
            __gnupg_lock_deny "Lock held by node '${node}' (this host is '${this_node}'): ${lock}"
            __gnupg_lock_deny "  This host cannot tell whether pid ${pid} on '${node}' is still running, and gpg waits on it without a bound. Check that machine before removing anything."
            refuse=1
            continue
        fi

        if kill -0 "${pid}" 2>/dev/null; then
            if __gnupg_lock_is_daemon_for_home "${pid}" "${home}"; then
                # Case 1: normal operation.
                __gnupg_lock_ok "Lock held by this home's own GnuPG daemon (pid ${pid}: $(__gnupg_lock_describe_pid "${pid}")): ${lock}"
            else
                # Case 3: someone else is using the store.
                __gnupg_lock_deny "Lock held by a LIVE process that is not this home's GnuPG daemon: ${lock}"
                __gnupg_lock_deny "  pid ${pid}: $(__gnupg_lock_describe_pid "${pid}")"
                __gnupg_lock_deny "  gpg waits on this lock and makes no progress. Let that process finish, or stop it, then run again."
                refuse=1
            fi
        else
            # Case 2: dead owner, this host. gpg clears these itself.
            __gnupg_lock_note "Stale lock from a previous run on this host (pid ${pid} is gone): ${lock}"
            stale_entries+=("${pid} ${lock}")
        fi
    done <<< "${locks}"

    if [[ ${#stale_entries[@]} -gt 0 ]]; then
        if [[ "${clear_stale}" == "1" ]]; then
            local entry dead_pid dead_lock magic
            for entry in "${stale_entries[@]}"; do
                dead_pid="${entry%% *}"
                dead_lock="${entry#* }"
                __gnupg_lock_info "Removing stale lock: ${dead_lock}"
                rm -f "${dead_lock}"
                # Remove the other half of THIS pair and nothing else. The
                # record file is named for the machine and the owning pid, so
                # both are required in the pattern: a directory can hold a
                # second lock whose owner is still running, and matching on the
                # machine name alone would take that owner's record file too.
                for magic in "$(dirname "${dead_lock}")"/.\#lk*."${this_node}"."${dead_pid}"; do
                    [[ -e "${magic}" ]] || continue
                    __gnupg_lock_info "Removing its lock record: ${magic}"
                    rm -f "${magic}"
                done
            done
            __gnupg_lock_ok "Stale locks cleared (${#stale_entries[@]})."
        else
            __gnupg_lock_note "gpg removes stale locks of this shape by itself; nothing is blocking the run."
            __gnupg_lock_note "Pass --clear-stale-locks to remove them here instead, so the run starts from a clean home."
        fi
    fi

    if [[ "${refuse}" == "1" ]]; then
        __gnupg_lock_deny "Refusing to start a signing run while the above lock(s) stand. Nothing was deleted."
        return 1
    fi

    return 0
}
