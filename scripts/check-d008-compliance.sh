#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d008-compliance.sh — D-008 compliance gate.
#
# THE REQUIREMENT (D-008, decided 2026-05-18): an ISO that includes the
# InterGen AI assistant may only ship it behind the provenance-gated tool
# dispatcher — every tool call carries where the request came from, calls
# that cross the trust boundary are routed through an explicit user decision
# with an audit record, and privileged work goes through a PolicyKit-guarded
# runner rather than direct escalation. This is a Class A ship-block that
# mirrors the D-007 gate pattern (compliance check script + phase_image hook
# + build-iso.sh hook).
#
# A build may omit InterGen entirely; the gate only fires when InterGen is
# included. The InterGen-side implementation landed at 7f966ab3 (dispatcher
# steps 1-12) + 49a585ca (pkexec runner).
#
# WHERE THE REQUIREMENT IS RECORDED IN THIS REPOSITORY:
#   docs/architecture/intergen-provenance-gate-design.md
#       the design this gate enforces, section by section;
#   intergen/interfaces/provenance.py + intergen/provenance.py
#       the provenance taxonomy and the verify_tool_call() gate itself;
#   intergen/tool_registry.py
#       the dispatch path that must route through that gate;
#   intergen/data/org.intergenos.intergen.policy
#       the PolicyKit policy the privileged runner is bound to;
#   scripts/check-d008-runtime.sh
#       the runtime half, which checks the assembled chroot.
#
# Run before phase_image (and any ISO-assembly path) in
# scripts/build-intergenos.sh + scripts/build-iso.sh. May also be invoked
# standalone:
#   scripts/check-d008-compliance.sh
#
# Exit codes:
#   0 — no violations found; build may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (wrong cwd, missing tooling, etc.)
#
# Canonical design: docs/architecture/intergen-provenance-gate-design.md

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

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

# Inclusion detection: the gate only fires when InterGen is included in the
# squashfs. At pre-build / source-tree time, the InterGen package being
# present in source AND not excluded from the build is the signal. A build
# can ship without InterGen by skipping the AI tier; in that case the source
# tree may still contain intergen/ but the squashfs won't. For Class A
# enforcement at this layer, we treat presence of intergen/ source as the
# trigger — architectural correctness is gated regardless of any particular
# ISO's manifest. (Source-tree absence of intergen/ entirely means the
# assistant has been removed from the project; the gate auto-passes then.)
if [ ! -d "intergen" ] || [ ! -d "packages/ai/intergen" ]; then
    green "InterGen not present in source tree — D-008 gate auto-passes (no architecture to verify)."
    exit 0
fi

# Gate A: Provenance taxonomy in interfaces + verify_tool_call gate in
# provenance.py logic module. Two files compose: interfaces/provenance.py
# defines the enum (RFC §3 taxonomy); top-level provenance.py implements
# the gate logic (RFC §6 dispatcher).
header "Gate A — Provenance taxonomy (interfaces) + verify_tool_call() gate (logic)"
declare -i GATE_A_VIOLATIONS=0
if [ ! -f "intergen/interfaces/provenance.py" ]; then
    violation "intergen/interfaces/provenance.py missing" \
              "D-008 v1.0 minimum requires the provenance taxonomy interface (RFC §3)."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
elif ! grep -q "^class Provenance" intergen/interfaces/provenance.py; then
    violation "Provenance enum not defined in intergen/interfaces/provenance.py" \
              "D-008 v1.0 minimum requires the three-category taxonomy (user_direct / user_implied / ingress_derived)."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
elif ! grep -qE "USER_DIRECT" intergen/interfaces/provenance.py \
   || ! grep -qE "USER_IMPLIED" intergen/interfaces/provenance.py \
   || ! grep -qE "INGRESS_DERIVED" intergen/interfaces/provenance.py; then
    violation "Provenance taxonomy categories incomplete in intergen/interfaces/provenance.py" \
              "All three categories (USER_DIRECT, USER_IMPLIED, INGRESS_DERIVED) must be defined."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
fi
if [ ! -f "intergen/provenance.py" ]; then
    violation "intergen/provenance.py missing" \
              "D-008 v1.0 minimum requires the gate-dispatcher logic module (RFC §6)."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
