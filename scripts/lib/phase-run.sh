# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# igos_run_phase — run one recipe phase function so that a failure INSIDE it
# is seen, and so that the recipe's own shell options cannot leak into the
# driver that called it.
#
# Sourced by scripts/toolchain-build.sh (Chapter 5, outside the chroot). The
# Python-tier and Chapter-8 drivers already have the equivalent in
# scripts/pkg-functions.sh (pkg_run_phase); this file gives the toolchain
# driver the same mechanism instead of a second copy of it.
#
# WHY THE CALL FORM MATTERS — measured on bash 5.3.0 (the version this tree
# builds, packages/core/bash/package.yml), 2026-08-19:
#
#   form                                            mid-phase failure seen?
#   ------------------------------------------------------------------
#   phase >> log 2>&1 || { …; exit 1; }             NO  — reported success
#   ( set -e; phase ) >> log 2>&1 || { …; exit 1; } NO  — reported success
#   set +e; ( set -e; phase ) >> log 2>&1; rc=$?    YES — rc=1
#
# A command on the left of `||` (or in an `if` condition) runs with errexit
# SUSPENDED, and the suspension applies to every function that command calls
# — a `set -e` written inside the recipe's own configure()/build() does not
# restore it, and a subshell wrapper does not either. Only a plain simple
# command whose status is read from $? is evaluated with errexit live. The
# first two forms let a phase continue past a failed command and report the
# status of whatever ran last: a cd that failed, a configure that never ran,
# and a green log line at the end of it.
#
# The subshell serves the second half: a recipe phase that runs `set -e`
# changes the option for the whole shell (bash has no function-local option
# scope), so a phase called directly leaves errexit ON in the driver that
# called it — this driver states in its own header that it must not run
# under errexit. Inside a subshell the change dies with the subshell.
#
# Usage:
#   igos_run_phase <function-name> <logfile>
#   rc=$IGOS_PHASE_RC

igos_run_phase() {
    local func="$1" log="$2" _e
    case $- in *e*) _e=1 ;; *) _e=0 ;; esac
    set +e
    ( set -e; "$func" ) >> "$log" 2>&1
    IGOS_PHASE_RC=$?
    if [ "$_e" = 1 ]; then set -e; else set +e; fi
    return 0
}
