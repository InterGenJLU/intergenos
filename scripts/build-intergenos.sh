#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# ==========================================================================
# InterGenOS Master Build Orchestrator
#
# Drives the entire build from fresh VM to bootable disk image.
# One command, clear progress, full control.
#
# Usage:
#   sudo bash build-intergenos.sh --user <username>
#   sudo bash build-intergenos.sh --user <username> --start-at <phase>
#   sudo bash build-intergenos.sh --user <username> --stop-after <phase>
#   sudo bash build-intergenos.sh --user <username> --checkpoint
#
# Phases (in order):
#   validate       — Verify host meets all build requirements
#   verify-sources — Audit all source: SHAs against downloaded tarballs
#   setup          — Create build root, verify sources and patches
#   toolchain    — Cross-compilation toolchain (LFS Chapters 5-6)
#   chroot-prep  — Mount virtual filesystems for chroot (Chapter 7 prep)
#   chroot-tools — Build temporary tools inside chroot (Chapter 7)
#   core         — Build LFS core packages in chroot (Chapter 8)
#   config       — System configuration in chroot (Chapter 9)
#   core-extra   — Build additional core packages in chroot
#   base         — Build base packages in chroot
#   desktop      — Build desktop packages in chroot (GNOME + dependencies)
#   image        — Package chroot into bootable disk image
#
# Controls:
#   --start-at <phase>           Start (or resume) at a specific phase
#   --start-at-pkg <name>        Resume at a specific PACKAGE within --start-at
#                                  phase (K23: explicit CLI > ambient env).
#                                  Scope = the start phase only; subsequent
#                                  phases run from their normal start.
#                                  Requires --start-at to scope against.
#                                  Example: --start-at core-extra
#                                           --start-at-pkg cryptsetup-static
#   --stop-after <phase>         Stop after the named phase completes
#   --checkpoint                 Save a tarball after each significant phase
#   --iso-name <file.iso>        Target ISO filename (bare name; lands in
#                                  build/). Chosen at launch and PERSISTED
#                                  across the ceremony-resume chain, so the
#                                  final --start-at iso resume mints under
#                                  the launch-chosen name — no hand-rename
#                                  after creation. phase_iso logs which
#                                  source won (flag/persisted/default). A
#                                  fresh full launch without the flag clears
#                                  the prior chain's persisted name.
#                                  Naming rule: a superseded/destroyed ISO's
#                                  replacement ROTATES the candidate ordinal
#                                  (…-ge-01-… -> …-ge-02-…), never reuses
#                                  the label (decided 2026-07-05).
#   touch /mnt/igos/.build-stop  Graceful halt between phases
#   Ctrl+C                       Immediate stop (traps SIGINT)
#
# ==========================================================================

set -euo pipefail

# ==========================================================================
# Constants
# ==========================================================================

IGOS=/mnt/igos
IGOS_TARGET=x86_64-igos-linux-gnu
SCRIPTS=/mnt/intergenos/scripts
PACKAGES_DIR=/mnt/intergenos/packages
SOURCES=/mnt/intergenos/build/sources
PATCHES=/mnt/intergenos/build/patches
LOGS=/mnt/intergenos/build/logs
PHASE_FILE="${LOGS}/.build-phase"
STOP_FILE="${IGOS}/.build-stop"
CHECKPOINT_DIR="/mnt/intergenos/checkpoints"
BUILD_LOG="${LOGS}/build-intergenos-$(date '+%Y%m%d-%H%M%S').log"

# JSON-line forensic-trace runid + start-timestamp. Exported so every child
# process (chroot-build-*.sh, igos-build, pkm) joins the same trace trail.
# Generated here (not at `phase_setup`) so every phase — including `validate`
# and `verify-sources` — appears in the same `<startts>-<runid>` suffix
# family in /mnt/intergenos/build/logs/trace/. The trace-envelope contract
# itself lives in scripts/lib/igos_trace.py.
IGOS_TRACE_RUNID="${IGOS_TRACE_RUNID:-$(uuidgen 2>/dev/null | tr -d '-' | cut -c1-16)}"
IGOS_TRACE_START_TS="${IGOS_TRACE_START_TS:-$(date -u '+%Y%m%dT%H%M%SZ')}"
export IGOS_TRACE_RUNID IGOS_TRACE_START_TS

PHASES=(
    validate
    verify-sources
    setup
    toolchain
    chroot-prep
    chroot-tools
    core
    config
    core-extra
    base
    kernel
    desktop
    extra
    compute
    ai
    bootloader
    image
    manifest
    squashfs
    ukis-verity
    iso
)

# ==========================================================================
# Argument parsing
# ==========================================================================

BUILD_USER=""
START_AT=""
START_AT_PKG=""
STOP_AFTER=""
CHECKPOINT=false
PUBLISH=false
ROOT_PASSWORD_ARG=""
USER_PASSWORD_ARG=""
ROOT_PASSWORD_PROVIDED=false
USER_PASSWORD_PROVIDED=false
IMAGE_USER_NAME="intergenos"
DEBUG_VERBOSE=false
ISO_NAME=""
# Launch-chain persistence for --iso-name (decided 2026-07-05: dynamic ISO
# naming — the flag is given ONCE at launch and must survive
# the resume chain through both signing ceremonies to the final
# `--start-at iso` invocation). Scope = one launch chain: resumes inherit
# it; a fresh full launch (no --start-at) without the flag clears it.
ISO_NAME_FILE="/mnt/intergenos/build/.iso-name"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            BUILD_USER="$2"
            shift 2
            ;;
        --start-at)
            START_AT="$2"
            shift 2
            ;;
        --start-at-pkg)
            # K23: explicit-intent per-package resume within the --start-at
            # phase. Distinct from the IGOS_START_AT env-var (which the
            # orchestrator unsets defensively against ambient-shell leakage
            # per Build #9 2026-05-13 incident, see phase_base comment at
            # line ~1019). CLI flag bypasses the unset for the matching
            # phase only — see apply_start_at_pkg() helper.
            START_AT_PKG="$2"
            shift 2
            ;;
        --stop-after)
            STOP_AFTER="$2"
            shift 2
            ;;
        --checkpoint)
            CHECKPOINT=true
            shift
            ;;
        --publish)
            PUBLISH=true
            shift
            ;;
        --root-password)
            ROOT_PASSWORD_ARG="$2"
            ROOT_PASSWORD_PROVIDED=true
            shift 2
            ;;
        --user-password)
            USER_PASSWORD_ARG="$2"
            USER_PASSWORD_PROVIDED=true
            shift 2
            ;;
        --image-user)
            IMAGE_USER_NAME="$2"
            shift 2
            ;;
        --iso-name)
            # Dynamic ISO naming (decided 2026-07-05): the target
            # ISO filename is set at invocation and carried through the whole
            # build via ISO_NAME_FILE, so ISOs are never hand-renamed after
            # creation. Validated fail-closed below; consumed by phase_iso.
            ISO_NAME="$2"
            shift 2
            ;;
        --debug-verbose)
            # Enable the JSON-line forensic trace (see scripts/lib/trace.sh).
            # Equivalent to setting IGOS_BUILD_DEBUG_VERBOSE=1 in the env;
            # this flag is the user-facing CLI on-switch. Zero-cost when off
            # (verbose-off path of trace.sh / igos_trace.py is the
            # straight-through fall-through). When on, writes structured
            # events to /mnt/intergenos/build/logs/trace/build-*.jsonl.
            DEBUG_VERBOSE=true
            export IGOS_BUILD_DEBUG_VERBOSE=1
            shift
            ;;
        -h|--help)
            # Print the whole header comment block (was head -30, which cut
            # the Controls list off mid-flag).
            head -57 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>]"
            exit 1
            ;;
    esac
done

if [ -z "$BUILD_USER" ]; then
    echo "Error: --user <username> is required"
    echo "Usage: sudo bash $0 --user <username> --root-password <pw> --user-password <pw> [--image-user <name>] [--start-at <phase>] [--stop-after <phase>] [--iso-name <file.iso>]"
    exit 1
fi

# --iso-name: validate fail-closed, then persist for the launch chain.
# A bare filename only (it lands in /mnt/intergenos/build/) — the charset
# gate rejects path separators, spaces, and anything scp/dd-hostile.
if [ -n "$ISO_NAME" ]; then
    if ! [[ "$ISO_NAME" =~ ^[A-Za-z0-9._-]+\.iso$ ]]; then
        echo "Error: --iso-name must be a bare filename ending in .iso"
        echo "       (charset [A-Za-z0-9._-], no path separators)."
        echo "       Example: --iso-name intergenos-ge-02-dev.iso"
        exit 1
    fi
    mkdir -p "$(dirname "$ISO_NAME_FILE")"
    printf '%s\n' "$ISO_NAME" > "$ISO_NAME_FILE"
elif [ -z "$START_AT" ] && [ -f "$ISO_NAME_FILE" ]; then
    # Fresh full launch without --iso-name = a NEW launch chain: clear the
    # previous chain's persisted name so it cannot silently leak across
    # arcs. Resumes (--start-at ...) inherit the persisted choice.
    rm -f "$ISO_NAME_FILE"
fi

# Image credentials. DO NOT CHANGE THIS DEFAULT (decided 2026-05-19,
# reaffirmed 2026-05-31; D-027). The BUILT-IN default is
# intergenos:intergenos (user:root).
#
# It is intentional and load-bearing: the first-boot greeter (Path 3)
# OVERWRITES BOTH passwords on the end-user's first boot, so the build-time
# values are a brief-window fallback nobody normally encounters. Retiring or
# randomizing the default therefore buys no security while breaking the
# unattended build path.
#
# This default has been changed away twice and reverted both times (retired
# to "required, no default" under S1/S2 decision A 2026-04-29; then to
# openssl-rand autogen in commit da2b1ab8). Do NOT retire, randomize, or
# require-explicit it again. If --root-password / --user-password are passed
# they override; if omitted, the default is "intergenos".
if ! $ROOT_PASSWORD_PROVIDED; then
    ROOT_PASSWORD_ARG="intergenos"
elif [ -z "$ROOT_PASSWORD_ARG" ]; then
    echo "Error: --root-password '' (empty) rejected. Omit the flag for the"
    echo "       intergenos default, or pass a non-empty value."
    exit 1
fi
if ! $USER_PASSWORD_PROVIDED; then
    USER_PASSWORD_ARG="intergenos"
elif [ -z "$USER_PASSWORD_ARG" ]; then
    echo "Error: --user-password '' (empty) rejected. Omit the flag for the"
    echo "       intergenos default, or pass a non-empty value."
    exit 1
fi
export ROOT_PASSWORD="$ROOT_PASSWORD_ARG"
export IMAGE_USER_PASSWORD="$USER_PASSWORD_ARG"
export IMAGE_USER="$IMAGE_USER_NAME"

# Verify running as root (needed for chroot phases)
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: must run as root (use sudo)"
    exit 1
fi

# Verify build user exists
if ! id "$BUILD_USER" > /dev/null 2>&1; then
    echo "Error: user '$BUILD_USER' does not exist"
    exit 1
fi

# Validate --start-at and --stop-after are real phase names
validate_phase_name() {
    local name="$1"
    local label="$2"
    if [ -n "$name" ]; then
        local found=false
        for p in "${PHASES[@]}"; do
            if [ "$p" = "$name" ]; then
                found=true
                break
            fi
        done
        if ! $found; then
            echo "Error: unknown phase '$name' for $label"
            echo "Valid phases: ${PHASES[*]}"
            exit 1
        fi
    fi
}

validate_phase_name "$START_AT" "--start-at"
validate_phase_name "$STOP_AFTER" "--stop-after"

# K23: --start-at-pkg requires --start-at (scope = the start phase only).
# Without --start-at, package name has no phase context to anchor against.
if [ -n "$START_AT_PKG" ] && [ -z "$START_AT" ]; then
    echo "Error: --start-at-pkg requires --start-at (the package name scopes to the start phase)"
    echo "Example: --start-at core-extra --start-at-pkg cryptsetup-static"
    exit 1
fi

# Conditionally enable publish phase
if $PUBLISH; then
    PHASES+=(publish)
fi

# ==========================================================================
# Logging
# ==========================================================================

mkdir -p "$LOGS"
mkdir -p "$LOGS/trace"
# The orchestrator runs as root, but the Ch-5 toolchain phase runs AS
# $BUILD_USER and writes its per-package logs into $LOGS — a root-owned
# 755 logs dir makes binutils-pass1 "fail" at configure in ~2s with NO
# log (the log REDIRECT is what fails, not binutils). On a FRESH
# worktree this mkdir is what creates the dir root-owned, so hand it to
# the build user. (Own-footgun class, GE-01 launch 2026-07-03: the dev
# worktree had no build/logs yet; the master worktree's was
# historically user-owned so the assumption never surfaced.)
if [ -n "$BUILD_USER" ]; then
    chown "$BUILD_USER":"$BUILD_USER" "$LOGS" "$LOGS/trace"
fi

# Source the JSON-line forensic-trace companion. This must come AFTER LOGS
# exists (trace.sh creates the per-scope sink under /mnt/intergenos/build/
# logs/trace/). Reads IGOS_BUILD_DEBUG_VERBOSE / FORGE_DEBUG_VERBOSE env-vars
# at source time and freezes its verbosity decision. Zero-cost when off.
#
# After sourcing, trace_* functions are available; trace_init opens the
# orchestrator sink build-orchestrator-<startts>-<runid>.jsonl.
IGOS_TRACE_ROOT="${IGOS_TRACE_ROOT:-${LOGS}/trace}"
export IGOS_TRACE_ROOT
# shellcheck source=lib/trace.sh
source "${SCRIPTS}/lib/trace.sh"
trace_init "orchestrator" "$IGOS_TRACE_RUNID"

# The shared build-output library — the single house style for the whole shell
# build pipeline (timestamp prefix, one phase/step style, error:/warning:/note:
# severity, TTY-aware color, the ✓/✗/⚠ markers). It provides the FORMATTING;
# this orchestrator's log() below keeps its own side effects (tee to BUILD_LOG,
# mirror to the JSONL trace).
# shellcheck source=lib/logging.sh
source "${SCRIPTS}/lib/logging.sh"

# Orchestrator narration line. Renders via the shared library's timestamp, then
# adds the orchestrator-specific sinks: echo to the console, append to the text
# build log, and mirror into the structured JSONL trail so the trace files
# carry the same human-readable story the text log shows (no-op when the
# verbose gate is off — trace_event short-circuits).
log() {
    local msg="[$(igos_timestamp)] $*"
    echo "$msg"
    echo "$msg" >> "$BUILD_LOG"
    trace_event "narration" "text=${msg}"
}

# ==========================================================================
# Checkpoint support
# ==========================================================================

save_checkpoint() {
    local phase="$1"
    local checkpoint="${CHECKPOINT_DIR}/intergenos-${phase}-$(date '+%Y%m%d-%H%M%S').tar.zst"

    log ""
    log "Saving checkpoint: $checkpoint"

    # Structured event: checkpoint_save_start with phase + path so the
    # forensic trail records the artifact boundary.
    trace_event "checkpoint_save_start" "phase=${phase}" "path=${checkpoint}"

    mkdir -p "${CHECKPOINT_DIR}"

    # Remove any checkpoint tarballs that landed inside the chroot
    # (from previous runs with old CHECKPOINT_DIR) so they don't compound
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.gz 2>/dev/null || true
    rm -f "${IGOS}/home/${BUILD_USER}"/intergenos-*.tar.zst 2>/dev/null || true

    # Tear down chroot mounts temporarily for a clean snapshot
    bash "${SCRIPTS}/chroot-teardown.sh" > /dev/null 2>&1 || true

    local start_time=$(date +%s)
    tar -C "$IGOS" --one-file-system --zstd -cf "$checkpoint" . 2>&1

    local elapsed=$(( $(date +%s) - start_time ))
    local size=$(du -h "$checkpoint" | cut -f1)
    local size_bytes=$(stat -c %s "$checkpoint" 2>/dev/null || echo 0)

    log "Checkpoint saved: $size in ${elapsed}s"
    log "    restore with: rm -rf ${IGOS}/* && tar -C ${IGOS} --zstd -xf ${checkpoint}"

    # Structured event: checkpoint_save_end pins the file size + duration.
    trace_event "checkpoint_save_end" \
        "phase=${phase}" \
        "path=${checkpoint}" \
        "size_bytes::=${size_bytes}" \
        "duration_ms::=$((elapsed * 1000))"

    # Re-mount chroot filesystems
    bash "${SCRIPTS}/chroot-setup.sh" > /dev/null 2>&1 || true
}

