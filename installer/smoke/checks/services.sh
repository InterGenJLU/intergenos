#!/usr/bin/env bash
# installer/smoke/checks/services.sh — Category 4: service health.
#
# Confirms the systemd manager state is healthy and the four critical
# services for an InterGenOS desktop boot are active.

# Hard-coded for v1.0 (per ratified design Q3); /etc/intergenos/smoke-services.list
# extension is v1.0+1 backlog.
SMOKE_CRITICAL_SERVICES=(dbus systemd-journald systemd-logind systemd-udevd)

check_services_system_state() {
    if ! command -v systemctl >/dev/null 2>&1; then
        check_fail "svc/system-state" "systemctl not in PATH — not a systemd system?"
        return
    fi

    local state
    state="$(systemctl is-system-running 2>&1 || true)"
    case "$state" in
        running)    check_pass "svc/system-state" "running" ;;
        degraded)   check_warn "svc/system-state" "degraded (some unit failed; check svc/failed)" ;;
        starting)   check_fail "svc/system-state" "still starting — boot incomplete" ;;
        maintenance|offline)
                    check_fail "svc/system-state" "$state" ;;
        *)          check_warn "svc/system-state" "unexpected: $state" ;;
    esac
}

check_services_no_failed() {
    local count failed
    failed="$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr '\n' ',' | sed 's/,$//')"
    count="$(systemctl --failed --no-legend 2>/dev/null | wc -l)"

    if [ "$count" -eq 0 ]; then
        check_pass "svc/failed" "no failed units"
        return
    fi
    check_fail "svc/failed" "$count failed: $failed"
}

check_services_critical_active() {
    local svc rc=0 inactive=()
    for svc in "${SMOKE_CRITICAL_SERVICES[@]}"; do
        if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
            inactive+=("$svc")
            rc=1
        fi
    done

    if [ $rc -eq 0 ]; then
        check_pass "svc/critical" "all ${#SMOKE_CRITICAL_SERVICES[@]} active: ${SMOKE_CRITICAL_SERVICES[*]}"
        return
    fi
    check_fail "svc/critical" "inactive: ${inactive[*]}"
}

run_services_checks() {
    check_services_system_state
    check_services_no_failed
    check_services_critical_active
}
