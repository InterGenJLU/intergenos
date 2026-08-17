#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
# ==========================================================================
# scripts/lib/trace.sh — bash companion to scripts/lib/igos_trace.py
#
# This file is the bash side of the JSON-line forensic-trace framework.
# Source it from every build-pipeline shell script so the bash + Python
# halves emit events into the SAME JSONL files under a SHARED schema.
#
# Usage:
#   source "/mnt/intergenos/scripts/lib/trace.sh"
#   trace_init "<scope>" "<runid>"
#   trace_event build_start runid="$IGOS_TRACE_RUNID" host="$(hostname)"
#   trace_phase_enter validate "Verify host requirements"
#   trace_run --tee "$LOG" --phase validate --intent "host-check.py" \
#       bash "$SCRIPTS/host-check.py"
#   trace_phase_exit validate "$rc" "$elapsed_ms"
#   trace_close
#
# Gate:
#   IGOS_BUILD_DEBUG_VERBOSE=1   (preferred for build-pipeline use)
#   FORGE_DEBUG_VERBOSE=1        (preserved for Forge installer use)
#
# Either env-var opts in. Read once when this file is sourced. When the gate
# is off, every public function returns immediately with zero JSONL writes —
# the framework is zero-cost when off.
#
# Schema alignment with scripts/lib/igos_trace.py:
#   - Same event envelope: {"type": "...", "ts": "...", ...domain fields}
#   - Same ISO-8601 millisecond UTC timestamp format
#   - Same event-type vocabulary (subprocess_start/end, phase_enter/exit,
#     pkg_enter/exit, pkg_phase, chroot_mount, build_start/end, etc.)
#   - Same byte-level capture contract: stdin_bytes + stdout_bytes +
#     stderr_bytes + the raw stream content captured verbatim, no truncation
#
# Cross-file `jq` joins by .runid / .ts work uniformly across Python + bash
# emitters because the schema is one schema.
#
# Implementation notes:
#   - JSON construction uses `jq -n -c` for safe escaping (every value
#     becomes a properly quoted JSON token).
#   - The sink file descriptor is opened via `exec {fd}>>"$path"` and held
#     open until trace_close. Writes use `flock` (in subshell scope, NOT
#     `flock -c` — the latter strips embedded JSON double-quotes during
#     shell evaluation) to serialize multi-process scenarios.
#   - REDACT_ENV_SUBSTRINGS mirrors the Python side: TOKEN / PASSWORD /
#     PASSPHRASE / SECRET / KEY / CRED / AUTH — case-insensitive substring
#     match against env-var names; matched values replaced with <REDACTED>
#     in subprocess_start.redacted_env_keys.
# ==========================================================================

# Bash 4+ required (associative arrays, ${parameter^^} upper-case expansion).
# We don't `set -e` here — sourcing this should NEVER abort the parent script
# even if a trace operation hits a sink-write failure. (The framework warns
# and continues, mirroring igos_trace.py:_emit's posture.)

# --------------------------------------------------------------------------
# Module-level state. Variables prefixed _TRACE_ so they don't clash with the
# parent script's namespace. Exported where child processes need to share.
# --------------------------------------------------------------------------

# Gate: either env-var enables. Frozen at source time.
if [[ "${IGOS_BUILD_DEBUG_VERBOSE:-}" =~ ^(1|true|yes|on)$ ]] \
    || [[ "${FORGE_DEBUG_VERBOSE:-}" =~ ^(1|true|yes|on)$ ]]; then
    _TRACE_VERBOSE=1
else
    _TRACE_VERBOSE=0
fi

# Sink file descriptor + open path. -1 means "no sink open".
_TRACE_FD=-1
_TRACE_SINK_PATH=""

# Run identifiers — inherit from the orchestrator's exported env, or
# generate at trace_init time if not set.
_TRACE_RUNID="${IGOS_TRACE_RUNID:-}"
_TRACE_START_TS="${IGOS_TRACE_START_TS:-}"

# Default durable-sink root (mirrors igos_trace.py:_DEFAULT_BUILD_LOGS_TRACE).
_TRACE_ROOT="${IGOS_TRACE_ROOT:-/mnt/intergenos/build/logs/trace}"

# JSON emission engine. We use python3 (NOT jq) because jq is absent inside the
# InterGenOS chroot (it is not an LFS/BLFS package), which silently dropped
# every in-chroot bash trace emit. python3 is present on the host AND in the
# chroot, reads large fields from files (no ARG_MAX), and handles non-UTF-8
# bytes via surrogateescape. trace-emit.py lives beside this file.
_TRACE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo /mnt/intergenos/scripts/lib)"
_TRACE_EMIT_PY="${_TRACE_LIB_DIR}/trace-emit.py"
_TRACE_PYTHON="$(command -v python3 2>/dev/null || true)"