# ==========================================================================
# Build summary emitter (forensic-trail tie-back)
# ==========================================================================
#
# emit_build_summary writes a JSON file at
# /mnt/intergenos/build/logs/trace/build-summary-<startts>-<runid>.json
# listing every per-phase + per-package + per-host JSONL trace file the
# build produced, plus aggregate counts and the final iso path + sha.
#
# This file is the "given the build, find every trace" index. The manifest
# phase reads SUMMARY_PATH out and pins it into the ISO manifest so any
# future artifact triage can locate the full forensic trail.
#
# Safe no-op when verbose mode is off: the summary file is not written, and
# SUMMARY_PATH is left empty so phase_manifest skips the pin.
emit_build_summary() {
    if ! trace_is_verbose; then
        return 0
    fi

    local elapsed_s="$1"
    local success="$2"
    local last_phase="$3"
    local trace_dir="${IGOS_TRACE_ROOT}"
    local summary_path="${trace_dir}/build-summary-${IGOS_TRACE_START_TS}-${IGOS_TRACE_RUNID}.json"

    # Collect every trace file matching this build's runid suffix.
    local trace_files_json="[]"
    if [ -d "$trace_dir" ]; then
        # shellcheck disable=SC2012
        trace_files_json=$(
            find "$trace_dir" -maxdepth 1 -type f \
                -name "*-${IGOS_TRACE_START_TS}-${IGOS_TRACE_RUNID}.jsonl" \
                -printf '%f\n' 2>/dev/null \
                | sort \
                | jq -R . | jq -s -c .
        )
        [ -z "$trace_files_json" ] && trace_files_json="[]"
    fi

    # Look up the final ISO artifact + sha if present. The canonical build
    # output directory is /mnt/intergenos/build/ — used by build-iso.sh +
    # phase_manifest above.
    local build_out="/mnt/intergenos/build"
    local iso_path=""
    local iso_sha=""
    if [ -f "${build_out}/intergenos-${INTERGENOS_BUILD_ID:-v1.0-dev1}.iso" ]; then
        iso_path="${build_out}/intergenos-${INTERGENOS_BUILD_ID:-v1.0-dev1}.iso"
        iso_sha=$(sha256sum "$iso_path" | awk '{print $1}')
    else
        # Fall back to the most-recent .iso under build/
        local recent
        recent=$(find "${build_out}" -maxdepth 1 -type f -name '*.iso' 2>/dev/null \
                  | sort -r | head -1)
        if [ -n "$recent" ]; then
            iso_path="$recent"
            iso_sha=$(sha256sum "$iso_path" | awk '{print $1}')
        fi
    fi

    mkdir -p "$trace_dir"
    jq -n -c \
        --arg runid "${IGOS_TRACE_RUNID}" \
        --arg start_ts "${IGOS_TRACE_START_TS}" \
        --arg end_ts "$(date -u '+%Y%m%dT%H%M%SZ')" \
        --argjson elapsed_s "${elapsed_s:-0}" \
        --argjson success "${success}" \
        --arg last_phase "${last_phase}" \
        --arg image_user "${IMAGE_USER:-}" \
        --arg build_id "${INTERGENOS_BUILD_ID:-v1.0-dev1}" \
        --arg iso_path "${iso_path}" \
        --arg iso_sha "${iso_sha}" \
        --argjson trace_files "${trace_files_json}" \
        '{runid:$runid, start_ts:$start_ts, end_ts:$end_ts,
          elapsed_s:$elapsed_s, success:$success, last_phase:$last_phase,
          image_user:$image_user, build_id:$build_id,
          iso_path:$iso_path, iso_sha:$iso_sha,
          trace_files:$trace_files}' \
        > "$summary_path"

    # Export for phase_manifest tie-back (read AFTER manifest phase has
    # already run is fine — most full builds emit summary last; but the
    # manifest phase can also include the path if SUMMARY_PATH was set
    # before manifest ran. We support both orderings.)
    export IGOS_BUILD_SUMMARY_PATH="$summary_path"

    trace_event "build_summary_emit" \
        "summary_path=${summary_path}" \
        "trace_file_count::=$(echo "$trace_files_json" | jq 'length')" \
        "iso_path=${iso_path}" \
        "iso_sha=${iso_sha}"

    log "Build summary written: $summary_path"
}

# ==========================================================================
# Signal handling
# ==========================================================================

CURRENT_PHASE=""

cleanup() {
    log ""
    log "warning: build interrupted during phase: ${CURRENT_PHASE:-none}"
    log "    cleaning up…"

    # Structured trail capture: emit phase_stop + build_end (cancelled=true)
    # before tearing down so the JSONL files record the operator-initiated
    # halt boundary. Mirrors Forge install's run_install_exit emit in its
    # signal-trap path (installer/backend/install.py).
    trace_event "phase_stop" \
        "phase=${CURRENT_PHASE:-none}" \
        "reason=interrupted_signal"
    trace_event "build_end" \
        "success::=false" \
        "cancelled::=true" \
        "last_phase=${CURRENT_PHASE:-none}" \
        "reason=interrupted_signal"

    # Emit build_summary on interruption too — operator triage of a
    # cancelled build benefits from the same trace_files index.
    local _elapsed=$(( $(date +%s) - BUILD_START ))
    emit_build_summary "${_elapsed}" "false" "${CURRENT_PHASE:-none}"

    trace_close

    # Tear down chroot mounts to prevent host filesystem corruption
    if [ -f "${SCRIPTS}/chroot-teardown.sh" ]; then
        bash "${SCRIPTS}/chroot-teardown.sh" >/dev/null 2>&1 || true
    fi

    # Kill any child processes spawned by this build
    pkill -P $$ 2>/dev/null || true

    log "    resume with: sudo bash $0 --user $BUILD_USER --start-at ${CURRENT_PHASE:-validate}"
    log ""
    exit 130
}

trap cleanup SIGINT SIGTERM SIGHUP

# ==========================================================================
# Phase runner
# ==========================================================================

SKIPPING=true
if [ -z "$START_AT" ]; then
    SKIPPING=false
fi

