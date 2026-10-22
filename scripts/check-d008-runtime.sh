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

# The reader of the deployed policy and registry that Gates A, C and F share
# with the source gate: the policy as PolicyKit reads it, the registry's values
# as the dispatcher holds them. The runtime gate runs from the build tree, so the
# checker sits beside it in scripts/lib/.
POLICY_CHECK="$(cd "$(dirname "$0")" && pwd)/lib/check-privileged-runner-policy.py"

# Gate A — PolicyKit policy file deployed + structurally valid.
header "Gate A — PolicyKit policy at /usr/share/polkit-1/actions/org.intergenos.intergen.policy"
POLICY_PATH="$CHROOT_ROOT/usr/share/polkit-1/actions/org.intergenos.intergen.policy"
declare -i GATE_A_VIOLATIONS=0
if [ ! -f "$POLICY_PATH" ]; then
    violation "PolicyKit policy file missing in chroot" \
              "Expected $POLICY_PATH (build.sh:187-188 install line)."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
else
    # The action ids as PolicyKit reads the deployed file (the checker's
    # --action-ids), never a grep, which a comment or a skipped element satisfies.
    if [ -f "$POLICY_CHECK" ] && ! GATE_A_IDS=$(python3 -I "$POLICY_CHECK" --action-ids \
            --policy "$POLICY_PATH" 2>&1); then
        printf '%s\n' "$GATE_A_IDS" | sed 's/^/  /'
        violation "PolicyKit would not read the deployed policy file as written" \
                  "The lines above name where; its action ids are read as PolicyKit reads the file."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    elif [ -f "$POLICY_CHECK" ] \
            && ! printf '%s\n' "$GATE_A_IDS" | grep -qE '^org\.intergenos\.intergen\.privileged-tool(\.|$)'; then
        violation "PolicyKit action id missing in deployed policy file" \
                  "Expected action id 'org.intergenos.intergen.privileged-tool' (or its per-tool ids) among the actions PolicyKit reads."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    fi
    # One administrator authentication per privileged call, read from the
    # DEPLOYED policy and the DEPLOYED tool_registry.py by the checker the
    # source gate runs (scripts/lib/check-privileged-runner-policy.py): every
    # action bound to the runner is auth_admin in all three defaults (no keep)
    # with its own exec.argv1, and the argv1 set equals _PRIVILEGED_TOOLS. Until
    # 2026-10-22 this check required auth_admin_keep, which keeps one
    # authentication for five minutes for the parent every runner shares.
    if [ ! -f "$POLICY_CHECK" ]; then
        violation "cannot check the deployed policy: $POLICY_CHECK missing" \
                  "The runtime gate runs from the build tree; the checker sits beside it in scripts/lib/."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    elif ! GATE_A_OUT=$(python3 -I "$POLICY_CHECK" --policy "$POLICY_PATH" \
            --registry "$INTERGEN_PYROOT/tool_registry.py" 2>&1); then
        printf '%s\n' "$GATE_A_OUT" | sed 's/^/  /'
        violation "deployed PolicyKit policy does not require one administrator authentication per privileged call" \
                  "Every action bound to /usr/bin/intergen-privileged-runner must be auth_admin in allow_any, allow_inactive and allow_active (no keep), carry its own exec.argv1, and the argv1 set must equal the deployed _PRIVILEGED_TOOLS, read as PolicyKit reads the file, with no element it reads once appearing twice."
        GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
    else
        printf '%s\n' "$GATE_A_OUT" | sed 's/^/  /'
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
    # The runner path the deployed dispatcher hands pkexec, read by the checker
    # as the dispatcher holds it (one module-level definition), never a grep,
    # which a comment, a dead branch or a rebound name would satisfy.
    if ! GATE_C_RUNNER=$(python3 -I "$POLICY_CHECK" --runner-path \
            --registry "$INTERGEN_PYROOT/tool_registry.py" 2>&1); then
        printf '%s\n' "$GATE_C_RUNNER" | sed 's/^/  /'
        violation "cannot read _PKEXEC_RUNNER_PATH from the deployed tool_registry.py as the dispatcher holds it" \
                  "Privileged-tool routing path must match runtime install location; the lines above name why its value cannot be read from one module-level definition."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    elif [ "$GATE_C_RUNNER" != "/usr/bin/intergen-privileged-runner" ]; then
        violation "deployed tool_registry.py does not bind _PKEXEC_RUNNER_PATH to the canonical install path" \
                  "Privileged-tool routing path must match runtime install location; it binds $GATE_C_RUNNER."
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
    # Every action's exec.path as PolicyKit reads the file (the checker's reading,
    # --exec-paths), never the first match of a grep, which a comment or a
    # repeated annotation would decide.
    if ! POLICY_EXECS=$(python3 -I "$POLICY_CHECK" --exec-paths --policy "$POLICY_PATH" 2>&1); then
        printf '%s\n' "$POLICY_EXECS" | sed 's/^/  /'
        violation "could not read exec.path from deployed PolicyKit policy as PolicyKit reads it" \
                  "PolicyKit would not read the file as written; the lines above name where."
    elif [ -z "$POLICY_EXECS" ]; then
        violation "could not extract exec.path from deployed PolicyKit policy" \
                  "Policy file structurally malformed or missing exec.path annotation."
    elif ! printf '%s\n' "$POLICY_EXECS" | cut -f2 | grep -qx '/usr/bin/intergen-privileged-runner'; then
        violation "PolicyKit exec.path does not match canonical runner location" \
                  "Got: $(printf '%s\n' "$POLICY_EXECS" | cut -f2 | sort -u | tr '\n' ' ')expected: /usr/bin/intergen-privileged-runner."
    else
        green "PASS — PolicyKit exec.path matches deployed runner location"
    fi
else
    yellow "SKIP — Gate A or Gate B failure already counted; cross-check not meaningful"
fi

# Summary.
header "D-008 runtime compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "CHECKS PASS — files at $CHROOT_ROOT: runner/CLI present and executable, review/audit/log-rotation present; runner-bound policy defaults are auth_admin (no keep), and the exec.argv1 set matches _PRIVILEGED_TOOLS. Full D-008 runtime behavior is unverified. Squashfs assembly may proceed."
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