# Re-resolve python3 at emit time (cached once found). The chroot-tools phase
# BUILDS python itself (chroot-build.sh: gettext, bison, perl, python, ...), so
# python3 does not exist in the chroot when trace.sh is first sourced there.
# Re-checking lets emits start working the moment python is installed mid-phase
# (texinfo/util-linux after python), and makes trace.sh robust wherever python
# appears late. Returns 0 if python is available.
_trace_ensure_python() {
    [ -n "$_TRACE_PYTHON" ] && return 0
    _TRACE_PYTHON="$(command -v python3 2>/dev/null || true)"
    [ -n "$_TRACE_PYTHON" ]
}

# Redaction policy — must match igos_trace.REDACT_ENV_SUBSTRINGS.
_TRACE_REDACT_ENV_SUBSTRINGS=("TOKEN" "PASSWORD" "PASSPHRASE" "SECRET" "KEY" "CRED" "AUTH")

# --------------------------------------------------------------------------
# Internal helpers.
# --------------------------------------------------------------------------

# Emit a millisecond-precision UTC ISO-8601 timestamp matching Python's
# igos_trace._iso_ts() byte-for-byte (Y-m-dTH:M:S.<3-digit-millis>Z).
_trace_iso_ts() {
    date -u '+%Y-%m-%dT%H:%M:%S.%3NZ'
}

# Build a compact JSON array from the given args via python3 (NOT jq — absent
# in chroot). Args are small (argv tokens, env-var names) so passing them on the
# command line is safe. Emits "[]" if python is unavailable.
_trace_json_array() {
    if _trace_ensure_python; then
        "$_TRACE_PYTHON" -c 'import sys,json; print(json.dumps(sys.argv[1:]))' "$@" 2>/dev/null || echo "[]"
    else
        echo "[]"
    fi
}

# Generate a 16-hex runid (matches igos_trace._gen_runid()).
_trace_gen_runid() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr -d '-' | cut -c1-16
    else
        # Fallback: /dev/urandom hex
        head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-16
    fi
}

# Check whether an env-var name matches the redact policy (case-insensitive
# substring match against _TRACE_REDACT_ENV_SUBSTRINGS).
_trace_env_should_redact() {
    local name_upper="${1^^}"
    local sub
    for sub in "${_TRACE_REDACT_ENV_SUBSTRINGS[@]}"; do
        if [[ "$name_upper" == *"$sub"* ]]; then
            return 0
        fi
    done
    return 1
}

# Emit one already-constructed JSON line to the open sink. Best-effort:
# sink-write failure warns to stderr and returns 0 (so a failed write never
# aborts the parent script — mirrors igos_trace.py:_emit's posture).
#
# Locking: we acquire the sink fd's advisory lock via `flock` in a subshell
# scope so multi-process scenarios (sub-shells in chroot-build-*.sh, package
# parallel-make worker pools) serialize their writes correctly. We do NOT
# use `flock -c "<cmd-string>"` because the -c shell-string strips embedded
# JSON double-quotes during shell evaluation — the resulting file contains
# unquoted broken JSON. The subshell-block form preserves the line verbatim.
_trace_emit_line() {
    local line="$1"
    if [ "$_TRACE_FD" -lt 0 ]; then
        return 0
    fi
    (
        # Acquire advisory lock on the sink fd (5-second deadline). If lock
        # acquisition fails we still try the write — losing the lock-order
        # guarantee is preferable to losing the event entirely.
        flock -w 5 "$_TRACE_FD" 2>/dev/null || true
        if ! printf '%s\n' "$line" >&"$_TRACE_FD" 2>/dev/null; then
            echo "trace.sh: sink write failed (best-effort dropped event)" >&2
        fi
    )
}