run_phase() {
    local phase="$1"
    local description="$2"
    shift 2
    # remaining args are the function/command to run

    # Handle --start-at
    if $SKIPPING; then
        if [ "$phase" = "$START_AT" ]; then
            SKIPPING=false
            # Refresh ld.so.cache in the chroot at the resume entry
            # point. /usr/lib64 libs installed by earlier resumes
            # (meson defaults to lib64 on x86_64; cache built once at
            # chroot-prep only knows /usr/lib) would otherwise be
            # invisible to any check() phase running runtime tests.
            # Caused 2026-05-07 sratom halt #13.
            if [ -x /mnt/igos/sbin/ldconfig ]; then
                log "[INFO ] Refreshing chroot ld.so.cache at --start-at $phase"
                chroot /mnt/igos /sbin/ldconfig 2>/dev/null || true
            fi
            # Source-staging sweep at resume entry. phase_setup was skipped
            # by --start-at, so packages added to master since the last full
            # phase_setup may have unfetched-or-unchroot-staged source
            # tarballs. ensure_sources_staged() backfills both. Scoped to
            # current + downstream tiers via tiers_for_start_at(). Halts
            # loudly on download failure (set -e propagates). Captured
            # 2026-05-12 after Build #9 r#21 halted at jemalloc 5.3.1.
            ensure_sources_staged
        else
            log "note: skip $phase — $description"
            # Structured trail: phase_skip event so the JSONL trail records
            # every phase that was skipped due to --start-at. Without this,
            # `jq` queries against "what phases ran" need to subtract the
            # phase_enter set from the full PHASES array — strictly worse
            # than emitting the skip events directly.
            trace_event "phase_skip" \
                "phase=${phase}" \
                "reason=before_start_at" \
                "start_at=${START_AT}"
            return 0
        fi
    fi

    # Check for graceful stop request
    if [ -f "$STOP_FILE" ]; then
        rm -f "$STOP_FILE"
        log ""
        log "Stop requested (found $STOP_FILE)"
        log "    stopped before phase: $phase"
        log "    resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        # Structured trail: phase_stop + build_end (graceful halt) before exit.
        trace_event "phase_stop" \
            "phase=${phase}" \
            "reason=stop_file_present"
        trace_event "build_end" \
            "success::=true" \
            "cancelled::=true" \
            "last_phase=${phase}" \
            "reason=stop_file_present"
        trace_close
        exit 0
    fi

    CURRENT_PHASE="$phase"
    local start_time=$(date +%s)

    # Structured trail: phase_enter at the top of every phase. Pins phase +
    # description so cross-file jq joins by phase work for every event
    # downstream of this boundary.
    trace_phase_enter "$phase" "$description"

    log ""
    log ">>> ${phase} — ${description}"
    log "    started $(date)"

    # Record current phase
    echo "$phase" > "$PHASE_FILE"

    # Staged-kernel exclusivity gate at every kernel-consuming phase entry
    # (decided 2026-07-12; scripts/preflight-single-kernel.sh).
    # A release-bumped kernel rebuild orphans the prior release's module
    # tree + vmlinuz (release-named paths), and downstream consumers pick
    # ambiguously (create-image symlinked the first glob match; squashfs
    # ships every tree it finds). From `kernel` onward every entry asserts
    # no twin; the kernel entry itself allows an empty chroot (from-scratch)
    # — more than one staged kernel fails in every mode. Explicit if-guard
    # so the halt does not depend on the errexit posture inside run_phase.
    case "$phase" in
        kernel)
            if ! bash "${SCRIPTS}/preflight-single-kernel.sh" --root /mnt/igos --allow-none 2>&1 | tee -a "$BUILD_LOG"; then
                log "error: staged-kernel exclusivity gate failed at ${phase} entry"
                exit 1
            fi
            ;;
        desktop|ai|extra|bootloader|image|manifest|squashfs|ukis-verity|iso)
            if ! bash "${SCRIPTS}/preflight-single-kernel.sh" --root /mnt/igos 2>&1 | tee -a "$BUILD_LOG"; then
                log "error: staged-kernel exclusivity gate failed at ${phase} entry"
                exit 1
            fi
            ;;
    esac

    # Run the phase
    "$@"
    local rc=$?

    local elapsed=$(( $(date +%s) - start_time ))
    local minutes=$(( elapsed / 60 ))
    local seconds=$(( elapsed % 60 ))
    local elapsed_ms=$(( elapsed * 1000 ))

    if [ $rc -ne 0 ]; then
        log ""
        log "error: phase failed: $phase ($description)"
        log "    exit code: $rc"
        log "    elapsed: ${minutes}m ${seconds}s"
        log "    resume with: sudo bash $0 --user $BUILD_USER --start-at $phase"
        log ""
        # Structured trail: phase_exit (failure) + build_failure event with
        # context so the JSONL trail captures the failure boundary even
        # if a downstream handler swallows the exit code.
        trace_phase_exit "$phase" "$rc" "$elapsed_ms"
        build_failure_emit \
            --where "build-intergenos.sh:run_phase" \
            --why "phase ${phase} (${description}) exited non-zero" \
            --phase "${phase}" \
            --rc "${rc}"
        trace_event "build_end" \
            "success::=false" \
            "last_phase=${phase}" \
            "rc::=${rc}" \
            "elapsed_s::=${elapsed}"

        # Emit build_summary on phase failure too.
        local _elapsed_total=$(( $(date +%s) - BUILD_START ))
        emit_build_summary "${_elapsed_total}" "false" "${phase}"

        trace_close
        exit $rc
    fi

    log ""
    log "${IGOS_MARK_OK} $phase — ${minutes}m ${seconds}s"
    # Structured trail: phase_exit (success) at the bottom of every phase.
    trace_phase_exit "$phase" 0 "$elapsed_ms"

    # Save checkpoint after significant phases.
    #
    # Cadence audited at 09cd6a5a (original 3-boundary set: toolchain |
    # core | desktop).
    #
    # Dropped vs the 1f139fe09 ad-hoc additions:
    #   - `kernel`: subsumed by `desktop` (desktop runs after kernel;
    #     desktop tarball already contains the built kernel)
    #   - `ai`: ~30GB tarball capturing a ~0.3GB delta from desktop
    #     (only 2 packages); waste
    #
    # Considered + deliberately NOT included:
    #   - `bootloader`: an earlier iteration of this code added bootloader
    #     to the case list framing it as "the portable golden-builder
    #     artifact." Two problems with that framing: (1) phase_bootloader
    #     itself hard-exits at line 1248 (ENFORCED PAUSE for the operator-
    #     only Nitrokey signing ceremony), so this code path never
    #     actually ran. (2) The unsigned-bootloader state isn't a useful
    #     portable artifact anyway — any recipient would still need the
    #     release signing key to produce a bootable ISO. The right
    #     capture point for a portable golden-builder is post-signing /
    #     pre-image, and only once the build is reliably end-to-end with
    #     zero intervention except the signing ceremony itself. We are
    #     not there yet (today's session needed two non-signing fixes:
    #     intergenos-extensions tarball wrap + zoneinfo symlink-leak).
    #     When that gate is met, add the capture at the appropriate
    #     post-signing point with the right framing — for now, on-VM
    #     iteration uses libvirt snapshots (near-instant restore vs
    #     tens-of-GB tarball extraction).
    if $CHECKPOINT; then
        case "$phase" in
            toolchain|core|desktop)
                save_checkpoint "$phase"
                ;;
        esac
    fi

    # Handle --stop-after
    if [ "$phase" = "$STOP_AFTER" ]; then
        log ""
        log "Stopping after phase: $phase (--stop-after)"
        local next_idx=0
        for i in "${!PHASES[@]}"; do
            if [ "${PHASES[$i]}" = "$phase" ]; then
                next_idx=$((i + 1))
                break
            fi
        done
        if [ $next_idx -lt ${#PHASES[@]} ]; then
            log "    resume with: sudo bash $0 --user $BUILD_USER --start-at ${PHASES[$next_idx]}"
        fi
        log ""
        # Structured trail: phase_stop + build_end (success, partial) before
        # exit so cross-file jq queries against `--stop-after` runs find the
        # intentional partial-build boundary.
        trace_event "phase_stop" \
            "phase=${phase}" \
            "reason=stop_after_flag" \
            "stop_after=${STOP_AFTER}"
        trace_event "build_end" \
            "success::=true" \
            "partial::=true" \
            "last_phase=${phase}" \
            "reason=stop_after_flag"
        trace_close
        exit 0
    fi
}

# ==========================================================================
# Phase implementations
# ==========================================================================

phase_validate() {
    # LFS 13.0 requires /bin/sh -> bash (Ubuntu defaults to dash)
    if [ "$(readlink -f /bin/sh)" != "/usr/bin/bash" ]; then
        log "  /bin/sh does not point to bash — fixing..."
        ln -sf /usr/bin/bash /bin/sh
        log "  /bin/sh -> bash"
    fi

    log "Running host requirements check..."
    python3 "${SCRIPTS}/host-check.py" 2>&1 | tee -a "$BUILD_LOG"

    # Build Development Rulebook Rule 17: pre-flight tier-coverage check.
    # Halts the build if any tier-declared package is unreachable from its
    # phase's build invocation. This is the mechanical guard against the
    # silent-skip class of failures (Build #6 found 6 such orphans).
    log "Running pre-flight tier-coverage check (Rulebook Rule 17)..."
    python3 "${SCRIPTS}/preflight-tier-coverage.py" 2>&1 | tee -a "$BUILD_LOG"

    # Reproducibility gate (2026-05-11): every in-scope package must have a
    # current, reconciled audit record in build/blfs-packages.db's
    # package_audit table. The audit captures build-system, declared deps,
    # configure flags, bundled libs, install output, and reproducibility
    # primitives — gating the build on it ensures we never re-introduce the
    # "we never looked at it first" failure class.
    log "Running audit-coverage check (reproducibility gate)..."
    python3 "${SCRIPTS}/preflight-audit-coverage.py" 2>&1 | tee -a "$BUILD_LOG"

    # Rule 1 + cross-tier dependency check via the canonical-tier validator.
    log "Running tier-validator (Rule 1 + cross-tier-dep audit)..."
    # FAIL-CLOSED on ANY nonzero validator exit (re-certification finding G1-a). The old
    # acceptance was a DENY-LIST — it re-ran the validator and halted only
    # when the output matched known verdict words, so a validator CRASH
    # (exit 1 with no summary: a malformed manifest, an import error, an
    # arg error, a not-yet-named verdict class) rode straight through as
    # the "acceptable glib2-bootstrap false positive". A gate that cannot
    # see must halt. The historical glib2 ↔ gobject-introspection false
    # positive does not fire on the current tree (validator exits 0); if it
    # ever resurfaces it HALTS here and is cleared explicitly per
    # build-rules §3.11 — never ridden through an exemption a crash can
    # hide inside. (Same PIPESTATUS shape as the source-tree-coverage gate
    # below.)
    python3 "${SCRIPTS}/validate-package-tiers.py" 2>&1 | tee -a "$BUILD_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: tier validator exited nonzero — every validator failure halts (fail-closed); read the rows above"
        return 1
    fi

    # Source-tree coverage gate (source-aware change detection): a first-party
    # package whose build.sh reads an EXTERNAL in-tree source dir (assets/,
    # intergen/, pkm/, installer/) MUST declare it in source_tree, or a source
    # edit there is invisible to the skip-built fingerprint and a targeted
    # build ships the STALE binary (the intergen-welcome class). Pure host-side
    # static analysis of build.sh vs source_tree — no chroot / generated-tarball
    # dependency — so it belongs here in validate.
    log "Running source-tree coverage gate (source-aware change detection)..."
    python3 "${SCRIPTS}/check-source-tree-coverage.py" 2>&1 | tee -a "$BUILD_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: source-tree coverage gap — declare the external source dir(s) above in source_tree"
        return 1
    fi

    # Reboot-required activation-semantics gate (3.0-F28): a package whose
    # build.sh installs a kernel image, a /lib/modules *.ko, or an
    # /etc/modprobe.d blacklist for an out-of-tree module MUST declare
    # reboot_required: true, or pkm cannot warn the user the payload is on disk
    # but inactive until reboot (the nvidia-behind-nouveau silent-install PD
    # failure). Pure host-side static analysis of build.sh vs package.yml.
    log "Running reboot-required activation-semantics gate (3.0-F28)..."
    python3 "${SCRIPTS}/check-reboot-required-declared.py" 2>&1 | tee -a "$BUILD_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: a module/boot-path package omits reboot_required: true — declare it per the rows above"
        return 1
    fi

    # Aspirational-stub gate (Rule 21): every path a shipped surface claims —
    # service units, .desktop files, tmpfiles.d, polkit rules, documentation,
    # pkm lifecycle-hook claims — must resolve to something the tree actually
    # produces, or be covered by the reviewed hook allowlist
    # (config/aspirational-stub-hook-allowlist.txt, the script's default).
    # Pure host-side static analysis, no chroot (measured 0.13s on the full
    # tree, 2026-08-19). Wired here 2026-08-19: the gate existed but had no
    # automatic caller, while docs/operations/README.md stated continuous
    # gating — this call makes that statement true. (Same PIPESTATUS shape as
    # the sibling gates in this phase.)
    log "Running aspirational-stub gate (claimed paths must resolve)..."
    python3 "${SCRIPTS}/check-aspirational-stubs.py" 2>&1 | tee -a "$BUILD_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: a claimed path resolves to nothing the tree produces — fix the claim or its producer per the rows above"
        return 1
    fi

    # Hook-contract gate: a recipe's lifecycle functions now travel inside the
    # signed archive and run on the target, which makes them a delivery
    # mechanism the manifest, the signature and every downstream integrity gate
    # cannot see. Decided 2026-07-30: a hook is MAINTENANCE-ONLY — enablement,
    # cache/database refresh, machine-unique generation, attribute restoration
    # on paths the package already owns. Content belongs in do_install, where
    # the builder stages it and pkm owns it. Fail-closed, host-side static
    # analysis of the same function text the seal seam extracts.
    log "Running hook-contract gate (lifecycle hooks are maintenance-only)..."
    python3 "${SCRIPTS}/check-hook-contract.py" 2>&1 | tee -a "$BUILD_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: a lifecycle hook writes payload — move the named lines into do_install"
        return 1
    fi

    # ISO-closure gate: fail-closed host-side runtime-edge check on the
    # package.yml tree (no chroot, no sources — ~1s). Computes every
    # package's EFFECTIVE iso_include via the parser (parser.py:571-575) and
    # HALTS on: a shipped package (iso_include True) runtime-depending a
    # MIRROR-only one (iso_include False — the GE-01 L28 mpv→libvdpau class
    # that pkm iso-prep otherwise aborts on at squashfs); a dangling
    # runtime-dep name; or a non-boolean explicit iso_include (the parser's
    # bool("false") is True footgun, which would silently ship a MIRROR-only
    # package). Turns a one-off sweep into a standing pre-launch gate.
    log "Running preflight-iso-closure scan (effective-iso_include runtime edges)..."
    python3 "${SCRIPTS}/preflight-iso-closure.py" 2>&1 | tee -a "$BUILD_LOG"

    # Build-order ordering gate (Scan A): for every run_package "consumer" line,
    # verify every declared dependencies.build entry is built EARLIER in the
    # same phase OR in a strictly earlier phase. Catches the class of bug
    # that halted Build #8 at mitkrb/libgcrypt + rpm/libgcrypt before they
    # were caught + closed at master 55b4da4. Pure pre-build static analysis
    # against the repo source tree; no chroot dependency.
    log "Running preflight-build-order scan (Scan A — ordering violations)..."
    python3 "${SCRIPTS}/preflight-build-order.py" 2>&1 | tee -a "$BUILD_LOG"

    # direct_install lane gate: `direct_install: true` is a contract only
    # igos-build implements (it takes the before/after filesystem snapshots the
    # flag promises). The bash builder never reads the flag — its archive is a
    # tar of the DESTDIR staging tree — so a recipe declaring it there deploys
    # its payload to the live filesystem and ships an archive without it. That
    # failed silently in both directions: the build passed and the live image
    # was correct, while every install from the archive was missing the package
    # content. Static scan of the build drivers + recipes; no chroot dependency.
    log "Running preflight-direct-install-lane gate (archive-payload contract)..."
    python3 "${SCRIPTS}/preflight-direct-install-lane.py" 2>&1 | tee -a "$BUILD_LOG"

    # Kernel-release lockstep gate: CONFIG_LOCALVERSION is stamped from
    # linux-kernel's release, so KERNELRELEASE — and every path named after it
    # in /boot and /usr/lib/modules — moves with that field. Recipes state those
    # paths by hand in verify_paths, and the hand-edit has been missed three
    # times (4->6, 6->7, then 7->8), each time surfacing hours later at squashfs
    # Step 4.5. This derives the expected value from the recipe build.sh reads
    # and refuses the build up front. Step 4.5 remains the landed-files proof.
    log "Running preflight-kernel-release-lockstep gate (verify_paths derivation)..."
    python3 "${SCRIPTS}/preflight-kernel-release-lockstep.py" 2>&1 | tee -a "$BUILD_LOG"

    # Licence-identifier gate: package.yml's `license:` is what the ISO SBOM
    # publishes as licenseDeclared and what the mirror index carries, and until
    # this gate nothing checked it. Declarations like `Public-Domain`,
    # `MIT-style` and `Various (redistributable)` propagated into both and
    # resolve for no SPDX consumer. Shape validation alone does not catch them
    # — every one of those is a well-formed token — so this checks membership
    # in the SPDX licence list bundled at config/spdx-license-list.json, and
    # checks WITH's right operand against the separate exception list. A
    # licence SPDX does not carry is declared LicenseRef-<Name>, which is
    # SPDX's own mechanism for exactly that. Deprecated-but-listed identifiers
    # pass and are reported as warnings: replacing GPL-2.0 with -only or
    # -or-later resolves an ambiguity only the package's licence text settles.
    # Static, milliseconds, no chroot and no network.
    log "Running preflight-license-identifiers gate (SPDX list membership)..."
    python3 "${SCRIPTS}/preflight-license-identifiers.py" 2>&1 | tee -a "$BUILD_LOG"

    # Silent-feature-loss gate (Scan B): for every package installed in the
    # prior-build chroot, cross-reference declared deps + BLFS-truth deps
    # against the configure log to surface declared-but-undetected and
    # undeclared-required-but-attempted patterns. Canonical case is the
    # Build #8 systemd-without-15-security-deps finding (master 55b4da4).
    # SKIPS cleanly when chroot data is absent (first-build / post-revert)
    # so this gate doesn't block bootstrap scenarios — it only catches
    # regressions against post-install state from a previous run.
    log "Running preflight-silent-loss scan (Scan B — silent feature loss)..."
    # On a --start-at resume with a POPULATED chroot (the targeted-build case),
    # a silent-loss SKIP means the gate could not reach data it should have —
    # hold it to --require-audit so a skip halts instead of waving through
    # (framework §3.5 step 3). On an empty chroot (from-scratch, or a resume
    # before the toolchain has run) the plain self-skip remains correct.
    # The hold's probe mirrors the auditor's own data requirement EXACTLY:
    # it reads installed state (var/lib/igos/packages) AND the per-package
    # build logs (mnt/intergenos/build/logs). phase_image destroys the logs
    # with the chroot copy, so a post-image resume (squashfs/ukis-verity/iso)
    # legitimately cannot be audited — the audit's anchor already fired at
    # the end of the final package phase in the same chain. Holding on the
    # installed dir alone fail-closed a squashfs resume on by-design-absent
    # logs (first firing, RC001 2026-08-15).
    local silent_loss_flags=()
    if [ "${RESUME_CONTEXT:-0}" = "1" ] && [ -d "${IGOS}/var/lib/igos/packages" ] \
       && [ -d "${IGOS}/mnt/intergenos/build/logs" ]; then
        log "  (resume with populated chroot — holding silent-loss to --require-audit)"
        silent_loss_flags+=(--require-audit)
    fi
    python3 "${SCRIPTS}/preflight-silent-loss.py" "${silent_loss_flags[@]}" 2>&1 | tee -a "$BUILD_LOG"

    # Bash-tier currency gate (decided 2026-08-06): the bash tiers have no
    # skip-built layer, so a core/base recipe that advanced after the
    # substrate's archive was sealed ships its stale build silently unless a
    # human remembers the tree-vs-archive sweep. On a --start-at resume with a
    # populated chroot, refuse any resume whose start phase skips a stale
    # bash-tier package's building phase (the gate reads sealed-archive
    # .PKGINFO — never the DB — and derives building phases from the drivers'
    # own run_package lines). Self-skips on an empty chroot.
    if [ "${RESUME_CONTEXT:-0}" = "1" ]; then
        log "Running preflight-bash-tier-currency gate (bash tiers' missing skip-built layer)..."
        python3 "${SCRIPTS}/preflight-bash-tier-currency.py" --start-at "${START_AT:-}" 2>&1 | tee -a "$BUILD_LOG"
        if [ "${PIPESTATUS[0]}" -ne 0 ]; then
            log "error: a stale bash-tier build would ship silently on this resume — cover the named packages"
            return 1
        fi
    fi

    # Chroot-vs-archive-union coverage gate (decided 2026-08-13, binding on
    # every build after R001): on a populated substrate, every built file
    # must be carried by a sealed archive or covered by the reviewed
    # allowlist — a chroot file no archive carries means the evaluated
    # system differs from every installed system (the stub class). The
    # script itself loud-skips on an empty archives corpus (from-scratch
    # launch); on package-phase resumes it fires fail-closed. Post-image
    # resumes skip it the same way the silent-loss hold does — the chroot
    # is terminal there and the gate's anchor already fired pre-capture.
    if [ "${RESUME_CONTEXT:-0}" = "1" ] && [ -d "${IGOS}/var/lib/igos/packages" ] \
       && [ -d "${IGOS}/mnt/intergenos/build/logs" ]; then
        log "Running chroot-archive-union coverage gate (build pre-flight)..."
        python3 "${SCRIPTS}/check-chroot-archive-union.py" --chroot "${IGOS}" \
            --allowlist /mnt/intergenos/config/chroot-archive-union-allowlist.txt \
            2>&1 | tee -a "$BUILD_LOG"
        if [ "${PIPESTATUS[0]}" -ne 0 ]; then
            log "error: the built chroot contains files no sealed archive carries — disposition per the gate's output before launching"
            return 1
        fi
    fi

    # Undeclared-build-dep gate (Scan A.2): for every package's source[0]
    # tarball, extract build-system files (configure.ac / meson.build /
    # CMakeLists.txt) and parse for the 5 dep-discovery patterns
    # (PKG_CHECK_MODULES, AC_CHECK_LIB, AC_CHECK_HEADERS, meson
    # dependency() / find_program(), cmake find_package(REQUIRED)).
    # Cross-reference against declared dependencies.build and emit HARD
    # findings for upstream-required deps that aren't declared. Catches
    # the class of bug that halted Build #8 at linux-pam (undeclared
    # docbook → meson xmllint check) and Build #9 at rpm 4.18.2
    # (undeclared lua → PKG_CHECK_MODULES). Conditional-context tracking
    # (shell if/fi, meson if/endif, AS_IF/AS_CASE) reduces false-positive
    # rate; comment-stripping prevents matches inside `#` and `dnl`
    # comments; build-system filter reads consumer build.sh and only
    # scans the buildfiles we actually invoke.
    #
    # First run extracts source tarballs into <repo>/build/scan-cache/
    # (~12 min). Subsequent runs hit the cache and complete in ~10s.
    log "Running preflight-undeclared-deps scan (Scan A.2 — undeclared build deps)..."
    python3 "${SCRIPTS}/preflight-undeclared-deps.py" --progress 2>&1 | tee -a "$BUILD_LOG"

    # Staged-kernel exclusivity (gate 8, decided 2026-07-12): a
    # release-bumped kernel rebuild orphans the prior release's module tree
    # + vmlinuz (release-named paths); downstream consumers pick ambiguously.
    # --allow-none: on a from-scratch run the chroot does not exist yet;
    # more than one staged kernel fails in every mode.
    log "Running preflight-single-kernel gate (staged-kernel exclusivity)..."
    bash "${SCRIPTS}/preflight-single-kernel.sh" --root /mnt/igos --allow-none 2>&1 | tee -a "$BUILD_LOG"
}

phase_verify_sources() {
    # Anti-supply-chain gate (design doc §5.1).
    # Audit every package.yml source: AND patches: entry with a sha256 against
    # the artifact on disk. Missing sha256 or mismatch = HARD FAIL.
    # build_artifacts: entries are NOT checked here — those are
    # audited at the manifest phase (Step 4).

    # Stage locally-vendored sources first. Packages whose source: is an
    # in-tree directory snapshot need their tarball regenerated to reflect
    # the current on-disk content before SHA verification runs, otherwise
    # edits to assets/* or installer/* are silently shadowed by the stale
    # snapshot. Same shape as chroot-rsync-coverage-gap.
    #
    # Two bundlers run here:
    #   - build-forge-tarball.sh: forge (installer/ tree + man/forge.1).
    #   - build-intergenos-source-tarballs.sh: intergen-welcome + intergenos-
    #     theme + 4 intergenos-extensions-* packages (sourced from assets/).
    #     Closes the D-003/D-004/D-017/J-003/J-018 reproducibility-script
    #     gap (theming-arc Item O).
    log "Staging locally-vendored sources (forge tarball)..."
    bash "$SCRIPTS/build-forge-tarball.sh" 2>&1 | tee -a "$BUILD_LOG"
    log "Staging locally-vendored sources (intergenos in-tree tarballs)..."
    bash "$SCRIPTS/build-intergenos-source-tarballs.sh" 2>&1 | tee -a "$BUILD_LOG"
    log "Staging locally-vendored sources (intergenos-wiki: rendered docs + signed page manifest)..."
    bash "$SCRIPTS/build-intergenos-wiki-tarball.sh" 2>&1 | tee -a "$BUILD_LOG"

    # Tarball-membership gate. Runs AFTER the three generators above, on
    # purpose: it must read the artifacts this build will actually use, never a
    # stale set left by a previous run.
    #
    # It asserts that every path a generated package's install step takes from
    # its extracted source tree is a member of that package's tarball. The
    # class it catches shipped a package that could not build for four days —
    # intergen-welcome's do_install installed org.intergenos.Wiki.svg from
    # release 19 while the generator never staged it, so every build from a
    # freshly generated tarball failed at `install: cannot stat`, and no other
    # check read the recipe and the generator together.
    #
    # A package the gate cannot parse is a FAILURE, not a skip: an unknown
    # consumed set would let the build read as covered while nothing was
    # checked.
    #
    # A package whose recipe declares `release_staged_source` is the one
    # exception, and only for an ABSENT tarball: its generator produces nothing
    # until a release-time input is staged (intergenos-wiki's rendered book).
    # That absence is reported as its own named unverified state instead of a
    # halt. A declared package whose tarball IS present is checked in full, and
    # an absent generated source is still fatal at the point that matters —
    # the builder refuses to build a package whose declared source is not on
    # disk.
    log "Verifying generated tarballs carry every file their recipes install..."
    python3 "$SCRIPTS/validate-tarball-membership.py" \
        --packages-dir "$PACKAGES_DIR" --sources-dir "$SOURCES" 2>&1 | tee -a "$BUILD_LOG"
    local MEMBERSHIP_RC=${PIPESTATUS[0]}
    if [ "$MEMBERSHIP_RC" -ne 0 ]; then
        log "FATAL: tarball-membership gate failed (rc=$MEMBERSHIP_RC) — refusing"
        log "       to build a package whose source tarball is missing a file its"
        log "       install step consumes. See the named paths above."
        exit 1
    fi

    # Auto-bump release: on first-party content change. Runs AFTER the
    # generators (so generated tarballs exist to hash) and BEFORE the build, so
    # a content change always advances the release the mirror/pkm sees — no
    # manual bump, and no "rebuilt it but the index never saw the new build"
    # footgun. Records a content_hash baseline in each first-party package.yml
    # and bumps release when the source content changed since that baseline.
    # Uses the SAME hashing as the builder's skip-built check
    # (igos-build/content_hash.py), so "what rebuilds" and "what re-releases"
    # can never drift. The host tree picks up the bump (committed after the
    # build). Idempotent across resumes (no change since baseline = no-op).
    log "Auto-bumping release on first-party content change..."
    python3 "$SCRIPTS/bump-changed-releases.py" \
        --packages-dir "$PACKAGES_DIR" --sources-dir "$SOURCES" 2>&1 | tee -a "$BUILD_LOG"
    local BUMP_RC=${PIPESTATUS[0]}
    if [ "$BUMP_RC" -ne 0 ]; then
        log "FATAL: release auto-bump failed (rc=$BUMP_RC) — refusing to build a"
        log "       mis-versioned package set. See log above."
        exit 1
    fi

    log "Verifying pinned source + patch SHAs against on-disk artifacts..."

    local PYSCRIPT PYEXIT UNPINNED MISMATCHES

    PYSCRIPT=$(python3 - "$PACKAGES_DIR" "$SOURCES" "$PATCHES" <<'PYEOF'
import sys, hashlib, os, re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FATAL: pyyaml required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

# Mirror igos_build.parser._resolve_variables: package.yml URLs and filenames
# carry ${name}/${version}/${version_major}/${version_major_minor}/${version_patch}
# placeholders that the build pipeline expands. verify-sources reads the YAML
# directly, so it must perform the same substitution before checking tarballs.
# If this set drifts from parser.py, audit both consumers.
_VAR_RE = re.compile(r"\$\{(\w+)\}")
def _resolve(text, variables):
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

packages_dir = Path(sys.argv[1])
sources_dir = Path(sys.argv[2])
patches_dir = Path(sys.argv[3])

unpinned = []
mismatches = []
build_artifacts_count = 0
patches_checked = 0

# Progress heartbeat: this sha sweep runs minutes-silent over ~1000 recipes,
# which is indistinguishable from a stall to a log-tailing reader and to the
# duration-budget watch (a fresh mtime proves liveness, not health). Purely
# additive INFO lines; verdicts are unchanged.
_all_ymls = sorted(packages_dir.rglob("package.yml"))
print(f"  [verify-sources] sweeping {len(_all_ymls)} recipes...", flush=True)
for _idx, yml_path in enumerate(_all_ymls, 1):
    if _idx % 100 == 0:
        print(f"  [verify-sources] {_idx}/{len(_all_ymls)} recipes checked", flush=True)
    # Per §1 B12: per-file YAML error handling. A malformed YAML file
    # used to produce a raw Python traceback that obscured which file
    # was bad. Catch + tag the file path so the operator can fix one
    # at a time instead of replaying tracebacks.
    try:
        with yml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        mismatches.append(f"{yml_path.relative_to(packages_dir)}: YAML parse error: {e}")
        continue

    if data is None:
        continue

    name = data.get("name", yml_path.parent.name)
    version = str(data.get("version", ""))
    version_parts = version.split(".")
    variables = {
        "name": name,
        "version": version,
        "version_major": version_parts[0] if version_parts else "",
        "version_major_minor": ".".join(version_parts[:2]) if len(version_parts) >= 2 else version,
        "version_patch": version_parts[2] if len(version_parts) >= 3 else "0",
    }
    src = data.get("source")
    build = data.get("build_artifacts", [])
    build_artifacts_count += len(build) if isinstance(build, list) else 0

    if not src or not isinstance(src, list):
        continue

    for i, item in enumerate(src):
        if not isinstance(item, dict):
            unpinned.append(f"{name}: source[{i}] malformed")
            continue
        url = _resolve(item.get("url", ""), variables)
        filename_raw = item.get("filename")
        if filename_raw:
            filename = _resolve(filename_raw, variables)
        else:
            filename = url.rsplit("/", 1)[-1].split("?")[0]
        tarball = sources_dir / filename

        # Generated first-party tarballs (generated: true) are built in-tree by
        # scripts/build-*-tarball*.sh and are NOT byte-pinnable — their
        # compressed bytes depend on the local tar/xz, so a committed sha would
        # be unportable across builders. Don't require/verify a pin; instead
        # assert the generator actually produced the tarball (a missing one is a
        # real build defect; different bytes are expected, not a fault).
        if item.get("generated"):
            if not tarball.exists():
                mismatches.append(f"{name}: {filename} (generated: true but the generator did not produce it)")
            continue

        sha = item.get("sha256")
        if not sha or not isinstance(sha, str) or len(sha) != 64:
            unpinned.append(f"{name}: {url} (no sha256 or invalid)")
            continue
        if not tarball.exists():
            mismatches.append(f"{name}: {filename} (not downloaded)")
            continue

        actual = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if actual != sha:
            mismatches.append(f"{name}: {filename} — expected={sha[:12]}... actual={actual[:12]}...")

    # Verify declared patches. The chroot's /sources/ contains both ${PATCHES}/*
    # and ${SOURCES}/* (build-intergenos.sh phase_setup copies both). Check
    # patches_dir first, fall back to sources_dir. A patch with no sha256, no
    # file on disk, or content mismatch is a HARD FAIL — this is the same
    # supply-chain gate that protects sources, extended to declared patches.
    patches = data.get("patches") or []
    if isinstance(patches, list):
        for j, pitem in enumerate(patches):
            if not isinstance(pitem, dict):
                unpinned.append(f"{name}: patches[{j}] malformed")
                continue
            pfile_raw = pitem.get("file")
            psha = pitem.get("sha256")
            if not pfile_raw:
                unpinned.append(f"{name}: patches[{j}] missing 'file'")
                continue
            pfile = _resolve(pfile_raw, variables)
            if not psha or not isinstance(psha, str) or len(psha) != 64:
                unpinned.append(f"{name}: patch {pfile} (no sha256 or invalid)")
                continue
            ppath = patches_dir / pfile
            if not ppath.exists():
                ppath = sources_dir / pfile
            if not ppath.exists():
                mismatches.append(f"{name}: patch {pfile} (not found in patches/ or sources/)")
                continue
            pactual = hashlib.sha256(ppath.read_bytes()).hexdigest()
            if pactual != psha:
                mismatches.append(f"{name}: patch {pfile} — expected={psha[:12]}... actual={pactual[:12]}...")
            patches_checked += 1

if unpinned:
    print("UNPINNED:", file=sys.stderr)
    for e in unpinned:
        print(f"  {e}", file=sys.stderr)
if mismatches:
    print("MISMATCHES:", file=sys.stderr)
    for e in mismatches:
        print(f"  {e}", file=sys.stderr)

if unpinned or mismatches:
    sys.exit(1)

print(f"OK: {build_artifacts_count} build_artifacts skipped, {patches_checked} patches verified, 0 source/patch SHAs un-pinned, 0 mismatches")
PYEOF
)
    PYEXIT=$?

    if [ "$PYEXIT" -ne 0 ]; then
        log "error: verify-sources failed. Fix the package.yml files or re-download"
        log "  the matching upstream tarballs before retrying the build."
        return "$PYEXIT"
    fi

    log "verify-sources: all source + patch SHAs verified"

    # Build-backend gate. Runs HERE, at the end of verify-sources, on purpose:
    # it reads the staged source distributions themselves, so it must run after
    # they are on disk AND after their SHAs are verified — checking the build
    # backend declared by bytes we have not authenticated would be reading an
    # attacker's pyproject.toml.
    #
    # Every recipe that builds a Python source distribution with
    # `pip --no-build-isolation` must supply the build backend that source's own
    # [build-system] table demands. That flag installs nothing for the build, so
    # a backend that is not already present cannot appear: the build dies at the
    # first wheel invocation with BackendUnavailable.
    #
    # The class was found on the timm recipe, which declared setuptools while
    # its pinned source declares pdm-backend — a set that could never have built
    # the package, and which nothing caught because no check read a recipe and
    # its own pinned source together.
    #
    # An undetermined verdict is a FAILURE, not a skip, for the same reason the
    # tarball-membership gate above treats one that way: a build that reads as
    # covered while nothing was checked is worse than a build that stops.
    log "Verifying recipes supply the build backends their pinned sources demand..."
    python3 "$SCRIPTS/preflight-build-backend.py" \
        --packages-dir "$PACKAGES_DIR" --sources-dir "$SOURCES" 2>&1 | tee -a "$BUILD_LOG"
    local BACKEND_RC=${PIPESTATUS[0]}
    if [ "$BACKEND_RC" -ne 0 ]; then
        log "FATAL: build-backend gate failed (rc=$BACKEND_RC) — refusing to"
        log "       build a package whose declared dependencies cannot supply"
        log "       the build backend its own pinned source requires. See the"
        log "       named recipes above; each names its correction."
        exit 1
    fi
}

# ==========================================================================
# Source-staging — idempotent helper used by phase_setup (the full-build path)
# AND by run_phase at the --start-at resume entry point. Closes the source-
# stage gap where a resume at --start-at <phase> would skip phase_setup and
# leave the chroot's /sources/ stale vs packages added to master since the
# last full phase_setup run.
#
# Captured 2026-05-12 after Build #9 r#21 halted at jemalloc 5.3.1 (extra
# tier, --start-at extra). Today's Wave 1b prereq landings (jemalloc,
# snappy, gflags, liburing, valkey, memcached, etcd, leveldb, rocksdb,
# scons) were all on master but their upstream tarballs had never been
# fetched to the host nor copied into the chroot, because phase_setup
# never ran on this resume.
#
# Historical pattern (POWER memory `feedback_source_stage_gap_on_start_at`)
# called for a manual `sudo cp` workaround per resume. This replaces that
# pattern with always-on automation.
# ==========================================================================

tiers_for_start_at() {
    # Echo the --tier flag set that download-sources.py needs, based on the
    # current --start-at value. Walks forward only — backtracking to fetch
    # sources for tiers that already completed is wasteful (their packages
    # are already in chroot + pkm-tracked).
    case "$START_AT" in
        ""|validate|verify-sources|setup)
            echo "--all" ;;
        toolchain|chroot-prep|chroot-tools|core|config|core-extra|base|kernel)
            echo "--tier core --tier base --tier desktop --tier ai --tier extra" ;;
        desktop)
            echo "--tier desktop --tier ai --tier extra" ;;
        ai)
            echo "--tier ai --tier extra" ;;
        extra|bootloader|image|manifest|publish)
            echo "--tier extra" ;;
        *)
            echo "--all" ;;
    esac
}

