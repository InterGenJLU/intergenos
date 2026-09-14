#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# installer/smoke/checks/pkm.sh — Category 1: pkm sanity.
#
# Confirms the package manager and its database are functional on the
# installed system. Sourced by smoke-test.sh after lib.sh. Each function
# emits exactly one PASS/FAIL/WARN/SKIP via check_*.

SMOKE_PKM="${SMOKE_PKM:-pkm}"
SMOKE_PKM_MARKER="${SMOKE_PKM_MARKER:-glibc}"

check_pkm_list() {
    local out rc=0 count
    out="$("$SMOKE_PKM" list 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_fail "pkm/list" "pkm list failed (exit $rc; DB unreadable?)"
        return
    fi

    # `pkm list` is human-oriented: one package can occupy several wrapped
    # description lines. Its header is the producer's explicit package count;
    # counting presentation lines inflated 865 installed packages to 1,949.
    count="$(printf '%s\n' "$out" \
        | sed -nE 's/^[[:space:]]*Installed packages \(([0-9]+)\):[[:space:]]*$/\1/p')"
    if [[ ! "$count" =~ ^[0-9]+$ ]]; then
        check_fail "pkm/list" "pkm list returned no single parseable installed-package count"
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
    out="$("$SMOKE_PKM" verify $mode 2>&1)" || rc=$?

    case "$rc" in
        0)
            if [ "$SMOKE_STRICT" = "1" ]; then
                check_pass "pkm/verify" "strict content verification completed"
            else
                check_pass "pkm/verify" "all tracked paths present (fast existence-only mode)"
            fi
            ;;
        1) check_fail "pkm/verify" "$(echo "$out" | head -3 | tr '\n' ';')" ;;
        2) check_fail "pkm/verify" "usage error — smoke invocation regression: missing --all or package arg" ;;
        3) check_warn "pkm/verify" "verification could not complete as this user — $(smoke_root_rerun)" ;;
        *) check_fail "pkm/verify" "unexpected exit $rc: $(echo "$out" | head -1)" ;;
    esac
}

check_pkm_info_marker() {
    local marker="${1:-$SMOKE_PKM_MARKER}" out rc=0 count
    out="$("$SMOKE_PKM" info "$marker" 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_fail "pkm/info" "marker package '$marker' not in DB — install incomplete?"
        return
    fi

    # `pkm info <missing>` intentionally exits zero because it can describe an
    # available package. Installed entries alone carry the numeric Files footer.
    count="$(printf '%s\n' "$out" \
        | sed -nE 's/^[[:space:]]*Files:[[:space:]]*([0-9]+)[[:space:]]*$/\1/p')"
    if [[ ! "$count" =~ ^[0-9]+$ ]] || [ "$count" -eq 0 ]; then
        check_fail "pkm/info" "pkm info did not confirm installed marker '$marker'"
        return
    fi
    check_pass "pkm/info" "marker package $marker present"
}

check_pkm_files_marker() {
    local marker="${1:-$SMOKE_PKM_MARKER}" out rc=0 count
    out="$("$SMOKE_PKM" files "$marker" 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ]; then
        check_fail "pkm/files" "pkm files $marker failed"
        return
    fi
    count="$(printf '%s\n' "$out" \
        | sed -nE "s/^[[:space:]]*Files in ${marker//\//\\/} \\(([0-9]+)\\):[[:space:]]*$/\\1/p")"
    if [[ ! "$count" =~ ^[0-9]+$ ]] || [ "$count" -eq 0 ]; then
        check_fail "pkm/files" "pkm files did not confirm tracked paths for marker '$marker'"
        return
    fi
    check_pass "pkm/files" "$marker owns $count tracked paths"
}

run_pkm_checks() {
    if ! command -v "$SMOKE_PKM" >/dev/null 2>&1; then
        check_fail "pkm/category" "pkm not in PATH — installed-system package health cannot be checked"
        return
    fi
    check_pkm_list
    check_pkm_verify
    check_pkm_info_marker
    check_pkm_files_marker
}
