#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# scripts/check-d010-compliance.sh — D-010 compliance gate.
#
# THE REQUIREMENT (D-010, decided 2026-05-19): the InterGen AI assistant is
# never enabled by default. No layer — package post_install, xdg autostart,
# a systemd preset rule, or a `systemctl --global enable` wired into
# non-installer code — may turn intergen.service on. The Forge installer
# asks the user, defaulting to NO, and enables the service at PHASE_SERVICES
# only on the YES path. This is a Class A gate — it blocks ISO/qcow2
# creation until that holds.
#
# WHERE THE REQUIREMENT IS RECORDED IN THIS REPOSITORY:
#   packages/ai/intergen/build.sh
#       the post_install that must NOT enable the service, with the
#       reasoning in its own comments;
#   installer/frontend/gui/screens/packages.py + installer/frontend/tui.py
#       the installer prompt that is the sole opt-in surface;
#   installer/backend/install.py
#       PHASE_SERVICES, which enables the service only on the YES path;
#   scripts/check-d010-runtime.sh
#       the runtime half, which checks the assembled chroot.
#
# Run before `phase_image` (and any ISO-assembly path) in
# scripts/build-intergenos.sh. May also be invoked standalone:
#   scripts/check-d010-compliance.sh
#
# Exit codes:
#   0 — no violations found; build may proceed
#   1 — one or more violations found; refuse to assemble shippable artifact
#   2 — script invocation error (wrong cwd, missing tooling, etc.)

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

# Gate A: no `systemctl --global enable intergen` in non-installer paths.
# The installer (installer/backend/install.py PHASE_SERVICES) is the only
# legitimate place the AI service may be enabled, and only on the YES
# path of the Forge prompt (install_io.intergen_ai_enable=True).
header "Gate A — no systemctl --global enable intergen outside installer/"
# Comment-line filter: skip matches where the systemctl reference sits
# inside a shell comment (`# ...`); those are explanatory references to
# the directive, not active code that would enable the service.
#
# Word-bounded match: D-010 protects the InterGen AI assistant (package
# name `intergen`, unit `intergen.service`), NOT every package whose
# name starts with `intergen-`. The bounded suffix `([.[:space:]]|$)`
# allows `.service`, trailing whitespace, or end-of-line after `intergen`
# but rejects `intergen-firstboot`, `intergen-welcome`, `intergen-pkm-
# notifier`, etc. -- those are distinct packages (first-login animation,
# first-login wizard, package-manager notifier) unrelated to the AI.
# Gate C carries the same bounded discipline via its `Exec=` regex.
HITS_A=$(grep -rn -E 'systemctl[[:space:]]+--global[[:space:]]+enable[[:space:]]+intergen([.[:space:]]|$)' \
    packages/ scripts/ config/ 2>/dev/null \
    | grep -v -E 'check-d010-compliance\.sh|\.md:|/research/|/audit/|/docs/' \
    | grep -v -E '^[^:]+:[0-9]+:[[:space:]]*#')
if [ -n "$HITS_A" ]; then
    violation "build-time \`systemctl --global enable intergen\` in non-installer path" \
              "D-010 forbids enabling the AI assistant by default. Move the enable into the Forge prompt YES path (installer/backend/install.py PHASE_SERVICES)."
    printf '%s\n' "$HITS_A" | sed 's/^/    /'
else
    green "PASS — no \`systemctl --global enable intergen\` outside installer/"
fi

# Gate B: no `systemctl enable intergen` (single-user variant) baked into
# any package's post_install block. The single-user form is what
# `systemctl --user enable` resolves at first-boot; baking it into
# package post_install would enable for every user that ever logs in.
header "Gate B — no \`systemctl enable intergen\` in package post_install blocks"
HITS_B=$(grep -rn -E 'systemctl[[:space:]]+enable[[:space:]]+intergen([.[:space:]]|$)' \
    packages/ 2>/dev/null \
    | grep -v -E 'check-d010-compliance\.sh|\.md:|--global')
if [ -n "$HITS_B" ]; then
    violation "package post_install enables intergen unconditionally" \
              "D-010 forbids unconditional enable. The Forge prompt at install time is the sole opt-in surface."
    printf '%s\n' "$HITS_B" | sed 's/^/    /'
else
    green "PASS — no \`systemctl enable intergen\` in package post_install"
fi

