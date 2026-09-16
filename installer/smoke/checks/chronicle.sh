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

# The install writes /etc/machine-id onto the target once, at install time
# (installer/backend/config.py), so its modification time is when this system
# was installed. A restore-point timeline whose newest entry is older than
# that belongs to some earlier system, not this one — and an empty timeline
# means there is no state to return to at all. Both are a WARN, never a FAIL:
# the machine works, the person just cannot roll it back.
check_chronicle_restore_point_covers_this_install() {
    local anchor="${SMOKE_MACHINE_ID:-/etc/machine-id}"
    local installed_at newest out rc

    if [ ! -e "$anchor" ]; then
        check_warn "chronicle/restore-point" \
            "cannot tell when this system was installed ($anchor is absent)"
        return
    fi
    if ! installed_at="$(stat -c %Y "$anchor" 2>/dev/null)" \
       || [ -z "$installed_at" ]; then
        check_warn "chronicle/restore-point" \
            "cannot read the installation time from $anchor"
        return
    fi

    out="$(chronicle list restore-point --json 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        # Unreadable is not absent: an ordinary account cannot reach the
        # engine, and reporting that as "no restore points" would be a lie
        # in the direction of alarm.
        check_warn "chronicle/restore-point" \
            "restore-point timeline unreadable here — re-run as root: sudo intergenos-smoke-test (${out})"
        return
    fi

    newest="$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    versions = json.loads(sys.stdin.read())
except Exception:
    sys.exit(3)
if not isinstance(versions, list):
    sys.exit(3)
stamps = [v.get("wall_clock") for v in versions
          if isinstance(v, dict) and isinstance(v.get("wall_clock"), (int, float))]
print(int(max(stamps)) if stamps else "")
' 2>/dev/null)"
    if [ $? -ne 0 ]; then
        check_warn "chronicle/restore-point" \
            "the restore-point timeline could not be parsed"
        return
    fi

    if [ -z "$newest" ]; then
        check_warn "chronicle/restore-point" \
            "no restore point exists — there is no system state to return to (take one: chronicle capture restore-point)"
        return
    fi
    if [ "$newest" -lt "$installed_at" ]; then
        check_warn "chronicle/restore-point" \
            "the newest restore point predates this installation — it belongs to an earlier system (take one: chronicle capture restore-point)"
        return
    fi
    check_pass "chronicle/restore-point" "a restore point covers this installation"
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
    check_chronicle_restore_point_covers_this_install
}
