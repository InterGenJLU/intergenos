#!/usr/bin/env bash
# installer/smoke/checks/pkm.sh — Category 1: pkm sanity.
#
# Confirms the package manager and its database are functional on the
# installed system. Sourced by smoke-test.sh after lib.sh. Each function
# emits exactly one PASS/FAIL/WARN/SKIP via check_*.

check_pkm_list() {
    local count
    if ! count="$(pkm list 2>/dev/null | wc -l)"; then
        check_fail "pkm/list" "pkm list failed (DB unreadable?)"
        return
    fi
    if [ "$count" -eq 0 ]; then
        check_fail "pkm/list" "pkm list returned 0 packages"
        return
    fi
    check_pass "pkm/list" "$count packages"
}

check_pkm_verify() {
    # Pass --all explicitly per H-006: bare `pkm verify` returns exit 2
    # (usage error) post-fix. Smoke must pass --all to perform a real
    # system-wide verify.
    local mode="--fast --all"
    [ "$SMOKE_STRICT" = "1" ] && mode="--strict --all"

    verbose "running: pkm verify $mode"
    local out rc=0
    out="$(pkm verify $mode 2>&1)" || rc=$?

    case "$rc" in
        0) check_pass "pkm/verify" "all files match (${mode%% --all} mode)" ;;
        1) check_fail "pkm/verify" "$(echo "$out" | head -3 | tr '\n' ';')" ;;
        2) check_fail "pkm/verify" "usage error — smoke invocation regression: missing --all or package arg" ;;
        *) check_fail "pkm/verify" "unexpected exit $rc: $(echo "$out" | head -1)" ;;
    esac
}

check_pkm_info_marker() {
    local marker="${1:-glibc-core}"
    if ! pkm info "$marker" >/dev/null 2>&1; then
        check_fail "pkm/info" "marker package '$marker' not in DB — install incomplete?"
        return
    fi
    check_pass "pkm/info" "marker package $marker present"
}

check_pkm_files_marker() {
    local marker="${1:-glibc-core}"
    local count
    if ! count="$(pkm files "$marker" 2>/dev/null | wc -l)"; then
        check_fail "pkm/files" "pkm files $marker failed"
        return
    fi
    if [ "$count" -eq 0 ]; then
        check_fail "pkm/files" "marker $marker has zero installed files"
        return
    fi
    check_pass "pkm/files" "$marker owns $count files"
}

run_pkm_checks() {
    if ! command -v pkm >/dev/null 2>&1; then
        check_skip "pkm/category" "pkm not in PATH — not on InterGenOS?"
        return
    fi
    check_pkm_list
    check_pkm_verify
    check_pkm_info_marker
    check_pkm_files_marker
}