# Gate C: no xdg autostart desktop file shipping intergen at session start.
# /etc/xdg/autostart/*.desktop + /usr/share/applications/*.desktop with
# Hidden=false or no Hidden line + the intergen Exec= line would launch
# the assistant for every user that logs in graphically — back-door
# auto-enable.
#
# Pattern requires the executable name `intergen` exactly (word-bounded);
# `intergen-welcome` and other `intergen-*` binaries are distinct
# packages (the welcomer is a one-shot first-login wizard, not the AI
# assistant). The bounded check prevents false positives on those.
header "Gate C — no xdg autostart desktop file launches intergen"
HITS_C=$(grep -rln -E '^Exec=(/usr/(local/)?bin/)?intergen([[:space:]]|$)' \
    packages/ config/ 2>/dev/null \
    | xargs -I{} grep -l -E '^\[Desktop Entry\]' {} 2>/dev/null \
    | xargs -I{} sh -c '
        f="$1"
        # Match if the file is under autostart/ OR if it has no Hidden=true
        # marker (default-visible desktop files launch at session start
        # when placed in autostart paths).
        case "$f" in
          */autostart/*.desktop)
              echo "$f: autostart-path desktop entry launches intergen" ;;
          *)
              if ! grep -q "^Hidden=true" "$f" 2>/dev/null; then
                  echo "$f: desktop entry launches intergen without Hidden=true (potential autostart)"
              fi ;;
        esac
    ' _ {} | grep -v -E 'check-d010-compliance\.sh')
if [ -n "$HITS_C" ]; then
    violation "xdg autostart / unhidden desktop file launches intergen" \
              "D-010 forbids back-door auto-enable. Either remove the autostart entry or set Hidden=true."
    printf '%s\n' "$HITS_C" | sed 's/^/    /'
else
    green "PASS — no xdg autostart desktop file launches intergen"
fi

# Gate D: packages/ai/intergen/build.sh post_install MUST NOT contain
# the enable line. This is the specific regression D-010 captures — a
# decision that lived only as spoken direction, with no written entry, hook
# or gate behind it, and rotted out of the tree unnoticed as a result.
#
# Comment-line filter mirrors Gate A: the post_install block legitimately
# carries a comment explaining the D-010 directive (the Forge prompt is
# the sole opt-in surface). Active code that would enable intergen is
# the violation; explanatory comments are not.
header "Gate D — packages/ai/intergen/build.sh post_install does NOT enable intergen"
if [ -f "packages/ai/intergen/build.sh" ]; then
    # Extract post_install block + check it has zero `systemctl ... enable` lines
    POST_INSTALL_BODY=$(awk '/^post_install\(\)/,/^}/' packages/ai/intergen/build.sh)
    ACTIVE_SYSTEMCTL=$(echo "$POST_INSTALL_BODY" \
        | grep -nE 'systemctl[[:space:]]+(--global[[:space:]]+)?enable[[:space:]]+intergen([.[:space:]]|$)' \
        | grep -v -E '^[0-9]+:[[:space:]]*#')
    if [ -n "$ACTIVE_SYSTEMCTL" ]; then
        violation "packages/ai/intergen/build.sh post_install contains systemctl enable intergen" \
                  "This is the D-010 regression site. Remove the enable line; the Forge prompt is the sole opt-in surface."
        printf '%s\n' "$ACTIVE_SYSTEMCTL" | sed 's/^/    /'
    else
        green "PASS — packages/ai/intergen/build.sh post_install does not enable intergen"
    fi
else
    violation "packages/ai/intergen/build.sh missing" \
              "Cannot verify D-010 compliance without the source build.sh in tree."
fi

# Summary
header "D-010 compliance summary"
if [ "$VIOLATIONS" -eq 0 ]; then
    green "ALL GATES PASS — D-010 compliance verified. Build may proceed."
    exit 0
else
    red "FAILED — $VIOLATIONS violation(s) found."
    yellow "The requirement: the InterGen AI assistant is never enabled by default — no"
    yellow "package post_install, xdg autostart entry, systemd preset rule or global"
    yellow "systemctl enable may turn intergen.service on. The installer prompt, which"
    yellow "defaults to NO, is the sole opt-in surface."
    yellow "The post_install that must not enable it is packages/ai/intergen/build.sh."
    yellow "Fix violations and re-run before ISO/qcow2 assembly may proceed."
    exit 1
fi