# Construct + emit a structured event via trace-emit.py (python, NOT jq —
# jq is absent in the chroot). Args: <event_type> [k=v ...]
# k=v          : string-typed value (--str)
# k::=v        : raw-JSON value (numbers/bools/arrays/objects via --json)
_trace_emit_event() {
    if [ "$_TRACE_VERBOSE" -eq 0 ] || [ "$_TRACE_FD" -lt 0 ]; then
        return 0
    fi
    if ! _trace_ensure_python || [ ! -f "$_TRACE_EMIT_PY" ]; then
        echo "trace.sh: python3/trace-emit.py unavailable; cannot emit (no silent drop)" >&2
        return 0
    fi
    local event_type="$1"
    shift

    local -a args=(--sink "$_TRACE_SINK_PATH" --str type "$event_type" --str ts "$(_trace_iso_ts)")
    local kv key val
    for kv in "$@"; do
        [ -z "$kv" ] && continue
        if [[ "$kv" == *"::="* ]]; then
            key="${kv%%::=*}"; val="${kv#*::=}"
            args+=(--json "$key" "$val")
        elif [[ "$kv" == *"="* ]]; then
            key="${kv%%=*}"; val="${kv#*=}"
            args+=(--str "$key" "$val")
        fi
    done
    "$_TRACE_PYTHON" "$_TRACE_EMIT_PY" "${args[@]}" \
        || echo "trace.sh: emit failed ($event_type)" >&2
}

# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------

# trace_is_verbose — return 0 if verbose mode is on, 1 otherwise.
trace_is_verbose() {
    [ "$_TRACE_VERBOSE" -eq 1 ]
}

# trace_init — open a per-scope sink. Idempotent; closes prior sink first.
# Args:
#   $1 = scope name. Recognized prefixes:
#        "orchestrator"  -> build-orchestrator-<startts>-<runid>.jsonl
#        "phase-<name>"  -> build-phase-<name>-<startts>-<runid>.jsonl
#        "tier-<name>"   -> build-tier-<name>-<startts>-<runid>.jsonl
#        "host-<name>"   -> build-host-<name>-<startts>-<runid>.jsonl
#        "pkg-<name>"    -> build-pkg-<name>-<startts>-<runid>.jsonl
#        anything else   -> build-<scope>-<startts>-<runid>.jsonl
#   $2 = optional runid override (otherwise inherits $IGOS_TRACE_RUNID or
#        generates fresh)
# Emits: trace_init event.
# Safe to call when verbose is off (no-op).
trace_init() {
    local scope="$1"
    local runid_override="${2:-}"

    # Close any prior open sink (mirrors igos_trace.py:init_trace idempotence).
    trace_close

    if [ "$_TRACE_VERBOSE" -eq 0 ]; then
        return 0
    fi

    # Populate _TRACE_RUNID + _TRACE_START_TS with precedence:
    #   1. explicit override arg
    #   2. inherited IGOS_TRACE_RUNID env var
    #   3. generate fresh
    if [ -n "$runid_override" ]; then
        _TRACE_RUNID="${runid_override:0:16}"
    elif [ -z "$_TRACE_RUNID" ]; then
        _TRACE_RUNID="$(_trace_gen_runid)"
    fi
    if [ -z "$_TRACE_START_TS" ]; then
        _TRACE_START_TS="$(date -u '+%Y%m%dT%H%M%SZ')"
    fi
    # Export so child processes (igos-build, pkm, chroot-build-*.sh) join the
    # same run trail.
    export IGOS_TRACE_RUNID="$_TRACE_RUNID"
    export IGOS_TRACE_START_TS="$_TRACE_START_TS"

    # Build sink filename by scope prefix.
    local fname
    case "$scope" in
        orchestrator)
            fname="build-orchestrator-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
        phase-*)
            local phase_name="${scope#phase-}"
            fname="build-phase-${phase_name}-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
        tier-*)
            local tier_name="${scope#tier-}"
            fname="build-tier-${tier_name}-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
        host-*)
            local host_name="${scope#host-}"
            fname="build-host-${host_name}-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
        pkg-*)
            local pkg_name="${scope#pkg-}"
            fname="build-pkg-${pkg_name}-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
        *)
            # Generic fallback
            fname="build-${scope}-${_TRACE_START_TS}-${_TRACE_RUNID}.jsonl"
            ;;
    esac

    _TRACE_SINK_PATH="${_TRACE_ROOT}/${fname}"
    # Ensure parent dir exists
    if ! mkdir -p "$_TRACE_ROOT" 2>/dev/null; then
        echo "trace.sh: could not create sink root $_TRACE_ROOT; trace disabled for this run" >&2
        _TRACE_VERBOSE=0
        return 0
    fi
    # Open the sink in append mode + hold the fd
    if ! exec {_TRACE_FD}>>"$_TRACE_SINK_PATH" 2>/dev/null; then
        echo "trace.sh: could not open sink $_TRACE_SINK_PATH; trace disabled for this run" >&2
        _TRACE_VERBOSE=0
        _TRACE_FD=-1
        return 0
    fi

    # Emit the trace_init event so the durable trail records its own opening.
    _trace_emit_event "trace_init" \
        "runid=${_TRACE_RUNID}" \
        "start_ts=${_TRACE_START_TS}" \
        "scope=${scope}" \
        "sink_path=${_TRACE_SINK_PATH}" \
        "pid::=$$" \
        "verbose::=true"
}