ensure_sources_staged() {
    # Idempotent: download missing tarballs to host, mirror host -> chroot.
    # Halts on download failure (set -euo pipefail in effect — non-zero
    # return propagates).
    #
    # Cheap when nothing's missing: download-sources.py is stat-only, cp -an
    # is no-op on already-present files. Only costs when there's real work.
    #
    # Tiers scoped to --start-at via tiers_for_start_at(): only fetch sources
    # for current + downstream tiers, not the entire tree.
    local tier_flags
    read -ra tier_flags <<< "$(tiers_for_start_at)"

    log "  Source-staging sweep (start-at=${START_AT:-<full-build>}, flags: ${tier_flags[*]})..."

    # Step 0: regenerate the in-tree-snapshot source tarballs (forge = the
    # installer/ tree; intergen-welcome + theme + extensions = assets/) from
    # LIVE on-disk content, BEFORE staging. These are `generated: true` sources
    # (no committed sha pin): the generators PRODUCE the tarballs and never
    # touch package.yml. phase_verify_sources does this on a full build, but a
    # --start-at resume SKIPS verify-sources -- so without this a resume rsyncs
    # a STALE host tarball into the chroot and builds old installer/asset code
    # (the forge-tarball-stale-on-resume class that would re-ship a pre-fix
    # Forge). The generators are deterministic: re-running on an unchanged tree
    # produces byte-identical tarballs, so this is cheap and the git tree stays
    # clean regardless — nothing is pinned, so nothing can drift.
    if [ -f "${SCRIPTS}/build-forge-tarball.sh" ]; then
        bash "${SCRIPTS}/build-forge-tarball.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi
    if [ -f "${SCRIPTS}/build-intergenos-source-tarballs.sh" ]; then
        bash "${SCRIPTS}/build-intergenos-source-tarballs.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi
    if [ -f "${SCRIPTS}/build-intergenos-wiki-tarball.sh" ]; then
        bash "${SCRIPTS}/build-intergenos-wiki-tarball.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi

    # Same tarball-membership gate phase_verify_sources fires, repeated here for
    # the same reason the generators are: a --start-at resume SKIPS
    # verify-sources, and a resume is exactly when a generator and a recipe are
    # most likely to have drifted apart since the last full build.
    if [ -f "${SCRIPTS}/validate-tarball-membership.py" ]; then
        python3 "${SCRIPTS}/validate-tarball-membership.py" \
            --packages-dir "${PACKAGES_DIR}" --sources-dir "${SOURCES}" 2>&1 | tee -a "$BUILD_LOG"
        local MEMBERSHIP_RC=${PIPESTATUS[0]}
        if [ "$MEMBERSHIP_RC" -ne 0 ]; then
            log "error: tarball-membership gate failed (rc=${MEMBERSHIP_RC}) — halting"
            log "       before chroot-stage; a generated tarball is missing a file"
            log "       its recipe installs, or a recipe could not be determined."
            log "       (A tarball absent because its source is staged at release"
            log "        time is NOT this failure — such a package declares"
            log "        release_staged_source and is reported, not halted on.)"
            exit 1
        fi
    fi

    # Step 1: fetch any missing tarballs on the host. download-sources.py is
    # idempotent (only downloads what isn't cached + sha256-verifies what is)
    # so a corrupted prior fetch surfaces as a verify failure here.
    if ! python3 "${SCRIPTS}/download-sources.py" "${tier_flags[@]}" 2>&1 | tee -a "$BUILD_LOG"; then
        log "error: download-sources.py failed — halting before chroot-stage"
        return 1
    fi

    # Step 2: mirror host /mnt/intergenos/build/sources/ -> chroot
    # /mnt/igos/sources/. rsync -a = archive + diff-aware: skips files
    # that are byte-identical on both sides (cheap for unchanged tarballs),
    # mirrors changes when host file differs from chroot file (size/mtime
    # delta detection). Replaces the prior cp -an (no-clobber) which only
    # mirrored NEW filenames — silently preserved stale chroot copies when
    # a vendor tarball was regenerated without a version bump (i.e.
    # same filename, different content). First case hit was influxdb
    # 3.9.0's vendor regen for the cargo-vendor-gen git-source config fix.
    mkdir -p "$IGOS/sources"
    chmod a+wt "$IGOS/sources"
    rsync -a "${SOURCES}/" "$IGOS/sources/" 2>/dev/null || true
    rsync -a "${PATCHES}/" "$IGOS/sources/" 2>/dev/null || true

    local count=$(ls "$IGOS/sources" 2>/dev/null | wc -l)
    log "  Sources staged: $count files in $IGOS/sources/"

    # Same build-backend gate phase_verify_sources fires, repeated here for the
    # same reason the tarball-membership gate above is: a --start-at resume
    # SKIPS verify-sources entirely, and a resume is exactly when a recipe and
    # its pinned source are most likely to have drifted apart — a version bump
    # can change a source's build backend without touching the recipe's declared
    # dependencies at all.
    #
    # It runs at the END of staging, after download-sources.py has fetched
    # whatever was missing, because it reads the staged sources.
    if [ -f "${SCRIPTS}/preflight-build-backend.py" ]; then
        python3 "${SCRIPTS}/preflight-build-backend.py" \
            --packages-dir "${PACKAGES_DIR}" --sources-dir "${SOURCES}" 2>&1 | tee -a "$BUILD_LOG"
        local BACKEND_RC=${PIPESTATUS[0]}
        if [ "$BACKEND_RC" -ne 0 ]; then
            log "error: build-backend gate failed (rc=${BACKEND_RC}) — halting"
            log "       before chroot-stage; a recipe cannot supply the build"
            log "       backend its pinned source demands, or a verdict could"
            log "       not be determined."
            exit 1
        fi
    fi
}

phase_setup() {
    # Create build root
    if [ ! -d "$IGOS" ]; then
        mkdir -p "$IGOS"
    fi
    chown "${BUILD_USER}:${BUILD_USER}" "$IGOS"
    chmod 755 "$IGOS"
    log "  /mnt/igos owned by $BUILD_USER"

    # Create LFS directory layout (Section 4.2)
    # These directories and symlinks must exist before the toolchain build
    mkdir -pv "$IGOS"/{etc,var} "$IGOS"/usr/{bin,lib,sbin}
    for i in bin lib sbin; do
        if [ ! -L "$IGOS/$i" ]; then
            ln -sv "usr/$i" "$IGOS/$i"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "$IGOS/lib64" ;;
    esac
    # Tools directory for cross-toolchain
    mkdir -pv "$IGOS/tools"
    # Narrow chown to the specific LFS-layout subdirs just created. The bare
    # `chown -R "$IGOS"` form is wrong on resume builds: if /mnt/igos already
    # contains /proc, /sys, /dev pseudo-FS mounts (left from a prior
    # chroot-prep), chown walks them and emits ~1800 "Operation not permitted"
    # errors. Worse, on a populated chroot from a partial prior build it
    # rewrites root-owned system files (/etc/shadow, setuid binaries) to
    # BUILD_USER, breaking the trust model. Canonical narrow pattern from
    # chroot-setup.sh:72; `2>/dev/null || true` swallows residual no-ops.
    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"/{etc,var,usr,tools} 2>/dev/null || true
    case $(uname -m) in
        x86_64) chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS/lib64" 2>/dev/null || true ;;
    esac
    # Symlinks bin/lib/sbin → usr/{bin,lib,sbin}: chown the symlink itself
    # (-h) so a stat lists BUILD_USER. Targets covered by the usr/ chown above.
    for i in bin lib sbin; do
        [ -L "$IGOS/$i" ] && chown -h "${BUILD_USER}:${BUILD_USER}" "$IGOS/$i" 2>/dev/null || true
    done
    log "  LFS directory layout created (Section 4.2)"

    # Verify virtiofs
    if ! mount | grep -q "intergenos.*virtiofs"; then
        log "error: /mnt/intergenos not mounted via virtiofs"
        return 1
    fi
    log "  virtiofs mount OK"

    # Verify critical sources exist
    local missing=0
    for src in binutils-2.46.0.tar.xz gcc-15.2.0.tar.xz glibc-2.43.tar.xz \
               linux-6.18.10.tar.xz gmp-6.3.0.tar.xz mpfr-4.2.2.tar.xz mpc-1.3.1.tar.gz; do
        if [ ! -f "${SOURCES}/$src" ]; then
            log "  missing: $src"
            missing=$((missing + 1))
        fi
    done
    if [ $missing -gt 0 ]; then
        log "error: $missing critical source tarballs missing from $SOURCES"
        return 1
    fi

    local total=$(ls "$SOURCES" | wc -l)
    log "  Sources: $total tarballs on host"

    # Verify patches
    if [ ! -f "${PATCHES}/glibc-fhs-1.patch" ]; then
        log "error: glibc-fhs-1.patch missing from $PATCHES"
        return 1
    fi
    log "  Patches: OK"

    # --- Place everything directly on the target filesystem ---
    # Like build_003: no bind mounts, no tricks. The chroot is self-contained.
    # Everything the chroot needs is physically present on $IGOS.

    # Stage source tarballs + patches into the chroot. Delegates to the
    # shared ensure_sources_staged() helper so the same logic runs on
    # --start-at resumes (wired in run_phase at the resume entry point).
    # ensure_sources_staged() also runs download-sources.py first to
    # backfill any missing tarballs on the host.
    ensure_sources_staged

    # Copy build infrastructure (scripts, packages, igos-build)
    # Preserves paths so /mnt/intergenos/scripts/... works inside the chroot
    log "  Copying build infrastructure to $IGOS/mnt/intergenos/..."
    mkdir -pv "$IGOS/mnt/intergenos"
    cp -a /mnt/intergenos/scripts    "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/packages   "$IGOS/mnt/intergenos/"
    cp -a /mnt/intergenos/igos-build "$IGOS/mnt/intergenos/"
    # pkm is a runtime dependency of igos-build/tracker.py (per RFC v1
    # 2026-05-01: tracker imports pkm.database._sha256 for tracker/verifier
    # parity). Without this sync, desktop-phase Python orchestrator fails
    # with ModuleNotFoundError on import.
    cp -a /mnt/intergenos/pkm        "$IGOS/mnt/intergenos/"
    # intergen source must be copied into the chroot for phase_ai. The
    # ai-tier build.sh references /mnt/intergenos/intergen/*.py and the
    # subdirs (interfaces/, tools/, tests/). Without this copy, phase_ai
    # halts at intergen with `cp: cannot stat ...`. Build #6 Halt at
    # intergen 2026-05-09 surfaced the omission.
    cp -a /mnt/intergenos/intergen   "$IGOS/mnt/intergenos/"
    cp    /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    # Repo-root files packages read at build time. SOURCES.md is the single
    # authored copy of the source-availability statement and is what
    # packages/core/intergenos-legal installs onto every system (that recipe
    # kept its own hand-carried copy until 2026-08-19, and it had drifted).
    # The dir copies above never reach repo-root files, so this copy and the
    # matching one in sync_chroot_scripts are what make it reachable in the
    # chroot; NOT masked with `|| true` — a missing legal notice must halt the
    # build, not ship absent.
    cp    /mnt/intergenos/SOURCES.md "$IGOS/mnt/intergenos/"
    # The shim SBAT CSV is consumed inside the chroot by
    # check-sbat-generations.sh (default SHIM_SBAT path), which
    # build-grub-standalone.sh fires during phase_bootloader. docker/ as a
    # whole is deliberately NOT copied; only the sbat CSV dir is needed.
    # Without it the checker's fail-closed not-found branch refuses the
    # bootloader phase. Same chroot-rsync-coverage class as docs/ + assets/.
    mkdir -p "$IGOS/mnt/intergenos/docker/shim-build/sbat"
    cp -a /mnt/intergenos/docker/shim-build/sbat/. "$IGOS/mnt/intergenos/docker/shim-build/sbat/"
    log "  Build infrastructure placed on target filesystem"

    # Narrow chown of build root: only subdirs we created or rsynced into.
    # Skips /proc, /sys, /dev pseudo-FS mounts on resume builds; preserves
    # root-owned system files on a populated chroot. See full rationale at
    # the sibling chown in the LFS-layout site above.
    chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS"/{etc,var,usr,tools,mnt} 2>/dev/null || true
    case $(uname -m) in
        x86_64) chown -R "${BUILD_USER}:${BUILD_USER}" "$IGOS/lib64" 2>/dev/null || true ;;
    esac
    for i in bin lib sbin; do
        [ -L "$IGOS/$i" ] && chown -h "${BUILD_USER}:${BUILD_USER}" "$IGOS/$i" 2>/dev/null || true
    done
    log "  Build root: $IGOS ready (self-contained)"
}

