#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# installer/smoke/checks/chronicle.sh — Category 6: Chronicle backup utility.
#
# Chronicle (the intergenos-backup package) is an OPTIONAL desktop app, so the
# whole category self-gates on it being installed: not installed -> one
# explicit SKIP, never silence. Installed -> assert the shipped surface is
# present and the schedule is wired: the CLI + engine binaries, the pkm
# pre-transaction restore-point handler (executable, in the discovery dir), the
# always-on engine unit, the three timers, and the polkit action.

_chronicle_installed() {
    [ -x /usr/bin/chronicle ] || command -v chronicle >/dev/null 2>&1
}

check_chronicle_binaries() {
    local missing=()
    for b in /usr/bin/chronicle /usr/bin/chronicled /usr/bin/chronicle-gui \
             /usr/libexec/chronicle/chronicled; do
        [ -e "$b" ] || missing+=("$b")
    done
    if [ ${#missing[@]} -eq 0 ]; then
        check_pass "chronicle/binaries" "cli + engine + gui present"
    else
        check_fail "chronicle/binaries" "missing: ${missing[*]}"
    fi
}

check_chronicle_pretxn_handler() {
    # The pre-transaction restore-point handler must be in pkm's discovery dir
    # AND executable (pkm skips non-executable handlers), or a package
    # transaction takes no restore point.
    local h=/usr/lib/pkm/pre-transaction.d/chronicle-restore-point
    if [ -x "$h" ]; then
        check_pass "chronicle/pretxn-handler" "restore-point handler installed + executable"
    elif [ -e "$h" ]; then
        check_fail "chronicle/pretxn-handler" "present but NOT executable — pkm will skip it"
    else
        check_fail "chronicle/pretxn-handler" "missing: $h"
    fi
}

check_chronicle_units() {
    if ! command -v systemctl >/dev/null 2>&1; then
        check_warn "chronicle/units" "systemctl not in PATH — cannot check units"
        return
    fi
    local missing=()
    for u in chronicled.service chronicle-userdata.timer \
             chronicle-offpeak.timer chronicle-scrub.timer \
             'chronicle-restore@.service'; do
        systemctl cat "$u" >/dev/null 2>&1 || missing+=("$u")
    done
    if [ ${#missing[@]} -ne 0 ]; then
        check_fail "chronicle/units" "not installed: ${missing[*]}"
        return
    fi
    # The engine + the three timers are enabled by post_install; the restore
    # template is on-demand and intentionally never enabled.
    local disabled=()
    for u in chronicled.service chronicle-userdata.timer \
             chronicle-offpeak.timer chronicle-scrub.timer; do
        systemctl is-enabled --quiet "$u" 2>/dev/null || disabled+=("$u")
    done
    if [ ${#disabled[@]} -eq 0 ]; then
        check_pass "chronicle/units" "engine + 3 timers installed and enabled"
    else
        check_warn "chronicle/units" "installed but not enabled: ${disabled[*]}"
    fi
}

check_chronicle_polkit() {
    local p=/usr/share/polkit-1/actions/org.intergenos.Chronicle.policy
    if [ -f "$p" ]; then
        check_pass "chronicle/polkit" "authorization action shipped"
    else
        check_fail "chronicle/polkit" "missing: $p"
    fi
}

run_chronicle_checks() {
    if ! _chronicle_installed; then
        check_skip "chronicle" "intergenos-backup not installed (optional desktop app)"
        return
    fi
    check_chronicle_binaries
    check_chronicle_pretxn_handler
    check_chronicle_units
    check_chronicle_polkit
}
