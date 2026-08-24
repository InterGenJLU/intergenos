#!/bin/bash
# The build's single-flight assertion for pkm.
#
# Sourceable on its own, with no errexit and no side effects, so a config phase
# can pick up this one function without inheriting pkg-functions.sh's `set -e`.
#
# It reports through pkg_log/pkg_error when those exist (inside pkg-functions.sh)
# and falls back to log/echo otherwise (a config phase), so the same function
# reads correctly in both places.

_sf_say() {
    if declare -F pkg_log >/dev/null 2>&1; then pkg_log "$@";
    elif declare -F log >/dev/null 2>&1; then log "$@";
    else echo "$@"; fi
}

_sf_err() {
    if declare -F pkg_error >/dev/null 2>&1; then pkg_error "$@";
    elif declare -F log >/dev/null 2>&1; then log "error: $@";
    else echo "error: $@" >&2; fi
}

# ---------------------------------------------------------------------------
# pkg_run_pkm_single_flight — run one pkm subcommand, and PROVE nothing else is
# running one at the same time.
#
# WHY THIS EXISTS. pkm's own mutation lock has one escape: when the lock
# directory cannot be created it runs the mutation with NO lock held, on the
# stated assumption that a build chroot runs one pkm at a time. That assumption
# was written down and never checked. This is the check, placed where the build
# actually invokes pkm, so the assumption becomes a measured property of the
# build rather than a claim about it.
#
# HOW. A kernel-held flock on a lock file under the build's own log directory,
# taken with -n so it never queues — never a check-then-act, and released by
# construction if this shell dies. A SECOND concurrent invocation does not queue
# and does not warn: it HALTS the build, printing what it found. A build that
# quietly serialized instead would hide the exact condition this exists to
# detect.
#
# The lock is the build's, not pkm's. pkm's own lock still applies inside the
# invocation wherever it can be taken; this one asserts the build's premise.
pkg_run_pkm_single_flight() {
    local subcommand="$1"
    shift || true
    local lock_file="${IGOS_LOGS:-/tmp}/.pkm-single-flight.lock"

    # No flock binary (a minimal chroot early in the build): say so and run
    # anyway. Silence here would be a claim of single-flight that nothing
    # checked, which is the shape this function exists to remove.
    if ! command -v flock >/dev/null 2>&1; then
        _sf_say "  (single-flight NOT asserted for 'pkm $subcommand': flock is not present in this environment)"
        pkm "$subcommand" "$@" 2>&1 | sed 's/^/  /' || _sf_say "  (pkm $subcommand non-fatal)"
        return 0
    fi

    exec {_sf_fd}>"$lock_file" || {
        _sf_err "cannot open the build's single-flight lock at $lock_file"
        return 1
    }
    if ! flock -n "$_sf_fd"; then
        exec {_sf_fd}>&-
        _sf_err "HALT: a second pkm invocation started while one was already running."
        _sf_err "  The build is expected to run exactly one pkm at a time, and pkm's own"
        _sf_err "  mutation lock is skipped in this environment on that assumption."
        _sf_err "  Two at once means an unserialized package-database write, so the build"
        _sf_err "  stops here rather than continuing on an assumption that has just been"
        _sf_err "  shown to be false."
        _sf_err "  Lock: $lock_file   subcommand: pkm $subcommand"
        if command -v fuser >/dev/null 2>&1; then
            _sf_err "  Held by: $(fuser "$lock_file" 2>&1 | tr -d '\n')"
        fi
        exit 1
    fi
    pkm "$subcommand" "$@" 2>&1 | sed 's/^/  /' || _sf_say "  (pkm $subcommand non-fatal)"
    flock -u "$_sf_fd"
    exec {_sf_fd}>&-
    return 0
}
