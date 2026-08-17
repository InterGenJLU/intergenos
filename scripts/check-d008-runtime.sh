#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d008-runtime.sh — D-008 runtime compliance gate.
#
# S-D 4 (USA-1 audit): companion to scripts/check-d008-compliance.sh —
# the source-grep gate verifies the InterGen v1.0 minimum scope is
# AUTHORED in source; this runtime gate verifies the v1.0 minimum scope
# is ACTUALLY DEPLOYED in the assembled chroot at canonical paths.
#
# A code change could pass the source-grep gate but produce a chroot
# with (e.g.) a missing PolicyKit policy file because some build step
# silently failed to install it. The runtime gate is the second line
# of defense.
#
# Auto-pass: a build may omit InterGen entirely. If
# /usr/lib/python3.14/site-packages/intergen/ is absent from the chroot,
# the InterGen package was not installed in this build and the gate
# auto-passes (no architecture to verify).
#
# Run during phase_squashfs (after chroot is fully assembled, before
# squashfs assembly) alongside scripts/check-d007-runtime.sh.
#
# Usage:
#   scripts/check-d008-runtime.sh <chroot-root>
#   scripts/check-d008-runtime.sh /mnt/igos
#
# Exit codes:
#   0 — no violations found; squashfs assembly may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (missing chroot, wrong path, etc.)
#
# THE REQUIREMENT (D-008, decided 2026-05-18): a chroot that carries the
# InterGen AI assistant actually deploys the provenance-gated tool dispatcher
# — the PolicyKit policy, the pkexec runner, the provenance modules, the
# user-decision and audit-log artifacts, and the CLI wrapper all present at
# their canonical paths, with the runner's exec path matching the policy.
#
# Canonical design: docs/architecture/intergen-provenance-gate-design.md
# Source-tree half of the same requirement: scripts/check-d008-compliance.sh

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

echo "D-008 runtime gate"
echo "  chroot: $CHROOT_ROOT"

# Auto-pass trigger: InterGen package not installed in this chroot.
# The Python package root is the single canonical deployment marker —
# build.sh:32 always creates it via `install -dm755`. Absent means
# the AI tier was skipped or intergen explicitly excluded; per D-008
# line 348 the gate auto-passes (no architecture to verify).
INTERGEN_PYROOT="$CHROOT_ROOT/usr/lib/python3.14/site-packages/intergen"
if [ ! -d "$INTERGEN_PYROOT" ]; then
    green "InterGen not installed in chroot — D-008 runtime gate auto-passes (no architecture to verify)."
    exit 0
fi

# Gate A — PolicyKit policy file deployed + structurally valid.
header "Gate A — PolicyKit policy at /usr/share/polkit-1/actions/org.intergenos.intergen.policy"
POLICY_PATH="$CHROOT_ROOT/usr/share/polkit-1/actions/org.intergenos.intergen.policy"
declare -i GATE_A_VIOLATIONS=0
if [ ! -f "$POLICY_PATH" ]; then
    violation "PolicyKit policy file missing in chroot" \
              "Expected $POLICY_PATH (build.sh:187-188 install line)."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
else
    if ! grep -q "org.intergenos.intergen.privileged-tool" "$POLICY_PATH"; then
        violation "PolicyKit action id missing in deployed policy file" \
                  "Expected action id 'org.intergenos.intergen.privileged-tool'."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    fi
    if ! grep -q "auth_admin_keep" "$POLICY_PATH"; then
        violation "auth_admin_keep missing in deployed PolicyKit policy file" \
                  "D-008 composes with D-007 — privileged actions must use auth_admin_keep per-action authentication."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    fi
    if ! grep -q '/usr/bin/intergen-privileged-runner' "$POLICY_PATH"; then
        violation "exec.path annotation missing or wrong in deployed PolicyKit policy" \
                  "Expected exec.path '/usr/bin/intergen-privileged-runner'."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_A_VIOLATIONS" -eq 0 ]; then
    green "PASS — PolicyKit policy deployed + structurally valid"
fi

# Gate B — pkexec runner deployed + executable.
header "Gate B — pkexec runner at /usr/bin/intergen-privileged-runner"
RUNNER_PATH="$CHROOT_ROOT/usr/bin/intergen-privileged-runner"
declare -i GATE_B_VIOLATIONS=0
if [ ! -f "$RUNNER_PATH" ]; then
    violation "pkexec runner missing in chroot" \
              "Expected $RUNNER_PATH (build.sh:189-190 install line, mode 755)."
    GATE_B_VIOLATIONS=$((GATE_B_VIOLATIONS + 1))
elif [ ! -x "$RUNNER_PATH" ]; then
    violation "pkexec runner not executable in chroot" \
              "Expected mode 755 on $RUNNER_PATH."
    GATE_B_VIOLATIONS=$((GATE_B_VIOLATIONS + 1))
fi
if [ "$GATE_B_VIOLATIONS" -eq 0 ]; then
    green "PASS — pkexec runner deployed + executable"
fi

# Gate C — Provenance taxonomy + dispatcher + tool registry deployed.
header "Gate C — provenance modules deployed (interfaces/provenance.py + provenance.py + tool_registry.py + interfaces/types.py)"
declare -i GATE_C_VIOLATIONS=0
for f in \
    "$INTERGEN_PYROOT/interfaces/provenance.py" \
    "$INTERGEN_PYROOT/provenance.py" \
    "$INTERGEN_PYROOT/tool_registry.py" \
    "$INTERGEN_PYROOT/interfaces/types.py"; do
    if [ ! -f "$f" ]; then
        violation "Python module missing in chroot: ${f#$CHROOT_ROOT}" \
                  "D-008 v1.0 minimum requires the provenance gate + dispatcher + ToolCall surface."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
