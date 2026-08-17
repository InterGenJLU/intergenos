#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# installer/smoke/checks/gaming.sh — Category 5: GE composed-path proof (RT-2).
#
# Component canaries (vulkaninfo32 on the bare host) prove the HOST stack
# only. Steam games run inside Valve's pressure-vessel container, which
# capsule-captures the host graphics stack into a foreign Debian-based
# runtime — the single most distro-specific, most-likely-to-break step in
# the whole GE arc, and the one no component canary exercises. This
# category runs Valve's own steam-runtime-system-info INSIDE that container
# and asserts the imported 32- and 64-bit stacks resolve there
# (ge-composed-path-assert.py carries the assertions, fail-closed).
#
# Expectation gating: the whole category keys on the `gaming` meta-package
# being installed (the GE surface is mirror-only — RT-11). Not installed ->
# one explicit SKIP, never silence. Installed but the Steam runtime not yet
# present (Steam fetches it on first launch) -> WARN by default, FAIL under
# --strict; THE GE EVAL BAR (the RDNA4 boot-proof) RUNS THIS STRICT — a
# composed path that cannot be probed there is a failure, not a shrug.

SRSI_TIMEOUT="${SRSI_TIMEOUT:-120}"

_ge_steam_root() {
    # Valve's two conventional homes; first hit wins.
    local cand
    for cand in "$HOME/.steam/root" "$HOME/.local/share/Steam"; do
        [ -d "$cand/steamapps" ] && { echo "$cand"; return 0; }
    done
    return 1
}

# The Steam Linux Runtime GENERATION is per-tool, not fixed: Proton 11 and
# GE-Proton11 require SLR 4.0 (require_tool_appid 4183110); Proton Hotfix /
# Experimental require sniper (1628350). A hardcoded "sniper" locator was
# blind to the SLR-4 path the GE eval actually exercises (measured on-metal
# 2026-07-08). Enumerate every known generation's container entry point that
# is present, newest first; the caller probes each and fail-closes if none.
_GE_SLR_GENERATIONS="SteamLinuxRuntime_4 SteamLinuxRuntime_sniper SteamLinuxRuntime_soldier"

_ge_present_runtimes() {
    # Emit one "<generation-dir> <entry-point>" line per installed generation
    # whose _v2-entry-point is executable.
    local steam_root="$1" gen entry
    for gen in $_GE_SLR_GENERATIONS; do
        entry="$steam_root/steamapps/common/$gen/_v2-entry-point"
        [ -x "$entry" ] && printf '%s %s\n' "$gen" "$entry"
    done
}

check_gaming_composed_path() {
    # 1. Is the GE surface expected on this box at all?
    if ! pkm info gaming >/dev/null 2>&1; then
        check_skip "gaming/composed-path" \
            "gaming meta not installed — GE surface not expected on this box"
        return
    fi

    # 2. GE expected: locate the container entry point(s). Probe EVERY
    #    installed runtime generation (per-tool, not a hardcoded "sniper"),
    #    fail-closed if NONE is present.
    local steam_root
    if ! steam_root="$(_ge_steam_root)"; then
        if [ "$SMOKE_STRICT" = "1" ]; then
            check_fail "gaming/composed-path" \
                "gaming meta installed but no Steam library found — the composed path CANNOT be proven (GE eval bar requires it green)"
        else
            check_warn "gaming/composed-path" \
                "gaming meta installed but no Steam library yet — composed path NOT PROVEN (install/run Steam once; the GE eval runs this strict)"
        fi
        return
    fi
    local runtimes
    runtimes="$(_ge_present_runtimes "$steam_root")"
    if [ -z "$runtimes" ]; then
        if [ "$SMOKE_STRICT" = "1" ]; then
            check_fail "gaming/composed-path" \
                "gaming meta installed but no Steam Linux Runtime present (probed SLR 4.0 / sniper / soldier) — the composed path CANNOT be proven"
        else
            check_warn "gaming/composed-path" \
                "gaming meta installed but no Steam Linux Runtime downloaded yet — composed path NOT PROVEN (launch a Proton title once; the GE eval runs this strict)"
        fi
        return
    fi

    # 3. THE proof, in EACH present generation: srsi INSIDE its pressure-vessel
    #    container, asserted fail-closed by the shipped assertor. Every failure
    #    mode (timeout, nonzero exit, malformed JSON, failed assertion) lands as
    #    FAIL naming the generation + stage — a gate that cannot see must halt.
    #    PASS only when every probed generation resolves.
    local gen entry report rc assert_out proved=0
    while read -r gen entry; do
        [ -n "$gen" ] || continue
        rc=0
        verbose "running ($gen): $entry --verb=waitforexitandrun -- steam-runtime-system-info --verbose"
        report="$(timeout "$SRSI_TIMEOUT" "$entry" --verb=waitforexitandrun -- \
                  steam-runtime-system-info --verbose 2>/dev/null)" || rc=$?
        if [ $rc -eq 124 ]; then
            check_fail "gaming/composed-path" \
                "[$gen] in-container system-info timed out after ${SRSI_TIMEOUT}s — container startup/composition is broken"
            return
        fi
        if [ $rc -ne 0 ] || [ -z "$report" ]; then
            check_fail "gaming/composed-path" \
                "[$gen] in-container system-info failed (rc=$rc) — pressure-vessel could not compose the runtime"
            return
        fi
        if ! assert_out="$(printf '%s' "$report" | \
                python3 "${SCRIPT_DIR}/ge-composed-path-assert.py" 2>&1)"; then
            check_fail "gaming/composed-path" \
                "[$gen] $(echo "$assert_out" | grep -v '^composed-path: info' | head -4 | tr '\n' ';')"
            return
        fi
        proved=$((proved + 1))
    done <<EOF
$runtimes
EOF
    check_pass "gaming/composed-path" \
        "32- and 64-bit Vulkan stacks resolve inside the pressure-vessel container ($proved runtime generation(s) proved)"
}

run_gaming_checks() {
    check_gaming_composed_path
}
