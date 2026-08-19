#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-k21f-runtime.sh — K21.F runtime helper-smoke compliance gate.
#
# S-D 4 (USA-1 audit): chroot-post-build syntactic smoke check on the
# pkm install-helper surface. Companion to scripts/check-h007-runtime.sh
# (presence + sourcing); this gate verifies each shipped helper script
# is SYNTACTICALLY VALID and uses the manifest API.
#
# A helper script that fails `bash -n` parse will silently fail when a
# user runs `pkm install-helper chrome` — the user only learns the
# helper is broken when they need it. Same for helper-lib.sh itself:
# a syntax regression in the library breaks every helper. The smoke
# gate catches both before squashfs assembly.
#
# Label note: this gate is named "K21.F-runtime" per the USA-1 audit's
# S-D 4 runtime-gate enumeration (2026-05-22 validation walk). The label "K21.F" is
# also used by docs/audit/2026-05-18-comprehensive-state-audit.md for
# supply-chain hardening waves 1/2 (helper apt-Release/gpg/sha256 chain),
# AND by earlier internal tracking for DCO Signed-off-by GitHub Actions enforcement —
# THIS gate covers neither of those; it covers HELPER SCRIPT SYNTACTIC
# SMOKE specifically. The label is preserved for traceability against
# the S-D 4 walk-6 enumeration.
#
# Auto-pass: if no helper-lib AND no helper scripts are present, the
# chroot has no helper infrastructure at all — nothing to smoke-test.
#
# Run during phase_squashfs alongside D-007/D-008/D-010/D-011/H-007.
#
# Usage:
#   scripts/check-k21f-runtime.sh <chroot-root>
#
# Exit codes:
#   0 — no violations found
#   1 — one or more violations found
#   2 — script invocation error
#
# Source-of-truth: step 6 of 6 of the every-claim-validation review. Pairs
# with scripts/check-h007-runtime.sh (step 5).

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

echo "K21.F runtime helper-smoke gate"
echo "  chroot: $CHROOT_ROOT"

HELPER_LIB="$CHROOT_ROOT/usr/share/igos/helpers/helper-lib.sh"
declare -a HELPERS
mapfile -t HELPERS < <(find "$CHROOT_ROOT/usr/bin" -maxdepth 1 -type f -name 'igos-install-*' 2>/dev/null | sort)

# Auto-pass — no helper infrastructure to smoke-test.
if [ ! -f "$HELPER_LIB" ] && [ "${#HELPERS[@]}" -eq 0 ]; then
    green "No helper infrastructure in chroot — K21.F runtime smoke gate auto-passes."
    exit 0
fi

# Gate A — helper-lib.sh passes bash -n syntax check.
header "Gate A — helper-lib.sh parses cleanly (bash -n)"
if [ ! -f "$HELPER_LIB" ]; then
    # Helpers present but no library — this is an H-007 violation; we
    # surface it here too rather than silently skip, since downstream gates
    # depend on the library.
    violation "helper-lib.sh missing in chroot (also an H-007 violation)" \
              "Expected $HELPER_LIB."
else
    if PARSE=$(bash -n "$HELPER_LIB" 2>&1); then
        green "PASS — helper-lib.sh parses cleanly"
    else
        violation "helper-lib.sh fails bash -n parse" \
                  "Output: $PARSE"
    fi
fi

# Gate B — every helper script has a shebang on the first line.
# A helper without a shebang relies on the kernel's binfmt fallback;
# in a chroot/initramfs context this can silently fail or execute under
# the wrong interpreter.
header "Gate B — every helper has a #!/bin/(ba)?sh shebang"
if [ "${#HELPERS[@]}" -eq 0 ]; then
    green "PASS — no helper scripts present (vacuous)"
else
    declare -i GATE_B_VIOLATIONS=0
    for h in "${HELPERS[@]}"; do
        first_line=$(head -n1 "$h" 2>/dev/null)
        case "$first_line" in
            '#!/bin/bash'|'#!/bin/sh'|'#!/usr/bin/env bash'|'#!/usr/bin/env sh')
                : ;;
            *)
                violation "${h#$CHROOT_ROOT} missing valid shebang on line 1" \
                          "Got: $first_line"
                GATE_B_VIOLATIONS=$((GATE_B_VIOLATIONS + 1))
                ;;
        esac
    done
    if [ "$GATE_B_VIOLATIONS" -eq 0 ]; then
        green "PASS — all ${#HELPERS[@]} helper(s) have a valid shebang"
    fi
fi

# Gate C — every helper passes bash -n syntax check.
# This is the core smoke check — catches typos, missing fi/done/}'s,
# unclosed quotes, malformed heredocs, etc. that would silently break
# the helper at user invoke time.
header "Gate C — every helper parses cleanly (bash -n)"
if [ "${#HELPERS[@]}" -eq 0 ]; then
    green "PASS — no helper scripts present (vacuous)"
else
    declare -i GATE_C_VIOLATIONS=0
    for h in "${HELPERS[@]}"; do
        if PARSE=$(bash -n "$h" 2>&1); then
            :
        else
            violation "${h#$CHROOT_ROOT} fails bash -n parse" \
                      "Output: $PARSE"
            GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
        fi
    done
    if [ "$GATE_C_VIOLATIONS" -eq 0 ]; then
        green "PASS — all ${#HELPERS[@]} helper(s) parse cleanly"
    fi
fi

# Gate D — every helper actually USES the manifest API (calls
# igos_helper_init + igos_helper_commit at minimum).
#
# A helper that sources helper-lib.sh but never calls the API is a
# subtler H-007 regression: the library is loaded but no manifest gets
# written, so pkm._run_helper sees no file list and add_files() is
# never called. Same end-state as never sourcing the library — files
# untracked.
header "Gate D — every helper calls igos_helper_init + igos_helper_commit"
if [ "${#HELPERS[@]}" -eq 0 ]; then
    green "PASS — no helper scripts present (vacuous)"
else
    declare -i GATE_D_VIOLATIONS=0
    for h in "${HELPERS[@]}"; do
        if ! grep -qE '\bigos_helper_init\b' "$h"; then
            violation "${h#$CHROOT_ROOT} does not call igos_helper_init" \
                      "Manifest API requires init to record package name + open the manifest."
            GATE_D_VIOLATIONS=$((GATE_D_VIOLATIONS + 1))
            continue
        fi
        if ! grep -qE '\bigos_helper_commit\b' "$h"; then
            violation "${h#$CHROOT_ROOT} does not call igos_helper_commit" \
                      "Manifest API requires commit to flush the manifest to /var/lib/igos/helpers/."
            GATE_D_VIOLATIONS=$((GATE_D_VIOLATIONS + 1))
        fi
    done
    if [ "$GATE_D_VIOLATIONS" -eq 0 ]; then
        green "PASS — all ${#HELPERS[@]} helper(s) call init + commit"
    fi
fi

# Summary.
header "K21.F runtime helper-smoke compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — K21.F runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "Source-of-truth: S-D 4 walk 6 of 6 — helper-script syntactic smoke + manifest-API usage"
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
