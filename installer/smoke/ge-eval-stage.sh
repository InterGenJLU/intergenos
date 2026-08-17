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
#   3. pkm verify on the installed GE set (fail-closed)
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
# Exit: 0 = stage green; 1 = a named step failed; 2 = environment unusable.

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
for p in "${META}" ${GE_PKGS}; do
    pkm verify "$p" || fail "pkm verify ${p} failed — the installed GE set is not intact"
done

step "4/4 strict smoke battery (incl. the composed-path category, strict)"
SMOKE_STRICT=1 bash "${SCRIPT_DIR}/smoke-test.sh"
rc=$?
if [ $rc -ne 0 ]; then
    fail "strict smoke battery reported failures (rc=${rc}) — read the FAIL lines above"
fi

step "GE mirror-install eval stage GREEN"
exit 0