done
# Spot-check the dispatcher actually references the gate entry point — a
# byte-empty file would silently slip through plain existence checks.
if [ -f "$INTERGEN_PYROOT/provenance.py" ]; then
    if ! grep -q "def verify_tool_call" "$INTERGEN_PYROOT/provenance.py"; then
        violation "verify_tool_call() entry point missing in deployed intergen/provenance.py" \
                  "Deployed provenance.py does not contain the dispatcher gate entry point — incomplete artifact."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
fi
if [ -f "$INTERGEN_PYROOT/tool_registry.py" ]; then
    if ! grep -q '_PKEXEC_RUNNER_PATH[[:space:]]*=[[:space:]]*"/usr/bin/intergen-privileged-runner"' "$INTERGEN_PYROOT/tool_registry.py"; then
        violation "deployed tool_registry.py does not reference _PKEXEC_RUNNER_PATH at canonical install path" \
                  "Privileged-tool routing path must match runtime install location."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_C_VIOLATIONS" -eq 0 ]; then
    green "PASS — provenance modules deployed + entry points present"
fi

# Gate D — User-decision artifacts deployed (review_modal.py + audit_log.py).
header "Gate D — user-decision artifacts (review_modal.py + audit_log.py)"
declare -i GATE_D_VIOLATIONS=0
for f in \
    "$INTERGEN_PYROOT/review_modal.py" \
    "$INTERGEN_PYROOT/audit_log.py"; do
    if [ ! -f "$f" ]; then
        violation "Python module missing in chroot: ${f#$CHROOT_ROOT}" \
                  "D-008 v1.0 minimum requires the user-facing review modal + per-call audit log."
        GATE_D_VIOLATIONS=$((GATE_D_VIOLATIONS + 1))
    fi
done
if [ -f "$INTERGEN_PYROOT/audit_log.py" ]; then
    if ! grep -q "tool-dispatch.jsonl" "$INTERGEN_PYROOT/audit_log.py"; then
        violation "deployed audit_log.py does not reference tool-dispatch.jsonl" \
                  "D-008 RFC §9 requires the canonical audit log filename."
        GATE_D_VIOLATIONS=$((GATE_D_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_D_VIOLATIONS" -eq 0 ]; then
    green "PASS — user-decision artifacts deployed"
fi

# Gate E — CLI wrapper + audit-log rotation snippet.
# build.sh:46 deploys /usr/bin/intergen (mode 755); build.sh:199-200 deploys
# /etc/logrotate.d/intergen-tool-dispatch per D-008 RFC §14.3 (retention
# decided 2026-05-19).
header "Gate E — CLI wrapper + audit-log rotation snippet"
CLI_PATH="$CHROOT_ROOT/usr/bin/intergen"
LOGROTATE_PATH="$CHROOT_ROOT/etc/logrotate.d/intergen-tool-dispatch"
declare -i GATE_E_VIOLATIONS=0
if [ ! -f "$CLI_PATH" ]; then
    violation "InterGen CLI wrapper missing in chroot" \
              "Expected $CLI_PATH (build.sh:46 install line, mode 755)."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
elif [ ! -x "$CLI_PATH" ]; then
    violation "InterGen CLI wrapper not executable in chroot" \
              "Expected mode 755 on $CLI_PATH."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
fi
if [ ! -f "$LOGROTATE_PATH" ]; then
    violation "audit-log rotation snippet missing in chroot" \
              "Expected $LOGROTATE_PATH per D-008 RFC §14.3 (retention decided 2026-05-19)."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
fi
if [ "$GATE_E_VIOLATIONS" -eq 0 ]; then
    green "PASS — CLI wrapper + audit-log rotation snippet deployed"
fi

# Gate F — D-007 composition: pkexec runner exec.path matches PolicyKit policy.
# Cross-check that both halves of the pkexec pair agree on the same exec
# path. A drift here would mean pkexec authenticates against one binary
# while the dispatcher invokes another — would either fail at runtime or
# (worse) authenticate one path while executing a different one.
header "Gate F — D-007 composition: pkexec runner exec.path matches PolicyKit policy"
if [ -f "$POLICY_PATH" ] && [ -f "$RUNNER_PATH" ]; then
    POLICY_EXEC=$(grep -oE 'org\.freedesktop\.policykit\.exec\.path[^>]*>[^<]+</annotate>' "$POLICY_PATH" 2>/dev/null \
                  | sed -E 's|.*>([^<]+)</annotate>|\1|' | head -1)
    if [ -z "$POLICY_EXEC" ]; then
        violation "could not extract exec.path from deployed PolicyKit policy" \
                  "Policy file structurally malformed or missing exec.path annotation."
    elif [ "$POLICY_EXEC" != "/usr/bin/intergen-privileged-runner" ]; then
        violation "PolicyKit exec.path does not match canonical runner location" \
                  "Got: $POLICY_EXEC, expected: /usr/bin/intergen-privileged-runner."
    else
        green "PASS — PolicyKit exec.path matches deployed runner location"
    fi
else
    yellow "SKIP — Gate A or Gate B failure already counted; cross-check not meaningful"
fi

# Summary.
header "D-008 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-008 runtime verified against $CHROOT_ROOT. Squashfs assembly may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found in built chroot at $CHROOT_ROOT."
    yellow "The requirement: a chroot carrying InterGen deploys the provenance-gated tool"
    yellow "dispatcher in full — PolicyKit policy, pkexec runner, provenance modules,"
    yellow "user-decision and audit-log artifacts, and CLI wrapper, at their canonical"
    yellow "paths, with the runner's exec path matching the policy."
    yellow "Canonical design: docs/architecture/intergen-provenance-gate-design.md"
    yellow "Fix violations in the build pipeline and re-assemble the chroot."
    exit 1
fi