# trace_close — flush + close the open sink. Idempotent. Safe at any time.
trace_close() {
    if [ "$_TRACE_FD" -ge 0 ]; then
        # Closing the fd flushes any buffered writes
        exec {_TRACE_FD}>&- 2>/dev/null || true
        _TRACE_FD=-1
    fi
    _TRACE_SINK_PATH=""
}

# trace_event — emit an ad-hoc structured event.
# Args:
#   $1 = event type (e.g. "build_start", "pkg_phase", "chroot_mount", "narration")
#   remaining args = k=v (string-typed) or k::=v (raw JSON: numbers/bools/arrays)
# Examples:
#   trace_event build_start runid="$IGOS_TRACE_RUNID" host="$(hostname)"
#   trace_event pkg_phase pkg=glibc phase=configure rc::=0 duration_ms::=12345
trace_event() {
    _trace_emit_event "$@"
}

# trace_phase_enter — emit phase_enter event at the start of a phase.
# Args: <phase> [intent]
trace_phase_enter() {
    local phase="$1"
    local intent="${2:-}"
    _trace_emit_event "phase_enter" \
        "phase=${phase}" \
        "intent=${intent}"
}

# trace_phase_exit — emit phase_exit event at the end of a phase.
# Args: <phase> <rc> <duration_ms>
trace_phase_exit() {
    local phase="$1"
    local rc="${2:-0}"
    local duration_ms="${3:-0}"
    _trace_emit_event "phase_exit" \
        "phase=${phase}" \
        "rc::=${rc}" \
        "duration_ms::=${duration_ms}"
}

# trace_pkg_enter — emit pkg_enter at the start of a per-package build.
# Args: <pkg> <version> [tier]
trace_pkg_enter() {
    local pkg="$1"
    local version="${2:-}"
    local tier="${3:-}"
    _trace_emit_event "pkg_enter" \
        "pkg=${pkg}" \
        "version=${version}" \
        "tier=${tier}"
}

# trace_pkg_exit — emit pkg_exit at the end of a per-package build.
# Args: <pkg> <rc> <duration_ms>
trace_pkg_exit() {
    local pkg="$1"
    local rc="${2:-0}"
    local duration_ms="${3:-0}"
    _trace_emit_event "pkg_exit" \
        "pkg=${pkg}" \
        "rc::=${rc}" \
        "duration_ms::=${duration_ms}"
}

# trace_pkg_phase — emit pkg_phase at configure/build/check/install/post_install
# boundary completions.
# Args: <pkg> <phase> <rc> <duration_ms>
trace_pkg_phase() {
    local pkg="$1"
    local phase="$2"
    local rc="${3:-0}"
    local duration_ms="${4:-0}"
    _trace_emit_event "pkg_phase" \
        "pkg=${pkg}" \
        "phase=${phase}" \
        "rc::=${rc}" \
        "duration_ms::=${duration_ms}"
}

# trace_chroot_mount — emit chroot_mount event when binding a vfs into the chroot.
# Args: <kind> <source> <target> [flags]
trace_chroot_mount() {
    local kind="$1"
    local source="$2"
    local target="$3"
    local flags="${4:-}"
    _trace_emit_event "chroot_mount" \
        "kind=${kind}" \
        "source=${source}" \
        "target=${target}" \
        "flags=${flags}"
}

# trace_chroot_unmount — emit chroot_unmount when releasing a vfs.
# Args: <target>
trace_chroot_unmount() {
    local target="$1"
    _trace_emit_event "chroot_unmount" \
        "target=${target}"
}