elif ! grep -q "def verify_tool_call" intergen/provenance.py; then
    violation "verify_tool_call() function missing in intergen/provenance.py" \
              "D-008 v1.0 minimum requires the dispatcher gate entry point per RFC §6."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
elif ! grep -qE "hold_for_review" intergen/provenance.py; then
    violation "behavior matrix (hold_for_review actions) missing in intergen/provenance.py" \
              "D-008 v1.0 minimum requires the RFC §6 behavior matrix routing user_implied/ingress_derived state-changing tools to hold_for_review."
    GATE_A_VIOLATIONS=$((GATE_A_VIOLATIONS + 1))
fi
if [ "$GATE_A_VIOLATIONS" -eq 0 ]; then
    green "PASS — Provenance taxonomy (interfaces) + verify_tool_call() gate (logic) + behavior matrix all present"
fi

# Gate B: tool_registry.py routes through provenance gate
header "Gate B — tool_registry.py routes through provenance gate"
if [ ! -f "intergen/tool_registry.py" ]; then
    violation "intergen/tool_registry.py missing" \
              "D-008 v1.0 minimum requires the dispatcher that routes through the provenance gate."
elif ! grep -q "def execute" intergen/tool_registry.py; then
    violation "execute() method missing in intergen/tool_registry.py" \
              "D-008 v1.0 minimum requires the dispatcher entry point."
elif ! grep -qE "verify_tool_call|from .provenance|from intergen.provenance|provenance\.verify_tool_call" intergen/tool_registry.py; then
    violation "tool_registry.py does not reference verify_tool_call from provenance.py" \
              "D-008 v1.0 minimum requires the dispatcher to consult the gate before tool execution."
elif ! grep -q "_PRIVILEGED_TOOLS" intergen/tool_registry.py; then
    violation "_PRIVILEGED_TOOLS frozenset missing in intergen/tool_registry.py" \
              "D-008 v1.0 minimum requires the privileged-tool allow-list for pkexec routing."
else
    green "PASS — tool_registry.py references provenance gate + has _PRIVILEGED_TOOLS"
fi

# Gate C: review modal present with v1.0 button surface + libnotify
# fallback (RFC §7 + §7.2)
header "Gate C — review_modal.py with 3-button surface + libnotify fallback (RFC §7 + §7.2)"
declare -i GATE_C_VIOLATIONS=0
if [ ! -f "intergen/review_modal.py" ]; then
    violation "intergen/review_modal.py missing" \
              "D-008 v1.0 minimum requires the user-facing review modal (RFC §7)."
    GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
