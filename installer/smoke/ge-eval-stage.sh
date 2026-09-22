#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# installer/smoke/ge-eval-stage.sh — the GE mirror-install eval stage (RT-11).
#
# THE GAP THIS CLOSES: every GE package is mirror-only (iso_include: false),
# and the standing post-install eval evaluates what an ISO install produced —
# so WITHOUT this stage, no routine eval cycle ever executes a single GE
# canary; the composed-path gate, the 32-bit hello, vulkaninfo32 would all be
# dead code from day one (the GE redteam's RT-11 finding). This stage makes
# the mirror surface part of the eval, explicitly and fail-closed.
#
# WHAT IT DOES (on an installed, network-reachable InterGenOS box):
#   1. pkm sync                       — refresh the signed repo index
#   2. pkm install gaming             — the mirror-only GE meta (pulls the
#                                       lib32 closure per the meta's deps)
#   3. pkm verify on the installed GE set (fail-closed, reading the status by
#      number: absent, unverifiable and corrupt are three different answers)
#   4. SMOKE_STRICT=1 smoke-test      — the full check battery INCLUDING the
#                                       gaming composed-path category, which
#                                       runs STRICT here: a composed path
#                                       that cannot be probed is a FAILURE
#                                       at eval, never a shrug.
#
# Every step is fail-closed: a sync/install/verify failure aborts the stage
# with the step named; the smoke run's exit code is the stage's verdict.
# This script is part of the eval PROCESS (run by the coordinator/operator
# during a GE eval cycle, per the eval runbook); it is deliberately NOT a
# boot-time unit — installing packages is an eval action, not a boot action.
#
# Usage: sudo bash /usr/lib/intergenos/ge-eval-stage.sh [--meta <name>]
# Exit: 0 = stage green; 1 = a named step failed; 2 = environment unusable —
#       no pkm on the box, or a verify that could not run its checks (pkm
#       verify status 3), which certifies nothing either way.

set -uo pipefail

META="gaming"
[ "${1:-}" = "--meta" ] && META="${2:-gaming}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

step() { echo "[ge-eval] $*"; }
fail() { echo "[ge-eval] FAIL: $*" >&2; exit 1; }

command -v pkm >/dev/null 2>&1 || { echo "[ge-eval] no pkm — not an InterGenOS box?" >&2; exit 2; }

step "1/4 pkm sync (signed index refresh)"
pkm sync || fail "pkm sync failed — cannot trust the mirror state; refusing to install"

step "2/4 pkm install ${META} (mirror-only GE surface)"
pkm install "${META}" || fail "pkm install ${META} failed — the GE surface did not install; nothing to evaluate"

step "3/4 pkm verify the installed GE set"
# Targeted fast-fail: the meta + its DIRECTLY-declared deps. This step is
# NOT the closure guarantee — a transitively-pulled lib32 member is covered
# by step 4, whose strict smoke battery runs a full-system
# `pkm verify --strict --all` in its pkm category (the verifier's scope
# note, 2026-07-02). Confirm at meta-authoring that the gaming meta
# declares the flat lib32 set as direct deps — the mirror-only-meta
# convention — which also makes THIS step closure-complete on its own.
GE_PKGS="$(pkm info "${META}" 2>/dev/null | sed -n 's/^Depends:[[:space:]]*//p' | tr ',' ' ')"
# The loop reads pkm verify's status BY NUMBER, because the three outcomes are
# three different facts about the GE surface and an eval reader has to be able
# to tell them apart (the statuses are declared in pkm/cli.py's cmd_verify):
#   4 = the package is NOT INSTALLED. Nothing was verified. Until pkm release
#       95 an absent package exited 0 here, so this loop's `|| fail` passed a
#       member that never installed; now it is named as absent, which is a
#       different repair from a corrupt file.
#   3 = the check COULD NOT BE RUN — no fault was found, verify was prevented
#       from looking (usually files it may not read). The stage cannot certify
#       a set it was not allowed to check, and that is an unusable environment
#       (stage exit 2), never a fault report.
#   other non-zero = a real integrity failure; the status is printed so the
#       reader is not left guessing which one.
for p in "${META}" ${GE_PKGS}; do
    pkm verify "$p"
    vrc=$?
    case "${vrc}" in
        0) ;;
        4) fail "pkm verify ${p}: the package is NOT INSTALLED (status 4) — the GE surface is incomplete and nothing about ${p} was verified" ;;
        3) echo "[ge-eval] FAIL: pkm verify ${p}: verify could not complete its checks (status 3) — no fault was found, verify was prevented from reading what it needed; the stage cannot certify this set. Re-run as root." >&2
           exit 2 ;;
        *) fail "pkm verify ${p} failed with status ${vrc} — the installed GE set is not intact" ;;
    esac
done

step "4/4 strict smoke battery (incl. the composed-path category, strict)"
SMOKE_STRICT=1 bash "${SCRIPT_DIR}/smoke-test.sh"
rc=$?
if [ $rc -ne 0 ]; then
    fail "strict smoke battery reported failures (rc=${rc}) — read the FAIL lines above"
fi

step "GE mirror-install eval stage GREEN"
exit 0