phase_toolchain() {
    # Toolchain must run as the build user, NOT root
    # env -i wipes ALL host variables (LFS 13.0 Section 4.4 requirement)
    # Only HOME, TERM, and PATH survive — prevents host CFLAGS, LD_LIBRARY_PATH, etc.
    # from contaminating the cross-compilation
    # The forensic-trace sink must be writable by the build user (these phases
    # run as $BUILD_USER, not root) or trace_init fails and toolchain capture is
    # silently lost. Make the run's sink root group/other-writable.
    if [ -n "${IGOS_TRACE_ROOT:-}" ]; then
        mkdir -p "${IGOS_TRACE_ROOT}" 2>/dev/null || true
        chmod 0777 "${IGOS_TRACE_ROOT}" 2>/dev/null || true
    fi
    log "Running cross-toolchain build as $BUILD_USER (Ch 5)..."
    # setpriv, NOT `su -` (work-plan 1.7, proven on the build VM 2026-07-07):
    # su opens a PAM session and pam_systemd migrates it into a session
    # scope under user-<uid>.slice, escaping this build's transient unit
    # cgroup — so `systemctl kill/stop <unit>` MISSED the entire toolchain
    # build (the GE-01 glibc recursion survived exactly this way; the
    # duration-budget tripwire depends on kill killing). setpriv drops
    # privileges with NO PAM and NO cgroup migration: children stay in the
    # unit and die with it (empirically proven both ways: the su- child
    # landed in a session scope and survived a unit stop; the setpriv child
    # stayed in the unit cgroup and died with it). The login-shell semantics
    # su provided were already unused — the command rebuilds its env from
    # scratch below.
    # Forensic-trace env MUST be threaded through env -i (which wipes the
    # environment) or toolchain-build.sh runs with trace disabled and emits
    # zero byte-capture. Each VAR=VAL is a single quoted argv element passed
    # straight to env — no intermediate shell ever re-parses them (which
    # also retires the old ${TERM@Q} re-evaluation hazard from the su -c
    # string form).
    setpriv --reuid "$BUILD_USER" --regid "$(id -g "$BUILD_USER")" --init-groups \
        env -i "HOME=/home/${BUILD_USER}" "TERM=${TERM:-}" \
        "IGOS_BUILD_DEBUG_VERBOSE=${IGOS_BUILD_DEBUG_VERBOSE:-}" \
        "IGOS_TRACE_RUNID=${IGOS_TRACE_RUNID:-}" \
        "IGOS_TRACE_START_TS=${IGOS_TRACE_START_TS:-}" \
        "IGOS_TRACE_ROOT=${IGOS_TRACE_ROOT:-}" \
        bash "${SCRIPTS}/toolchain-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Check if toolchain produced the expected output
    if [ ! -x "${IGOS}/tools/bin/${IGOS_TARGET}-gcc" ]; then
        log "error: Toolchain build did not produce ${IGOS_TARGET}-gcc"
        return 1
    fi
    log "  Cross-toolchain verified: ${IGOS_TARGET}-gcc exists"

    # Temp tools (Ch 6) — cross-compiled utilities needed inside the chroot
    log "Running temp-tools build as $BUILD_USER (Ch 6)..."
    # setpriv, not `su -` — same cgroup-containment rationale as the
    # toolchain invocation above (work-plan 1.7).
    setpriv --reuid "$BUILD_USER" --regid "$(id -g "$BUILD_USER")" --init-groups \
        env -i "HOME=/home/${BUILD_USER}" "TERM=${TERM:-}" \
        "IGOS_BUILD_DEBUG_VERBOSE=${IGOS_BUILD_DEBUG_VERBOSE:-}" \
        "IGOS_TRACE_RUNID=${IGOS_TRACE_RUNID:-}" \
        "IGOS_TRACE_START_TS=${IGOS_TRACE_START_TS:-}" \
        "IGOS_TRACE_ROOT=${IGOS_TRACE_ROOT:-}" \
        bash "${SCRIPTS}/temp-tools-build.sh" 2>&1 | tee -a "$BUILD_LOG"
    # Verify coreutils installed (env is needed for chroot entry)
    if [ ! -x "${IGOS}/usr/bin/env" ]; then
        log "error: Temp-tools build did not produce /usr/bin/env (coreutils)"
        return 1
    fi
    log "  Temp-tools verified: /usr/bin/env exists"
}

phase_chroot_prep() {
    log "Setting up chroot environment..."
    bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"

    # Verify mounts
    if ! mountpoint -q "${IGOS}/dev"; then
        log "error: ${IGOS}/dev not mounted"
        return 1
    fi
    log "  Chroot mounts verified"
}

# K23: Apply --start-at-pkg CLI flag for the matching phase only.
#
# The orchestrator unsets IGOS_START_AT at the top of every phase function
# as a defensive measure against ambient-shell env-var leakage (see Build
# #9 incident, 2026-05-13, documented in phase_base comment). That defense
# is necessary BUT it also blocks the legitimate "resume at package N
# within phase X" use case (Rule #22 + docs/operations/02).
#
# This helper interposes AFTER the defensive unset: if the operator
# explicitly passed --start-at-pkg <name> on the CLI AND we're entering
# the phase named by --start-at, restore IGOS_START_AT so the inner
# chroot-build-*.sh script's resume-skip logic activates. One-shot: clear
# START_AT_PKG after the first apply so subsequent phases run from their
# normal start (no leak into phase_base from phase_core_extra, etc.).
#
# Call at the top of each phase function that supports per-package resume,
# AFTER the unset and BEFORE the chroot-enter invocation:
#
#   phase_core_extra() {
#       unset IGOS_START_AT IGOS_STOP_AFTER
#       apply_start_at_pkg "core-extra"
#       ...
#   }
apply_start_at_pkg() {
    local phase="$1"
    if [ -n "${START_AT_PKG:-}" ] && [ "$phase" = "${START_AT:-}" ]; then
        export IGOS_START_AT="$START_AT_PKG"
        log "  --start-at-pkg ${START_AT_PKG} (explicit CLI; defeats ambient-env defense for phase=${phase} only)"
        START_AT_PKG=""  # one-shot — subsequent phases unset normally
    fi
}

phase_chroot_tools() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    log "Building temporary tools in chroot..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build.sh" 2>&1 | tee -a "$BUILD_LOG"
}

sync_chroot_scripts() {
    command -v rsync >/dev/null || { log "error: rsync required but not installed"; return 1; }

    # Ensure chroot virtual filesystems are mounted.
    # When using --start-at to resume from a later phase, the chroot-prep
    # phase (which normally mounts these) is skipped. Without mounts,
    # chroot-enter.sh refuses to enter.
    if ! mountpoint -q "${IGOS}/dev" 2>/dev/null; then
        log "  Chroot not mounted — running chroot-setup.sh..."
        bash "${SCRIPTS}/chroot-setup.sh" 2>&1 | tee -a "$BUILD_LOG"
    fi

    # Sync scripts and packages into the chroot copy.
    # The setup phase copies build infrastructure to $IGOS/mnt/intergenos/,
    # but --start-at skips setup and code changes between restarts aren't
    # reflected. This ensures the chroot always has the latest.
    log "  Syncing scripts into chroot..."
    rsync -a --delete /mnt/intergenos/scripts/   "$IGOS/mnt/intergenos/scripts/"
    rsync -a --delete /mnt/intergenos/packages/  "$IGOS/mnt/intergenos/packages/"
    rsync -a --delete /mnt/intergenos/config/    "$IGOS/mnt/intergenos/config/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/installer/ "$IGOS/mnt/intergenos/installer/" 2>/dev/null || true
    # docs/ sync added 2026-05-23: defense against the silent-no-build
    # class — packages that reference /mnt/intergenos/docs/<file> at
    # build time (signing-key.asc, license bundles, etc.) need the file
    # reachable inside the chroot. Surfaced when intergenos-keyring
    # exit-1'd on missing /mnt/intergenos/docs/signing-key.asc; that
    # specific package was refactored to ship its own bundled artifact
    # (see packages/core/intergenos-keyring/build.sh header), but the
    # sync is kept as defense for any future package that legitimately
    # consumes docs/ at build time.
    rsync -a --delete /mnt/intergenos/docs/      "$IGOS/mnt/intergenos/docs/"      2>/dev/null || true
    # assets/ sync added 2026-05-23 17:51 CDT: 4 desktop-tier packages
    # (intergen-firstboot, intergen-mark, intergen-pkm-notifier,
    # intergen-no-overview) cp -a from /mnt/intergenos/assets/<name>/
    # at do_install time. assets/ holds first-party design source content
    # (GNOME-shell extensions, brand-mark icon stack, theming source);
    # intergen-mark/build.sh explicitly documents the anti-duplication SoT
    # discipline of keeping these files single-sourced under assets/
    # rather than per-package. Same chroot-rsync-coverage class as the
    # docs/ gap; same fix shape.
    rsync -a --delete /mnt/intergenos/assets/    "$IGOS/mnt/intergenos/assets/"    2>/dev/null || true
    # Sync Python builder for desktop tier (igos-build + its pkm dependency
    # per RFC v1 tracker/verifier parity)
    rsync -a /mnt/intergenos/igos-build.py "$IGOS/mnt/intergenos/" 2>/dev/null || true
    # Repo-root SOURCES.md (parity with the phase_setup copy above): the single
    # authored source-availability statement, installed by
    # packages/core/intergenos-legal. Unmasked for the same reason.
    rsync -a /mnt/intergenos/SOURCES.md "$IGOS/mnt/intergenos/"
    rsync -a --delete /mnt/intergenos/igos-build/   "$IGOS/mnt/intergenos/igos-build/" 2>/dev/null || true
    rsync -a --delete /mnt/intergenos/pkm/          "$IGOS/mnt/intergenos/pkm/"        2>/dev/null || true
    # intergen source for phase_ai (parity with phase_setup copy above)
    rsync -a --delete /mnt/intergenos/intergen/     "$IGOS/mnt/intergenos/intergen/"   2>/dev/null || true
    # shim SBAT CSV for the in-chroot check-sbat-generations.sh firing in
    # phase_bootloader (parity with the phase_setup copy; the checker's
    # not-found branch is fail-closed and refuses the phase without it)
    mkdir -p "$IGOS/mnt/intergenos/docker/shim-build/sbat"
    rsync -a --delete /mnt/intergenos/docker/shim-build/sbat/ "$IGOS/mnt/intergenos/docker/shim-build/sbat/"
}

phase_core() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    apply_start_at_pkg "core"  # K23: restore IGOS_START_AT if --start-at-pkg explicit
    sync_chroot_scripts
    log "Building core system in chroot (Ch 8, LFS order)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch8.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_config() {
    # Clear IGOS_START_AT / IGOS_STOP_AFTER so per-package resume
    # context from one phase doesn't leak into subsequent phases
    # (config, core-extra, kernel).
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Configuring system in chroot (Ch 9)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-config-ch9.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_core_extra() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    apply_start_at_pkg "core-extra"  # K23: restore IGOS_START_AT if --start-at-pkg explicit
    sync_chroot_scripts
    log "Building additional core packages in chroot (BLFS)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-core-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_base() {
    # Build the base tier — end-user CLI tools (htop, rsync, strace, screen,
    # etc.) that aren't core build dependencies but are expected on every
    # InterGenOS install. The base orchestration was dormant from 2026-04-04
    # (commit 45421d7 unified into chroot-build-tier.sh, then 66ef3da
    # restored chroot-build-base.sh from archive but never re-wired it into
    # build-intergenos.sh). 2026-05-09 stub-audit follow-up surfaced the
    # gap — without this phase, 16 base-tier packages were silently skipped
    # at install-time, degrading the user-facing CLI surface against the
    # Prime Directive.
    #
    # 2026-05-13 Build #9 audit: phase_base re-wired but ran 0-second because
    # an ambient IGOS_START_AT in the operator shell leaked through into
    # chroot-build-base.sh — every run_package returned 0 via the SKIP
    # logic without building anything. Result: 17 of 19 base packages
    # missing in chroot, surfaced 18h later when libreoffice configure
    # couldn't find /usr/bin/zip. See clear_per_pkg_resume_env() — unset
    # before chroot-build-*.sh invoke. K23 restores per-package resume via
    # the explicit-intent --start-at-pkg CLI flag (apply_start_at_pkg
    # below); the env-var ambient-leakage defense remains intact.
    unset IGOS_START_AT IGOS_STOP_AFTER
    apply_start_at_pkg "base"  # K23: restore IGOS_START_AT if --start-at-pkg explicit
    sync_chroot_scripts
    log "Building base packages in chroot (end-user CLI tools)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-base.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_kernel() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building kernel in chroot (Ch 10)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ch10.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_desktop() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building desktop packages in chroot (GNOME + dependencies)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-desktop.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_ai() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building AI tier packages in chroot (InterGen assistant)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-ai.sh" 2>&1 | tee -a "$BUILD_LOG"

    # END-OF-BUILD silent-feature-loss GATE (decided 2026-06-25).
    # The anchor FOLLOWS the FINAL package phase — the same principle as the
    # --stop-after capture halt: fire at the earliest moment the full package
    # corpus is built, before phase_bootloader. That phase is `ai` under the
    # 2026-07-21 reorder (desktop → extra → compute → ai); it was `extra`
    # before it, and this call site moved with the reorder (drift caught
    # 2026-07-22 — the gate briefly fired mid-build at end-of-extra, leaving
    # compute+ai rebuilds unaudited). If tiers ever reorder again, re-derive
    # the anchor from the run_phase block; never assume the tier name.
    # The copy wired into phase_validate runs PRE-chroot and SKIPs (exit 0),
    # so it has been a no-op; this run audits the real, just-built chroot and
    # a finding HALTS the build (set -euo pipefail → non-zero from the gate
    # aborts before phase_bootloader), so a silently-degraded package can
    # never reach the ISO.
    # --require-audit closes the fail-open WC flagged 2026-06-25: at THIS call site
    # the chroot MUST be populated, so a SKIP (chroot/BLFS data absent) is not a
    # pass — it means the audit could not run, and must halt (exit 3) rather than
    # be waved through as exit 0. (The phase_validate copy keeps the exit-0 skip:
    # there the empty post-revert chroot is the legitimate bootstrap case.)
    # Resolve a finding by wiring the dep + build-order, or declaring
    # `silent_loss_accepted:` with a one-line rationale (gate's sanctioned path).
    log ""
    log "Running END-OF-BUILD silent-feature-loss gate (full chroot audit, post-ai — final package phase)..."
    python3 "${SCRIPTS}/preflight-silent-loss.py" --require-audit 2>&1 | tee -a "$BUILD_LOG"
    # Explicit ${PIPESTATUS[0]} guard (P1, post-burn sweep): the gate's non-zero
    # must halt on its OWN merits, not only via `set -o pipefail` propagating
    # through the tee — the same fail-closed shape the tier-validator gate uses
    # (~L805). Defense-in-depth so a future pipefail change cannot silently
    # reopen this fail-open (exit 1 = findings; exit 3 = audit could not run).
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "error: END-OF-BUILD silent-feature-loss gate exited nonzero — a silently-degraded package would otherwise reach the ISO; halting (fail-closed). Resolve per the gate output above (wire the dep + build-order, or declare silent_loss_accepted with a rationale)."
        return 1
    fi
    log "  silent-feature-loss gate PASSED — no silently-dropped features."
}

phase_compute() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building compute tier packages in chroot (opt-in GPU SDKs, mirror-only)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-compute.sh" 2>&1 | tee -a "$BUILD_LOG"
}

phase_extra() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    sync_chroot_scripts
    log "Building extra tier packages in chroot (user applications)..."
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-extra.sh" 2>&1 | tee -a "$BUILD_LOG"
    # NOTE: the END-OF-BUILD silent-feature-loss gate lived here while `extra`
    # was the final package phase; it moved to phase_ai with the 2026-07-21
    # tier reorder (the anchor follows the final package phase — see phase_ai).
}