# trace_run — wrap an external command with subprocess_start/subprocess_end
# events that capture argv + rc + stdout + stderr + stdin_bytes + stdout_bytes
# + stderr_bytes + duration_ms. Byte-level capture is preserved by piping
# stdout/stderr through temp files we then read back into the event.
#
# Optional named flags BEFORE the command:
#   --phase <name>      — phase tag
#   --intent <text>     — free-text intent description
#   --pkg <name>        — package tag
#   --tee <file>        — also append stdout+stderr to <file> (for existing
#                         per-phase / per-tier text logs that callers want
#                         preserved alongside the structured event)
#   --stdin-file <file> — supply this file's contents as stdin to the command
#
# After flags, the rest of "$@" is the argv to run.
#
# Returns the command's exit code.
#
# When verbose is off, this falls through to plain command execution (with the
# --tee behavior preserved so callers that already used `cmd | tee -a` get the
# same external behavior).
trace_run() {
    local phase=""
    local intent=""
    local pkg=""
    local tee_file=""
    local stdin_file=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --phase) phase="$2"; shift 2 ;;
            --intent) intent="$2"; shift 2 ;;
            --pkg) pkg="$2"; shift 2 ;;
            --tee) tee_file="$2"; shift 2 ;;
            --stdin-file) stdin_file="$2"; shift 2 ;;
            --) shift; break ;;
            *) break ;;
        esac
    done

    if [ "$_TRACE_VERBOSE" -eq 0 ]; then
        # Zero-cost path: just run the command, preserving --tee if requested.
        if [ -n "$tee_file" ]; then
            if [ -n "$stdin_file" ]; then
                "$@" < "$stdin_file" 2>&1 | tee -a "$tee_file"
            else
                "$@" 2>&1 | tee -a "$tee_file"
            fi
            return "${PIPESTATUS[0]}"
        else
            if [ -n "$stdin_file" ]; then
                "$@" < "$stdin_file"
            else
                "$@"
            fi
            return $?
        fi
    fi

    # Verbose path: capture argv as a JSON array (python, not jq).
    local argv_json
    argv_json="$(_trace_json_array "$@")"

    # Compute stdin byte length if a stdin file was passed.
    local stdin_bytes=0
    if [ -n "$stdin_file" ] && [ -r "$stdin_file" ]; then
        stdin_bytes=$(wc -c < "$stdin_file")
    fi

    # Capture redacted env_extra: list every env-var whose NAME matches the
    # redact policy. We emit only the NAMES (no values) so callers can audit
    # which secret-bearing vars were live without exposing the secrets
    # themselves. The Python side (igos_trace._redact_env) replaces values
    # with "<REDACTED>" because it has the value dict in hand; the bash side
    # has only the env keyspace, which is the safest projection.
    local env_redact_keys_json="[]"
    if compgen -e >/dev/null 2>&1; then
        local -a redacted_keys=()
        local varname
        while IFS= read -r varname; do
            if _trace_env_should_redact "$varname"; then
                redacted_keys+=("$varname")
            fi
        done < <(compgen -e)
        if [ ${#redacted_keys[@]} -gt 0 ]; then
            env_redact_keys_json="$(_trace_json_array "${redacted_keys[@]}")"
        fi
    fi

    # Emit subprocess_start
    _trace_emit_event "subprocess_start" \
        "phase=${phase}" \
        "intent=${intent}" \
        "pkg=${pkg}" \
        "cmd::=${argv_json}" \
        "stdin_bytes::=${stdin_bytes}" \
        "redacted_env_keys::=${env_redact_keys_json}"

    # Run the command with stdout + stderr piped through temp files so we
    # can both tee + capture the full bytes for the event.
    local stdout_tmp stderr_tmp
    stdout_tmp="$(mktemp /tmp/trace.stdout.XXXXXX)"
    stderr_tmp="$(mktemp /tmp/trace.stderr.XXXXXX)"

    local start_ms
    start_ms=$(date +%s%3N)

    # Stream stdout/stderr LIVE — to the tee file (if any) and the parent's
    # stdout/stderr — WHILE capturing each stream byte-exact to a temp file for
    # the subprocess_end event. Previously this redirected straight to the temp
    # files and only cat'd them to the log AFTER the command exited; for a
    # long-running tier builder (one python process building hundreds of pkgs)
    # that withheld ALL narration until the whole tier finished, so a failure at
    # the start was invisible for hours. The {fd}+PID dance closes the fds and
    # waits for the tee children to flush before we read the temp files, so the
    # byte counts and --rawfile capture below stay exact. (2026-06-02)
    local rc=0 _ofd _efd _opid _epid
    if [ -n "$tee_file" ]; then
        exec {_ofd}> >(tee -a "$tee_file" "$stdout_tmp"); _opid=$!
        exec {_efd}> >(tee -a "$tee_file" "$stderr_tmp" >&2); _epid=$!
    else
        exec {_ofd}> >(tee "$stdout_tmp"); _opid=$!
        exec {_efd}> >(tee "$stderr_tmp" >&2); _epid=$!
    fi
    if [ -n "$stdin_file" ]; then
        "$@" < "$stdin_file" >&${_ofd} 2>&${_efd} || rc=$?
    else
        "$@" >&${_ofd} 2>&${_efd} || rc=$?
    fi
    exec {_ofd}>&- {_efd}>&-
    wait "$_opid" "$_epid" 2>/dev/null || true

    local end_ms
    end_ms=$(date +%s%3N)
    local duration_ms=$((end_ms - start_ms))

    # Output already streamed LIVE above: tee -> tee_file (if any) + the parent's
    # stdout/stderr, interleaved as it was produced, and was simultaneously
    # captured byte-exact per-stream to the temp files for the structured
    # subprocess_end event below. tee_file is human-readable narration; consumers
    # needing strict chronological / per-stream ordering consume the structured
    # stream, not the tee_file.

    # Compute byte lengths
    local stdout_bytes stderr_bytes
    stdout_bytes=$(wc -c < "$stdout_tmp")
    stderr_bytes=$(wc -c < "$stderr_tmp")

    # Emit subprocess_end via trace-emit.py. stdout/stderr can be multi-MB —
    # passed as FILE PATHS (--rawfile), read directly by python (no ARG_MAX,
    # bytes verbatim, non-UTF-8 via surrogateescape). cmd is a small JSON array.
    if _trace_ensure_python && [ -f "$_TRACE_EMIT_PY" ]; then
        "$_TRACE_PYTHON" "$_TRACE_EMIT_PY" --sink "$_TRACE_SINK_PATH" \
            --str type subprocess_end --str ts "$(_trace_iso_ts)" \
            --str phase "$phase" --str intent "$intent" --str pkg "$pkg" \
            --json cmd "$argv_json" --json rc "$rc" \
            --json stdout_bytes "$stdout_bytes" --json stderr_bytes "$stderr_bytes" \
            --json duration_ms "$duration_ms" \
            --rawfile stdout "$stdout_tmp" --rawfile stderr "$stderr_tmp" \
            || echo "trace.sh: subprocess_end emit failed (pkg=$pkg)" >&2
    else
        echo "trace.sh: python3/trace-emit.py unavailable; subprocess_end not emitted" >&2
    fi

    # Cleanup temp files
    rm -f "$stdout_tmp" "$stderr_tmp" 2>/dev/null || true

    return $rc
}

# trace_run_chroot — convenience wrapper for chroot subprocess execution.
# Args:
#   $1 = chroot root (e.g. /mnt/igos)
#   remaining args = the command + args to run inside the chroot
# Optional named flags BEFORE the chroot arg: --phase, --intent, --pkg, --tee.
trace_run_chroot() {
    local phase=""
    local intent=""
    local pkg=""
    local tee_file=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --phase) phase="$2"; shift 2 ;;
            --intent) intent="$2"; shift 2 ;;
            --pkg) pkg="$2"; shift 2 ;;
            --tee) tee_file="$2"; shift 2 ;;
            --) shift; break ;;
            *) break ;;
        esac
    done

    local chroot_root="$1"
    shift

    local tee_args=()
    [ -n "$tee_file" ] && tee_args=(--tee "$tee_file")
    trace_run \
        --phase "$phase" \
        --intent "${intent:-chroot exec: $*}" \
        --pkg "$pkg" \
        "${tee_args[@]}" \
        chroot "$chroot_root" "$@"
}

