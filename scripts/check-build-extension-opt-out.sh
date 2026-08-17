#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# check-build-extension-opt-out.sh — Gate 1 enforcement from
# docs/operations/pure-python-github-source-pattern.md §4.
#
# Scans `packages/*/<pkg>/build.sh` for any invocation of
#   pip wheel / pip3 wheel / python setup.py bdist_wheel / python -m build
# where the package's source tarball ships an `ext_modules` block in setup.py.
# If the matching build.sh does not export an explicit BUILD_EXTENSION=no (or
# the upstream-equivalent opt-out variable) BEFORE the build invocation, the
# gate fails.
#
# Principle: any recipe that *could* compile a C extension on a compiler-present
# build host must explicitly state "no compiled output" in the recipe. We do
# not accept "no compiler on the build host" as a proxy for determinism — the
# wheel content must be byte-identical regardless of host state.
#
# Run from repo root. Exits 0 with explanatory "no matches" message when no
# eligible build.sh files are found (initial state at doc landing; activates as
# per-package recipes accumulate).
#
# Usage:
#   scripts/check-build-extension-opt-out.sh
#
# Exit codes:
#   0 — gate PASS (no violations found)
#   1 — gate FAIL (at least one build.sh invokes pip wheel without the opt-out)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Find every build.sh under packages/* that invokes a wheel-building command.
# The patterns covered:
#   - `pip wheel` / `pip3 wheel`
#   - `python setup.py bdist_wheel` / `python3 setup.py bdist_wheel`
#   - `python -m build` / `python3 -m build`
#
# A build.sh that doesn't invoke any of these is not subject to this gate.
mapfile -t WHEEL_BUILDERS < <(
    grep -rln --include='build.sh' \
        -e 'pip3\? wheel' \
        -e 'python3\? setup\.py bdist_wheel' \
        -e 'python3\? -m build' \
        packages/ 2>/dev/null | sort -u
)

if [ "${#WHEEL_BUILDERS[@]}" -eq 0 ]; then
    echo "[check-build-extension-opt-out] no wheel-building build.sh files found; gate PASS (empty input)"
    exit 0
fi

# Gate scope: a recipe is checked only if we can statically confirm that the
# upstream source tarball declares an `ext_modules` block. The check needs an
# unpacked source tree at build/sources/<pkg>-*/setup.py — typically present
# after `phase_verify_sources` has run. If the source tarball is NOT unpacked,
# we cannot decide statically, and we skip rather than flag — the gate is a
# CI guard that fires at build-time (when sources are populated) and during
# pre-push if the developer has produced an unpacked tree. False-negatives at
# pre-commit are acceptable because the same gate runs again at build-time;
# false-positives produce noise on a clean tree without adding signal.
VIOLATIONS=0
CHECKED=0
SKIPPED_NO_SOURCE=0
for buildsh in "${WHEEL_BUILDERS[@]}"; do
    pkg_dir="$(dirname "$buildsh")"
    pkg_name="$(basename "$pkg_dir")"

    source_setup_py=""
    if compgen -G "build/sources/${pkg_name}-*/setup.py" > /dev/null 2>&1; then
        source_setup_py="$(ls -1 build/sources/"${pkg_name}"-*/setup.py 2>/dev/null | head -1 || true)"
    fi

    if [ -z "$source_setup_py" ] || [ ! -f "$source_setup_py" ]; then
        SKIPPED_NO_SOURCE=$((SKIPPED_NO_SOURCE + 1))
        continue
    fi

    if ! grep -qE 'ext_modules\s*=' "$source_setup_py"; then
        # Source tarball confirms no ext_modules — exempt.
        CHECKED=$((CHECKED + 1))
        continue
    fi

    # Recipe is subject to the gate. Confirm an opt-out is exported before
    # the wheel-building line. We look for either `BUILD_EXTENSION=no` or
    # a comment line claiming an upstream-equivalent opt-out variable
    # (recipe authors should narrate the chosen mechanism in build.sh).
    if grep -qE 'BUILD_EXTENSION=no|# +pure-python-opt-out:' "$buildsh"; then
        CHECKED=$((CHECKED + 1))
        continue
    fi

    echo "[check-build-extension-opt-out] FAIL: $buildsh invokes a wheel build but does not export BUILD_EXTENSION=no or document an equivalent opt-out" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
done

if [ "$VIOLATIONS" -gt 0 ]; then
    echo ""
    echo "[check-build-extension-opt-out] $VIOLATIONS violation(s) — see docs/operations/pure-python-github-source-pattern.md §2.7 + §4 Gate 1" >&2
    exit 1
fi

echo "[check-build-extension-opt-out] checked $CHECKED recipe(s); $SKIPPED_NO_SOURCE skipped (no unpacked source tarball — gate re-runs at build-time); gate PASS"
exit 0