# Pin SOURCE_DATE_EPOCH for the artifact-assembly pipeline (bootloader →
# squashfs → ukis-verity → iso) so those phases are build-reproducible by
# construction, not merely replay-reproducible via the printed replay
# command. Decided 2026-07-15 (build-trace audit action item): derive the
# epoch from the recipe tip's commit time. Precedence:
#   1. caller-provided SOURCE_DATE_EPOCH (respected verbatim);
#   2. live `git log -1 --format=%ct` on the mounted tree (works when the
#      tree is a full clone);
#   3. the host-stamped build/.recipe-epoch (written by the versioned
#      .githooks/post-commit|post-checkout|post-merge hooks — needed when
#      the mounted tree is a git WORKTREE whose .git pointer does not
#      resolve on this side of the virtiofs boundary);
#   4. nothing — downstream scripts keep their existing LOUD wall-clock
#      fallback (never a silent default).
# Deriving from the recipe tip also keeps the epoch IDENTICAL across the
# ceremony-resume chain (squashfs in one invocation, iso in a later one),
# which per-invocation wall-clock could never guarantee.
ensure_source_date_epoch() {
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        log "  SOURCE_DATE_EPOCH already set by caller: ${SOURCE_DATE_EPOCH} (respected)"
        return 0
    fi
    local epoch=""
    if epoch=$(git -C /mnt/intergenos log -1 --format=%ct 2>/dev/null) \
            && [[ "$epoch" =~ ^[0-9]+$ ]]; then
        export SOURCE_DATE_EPOCH="$epoch"
        log "  SOURCE_DATE_EPOCH pinned from recipe tip (live git): ${epoch}"
        return 0
    fi
    local stamp="/mnt/intergenos/build/.recipe-epoch"
    if [ -s "$stamp" ]; then
        epoch=$(head -n1 "$stamp")
        if [[ "$epoch" =~ ^[0-9]+$ ]]; then
            export SOURCE_DATE_EPOCH="$epoch"
            log "  SOURCE_DATE_EPOCH pinned from recipe tip (stamp ${stamp}): ${epoch}"
            return 0
        fi
        log "  warning: ${stamp} is malformed ('${epoch}') — ignoring it"
    fi
    log "  warning: recipe-tip epoch unavailable (git unresolvable here, no usable stamp);"
    log "           downstream scripts will fall back LOUDLY to wall-clock."
    return 0
}