else
    if ! grep -q "Allow once" intergen/review_modal.py; then
        violation "'Allow once' button label missing in review_modal.py" \
                  "D-008 v1.0 minimum requires the 3-button review-modal surface."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
    if ! grep -q "Allow for this conversation" intergen/review_modal.py; then
        violation "'Allow for this conversation' button label missing in review_modal.py" \
                  "D-008 v1.0 minimum requires the 3-button review-modal surface."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
    if ! grep -q "Deny" intergen/review_modal.py; then
        violation "'Deny' button label missing in review_modal.py" \
                  "D-008 v1.0 minimum requires the 3-button review-modal surface."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
    if ! grep -qE "libnotify|notify-send" intergen/review_modal.py; then
        violation "libnotify fallback missing in review_modal.py" \
                  "D-008 v1.0 minimum requires the notification fallback for held actions when session is locked / headless / zenity unavailable (RFC §7.2)."
        GATE_C_VIOLATIONS=$((GATE_C_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_C_VIOLATIONS" -eq 0 ]; then
    green "PASS — review_modal.py with 3-button surface + libnotify fallback (RFC §7 + §7.2)"
fi

# Gate D: audit log at canonical XDG path (RFC §9)
header "Gate D — audit log at \$XDG_STATE_HOME/intergen/tool-dispatch.jsonl"
if [ ! -f "intergen/audit_log.py" ]; then
    violation "intergen/audit_log.py missing" \
              "D-008 v1.0 minimum requires the per-tool-call audit log (RFC §9)."
elif ! grep -q "tool-dispatch.jsonl" intergen/audit_log.py; then
    violation "tool-dispatch.jsonl path not referenced in intergen/audit_log.py" \
              "D-008 v1.0 minimum requires the canonical audit log filename per RFC §9."
elif ! grep -q "XDG_STATE_HOME" intergen/audit_log.py; then
    violation "XDG_STATE_HOME not referenced in intergen/audit_log.py" \
              "D-008 v1.0 minimum requires the audit log at \$XDG_STATE_HOME/intergen/."
else
    green "PASS — audit log at \$XDG_STATE_HOME/intergen/tool-dispatch.jsonl"
fi

# Gate E: pkexec runner + PolicyKit policy artifacts (D-007 composition)
header "Gate E — pkexec runner + PolicyKit policy artifacts (D-007 composition)"
declare -i GATE_E_VIOLATIONS=0
if [ ! -f "intergen/data/intergen-privileged-runner" ]; then
    violation "intergen/data/intergen-privileged-runner missing" \
              "D-008 v1.0 minimum requires the pkexec runner binary for privileged-tool routing."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
fi
if [ ! -f "intergen/data/org.intergenos.intergen.policy" ]; then
    violation "intergen/data/org.intergenos.intergen.policy missing" \
              "D-008 v1.0 minimum requires the PolicyKit policy file for the pkexec gate."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
elif ! grep -q "org.intergenos.intergen.privileged-tool" intergen/data/org.intergenos.intergen.policy; then
    violation "PolicyKit action id missing in policy file" \
              "D-008 v1.0 minimum requires action id 'org.intergenos.intergen.privileged-tool'."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
fi
if ! grep -qE '_PKEXEC_RUNNER_PATH[[:space:]]*=[[:space:]]*"/usr/bin/intergen-privileged-runner"' intergen/tool_registry.py; then
    violation "tool_registry.py does not reference _PKEXEC_RUNNER_PATH at canonical install path" \
              "D-008 v1.0 minimum requires tool_registry.py to route privileged tools through /usr/bin/intergen-privileged-runner."
    GATE_E_VIOLATIONS=$((GATE_E_VIOLATIONS + 1))
fi
if [ "$GATE_E_VIOLATIONS" -eq 0 ]; then
    green "PASS — pkexec runner + PolicyKit policy artifacts + canonical install path present"
fi

# Gate F: source_of_request field enforced at both layers (dataclass +
# gate logic) per RFC §5.3 + §8
header "Gate F — source_of_request field enforced on ToolCall (RFC §5.3 + §8)"
declare -i GATE_F_VIOLATIONS=0
# Dataclass-level enforcement: ToolCall.source_of_request REQUIRED + __post_init__ guard
if [ ! -f "intergen/interfaces/types.py" ]; then
    violation "intergen/interfaces/types.py missing" \
              "D-008 v1.0 minimum requires the ToolCall dataclass interface definition."
    GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
elif ! grep -q "class ToolCall" intergen/interfaces/types.py; then
    violation "ToolCall class missing in intergen/interfaces/types.py" \
              "D-008 v1.0 minimum requires the ToolCall dataclass."
    GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
elif ! grep -q "source_of_request" intergen/interfaces/types.py; then
    violation "source_of_request field missing on ToolCall (intergen/interfaces/types.py)" \
              "D-008 v1.0 minimum requires the source_of_request field on ToolCall (RFC §5.3)."
    GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
elif ! grep -qE "source_of_request is REQUIRED" intergen/interfaces/types.py; then
    violation "ToolCall.__post_init__ does not enforce source_of_request REQUIRED" \
              "D-008 v1.0 minimum requires dataclass-level rejection of ToolCalls missing source_of_request (RFC §5.3 no-fallback policy)."
    GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
fi
# Gate-logic level enforcement: provenance.py rejects missing source_of_request
if [ -f "intergen/provenance.py" ]; then
    if ! grep -q "source_of_request" intergen/provenance.py; then
        violation "source_of_request not referenced in intergen/provenance.py" \
                  "D-008 v1.0 minimum requires the gate logic to consult source_of_request."
        GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
    elif ! grep -qE "RFC[[:space:]]*§5\.3[[:space:]]*violation" intergen/provenance.py; then
        violation "Gate logic does not document RFC §5.3 violation explicitly in intergen/provenance.py" \
                  "D-008 v1.0 minimum requires explicit rejection messaging for ToolCalls missing source_of_request."
        GATE_F_VIOLATIONS=$((GATE_F_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_F_VIOLATIONS" -eq 0 ]; then
    green "PASS — source_of_request enforced at dataclass + gate-logic layers"
fi

# Gate G: PolicyKit policy uses auth_admin_keep (per-action authentication;
# composes with D-007's per-action password posture)
header "Gate G — PolicyKit policy uses auth_admin_keep (D-007 composition)"
if grep -qE "auth_admin_keep" intergen/data/org.intergenos.intergen.policy 2>/dev/null; then
    green "PASS — PolicyKit policy uses auth_admin_keep (composes with D-007 per-action authentication)"
else
    violation "PolicyKit policy does not use auth_admin_keep" \
              "D-008 v1.0 composes with D-007: privileged actions must require per-action user authentication."
fi

# Gate H: IngressTracker + ConversationTrustState present (per D-008
# amendment 2026-05-19T21:47:58Z pulling spotlighting + per-conv trust
# state into v1.0 minimum scope)
header "Gate H — IngressTracker + ConversationTrustState (RFC §5.1 + per-conv state)"
declare -i GATE_H_VIOLATIONS=0
if [ ! -f "intergen/interfaces/provenance.py" ]; then
    violation "intergen/interfaces/provenance.py missing (re-check; Gate A also requires this)" \
              "Cannot verify Gate H without the interfaces/provenance.py file."
    GATE_H_VIOLATIONS=$((GATE_H_VIOLATIONS + 1))
else
    if ! grep -qE "^class IngressTracker" intergen/interfaces/provenance.py; then
        violation "IngressTracker class missing in intergen/interfaces/provenance.py" \
                  "D-008 v1.0 minimum (amended 2026-05-19) requires the mechanical ingress-tool-watermark verification class (RFC §5.1)."
        GATE_H_VIOLATIONS=$((GATE_H_VIOLATIONS + 1))
    fi
    if ! grep -qE "^class ConversationTrustState" intergen/interfaces/provenance.py; then
        violation "ConversationTrustState class missing in intergen/interfaces/provenance.py" \
                  "D-008 v1.0 minimum (amended 2026-05-19) requires per-conversation trust state for denied tool+source combinations."
        GATE_H_VIOLATIONS=$((GATE_H_VIOLATIONS + 1))
    fi
fi
# Verify the dispatcher consults both classes
if [ -f "intergen/provenance.py" ]; then
    if ! grep -q "IngressTracker" intergen/provenance.py; then
        violation "IngressTracker not referenced in intergen/provenance.py dispatcher" \
                  "D-008 v1.0 minimum requires the dispatcher to escalate effective provenance based on prior ingress-tool calls."
        GATE_H_VIOLATIONS=$((GATE_H_VIOLATIONS + 1))
    fi
    if ! grep -q "ConversationTrustState" intergen/provenance.py; then
        violation "ConversationTrustState not referenced in intergen/provenance.py dispatcher" \
                  "D-008 v1.0 minimum requires the dispatcher to consult per-conversation trust state."
        GATE_H_VIOLATIONS=$((GATE_H_VIOLATIONS + 1))
    fi
fi
if [ "$GATE_H_VIOLATIONS" -eq 0 ]; then
    green "PASS — IngressTracker + ConversationTrustState defined + consulted by dispatcher"
fi

# Summary
header "D-008 compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-008 compliance verified. Build may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found."
    yellow "The requirement: InterGen may only ship behind the provenance-gated tool"
    yellow "dispatcher — every tool call carries its source, calls crossing the trust"
    yellow "boundary go through an explicit user decision with an audit record, and"
    yellow "privileged work goes through the PolicyKit-guarded runner."
    yellow "Canonical design: docs/architecture/intergen-provenance-gate-design.md"
    yellow "The gate itself is intergen/provenance.py; the dispatch path that must use it"
    yellow "is intergen/tool_registry.py."
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