# _trace_json_escape_small — minimal JSON string escaper for SMALL, controlled
# string values (type/ts/pkg/version/tier/phase/argv) used by the python-free
# pkg_capture fallback below. Escapes backslash FIRST (so we never double-escape
# the backslashes we introduce), then double-quote, then the bare control chars
# tab/CR/LF. These fields are package names, versions and ISO timestamps — short,
# single-token values — so this is sufficient; the large arbitrary-byte fields
# (input_cmd/output) are NOT escaped this way, they go through base64 transport.
_trace_json_escape_small() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\n'/\\n}"
    printf '%s' "$s"
}

# _trace_pkg_capture_nopy — python-free / jq-free pkg_capture emitter. Used ONLY
# when python3 is not yet present in the chroot: the chroot-tools tier builds
# gettext → bison → perl BEFORE python itself, so trace-emit.py (a python script)
# has no interpreter and the python path below would otherwise bail, leaving those
# three packages uncaptured. This reads the SAME per-package output log + input
# build-script that the python path reads (identical capture point, identical
# bytes), and transports both as base64 so the emitted line is valid UTF-8 /
# valid JSON with ZERO byte loss and ZERO dependency on python/jq/perl — only
# coreutils `base64` + `tr` + bash, all present in the chroot from Ch6 temp-tools.
#
# Schema: mirrors trace-emit.py's non-UTF-8 branch exactly — <key>_b64 plus
# <key>_encoding="base64" (never the plaintext <key>) — so any consumer that
# already handles trace-emit.py's base64 output reads these records unchanged.
# An `emit_engine`:"bash-base64-fallback" marker makes the degraded-transport
# records self-identifying for audit (extra keys are valid JSON; consumers ignore
# unknown fields). The bytes are byte-for-byte the same as the python path.
#
# Args (positional): pkg version tier phase argv rc duration_ms input_bytes
#                    output_bytes in_src out_src
_trace_pkg_capture_nopy() {
    local pkg="$1" version="$2" tier="$3" phase="$4" argv="$5"
    local rc="$6" duration_ms="$7" input_bytes="$8" output_bytes="$9"
    local in_src="${10}" out_src="${11}"

    # base64 transport, single line. `tr -d '\n'` rather than `base64 -w0` so we
    # do not depend on the GNU -w extension. /dev/null sources yield "" cleanly.
    local in_b64 out_b64
    in_b64="$(base64 < "$in_src" 2>/dev/null | tr -d '\n')"
    out_b64="$(base64 < "$out_src" 2>/dev/null | tr -d '\n')"

    local line
    line="{\"type\":\"pkg_capture\""
    line+=",\"ts\":\"$(_trace_iso_ts)\""
    line+=",\"pkg\":\"$(_trace_json_escape_small "$pkg")\""
    line+=",\"version\":\"$(_trace_json_escape_small "$version")\""
    line+=",\"tier\":\"$(_trace_json_escape_small "$tier")\""
    line+=",\"phase\":\"$(_trace_json_escape_small "$phase")\""
    line+=",\"argv\":\"$(_trace_json_escape_small "$argv")\""
    line+=",\"rc\":${rc}"
    line+=",\"duration_ms\":${duration_ms}"
    line+=",\"input_bytes\":${input_bytes}"
    line+=",\"output_bytes\":${output_bytes}"
    line+=",\"input_cmd_b64\":\"${in_b64}\",\"input_cmd_encoding\":\"base64\""
    line+=",\"output_b64\":\"${out_b64}\",\"output_encoding\":\"base64\""
    line+=",\"emit_engine\":\"bash-base64-fallback\"}"

    _trace_emit_line "$line"
}