phase_bootloader() {
    # See clear_per_pkg_resume_env() — unset before chroot-build-*.sh invoke.
    unset IGOS_START_AT IGOS_STOP_AFTER
    ensure_source_date_epoch
    sync_chroot_scripts

    # B-003 (T0-2 2026-05-18): wipe stale .signed before rebuilding. The
    # signing-ceremony output writes .signed files alongside the unsigned
    # .efi; on a fresh phase_bootloader run those .signed reflect a prior
    # cycle's artifacts. Re-signing a fresh build against stale .signed
    # in the directory would silently mix lineages — the cycle-5 manifest
    # vs ESP-content mismatch class. Always start a bootloader rebuild
    # from a clean directory.
    local host_bootloader_dir="/mnt/intergenos/build/bootloader"
    if [ -d "$host_bootloader_dir" ]; then
        local stale_signed=()
        while IFS= read -r -d '' s; do
            stale_signed+=( "$s" )
        done < <(find "$host_bootloader_dir" -maxdepth 1 -name '*.efi.signed' -print0 2>/dev/null)
        if (( ${#stale_signed[@]} > 0 )); then
            log "  wiping ${#stale_signed[@]} stale .signed artifact(s) before rebuild"  # B-003
            rm -f "${stale_signed[@]}"
        fi
    fi

    log "Assembling unsigned bootloader artifacts in chroot..."
    log "  (grubx64.efi + initramfs.cpio.gz + igos-live.efi UKI)"
    log ""
    bash "${SCRIPTS}/chroot-enter.sh" "${SCRIPTS}/chroot-build-bootloader.sh" 2>&1 | tee -a "$BUILD_LOG"
    log ""
    log "  Bootloader artifacts at: ${IGOS}/mnt/intergenos/build/bootloader/"

    # Copy bootloader artifacts from chroot to host-visible build dir so
    # phase_iso (and operator ceremony scripts at /mnt/intergenos/build/
    # bootloader/) can access them after phase_image cleans the chroot.
    # The chroot is a self-contained copy of the target filesystem (no
    # bind mount), so the copy is mandatory.
    local chroot_bootloader_dir="${IGOS}/mnt/intergenos/build/bootloader"
    mkdir -p "$host_bootloader_dir"
    if [ -d "$chroot_bootloader_dir" ]; then
        cp -av "$chroot_bootloader_dir"/*.efi "$host_bootloader_dir/" 2>&1 | tee -a "$BUILD_LOG" || true
        # Also copy initramfs.cpio.gz if present (UKI is the canonical path,
        # but the standalone initramfs is useful for diagnostic boots).
        [ -f "$chroot_bootloader_dir/initramfs.cpio.gz" ] && \
            cp -av "$chroot_bootloader_dir/initramfs.cpio.gz" "$host_bootloader_dir/" 2>&1 | tee -a "$BUILD_LOG"
        # Copy the host-side UKI-build inputs staged by chroot-build-bootloader.sh
        # (3/3 vmlinuz + os-release, 2.7/3 microcode cpios). Since the 2026-05-28
        # dm-verity reorder, the UKIs are assembled in phase_ukis_verity from the
        # HOST after phase_image tears down the chroot's /mnt/intergenos — so these
        # must be copied out here or ukis-verity exit-1's ("vmlinuz not staged").
        for art in vmlinuz os-release intel-ucode.img amd-ucode.img; do
            [ -f "$chroot_bootloader_dir/$art" ] && \
                cp -av "$chroot_bootloader_dir/$art" "$host_bootloader_dir/" 2>&1 | tee -a "$BUILD_LOG"
        done
        # B1 (USA-1 audit S-W2 closure): stage shimx64.efi from the shim-signed
        # package install path. shim is shipped by packages/core/shim-signed/
        # at /usr/share/shim-signed/ NOT in the chroot's build/bootloader/ tree,
        # so the *.efi glob above doesn't catch it. phase_iso requires shim at
        # $host_bootloader_dir/ and exit-1's on absence — without this copy,
        # every full end-to-end build halts at phase_iso.
        local chroot_shim="${IGOS}/usr/share/shim-signed/shimx64.efi"
        if [ -f "$chroot_shim" ]; then
            cp -av "$chroot_shim" "$host_bootloader_dir/shimx64.efi" 2>&1 | tee -a "$BUILD_LOG"
        else
            log "  warning: shim not found at $chroot_shim — shim-signed package install missing"
            log "  phase_iso will fail: shim required at $host_bootloader_dir/shimx64.efi"
        fi
        # PI-ge9b04-A: MokManager rides with shim. shim only looks for
        # mmx64.efi in the directory it was launched from; a live ESP without
        # it dead-ends an SB=ON boot on virgin hardware at Verification
        # Failed (0x1A) with NO enrollment path (and with MokNew pending it
        # bootloops). Same Fedora-MS-signed provenance as shim itself.
        local chroot_mokmanager="${IGOS}/usr/share/shim-signed/mmx64.efi"
        if [ -f "$chroot_mokmanager" ]; then
            cp -av "$chroot_mokmanager" "$host_bootloader_dir/mmx64.efi" 2>&1 | tee -a "$BUILD_LOG"
        else
            log "  warning: MokManager not found at $chroot_mokmanager — shim-signed package install missing"
            log "  phase_iso will fail: MokManager required at $host_bootloader_dir/mmx64.efi"
        fi
        log "  Bootloader artifacts copied to host: $host_bootloader_dir/"
    else
        log "  warning: chroot bootloader dir missing: $chroot_bootloader_dir"
        log "  phase_iso will fail unless bootloader artifacts are placed at $host_bootloader_dir/"
    fi
    log ""

    # Lever 4 (2026-05-28): signing ceremony pause moved from end of
    # phase_bootloader to end of phase_ukis_verity. The live-mode UKI's
    # sealed cmdline must include the verity root hash, which is only
    # known after phase_squashfs runs `veritysetup format`. UKIs are
    # built post-squashfs by phase_ukis_verity, so the operator signs
    # grub + all 3 UKIs in one session at THAT pause, not here.
    log "  Pre-UKI artifacts staged. Orchestrator continuing to phase_image."
    log "  (UKIs will be built post-squashfs in phase_ukis_verity, then the"
    log "   single signing ceremony pause covers grub + 3 UKIs.)"
}

phase_ukis_verity() {
    ensure_source_date_epoch
    # Lever 4 (2026-05-28) — build the three mode UKIs from HOST using the
    # verity root hash emitted by phase_squashfs's veritysetup format step.
    # init.sh in each UKI's bundled initramfs reads `igos.verity.roothash=`
    # from the sealed cmdline and activates dm-verity against the squashfs
    # + hashtree on the ISO, replacing the prior 73-second whole-file
    # sha256 boot-wait with kernel-level per-block verification.
    #
    # Runs from HOST (not chroot): phase_image has already torn down the
    # chroot pseudo-fs mounts and cleaned /mnt/intergenos from inside the
    # chroot. The host has access to vmlinuz + initramfs + microcode cpios
    # + os-release at $host_bootloader_dir (chroot-build-bootloader.sh
    # staged them before phase_image's cleanup).

    local host_bootloader_dir="/mnt/intergenos/build/bootloader"
    local verity_params="${IGOS}/mnt/intergenos/build/filesystem.squashfs.verity-params"

    # phase_squashfs's output may live inside the (now-cleaned) chroot's
    # /mnt/intergenos OR at a host-side build dir depending on how the
    # orchestrator invoked build-squashfs.sh. Search both common locations.
    if [ ! -f "$verity_params" ]; then
        for candidate in \
            /mnt/intergenos/build/filesystem.squashfs.verity-params \
            "${IGOS}/mnt/intergenos/build/filesystem.squashfs.verity-params" \
            /mnt/intergenos/build/squashfs/filesystem.squashfs.verity-params ; do
            if [ -f "$candidate" ]; then
                verity_params="$candidate"
                break
            fi
        done
    fi

    if [ ! -f "$verity_params" ]; then
        log "  error: verity-params file not found at any expected location."
        log "  phase_squashfs should have emitted filesystem.squashfs.verity-params"
        log "  alongside the squashfs. Searched:"
        log "    /mnt/intergenos/build/filesystem.squashfs.verity-params"
        log "    ${IGOS}/mnt/intergenos/build/filesystem.squashfs.verity-params"
        log "    /mnt/intergenos/build/squashfs/filesystem.squashfs.verity-params"
        log "  Re-run phase_squashfs."
        exit 1
    fi

    log "Building verity-augmented UKIs from host..."
    log "  verity-params: $verity_params"
    log "  bootloader dir: $host_bootloader_dir"
    log ""

    BOOTLOADER_DIR="$host_bootloader_dir" \
    VERITY_PARAMS="$verity_params" \
    IGOS="$IGOS" \
        bash "${SCRIPTS}/build-ukis-verity.sh" 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  UKIs built. Unsigned at: $host_bootloader_dir/"
    log "    igos-live.efi"
    log "    igos-install-gui.efi"
    log "    igos-install-tui.efi"

    # A-002 (T0-2): UNSIGNED_TEST=1 lets the orchestrator run end-to-end
    # without an operator ceremony pause. The .unsigned-test.iso variant
    # is for dev iteration on Secure Boot OFF VMs; release ISOs still
    # require the operator-only signing ceremony described below.
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        log ">>> UNSIGNED_TEST=1 — skipping operator-only ceremony pause"
        log ""
        log "  The orchestrator will continue to phase_iso to produce an"
        log "  .unsigned-test.iso artifact (Secure Boot OFF required)."
        log ""
        log "  For release-grade signed ISOs, re-run without UNSIGNED_TEST=1"
        log "  to hit the ceremony pause below."
        log ""
        return 0
    fi

    log ">>> Enforced pause: bootloader + UKI artifacts are unsigned"
    log ""
    log "  Signing covers grub + 3 UKIs in one ceremony. Without signing,"
    log "  the ISO ships with unsigned binaries and fails shim Secure Boot"
    log "  verification at boot."
    log ""
    log "  This stop is HARD-CODED (not gated by --stop-after) because the"
    log "  signing ceremony is operator-only and cannot be skipped via flag."
    log "  See docs/signing-procedure.md for the operational runbook."
    log ""
    log "  Next step (Rule F — pick the lane that matches who is signing):"
    log "    If operator driven:    sudo bash scripts/sign-bootloader.sh"
    log "                           (-> grubx64/igos-*.efi.signed — the artifacts phase_iso consumes)"
    log "    If automated/CI:       scripts/sign-release.sh --artifacts $host_bootloader_dir --output <signed-dir>"
    log "                           (superset: grub+UKIs+index+manifest; outputs signed *.efi — place them as *.efi.signed in $host_bootloader_dir. See docs/operations/03-automating-signing)"
    log ""
    log "  After signing: place .efi.signed files at $host_bootloader_dir/."
    log "  Resume with: sudo bash $0 --user $BUILD_USER --start-at iso"
    log ""
    exit 0
}

phase_image() {
    log "Packaging chroot into bootable disk image..."

    # Defensive preset-state resync — same rationale as the resync at
    # the top of phase_squashfs. When the build is resumed via
    # --start-at image (skipping phase_config), the chroot's
    # /etc/systemd/system/*.target.wants/ symlinks reflect whatever
    # the last phase_config landed. Subsequent preset file additions
    # (new core package installs, recipe changes) don't take effect
    # until preset-all reruns. Running it here ensures the qcow2 we
    # assemble below ships the CURRENT preset policy, not a stale
    # snapshot of it. Surfaced 2026-05-24 by D-011 runtime Gate E
    # catching nftables.service missing from multi-user.target.wants/.
    log "  Re-syncing chroot preset state via systemctl preset-all..."
    chroot "$IGOS" /bin/bash -c 'systemctl preset-all 2>&1 | tail -20' \
        | sed 's/^/    /' | tee -a "$BUILD_LOG" || true

    # D-007 compliance gate — refuse to assemble any shippable artifact
    # until SSH/credentials posture is correct (Class A gate). The
    # requirement is stated in scripts/check-d007-compliance.sh.
    log "  Running SSH and credentials posture gate..."
    if ! bash "${SCRIPTS}/check-d007-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: SSH and credentials posture gate failed."
        log "  Refusing to assemble disk image with SSH/credentials posture violations."
        log "  See scripts/check-d007-compliance.sh output above."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  SSH and credentials posture gate passed"

    # D-010 compliance gate — refuse to assemble any shippable artifact
    # if the InterGen AI assistant is enabled by default at any layer
    # (Class A gate). The requirement is stated in
    # scripts/check-d010-compliance.sh.
    log "  Running AI assistant opt-in posture gate..."
    if ! bash "${SCRIPTS}/check-d010-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: AI assistant opt-in posture gate failed."
        log "  Refusing to assemble disk image with InterGen AI opt-in posture violations."
        log "  See scripts/check-d010-compliance.sh output above."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  AI assistant opt-in posture gate passed"

    # D-011 compliance gate — refuse to assemble any shippable artifact
    # until default-deny firewall posture is correct (Class A gate). The
    # requirement is stated in scripts/check-d011-compliance.sh.
    log "  Running default-deny firewall posture gate..."
    if ! bash "${SCRIPTS}/check-d011-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: default-deny firewall posture gate failed."
        log "  Refusing to assemble disk image with firewall-policy violations."
        log "  See scripts/check-d011-compliance.sh output above."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  default-deny firewall posture gate passed"

    # D-008 compliance gate — refuse to assemble any shippable artifact
    # that includes InterGen without the provenance-gated tool dispatcher
    # (Class A ship-block). The requirement is stated in
    # scripts/check-d008-compliance.sh. Composes with D-007
    # (the pkexec gate); D-008 is upstream of D-007 — intent then auth.
    # Auto-passes when InterGen is not present in the source tree.
    log "  Running InterGen provenance-gate gate..."
    if ! bash "${SCRIPTS}/check-d008-compliance.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: InterGen provenance-gate gate failed."
        log "  Refusing to assemble disk image with InterGen provenance-gate violations."
        log "  See scripts/check-d008-compliance.sh output above."
        log "  Fix violations and re-run phase_image."
        exit 1
    fi
    log "  InterGen provenance-gate gate passed"

    # Tear down chroot mounts before imaging
    log "  Tearing down chroot mounts..."
    bash "${SCRIPTS}/chroot-teardown.sh" 2>&1 | tee -a "$BUILD_LOG" || true

    # Clean build infrastructure from target rootfs.
    # Kernel source is staged at /usr/src/linux-* by the linux-kernel(-pass2)
    # package's do_install (NOT under /mnt/intergenos or /sources or /tmp),
    # so these rm operations don't touch it. See packages/core/linux-kernel*.
    log "  Cleaning build artifacts from target..."
    rm -rf "${IGOS}/mnt/intergenos"
    rm -rf "${IGOS}/sources"
    rm -rf "${IGOS}/tmp"/*
    mkdir -p "${IGOS}/tmp"
    chmod 1777 "${IGOS}/tmp"
    log "  Build artifacts removed"

    # Sanity gate: kernel source MUST be staged to /usr/src/linux-* before
    # imaging. If missing, the linux-kernel-pass2 package's do_install
    # regressed — DKMS / out-of-tree modules (NVIDIA, VirtualBox, ZFS)
    # would not work on the shipped ISO. Fail loudly rather than ship a
    # broken rootfs.
    if ! ls -d "${IGOS}/usr/src/linux-"* >/dev/null 2>&1; then
        log "  error: /usr/src/linux-* missing from chroot — kernel source not staged"
        log "  This is a regression in packages/core/linux-kernel-pass2/build.sh's do_install."
        log "  Refusing to image without source. Fix the kernel package + rebuild."
        exit 1
    fi
    local src_dir
    src_dir=$(ls -d "${IGOS}/usr/src/linux-"*/ | head -1)
    log "  Sanity gate passed: kernel source staged at ${src_dir#${IGOS}}"

    # Create the image — write to virtiofs-shared path so the host
    # can access it directly without copying through SSH
    local image_path="/mnt/intergenos/build/intergenos.qcow2"
    bash "${SCRIPTS}/create-image.sh" "$image_path" 500G 2>&1 | tee -a "$BUILD_LOG"

    log ""
    log "  Disk image created at: $image_path"
    log "  (accessible from host via virtiofs)"
    log ""
    log "  Create a VM with:"
    log "    cp ${image_path} /mnt/jarvis-storage/VMs/intergenos.qcow2"
    log "    See create-image.sh output above for virt-install command."
}

phase_manifest() {
    # Step 4 of 7 ship-gate (install-time integrity verification design doc
    # docs/research/security/install-integrity-verification.md §5.2):
    # emit a BSD-style sha256sum manifest covering every .igos.tar.gz the
    # build produced. Manifest is unsigned at this point — sign-release.sh
    # --manifest signs it on the signing workstation; build-iso.sh embeds
    # the signed manifest + release-key public component in the ISO at
    # /install/intergenos-archive-manifest.txt + /install/intergenos-release-key.asc.
    #
    # Wiki-manifest gate FIRST (fail-closed, before the operator is summoned to
    # the signing ceremony): the shipped wiki book in the chroot must match the
    # operator-signed pages-manifest.json, verified with gpgv against the
    # chroot's own trusted.gpg + the pinned fingerprint — the same chain the
    # installed system's citation layer enforces at cite time. A stale or
    # unverifiable wiki signature refuses HERE, not on the installed system.
    # UNSIGNED_TEST=1 (dev/test ISO) downgrades ABSENT wiki inputs to a loud
    # warn; a present-but-failing signature or drifted page always refuses.
    log "Verifying shipped wiki book against its signed page manifest..."
    if ! python3 "${SCRIPTS}/check-wiki-manifest.py" --root "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log "  FATAL: wiki-manifest gate refused — the shipped book does not match the signed manifest"
        return 1
    fi

    local archives_dir="${IGOS}/var/lib/igos/archives"
    local out_dir="/mnt/intergenos/build"
    local manifest="${out_dir}/intergenos-archive-manifest.txt"
    local build_id="${INTERGENOS_BUILD_ID:-v1.0-dev1}"
    local built_on="${INTERGENOS_BUILD_HOST:-$(hostname -f 2>/dev/null || hostname)}"
    local built_at_iso
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        # Honor SDE for reproducibility (Q-REPRO-GOAL=v1.0 bit-identical)
        built_at_iso=$(date -u -d "@${SOURCE_DATE_EPOCH}" '+%Y-%m-%dT%H:%M:%SZ')
    else
        built_at_iso=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    fi

    if [ ! -d "$archives_dir" ]; then
        log "  error: archives dir not found: $archives_dir"
        log "  (manifest phase requires phase_image to have completed; chroot still mounted)"
        return 1
    fi

    mkdir -p "$out_dir"

    # Emit header. Lines starting with '#' are comments per BSD sha256sum
    # convention; sha256sum -c ignores them.
    {
        printf '# InterGenOS archive integrity manifest\n'
        printf '# Build: %s\n' "$build_id"
        printf '# Built: %s\n' "$built_at_iso"
        printf '# Built-on: %s\n' "$built_on"
        printf '# Manifest-version: 1\n'
    } > "$manifest"

    # Walk archives_dir; sort for deterministic output (cross-host
    # byte-identity per Q-REPRO-GOAL). Path in the manifest is relative
    # to /var/lib/igos/archives/ so the install-time verifier doesn't
    # need to know the build host's absolute path.
    local archive_count=0
    local rel
    while IFS= read -r -d '' archive; do
        rel="${archive#${archives_dir}/}"
        local sha
        sha=$(sha256sum "$archive" | awk '{print $1}')
        printf 'SHA256 (%s) = %s\n' "$rel" "$sha" >> "$manifest"
        archive_count=$((archive_count + 1))
    done < <(find "$archives_dir" -type f -name '*.igos.tar.gz' -print0 | sort -z)

    # Tie the build's forensic-trace summary file (when verbose mode wrote
    # one) into the manifest so any future install-time / artifact-triage
    # workflow can pivot from this ISO's manifest to the full per-phase /
    # per-package JSONL trail by shared runid. Lines emitted as BSD-style
    # comments so sha256sum -c ignores them.
    if [ -n "${IGOS_BUILD_SUMMARY_PATH:-}" ] && [ -f "${IGOS_BUILD_SUMMARY_PATH}" ]; then
        local summary_sha
        summary_sha=$(sha256sum "${IGOS_BUILD_SUMMARY_PATH}" | awk '{print $1}')
        local summary_rel="${IGOS_BUILD_SUMMARY_PATH#/mnt/intergenos/build/logs/trace/}"
        printf '# Trace-summary: %s\n' "$summary_rel" >> "$manifest"
        printf '# Trace-summary-sha256: %s\n' "$summary_sha" >> "$manifest"
        printf '# Trace-runid: %s\n' "${IGOS_TRACE_RUNID}" >> "$manifest"
        log "  Trace summary pinned in manifest: ${summary_rel}"
    fi

    printf '# End of manifest.\n' >> "$manifest"

    log "  Manifest emitted: $manifest"
    log "  Archives covered: $archive_count"
    log "  SHA256 of manifest: $(sha256sum "$manifest" | awk '{print $1}')"

    if [ "$archive_count" -eq 0 ]; then
        log "  warning: 0 archives found in $archives_dir; manifest is empty."
        log "  This may be expected during partial-build runs (e.g. --stop-after toolchain)"
        log "  but is unexpected after a full build pipeline. Investigate before signing."
        # Per §1 B14: opt-in strict mode for full-build CI. When set,
        # an empty manifest fails the manifest phase rather than warning.
        # Useful for full builds where 0 archives indicates a real bug.
        if [ "${MANIFEST_STRICT:-0}" = "1" ]; then
            log "  error: MANIFEST_STRICT=1 set; failing on empty manifest."
            return 1
        fi
    fi

    log ""
    # Install-integrity Option 1: the signed trust triplet
    # {manifest, .sig, release-key} is verity-SEALED into the squashfs at
    # $CHROOT/install/, so it must be signed BEFORE phase_squashfs runs
    # mksquashfs. This is a distinct, pre-squashfs signing moment from the
    # post-squashfs UKI/verity ceremony in phase_ukis_verity (the verity
    # roothash can only be signed AFTER the squashfs is sealed; the manifest
    # must be signed BEFORE). build-squashfs Step 4.8 stages + fail-closed
    # asserts the triplet against the staged release key.

    # A-002 parity with phase_ukis_verity: UNSIGNED_TEST=1 runs end-to-end
    # without the operator ceremony pause. build-squashfs Step 4.8 stages the
    # explicit IGOS_DEV_ALLOW_UNVERIFIED marker instead of a signed triplet —
    # the sanctioned, loud, clearly-marked dev seam the installer keys its
    # skip off of. Release ISOs hit the enforced pause below.
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        log ">>> UNSIGNED_TEST=1 — skipping operator manifest-signing pause"
        log "  build-squashfs Step 4.8 will stage the IGOS_DEV_ALLOW_UNVERIFIED"
        log "  dev marker (NOT a signed triplet). The installer's archive-"
        log "  integrity gate is bypassed on this artifact EXPLICITLY + loudly."
        log "  Secure Boot OFF. For a release-grade ISO, re-run without"
        log "  UNSIGNED_TEST=1 to hit the manifest-signing pause."
        return 0
    fi

    log ">>> Enforced pause: archive integrity manifest is unsigned"
    log ""
    log "  Option 1 seals the signed trust triplet INTO the squashfs, so the"
    log "  manifest MUST be signed BEFORE mksquashfs runs. This stop is"
    log "  HARD-CODED (not gated by --stop-after) because manifest signing is"
    log "  operator-only and cannot be skipped via flag. See"
    log "  docs/research/security/install-integrity-verification.md."
    log ""
    log "  Next step (Rule F — pick the lane that matches who is signing):"
    log "    If operator driven:    /bin/bash scripts/sign-manifest.sh"
    log "                           (NO sudo — OpenPGP card-signing runs AS the operator;"
    log "                           sudo hits root's empty keyring with no card stub and fails)"
    log "                           (sign $manifest in place -> ${manifest}.sig)"
    log "    If automated/CI:       this pause is MANIFEST-ONLY. Stage the manifest in a CLEAN"
    log "                           manifest-only dir, then:"
    log "                           scripts/sign-release.sh --artifacts <clean-manifest-dir> --output <signed-dir> --manifest $manifest"
    log "                           (--artifacts + --output are REQUIRED — sign-release.sh dies exit 2 without them. Do NOT point"
    log "                           --artifacts at the shared build dir: sign-release.sh signs ALL present artifacts, and the UKIs"
    log "                           there are NOT verity-sealed yet — the full grub+UKI signing belongs at the POST-squashfs"
    log "                           ukis-verity pause. See docs/operations/03-automating-signing)"
    log ""
    log "  After signing, place all THREE trust artifacts in $out_dir/:"
    log "    intergenos-archive-manifest.txt       (this manifest)"
    log "    intergenos-archive-manifest.txt.sig   (the detached signature)"
    log "    intergenos-release-key.asc            (release public key: master + S1 only)"
    log "  build-squashfs Step 4.8 copies them into \$CHROOT/install/ and"
    log "  fail-closed asserts the set (signature + coverage) before sealing."
    log ""
    log "  Resume with: sudo bash $0 --user $BUILD_USER --start-at squashfs"
    log ""
    exit 0
}

phase_squashfs() {
    ensure_source_date_epoch
    # A-002 (T0-2 2026-05-18): wire build-squashfs.sh into the orchestrator
    # pipeline. Previously the script existed but was operator-driven via
    # ad-hoc kickoff scripts under build/; ops doc 02 framed orchestrator end-to-end
    # ISO build as if it worked, but neither phase_squashfs nor phase_iso
    # existed. Runs AFTER phase_image (which cleans build infrastructure
    # from the chroot — /mnt/intergenos, /sources, /tmp/*) so the squashfs
    # captures only the bootable end-user filesystem.

    # Defensive preset-state resync. chroot-config-ch9.sh:510 runs
    # `systemctl preset-all` during phase_config to materialize *.preset
    # files into /etc/systemd/system/*.target.wants/ symlinks. But when
    # the build resumes via --start-at image (or later), phase_config is
    # skipped, and the chroot's preset state is whatever the last
    # phase_config landed — which may pre-date subsequent preset file
    # additions (new core package installs, recipe changes, etc.). The
    # D-011 runtime Gate E (nftables preset-enabled) caught this exact
    # drift class on 2026-05-24 — chroot's multi-user.target.wants/
    # was missing nftables.service because preset-all hadn't been re-run
    # since the nftables preset shipped. Re-running here brings the
    # chroot's preset state in sync with whatever's currently in
    # /usr/lib/systemd/system-preset/ before the runtime gates fire.
    log "  Re-syncing chroot preset state via systemctl preset-all..."
    chroot "$IGOS" /bin/bash -c 'systemctl preset-all 2>&1 | tail -20' \
        | sed 's/^/    /' | tee -a "$BUILD_LOG" || true

    # S-D 4 (USA-1 — "most leverage of any item in this report") — D-007
    # runtime gate verifies the built chroot at $IGOS has the SSH/credentials
    # policy ACTUALLY DEPLOYED, not just absent-from-source. Companion to
    # scripts/check-d007-compliance.sh (source-grep). A code change can pass
    # the source-grep gate yet produce a chroot whose /etc/shadow root has a
    # real password hash, whose /etc/ssh/ has baked host keys, etc. — this
    # gate is the built-artifact second line of defense.
    log "  Running SSH and credentials posture runtime gate..."
    if ! bash "${SCRIPTS}/check-d007-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: SSH and credentials posture runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot that does not embody"
        log "  the SSH/credentials policy. See scripts/check-d007-runtime.sh output above."
        log "  Fix the build pipeline so the chroot ships the right state and re-run phase_squashfs."
        exit 1
    fi
    log "  SSH and credentials posture runtime gate passed"

    # S-D 4 (USA-1) — D-008 runtime gate verifies the InterGen provenance-
    # gated tool dispatcher is ACTUALLY DEPLOYED in the assembled chroot
    # (PolicyKit policy + pkexec runner + Python provenance modules + audit
    # log infra). Companion to scripts/check-d008-compliance.sh (source-grep).
    # Auto-passes if InterGen was not installed in this chroot — a build may
    # ship without InterGen entirely. When InterGen IS installed, every
    # required artifact must be present + structurally valid.
    log "  Running InterGen provenance-gate runtime gate..."
    if ! bash "${SCRIPTS}/check-d008-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: InterGen provenance-gate runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot that ships InterGen"
        log "  without the v1.0 minimum provenance-gated dispatcher. See"
        log "  scripts/check-d008-runtime.sh output above."
        log "  Fix the build pipeline so the chroot ships the gate artifacts and re-run phase_squashfs."
        exit 1
    fi
    log "  InterGen provenance-gate runtime gate passed"

    # S-D 4 (USA-1) — D-010 runtime gate verifies the InterGen opt-in posture
    # holds in the assembled chroot: no preset-enable symlinks for
    # intergen.service in any *.target.wants/, no xdg autostart launching
    # intergen, no systemd preset rule enabling it, no /etc/skel template.
    # Companion to scripts/check-d010-compliance.sh (source-grep). Auto-passes
    # when InterGen is not installed in the chroot.
    log "  Running AI assistant opt-in posture runtime gate..."
    if ! bash "${SCRIPTS}/check-d010-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: AI assistant opt-in posture runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot that auto-enables InterGen."
        log "  The AI assistant requires opt-in via Forge's prompt; the shipped chroot must not"
        log "  contain any preset-enable / autostart / preset-rule / skel-template path"
        log "  that would activate the AI assistant by default. See"
        log "  scripts/check-d010-runtime.sh output above."
        log "  Fix the build pipeline so the chroot ships opt-in-only and re-run phase_squashfs."
        exit 1
    fi
    log "  AI assistant opt-in posture runtime gate passed"

    # S-D 4 (USA-1) — D-011 runtime gate verifies the default-deny firewall
    # policy is ACTUALLY DEPLOYED at /etc/nftables.conf with policy=drop on
    # both input + forward chains, the verbatim accept-rule set (ct
    # established, loopback, ICMP echo, narrowed PMTUd, IPv6 ND, invalid drop),
    # SSH closed by default, AND nftables.service preset-enabled.
    # Companion to scripts/check-d011-compliance.sh (source-grep). No auto-
    # pass — D-011 is baseline security policy per intergenos-firewall-defaults
    # (tier=core, not optional).
    log "  Running default-deny firewall posture runtime gate..."
    if ! bash "${SCRIPTS}/check-d011-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: default-deny firewall posture runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot whose /etc/nftables.conf"
        log "  does not embody the default-deny firewall policy. See"
        log "  scripts/check-d011-runtime.sh output above."
        log "  Fix the build pipeline so the chroot ships the right firewall state"
        log "  and re-run phase_squashfs."
        exit 1
    fi
    log "  default-deny firewall posture runtime gate passed"

    # S-D 4 (USA-1) — H-007 runtime gate verifies the helper-manifest
    # infrastructure (intergenos-helper-lib) is ACTUALLY DEPLOYED in the
    # chroot, AND that every /usr/bin/igos-install-* helper script sources
    # the library. Orphan helpers re-introduce the H-007 gap (files land
    # untracked because the helper bypasses the manifest API).
    # Auto-passes when no helper infrastructure is present at all.
    log "  Running helper-manifest completeness runtime gate..."
    if ! bash "${SCRIPTS}/check-h007-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: helper-manifest completeness runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot whose helper infrastructure"
        log "  is incomplete or has orphan helpers. See scripts/check-h007-runtime.sh"
        log "  output above."
        log "  Fix the build pipeline so the chroot ships helper-lib + sourcing helpers"
        log "  and re-run phase_squashfs."
        exit 1
    fi
    log "  helper-manifest completeness runtime gate passed"

    # S-D 4 (USA-1) — K21.F runtime helper-smoke gate is the SYNTACTIC
    # companion to H-007's presence/sourcing check. Verifies every
    # /usr/bin/igos-install-* helper script passes `bash -n` parse, has a
    # valid shebang, AND actually calls igos_helper_init + igos_helper_commit
    # (subtler H-007 regression: sourcing the lib without using the API
    # leaves the manifest empty, files untracked). Also smoke-checks
    # helper-lib.sh itself.
    # NOTE: This gate is named K21.F after the validation enumeration it came
    # from; it is NOT the same as the audit document's K21.F (helper
    # supply-chain hardening) or the release ledger's K21.F (DCO Signed-off-by
    # check).
    # Auto-passes when no helper infrastructure is present.
    log "  Running helper-smoke runtime gate..."
    if ! bash "${SCRIPTS}/check-k21f-runtime.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: helper-smoke runtime gate failed."
        log "  Refusing to assemble squashfs from a chroot whose helpers have"
        log "  syntactic / API-usage defects. See scripts/check-k21f-runtime.sh"
        log "  output above."
        log "  Fix the offending helper(s) and re-run phase_squashfs."
        exit 1
    fi
    log "  helper-smoke runtime gate passed"

    # K21.B compliance gate — refuse to bundle the rootfs into a squashfs
    # if any installed package lacks a license bundle at
    # /usr/share/licenses/<pkg>/. Mirrors the D-007/D-010/D-011 Class-A
    # gates in phase_image — same shape, applied to legal-readiness.
    # See scripts/check-license-bundle.sh + igos-build/builder.py
    # bundle_license() for the producer + audit sides of K21.B.
    log "  Running license-bundle compliance gate..."
    if ! bash "${SCRIPTS}/check-license-bundle.sh" "${IGOS}" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: license-bundle compliance gate failed."
        log "  Refusing to assemble squashfs with packages missing license bundles."
        log "  See scripts/check-license-bundle.sh resolution-paths output above."
        log "  Fix offending packages and re-run phase_squashfs."
        exit 1
    fi
    log "  license-bundle compliance gate passed"

    log "Building live-ISO root filesystem squashfs from cleaned chroot..."
    # Thread UNSIGNED_TEST so Step 4.8 picks the dev-marker vs signed-triplet
    # path, and SIGNED_MANIFEST_DIR so it reads the operator-signed triplet
    # from the same build dir phase_manifest emitted the manifest into.
    # Explicit failure branch (same shape as the license-bundle gate above):
    # a bare `... | tee` under set -euo pipefail killed this phase with ZERO
    # log lines when build-squashfs fail-closed (ge9b-10 Step-2.7 first
    # firing) — the log's last line was the cleanup trap, with no when/why.
    if ! OUTPUT="/mnt/intergenos/build/filesystem.squashfs" \
         UNSIGNED_TEST="${UNSIGNED_TEST:-0}" \
         SIGNED_MANIFEST_DIR="/mnt/intergenos/build" \
         bash "${SCRIPTS}/build-squashfs.sh" 2>&1 | tee -a "$BUILD_LOG"; then
        log ""
        log "  error: build-squashfs.sh FAILED — halting phase_squashfs."
        log "  See the [build-squashfs] output above (gate verdicts + remedies);"
        log "  Step 2.7 findings land in /tmp/iso-metadata-sync-report.txt on the build host."
        exit 1
    fi

    if [ ! -f "/mnt/intergenos/build/filesystem.squashfs" ]; then
        log "  error: squashfs not produced at /mnt/intergenos/build/filesystem.squashfs"
        return 1
    fi
    local squashfs_size
    squashfs_size=$(stat -c '%s' "/mnt/intergenos/build/filesystem.squashfs")
    log "  squashfs at /mnt/intergenos/build/filesystem.squashfs (size=$squashfs_size bytes)"
}

phase_iso() {
    ensure_source_date_epoch
    # A-002 (T0-2 2026-05-18): wire build-iso.sh into the orchestrator. The
    # bootloader artifacts come from phase_bootloader's host copy at
    # /mnt/intergenos/build/bootloader/ — either the unsigned originals
    # (UNSIGNED_TEST=1 path) or the .signed variants placed there by the
    # operator after running scripts/sign-release.sh on the signing
    # workstation.
    log "Assembling live ISO from bootloader artifacts + squashfs..."

    local bootloader_dir="/mnt/intergenos/build/bootloader"
    local squashfs="/mnt/intergenos/build/filesystem.squashfs"

    # ISO filename resolution (decided 2026-07-05 — dynamic ISO naming;
    # ISOs are never hand-renamed after creation). Precedence:
    # this invocation's --iso-name > the launch-persisted choice in
    # ISO_NAME_FILE (survives the ceremony-resume chain) > the legacy
    # default. The winning source is logged — the name is never silent.
    local iso_name iso_name_src
    if [ -n "$ISO_NAME" ]; then
        iso_name="$ISO_NAME"
        iso_name_src="--iso-name flag (this invocation)"
    elif [ -s "$ISO_NAME_FILE" ]; then
        iso_name="$(head -n1 "$ISO_NAME_FILE")"
        iso_name_src="persisted launch choice (${ISO_NAME_FILE})"
        if ! [[ "$iso_name" =~ ^[A-Za-z0-9._-]+\.iso$ ]]; then
            log "  error: persisted ISO name in ${ISO_NAME_FILE} is malformed: '${iso_name}'"
            log "  Re-run with an explicit --iso-name (or remove the file for the default)."
            return 1
        fi
    else
        iso_name="intergenos-1.0-dev1.iso"
        iso_name_src="legacy default (no --iso-name given this launch chain)"
    fi
    local iso_out="/mnt/intergenos/build/${iso_name}"
    log "  ISO name: ${iso_name} (source: ${iso_name_src})"

    if [ ! -d "$bootloader_dir" ]; then
        log "  error: bootloader dir missing: $bootloader_dir"
        log "  phase_bootloader copies artifacts there. If running --start-at iso,"
        log "  place signed (or unsigned-test) shimx64.efi/grubx64.efi/igos-live.efi/"
        log "  igos-install-gui.efi/igos-install-tui.efi at $bootloader_dir/ first."
        return 1
    fi
    if [ ! -f "$squashfs" ]; then
        log "  error: squashfs missing at $squashfs"
        log "  phase_squashfs must complete before phase_iso. Run --start-at squashfs."
        return 1
    fi

    # Select shim/grub/UKI input filenames by signed state. The .signed
    # extension is sign-release.sh / sign-bootloader.sh's output convention.
    #
    # NOTE on shim: shim is Microsoft-signed by upstream Fedora's chain
    # (packages/core/shim-signed extracts shimx64.efi from Fedora's
    # shim-x64 RPM). We never re-sign shim — the MS signature is what
    # gives our boot chain its UEFI-default-CA trust path. So shim's
    # filename is `shimx64.efi` in BOTH UNSIGNED_TEST mode AND signed
    # mode (no `.signed` suffix exists or should ever exist for shim).
    # Only the artifacts WE sign (grub + 3 UKIs via our InterGenJLU
    # vendor cert) gain the `.signed` suffix in signed mode.
    local shim mokmanager grub uki_live uki_install_gui uki_install_tui
    shim="$bootloader_dir/shimx64.efi"
    # MokManager is never re-signed either (same Fedora-MS-signed provenance
    # as shim). Required fail-closed: an ISO without it dead-ends SB=ON
    # first-boots on virgin hardware with no enrollment path (PI-ge9b04-A).
    mokmanager="$bootloader_dir/mmx64.efi"
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        grub="$bootloader_dir/grubx64.efi"
        uki_live="$bootloader_dir/igos-live.efi"
        uki_install_gui="$bootloader_dir/igos-install-gui.efi"
        uki_install_tui="$bootloader_dir/igos-install-tui.efi"
    else
        grub="$bootloader_dir/grubx64.efi.signed"
        uki_live="$bootloader_dir/igos-live.efi.signed"
        uki_install_gui="$bootloader_dir/igos-install-gui.efi.signed"
        uki_install_tui="$bootloader_dir/igos-install-tui.efi.signed"
    fi

    local missing=()
    for f in "$shim" "$mokmanager" "$grub" "$uki_live" "$uki_install_gui" "$uki_install_tui"; do
        [ -f "$f" ] || missing+=( "$f" )
    done
    if (( ${#missing[@]} > 0 )); then
        log "  error: required bootloader artifact(s) missing:"
        for f in "${missing[@]}"; do
            log "    - $f"
        done
        if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
            log "  Re-run phase_bootloader to regenerate unsigned artifacts."
        else
            log "  Next step (Rule F — pick the lane that matches who is signing):"
            log "    If operator driven:    sudo bash scripts/sign-bootloader.sh"
            log "                           (-> grubx64/igos-*.efi.signed — the artifacts phase_iso consumes)"
            log "    If automated/CI:       scripts/sign-release.sh --artifacts <dir> --output <signed-dir>"
            log "                           (superset; outputs signed *.efi — place them as *.efi.signed in $bootloader_dir. See docs/operations/03-automating-signing)"
            log "  Then copy the .efi.signed files to $bootloader_dir/ before resuming."
        fi
        return 1
    fi

    UNSIGNED_TEST="${UNSIGNED_TEST:-0}" \
    SHIM="$shim" \
    MOKMANAGER="$mokmanager" \
    CA_CERT="${SCRIPTS%/scripts}/docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem" \
    GRUB="$grub" \
    UKI_LIVE="$uki_live" \
    UKI_INSTALL_GUI="$uki_install_gui" \
    UKI_INSTALL_TUI="$uki_install_tui" \
    SQUASHFS="$squashfs" \
    OUTPUT="$iso_out" \
        bash "${SCRIPTS}/build-iso.sh" 2>&1 | tee -a "$BUILD_LOG"

    # build-iso.sh appends .unsigned-test.iso suffix when UNSIGNED_TEST=1,
    # so the actual output filename depends on mode.
    local actual_iso="$iso_out"
    if [ "${UNSIGNED_TEST:-0}" = "1" ]; then
        actual_iso="${iso_out%.iso}.unsigned-test.iso"
    fi
    if [ ! -f "$actual_iso" ]; then
        log "  error: ISO not found at $actual_iso post-build (check build-iso.sh output)"
        return 1
    fi
    local iso_size
    iso_size=$(stat -c '%s' "$actual_iso")
    log "  ISO at $actual_iso (size=$iso_size bytes)"

    # B-018 + B-034 (T0-2 2026-05-18): atomic provenance manifest. The
    # cycle-5 ISO's manifest carried input-SHAs that did not match the
    # UKIs actually written into the ESP — i.e. the manifest existed for
    # a different build than the ISO. Re-emit at the moment of ISO
    # finalization (post-build-iso.sh success) so input-SHAs always
    # match what xorriso just consumed. Manifest filename mirrors the
    # ISO basename so lineage is unambiguous even when both .iso and
    # .unsigned-test.iso coexist in build/.
    local manifest_file="${actual_iso}.manifest"
    log "  Emitting build provenance manifest: $manifest_file"
    {
        printf '# InterGenOS ISO build provenance manifest\n'
        printf '# ISO basename: %s\n' "$(basename "$actual_iso")"
        printf '# Generated: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        printf '# Build mode: %s\n' \
            "$([ "${UNSIGNED_TEST:-0}" = "1" ] && echo "UNSIGNED_TEST" || echo "signed")"
        [ -n "${SOURCE_DATE_EPOCH:-}" ] && \
            printf '# SOURCE_DATE_EPOCH: %s\n' "$SOURCE_DATE_EPOCH"
        printf '# Build host: %s\n' "$(hostname -f 2>/dev/null || hostname)"
        printf '# Manifest-version: 1\n'
        printf '#\n'
        printf '# Input artifacts (SHAs as fed into build-iso.sh):\n'
        for input in "$shim" "$grub" "$uki_live" "$uki_install_gui" \
                     "$uki_install_tui" "$squashfs"; do
            local _sha
            _sha=$(sha256sum "$input" | awk '{print $1}')
            printf 'SHA256 (input %s) = %s\n' "$(basename "$input")" "$_sha"
        done
        printf '#\n'
        printf '# Output ISO:\n'
        local _iso_sha
        _iso_sha=$(sha256sum "$actual_iso" | awk '{print $1}')
        printf 'SHA256 (output %s) = %s\n' "$(basename "$actual_iso")" "$_iso_sha"
        printf '# End of manifest.\n'
    } > "$manifest_file"
    log "  Manifest written: $manifest_file"
}

phase_publish() {
    # Post-build publish hook (E1.B.8). Publishes the binary repository to
    # repo.intergenos.org if --publish flag was passed.
    # Only runs after a successful full build; gated behind --publish flag
    # to prevent accidental publishing from development/CI builds.
    #
    # Calls scripts/publish-repo.sh which:
    # 1. Generates InterGenOS.db index via pkm.repo.generate_index()
    # 2. PGP-signs it via pkm.repo.sign_index()
    # 3. Rsyncs archives + index + signature to staging
    # 4. Atomically promotes staging → live on remote
    log "Publishing binary repository..."
    log "  Archive dir: ${IGOS}/var/lib/igos/archives"

    local publish_script="${SCRIPTS}/publish-repo.sh"
    if [ ! -f "$publish_script" ]; then
        log "  error: publish script not found: $publish_script"
        return 1
    fi

    if [ ! -d "${IGOS}/var/lib/igos/archives" ]; then
        log "  error: archives dir not found: ${IGOS}/var/lib/igos/archives"
        return 1
    fi

    bash "$publish_script" --archive-dir "${IGOS}/var/lib/igos/archives" || {
        log "  error: publish-repo.sh failed"
        return 1
    }

    log "  Repo published. Verify: pk sync + pkm install <test-pkg> on fresh target."
}

# ==========================================================================
# Main — run all phases
# ==========================================================================

BUILD_START=$(date +%s)

log ""
log ">>> InterGenOS build"
log "    user:    $BUILD_USER"
log "    target:  $IGOS"
log "    started: $(date)"
if [ -n "$START_AT" ]; then
    log "    starting at: $START_AT"
fi
if [ -n "$STOP_AFTER" ]; then
    log "    stopping after: $STOP_AFTER"
fi
if $CHECKPOINT; then
    log "    checkpoints: enabled (saving to ${CHECKPOINT_DIR}/)"
fi
if $DEBUG_VERBOSE; then
    log "    debug verbose: on (forensic JSONL trail at ${IGOS_TRACE_ROOT}/)"
fi

# Structured trail: build_start event. Emitted before the first phase so the
# forensic trail records the complete kickoff context (phase list, host,
# user, resume points, checkpoint flag, publish flag). The IGOS_TRACE_RUNID
# + IGOS_TRACE_START_TS exported above flow into every child process so the
# whole build trail shares one <startts>-<runid> suffix family.
trace_event "build_start" \
    "runid=${IGOS_TRACE_RUNID}" \
    "start_ts=${IGOS_TRACE_START_TS}" \
    "host=$(hostname -f 2>/dev/null || hostname)" \
    "build_user=${BUILD_USER}" \
    "image_user=${IMAGE_USER}" \
    "target=${IGOS}" \
    "start_at=${START_AT}" \
    "stop_after=${STOP_AFTER}" \
    "start_at_pkg=${START_AT_PKG}" \
    "checkpoint::=${CHECKPOINT}" \
    "publish::=${PUBLISH}" \
    "debug_verbose::=${DEBUG_VERBOSE}" \
    "phase_list::=$(printf '%s\n' "${PHASES[@]}" | jq -R . | jq -s -c .)"

# Auto-fire the validate gates on EVERY --start-at resume (decided 2026-08-06).
# `--start-at <phase>` skips phase_validate, and the manual fire-the-gates-by-
# hand discipline that replaced it was the single most-likely-to-be-missed step
# of a targeted build. The gates are host-side (~seconds against a warm scan
# cache), so a resume pays them up front instead of trusting a human to
# remember them. RESUME_CONTEXT=1 makes phase_validate hold the silent-loss
# gate to --require-audit when the chroot is populated (a targeted resume must
# not wave that gate through as a skip). Called PLAIN, not inside a condition:
# an `if ! phase_validate` wrapper would suppress `set -e` inside the function
# and let individual gate failures ride to the last command's status.
if [ -n "$START_AT" ] && [ "$START_AT" != "validate" ]; then
    log "=== Auto-firing validate gates before --start-at ${START_AT} resume ==="
    trace_event "resume_validate_autofire" "start_at=${START_AT}"
    RESUME_CONTEXT=1 phase_validate
    log "=== Validate gates PASS — proceeding to --start-at ${START_AT} ==="
fi

run_phase "validate"       "Verify host requirements"            phase_validate
run_phase "verify-sources" "Audit source SHAs against tarballs"  phase_verify_sources
run_phase "setup"          "Create build environment"            phase_setup
run_phase "toolchain"    "Cross-compilation toolchain (Ch 5-6)" phase_toolchain
run_phase "chroot-prep"  "Prepare chroot environment (Ch 7)"   phase_chroot_prep
run_phase "chroot-tools" "Build temp tools in chroot (Ch 7)"   phase_chroot_tools
run_phase "core"         "Build core system (Ch 8, LFS order)" phase_core
run_phase "config"       "System configuration (Ch 9)"         phase_config
run_phase "core-extra"   "Build extra core packages (BLFS)"    phase_core_extra
run_phase "base"         "Build base CLI tools (end-user)"     phase_base
run_phase "kernel"       "Build kernel (Ch 10)"                phase_kernel
run_phase "desktop"     "Build desktop (GNOME on Wayland)"    phase_desktop
run_phase "extra"       "Build extra tier (applications)"     phase_extra
run_phase "compute"     "Build compute tier (opt-in GPU SDKs)" phase_compute
run_phase "ai"          "Build AI tier (InterGen assistant)"  phase_ai
run_phase "bootloader"  "Assemble unsigned bootloader artifacts" phase_bootloader
run_phase "image"       "Package bootable disk image"         phase_image
run_phase "manifest"    "Emit archive integrity manifest"     phase_manifest
run_phase "squashfs"    "Build live-ISO root filesystem squashfs" phase_squashfs
run_phase "ukis-verity" "Build verity-augmented UKIs from host"     phase_ukis_verity
run_phase "iso"         "Assemble live ISO (signed or unsigned-test)" phase_iso
if $PUBLISH; then
    run_phase "publish" "Publish binary repository to repo.intergenos.org" phase_publish
fi

# ==========================================================================
# Done
# ==========================================================================

BUILD_ELAPSED=$(( $(date +%s) - BUILD_START ))
BUILD_HOURS=$(( BUILD_ELAPSED / 3600 ))
BUILD_MINUTES=$(( (BUILD_ELAPSED % 3600) / 60 ))

log ""
log "${IGOS_MARK_OK} InterGenOS build complete"
log "    total time: ${BUILD_HOURS}h ${BUILD_MINUTES}m"
log "    finished:   $(date)"
log ""

# Structured trail: build_end event (success) pinning total elapsed seconds
# and the final phase that ran. emit_build_summary writes the
# build-summary-<startts>-<runid>.json index file linking every per-phase /
# per-package / per-host JSONL produced under this runid, which becomes the
# "given the build, find every trace" pivot. The manifest phase has already
# run by this point, but resumed builds (--start-at manifest after a fresh
# verbose run) honor the IGOS_BUILD_SUMMARY_PATH export and pin the path
# into the next manifest emission.
trace_event "build_end" \
    "success::=true" \
    "elapsed_s::=${BUILD_ELAPSED}" \
    "last_phase=${CURRENT_PHASE:-iso}" \
    "runid=${IGOS_TRACE_RUNID}"

emit_build_summary "${BUILD_ELAPSED}" "true" "${CURRENT_PHASE:-iso}"

trace_close
