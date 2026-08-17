#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-h007-runtime.sh — H-007 runtime compliance gate.
#
# S-D 4 (USA-1 audit): chroot-post-build verification of the H-007
# audit-row closure. H-007 documented that pkm/installer.py's
# _run_helper path never called add_files() or add_depends(), so the
# 8 *-helper packages (chrome, vscode, edge, brave, discord, spotify,
# claude-code, ffmpeg-nonfree) deposited files into /opt/, /usr/bin/
# etc. with zero pkm tracking. The fix shipped
# packages/core/intergenos-helper-lib + a manifest-spec contract: each
# /usr/bin/igos-install-<name> script sources helper-lib.sh, records
# its install footprint via the API, and pkm._run_helper reads the
# resulting /var/lib/igos/helpers/<name>.manifest to thread file paths
# through PackageDB.add_files / add_depends.
#
# This runtime gate verifies the infrastructure is actually deployed
# AND that every installed helper script consumes it. A code change
# could land helper-lib in source but ship a chroot whose helpers
# never source it (orphan helpers — files untracked again, H-007
# regression).
#
# Auto-pass: if neither helper-lib NOR any helper scripts are present,
# the chroot has no helper infrastructure — fully-absent state, nothing
# to verify. If either half is present, both must be present + consistent.
#
# Run during phase_squashfs alongside D-007/D-008/D-010/D-011 runtime gates.
#
# Usage:
#   scripts/check-h007-runtime.sh <chroot-root>
#
# Exit codes:
#   0 — no violations found
#   1 — one or more violations found
#   2 — script invocation error
#
# Source-of-truth: docs/audit/2026-05-18-comprehensive-state-audit.md H-007
# Manifest spec: docs/architecture/helper-manifest-spec-v1.md

set -uo pipefail

CHROOT_ROOT="${1:-}"
[ -n "$CHROOT_ROOT" ] || { echo "FATAL: chroot-root argument required (e.g. /mnt/igos)" >&2; exit 2; }
[ -d "$CHROOT_ROOT" ] || { echo "FATAL: chroot-root does not exist: $CHROOT_ROOT" >&2; exit 2; }
CHROOT_ROOT="$(cd "$CHROOT_ROOT" && pwd)"

declare -i VIOLATIONS=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
header() { printf '\n=== %s ===\n' "$*"; }

violation() {
    red "VIOLATION: $1"
    [ -n "${2:-}" ] && printf '  %s\n' "$2"
    VIOLATIONS=$((VIOLATIONS + 1))
}

echo "H-007 runtime gate"
echo "  chroot: $CHROOT_ROOT"

HELPER_LIB="$CHROOT_ROOT/usr/share/igos/helpers/helper-lib.sh"
MANIFEST_DIR="$CHROOT_ROOT/var/lib/igos/helpers"

# Discover helper scripts: /usr/bin/igos-install-* by convention (per
# packages/extra/*-helper/build.sh install lines).
declare -a HELPERS
mapfile -t HELPERS < <(find "$CHROOT_ROOT/usr/bin" -maxdepth 1 -type f -name 'igos-install-*' 2>/dev/null | sort)

# Auto-pass: neither helper-lib nor helper scripts present → fully-absent
# state, nothing to verify.
if [ ! -f "$HELPER_LIB" ] && [ "${#HELPERS[@]}" -eq 0 ]; then
    green "No helper infrastructure in chroot — H-007 runtime gate auto-passes (nothing to verify)."
    exit 0
fi

# Gate A — helper-lib.sh deployed at canonical path.
header "Gate A — /usr/share/igos/helpers/helper-lib.sh deployed"
if [ ! -f "$HELPER_LIB" ]; then
    violation "helper-lib.sh missing in chroot" \
              "Expected $HELPER_LIB. Without the library, every helper script breaks on first source — H-007 regression."
    # Hard early-exit: every downstream gate depends on the library being
    # present (Gate C greps each helper for the source line; without the
    # library the source itself is meaningless).
    red "FAILED — cannot evaluate Gates B-C without helper-lib.sh."
    yellow "Source-of-truth: docs/audit/2026-05-18-comprehensive-state-audit.md H-007"
    exit 1
fi
# Spot-check the deployed library carries the expected API surface.
declare -i GATE_A_VIOLATIONS=0
for fn in igos_helper_init igos_helper_record_file igos_helper_record_symlink igos_helper_commit; do
    if ! grep -qE "^${fn}[[:space:]]*\(\)" "$HELPER_LIB"; then
        violation "helper-lib.sh missing API function: $fn" \
                  "Deployed library does not define the H-007 manifest API; helpers will fail at call site."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    fi
done
if [ "$GATE_A_VIOLATIONS" -eq 0 ]; then
    green "PASS — helper-lib.sh deployed with full API surface"
fi

# Gate B — /var/lib/igos/helpers/ manifest directory exists.
# packages/core/intergenos-helper-lib/build.sh:66 creates this; without
# it, igos_helper_commit's manifest write fails.
header "Gate B — /var/lib/igos/helpers/ manifest directory"
if [ ! -d "$MANIFEST_DIR" ]; then
    violation "$MANIFEST_DIR missing in chroot" \
              "intergenos-helper-lib build.sh creates this; without it, manifest writes fail."
else
    green "PASS — manifest directory deployed"
fi

# Gate C — every installed helper script sources helper-lib.sh.
# Orphan helpers (don't source the library) re-introduce the H-007 gap:
# install runs, files land on disk, pkm has zero record of them.
header "Gate C — every helper sources helper-lib.sh (no orphan helpers)"
if [ "${#HELPERS[@]}" -eq 0 ]; then
    green "PASS — no helper scripts present (vacuously no orphans)"
else
    declare -i GATE_C_VIOLATIONS=0
    for h in "${HELPERS[@]}"; do
        # Match `source /usr/share/igos/helpers/helper-lib.sh` (canonical
        # form per the manifest spec) OR `. /usr/share/igos/helpers/
        # helper-lib.sh` (POSIX equivalent).
        if ! grep -qE '^[[:space:]]*(source|\.)[[:space:]]+/usr/share/igos/helpers/helper-lib\.sh' "$h"; then
            violation "orphan helper script: ${h#$CHROOT_ROOT}" \
                      "Does not source /usr/share/igos/helpers/helper-lib.sh — H-007 regression (files would land untracked)."
            GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
        fi
    done
    if [ "$GATE_C_VIOLATIONS" -eq 0 ]; then
        green "PASS — all ${#HELPERS[@]} helper script(s) source helper-lib.sh"
    fi
fi

# Summary.
header "H-007 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — H-007 runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "Source-of-truth: docs/audit/2026-05-18-comprehensive-state-audit.md H-007"
    yellow "Manifest spec: docs/architecture/helper-manifest-spec-v1.md"
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