# trace_pkg_capture — emit a per-package byte-capture event by reading an
# ALREADY-written per-package log (output bytes) plus an optional command /
# build-script file (input bytes).
#
# Rationale: the LFS bash builders (toolchain-build.sh, temp-tools-build.sh,
# chroot-build*.sh, chroot-build-ch8/ch10/base/core-extra.sh) run each
# package's configure/build/check/install with the output already redirected
# to a per-package log via `>> "$pkg_log" 2>&1`. Re-plumbing every one of
# those redirections through trace_run's live capture would be invasive and
# build-risky. Instead this reads the finished per-package log VERBATIM (no
# truncation) and records it alongside the exact input commands — giving the
# same "every input byte + corresponding output byte" guarantee trace_run
# gives, with ZERO change to how the build executes (pure post-hoc read).
#
# stdout/stderr are merged in the source log (the builders use `2>&1`), so the
# captured `output` field is the merged stream exactly as the build produced
# it. The python-builder tiers (igos-build) keep stdout/stderr separated via
# traced_run; this helper covers the bash LFS surface.
#
# Args (all via flags):
#   --pkg <name>            package name (required)
#   --version <v>           package version
#   --tier <name>           tier/phase-group label
#   --phase <label>         sub-phase label (e.g. "all", "configure+build+install")
#   --rc <int>              exit code of the package build
#   --duration-ms <int>     wall time
#   --log <file>            per-package OUTPUT log (required; bytes -> output)
#   --cmd-file <file>       INPUT command/build script (optional; bytes -> input_cmd)
#   --argv <text>           free-text argv/command summary (optional)
# Safe + no-op when verbose is off or sink not open.
trace_pkg_capture() {
    if [ "$_TRACE_VERBOSE" -eq 0 ] || [ "$_TRACE_FD" -lt 0 ]; then
        return 0
    fi
    local pkg="" version="" tier="" phase="" rc="0" duration_ms="0"
    local log_file="" cmd_file="" argv=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --pkg) pkg="$2"; shift 2 ;;
            --version) version="$2"; shift 2 ;;
            --tier) tier="$2"; shift 2 ;;
            --phase) phase="$2"; shift 2 ;;
            --rc) rc="$2"; shift 2 ;;
            --duration-ms) duration_ms="$2"; shift 2 ;;
            --log) log_file="$2"; shift 2 ;;
            --cmd-file) cmd_file="$2"; shift 2 ;;
            --argv) argv="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    # Byte counts come straight from wc -c on the files (exact, any size).
    local output_bytes=0 input_bytes=0
    local out_src="/dev/null" in_src="/dev/null"
    [ -n "$log_file" ] && [ -r "$log_file" ] && { out_src="$log_file"; output_bytes=$(wc -c < "$log_file"); }
    [ -n "$cmd_file" ] && [ -r "$cmd_file" ] && { in_src="$cmd_file"; input_bytes=$(wc -c < "$cmd_file"); }

    [[ "$rc" =~ ^-?[0-9]+$ ]] || rc=0
    [[ "$duration_ms" =~ ^[0-9]+$ ]] || duration_ms=0

    if _trace_ensure_python && [ -f "$_TRACE_EMIT_PY" ]; then
        # python path — trace-emit.py reads the OUTPUT log + INPUT cmd-file
        # directly (no ARG_MAX), verbatim, base64-ing non-UTF-8 — no jq, no size
        # limit, no silent drop.
        "$_TRACE_PYTHON" "$_TRACE_EMIT_PY" --sink "$_TRACE_SINK_PATH" \
            --str type pkg_capture --str ts "$(_trace_iso_ts)" \
            --str pkg "$pkg" --str version "$version" --str tier "$tier" \
            --str phase "$phase" --str argv "$argv" \
            --json rc "$rc" --json duration_ms "$duration_ms" \
            --json input_bytes "$input_bytes" --json output_bytes "$output_bytes" \
            --rawfile input_cmd "$in_src" --rawfile output "$out_src" \
            || echo "trace.sh: pkg_capture emit failed ($pkg) — investigate" >&2
    else
        # python-free fallback — python3 does not exist in the chroot yet
        # (chroot-tools builds gettext/bison/perl BEFORE python). Capture the
        # SAME per-package log + build-script via base64 so these three packages
        # are byte-captured live like every other package, with no python/jq/perl.
        # No silent drop: this branch ALWAYS emits.
        _trace_pkg_capture_nopy "$pkg" "$version" "$tier" "$phase" "$argv" \
            "$rc" "$duration_ms" "$input_bytes" "$output_bytes" "$in_src" "$out_src"
    fi
}

