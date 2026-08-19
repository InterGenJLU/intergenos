#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# installer/smoke/lib.sh — shared helpers for the smoke-test framework.
#
# Sourced by smoke-test.sh (the orchestrator) and made available to each
# check module under installer/smoke/checks/. Provides the check-runner
# emit functions, output formatting, and tally state.

# ---------------------------------------------------------------------------
# Tally state — one entry per check, append-only.
# Each entry: "<status>|<id>|<message>" where status ∈ {PASS,FAIL,WARN,SKIP}
# ---------------------------------------------------------------------------
SMOKE_RESULTS=()

# ---------------------------------------------------------------------------
# Output mode flags — set by orchestrator from CLI args.
# ---------------------------------------------------------------------------
SMOKE_VERBOSE="${SMOKE_VERBOSE:-0}"
SMOKE_JSON="${SMOKE_JSON:-0}"
SMOKE_STRICT="${SMOKE_STRICT:-0}"

# ---------------------------------------------------------------------------
# emit functions — call these from check modules. Append to SMOKE_RESULTS
# and (in non-JSON mode) print one line immediately so the user sees
# progress on long-running checks.
# ---------------------------------------------------------------------------
check_pass() {
    local id="$1" msg="${2:-}"
    SMOKE_RESULTS+=("PASS|${id}|${msg}")
    [ "$SMOKE_JSON" = "1" ] || printf '[\033[32mPASS\033[0m] %-22s : %s\n' "$id" "$msg"
}

check_fail() {
    local id="$1" msg="${2:-}"
    SMOKE_RESULTS+=("FAIL|${id}|${msg}")
    [ "$SMOKE_JSON" = "1" ] || printf '[\033[31mFAIL\033[0m] %-22s : %s\n' "$id" "$msg"
}

check_warn() {
    local id="$1" msg="${2:-}"
    SMOKE_RESULTS+=("WARN|${id}|${msg}")
    [ "$SMOKE_JSON" = "1" ] || printf '[\033[33mWARN\033[0m] %-22s : %s\n' "$id" "$msg"
}

check_skip() {
    local id="$1" msg="${2:-}"
    SMOKE_RESULTS+=("SKIP|${id}|${msg}")
    [ "$SMOKE_JSON" = "1" ] || printf '[\033[90mSKIP\033[0m] %-22s : %s\n' "$id" "$msg"
}

# ---------------------------------------------------------------------------
# smoke_live_media() — true when this session was booted from the install
# medium rather than from an installed system.
#
# The live boot puts `igos.mode=` on the kernel command line (live |
# install-gui | install-tui — installer/init/init.sh dispatches on it); an
# installed system never carries it. Checks that assert state which only an
# INSTALL produces must consult this and SKIP with a stated reason, because
# on live media the state is absent by design and a FAIL there says the
# system is broken when it is not.
#
# Measured need: the pre-install evaluation of the first release candidate
# ran this harness on the booted medium and got 28 PASS / 1 FAIL / 4 WARN /
# 11 SKIP, where the single FAIL was integrity/self-reval — a check on
# /var/lib/igos/manifest, a directory the installer creates. A false FAIL in
# a pre-install verdict costs the reading of every other line in it.
#
# A SKIP is the loud form here: it prints its own line and is counted in the
# summary, so the check is visibly not-applicable rather than absent.
# ---------------------------------------------------------------------------
# Injection point (same shape as the hardware category's SMOKE_HW_*): the
# tests point this at a fixture so they never assert the suite host's own
# boot state.
SMOKE_CMDLINE="${SMOKE_CMDLINE:-/proc/cmdline}"

smoke_live_media() {
    grep -q 'igos\.mode=' "$SMOKE_CMDLINE" 2>/dev/null
}

# ---------------------------------------------------------------------------
# verbose() — print only when -v supplied. Used inside check bodies for
# diagnostic context that's noisy in default mode.
# ---------------------------------------------------------------------------
verbose() {
    [ "$SMOKE_VERBOSE" = "1" ] && printf '       %s\n' "$*" >&2 || true
}

# ---------------------------------------------------------------------------
# summary() — emit final tally. Called by orchestrator after all checks.
# Exit codes (return value, not exit):
#   0 — all PASS or only WARN/SKIP
#   1 — at least one FAIL
# ---------------------------------------------------------------------------
summary() {
    local pass=0 fail=0 warn=0 skip=0
    for r in "${SMOKE_RESULTS[@]}"; do
        case "${r%%|*}" in
            PASS) pass=$((pass+1));;
            FAIL) fail=$((fail+1));;
            WARN) warn=$((warn+1));;
            SKIP) skip=$((skip+1));;
        esac
    done

    if [ "$SMOKE_JSON" = "1" ]; then
        emit_json "$pass" "$fail" "$warn" "$skip"
    else
        printf '\n==== SUMMARY: %d PASS / %d FAIL / %d WARN / %d SKIP ====\n' \
            "$pass" "$fail" "$warn" "$skip"
    fi

    [ "$fail" -eq 0 ]
}

# ---------------------------------------------------------------------------
# emit_json() — machine-parseable structured output. Used by CI/scripts.
# Hand-rolled JSON (no jq dependency); sanitization escapes the message
# field's quotes and backslashes.
# ---------------------------------------------------------------------------
emit_json() {
    local pass="$1" fail="$2" warn="$3" skip="$4"
    printf '{\n  "checks": [\n'
    local first=1
    for r in "${SMOKE_RESULTS[@]}"; do
        local status="${r%%|*}"
        local rest="${r#*|}"
        local id="${rest%%|*}"
        local msg="${rest#*|}"
        msg="${msg//\\/\\\\}"
        msg="${msg//\"/\\\"}"
        [ $first -eq 1 ] || printf ',\n'
        first=0
        printf '    {"status": "%s", "id": "%s", "message": "%s"}' \
            "$status" "$id" "$msg"
    done
    printf '\n  ],\n  "summary": {"pass": %d, "fail": %d, "warn": %d, "skip": %d}\n}\n' \
        "$pass" "$fail" "$warn" "$skip"
}