# build_failure_emit — emit a build_failure event before the parent script
# calls `exit 1`. Mirrors igos_trace.build_failure (Python side) so durable
# JSONL files always capture the structured failure context regardless of
# which side of the build pipeline emitted it.
#
# Args:
#   --where <site>     — where the failure happened (e.g. "chroot-build-core.sh:build_glibc")
#   --why <text>       — one-line explanation of the failure
#   --phase <name>     — optional phase tag
#   --pkg <name>       — optional package tag
#   --rc <int>         — optional exit code of the failed subprocess
#   --cmd <text>       — optional command text (joined argv)
#   --stderr <text>    — optional stderr capture
build_failure_emit() {
    local where=""
    local why=""
    local phase=""
    local pkg=""
    local rc=""
    local cmd=""
    local stderr=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --where) where="$2"; shift 2 ;;
            --why) why="$2"; shift 2 ;;
            --phase) phase="$2"; shift 2 ;;
            --pkg) pkg="$2"; shift 2 ;;
            --rc) rc="$2"; shift 2 ;;
            --cmd) cmd="$2"; shift 2 ;;
            --stderr) stderr="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    local -a args=()
    args+=("where=${where}")
    args+=("why=${why}")
    [ -n "$phase" ] && args+=("phase=${phase}")
    [ -n "$pkg" ] && args+=("pkg=${pkg}")
    if [ -n "$rc" ]; then
        args+=("rc::=${rc}")
    fi
    [ -n "$cmd" ] && args+=("cmd=${cmd}")
    [ -n "$stderr" ] && args+=("stderr=${stderr}")

    _trace_emit_event "build_failure" "${args[@]}"
}

# --------------------------------------------------------------------------
# Sourceable end-of-file marker. Callers should check IGOS_TRACE_LIB_LOADED=1
# to guard against double-sourcing.
# --------------------------------------------------------------------------

IGOS_TRACE_LIB_LOADED=1
