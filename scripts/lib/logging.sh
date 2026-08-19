# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ============================================================================
# InterGenOS shell build-output library — the single house style.
# ============================================================================
#
# This is the one place the shell build pipeline (the orchestrator
# build-intergenos.sh, the per-tier chroot-build-*.sh scripts, build-squashfs.sh,
# build-iso.sh, the sign-*.sh ceremony scripts, and pkg-functions.sh) gets its
# user-facing voice from. Before this library, each script carried its own
# near-identical log() copy and its own ad-hoc zoo of phase tags (===bars / >>>
# / !!! / [ALLCAPS] / [WARN] / [FATAL] / ERROR:), which drifted independently.
#
# The house style is calibrated to prevailing distro build-output convention
# (the consistent phase/section markers + colored status you see from Gentoo's
# emerge >>>, Arch makepkg's ==>, and Debian's tooling) rather than a forced
# global prose indent. The 2-space prose margin is a CLI-tool convention that
# stays with pkm; the builder and Forge follow build-output convention instead.
# Indentation is used only where it is already structural (sub-steps).
#
# Design contract:
#   * KEEP the [YYYY-MM-DD HH:MM:SS] timestamp prefix on the narration line —
#     the operator reads it as professional, and the text logs are timestamped.
#   * The builder + the signing ceremony KEEP their detailed/verbose-leaning
#     default. We are cleaning the VOICE, not cutting the VOLUME — the operator
#     wants build/install detail.
#   * ONE phase/step line style with aligned labels.
#   * ONE severity scheme: error: / warning: / note: (apt/dnf style, lower-case),
#     routed to stderr for warning/error. A single sanctioned verdict marker
#     pair: ✓ (ok) / ✗ (fail), plus ⚠ for warnings.
#   * No yelling — no !!! and no gratuitous ALL-CAPS.
#   * No internal codenames in user-facing output (those may stay in code
#     comments only).
#   * Color is minimal, consistent, and TTY-aware: auto-off when stdout is not
#     a terminal or when NO_COLOR is set.
#
# This library does NOT own the per-script side effects (tee to a per-tier
# text log, JSONL trace mirroring). Those differ by script — sink path and
# trace tag — and stay in each script's own log() wrapper. This library owns
# the FORMATTING: it renders the line; the caller decides where it lands.
#
# Idempotent: guarded so sourcing twice (orchestrator + a sourced helper) is a
# no-op the second time.
# ============================================================================

if [ -n "${IGOS_LOGGING_LIB_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
IGOS_LOGGING_LIB_LOADED=1

# ----------------------------------------------------------------------------
# Color — minimal, consistent, TTY-aware.
#
# Colors are emitted ONLY when stdout is a real terminal and NO_COLOR is unset
# (https://no-color.org/). When piped to a file or a pager, every code below
# resolves to the empty string, so the text logs stay clean and grep-friendly.
# ----------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    IGOS_C_RESET=$'\033[0m'
    IGOS_C_BOLD=$'\033[1m'
    IGOS_C_DIM=$'\033[2m'
    IGOS_C_BLUE=$'\033[34m'
    IGOS_C_GREEN=$'\033[32m'
    IGOS_C_YELLOW=$'\033[33m'
    IGOS_C_RED=$'\033[31m'
else
    IGOS_C_RESET=""
    IGOS_C_BOLD=""
    IGOS_C_DIM=""
    IGOS_C_BLUE=""
    IGOS_C_GREEN=""
    IGOS_C_YELLOW=""
    IGOS_C_RED=""
fi

# Sanctioned verdict markers — the only emoji this library emits.
IGOS_MARK_OK="✓"
IGOS_MARK_FAIL="✗"
IGOS_MARK_WARN="⚠"

# Aligned label column for step lines, e.g. "Build:" / "Verify:" / "Deploy:".
# Mirrors pkm's Reporter._LABEL_WIDTH so the two surfaces read the same.
IGOS_LABEL_WIDTH=11

# ----------------------------------------------------------------------------
# igos_timestamp — the [YYYY-MM-DD HH:MM:SS] prefix the build logs keep.
# ----------------------------------------------------------------------------
igos_timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

# ----------------------------------------------------------------------------
# log — the primary narration line.
#
# Renders "[<timestamp>] <message>" to stdout. This is the formatting half of
# the per-script log() wrappers: each script keeps its own one-line wrapper
# that calls this and then does its script-specific side effects (tee to the
# tier text log, mirror to the JSONL trace). Centralizing the rendering kills
# the divergent copies while leaving each sink/tag where it belongs.
# ----------------------------------------------------------------------------
log() {
    printf '[%s] %s\n' "$(igos_timestamp)" "$*"
}

# ----------------------------------------------------------------------------
# Severity — ONE scheme: note: / warning: / error: (lower-case, apt/dnf style).
#
# note    -> stdout (an informational aside).
# warning -> stderr, led with the ⚠ triangle (bold yellow on a TTY) so it stays
#            scannable even when color is stripped (pipe/log).
# error   -> stderr, led with the ✗ mark (bold red on a TTY), same rationale.
#
# Each carries the timestamp prefix so it reads consistently with log() lines
# in the same stream and in the text logs.
# ----------------------------------------------------------------------------
note() {
    printf '[%s] note: %s\n' "$(igos_timestamp)" "$*"
}

warn() {
    printf '[%s] %s%s%s warning:%s %s\n' \
        "$(igos_timestamp)" "${IGOS_C_BOLD}" "${IGOS_C_YELLOW}" "${IGOS_MARK_WARN}" "${IGOS_C_RESET}" "$*" >&2
}

error() {
    printf '[%s] %s%s%s error:%s %s\n' \
        "$(igos_timestamp)" "${IGOS_C_BOLD}" "${IGOS_C_RED}" "${IGOS_MARK_FAIL}" "${IGOS_C_RESET}" "$*" >&2
}

# die — emit an error and exit non-zero (default rc 1; pass an int as the LAST
# argument is NOT supported — keep the call site explicit: `die "msg"; exit N`
# only if a non-1 code is required). Most callers want the simple 1.
die() {
    error "$*"
    exit 1
}

# ----------------------------------------------------------------------------
# phase_banner — the ONE phase/section header style.
#
# Replaces the zoo of ===bars / >>> / !!! / [ALLCAPS] phase tags with a single
# distro-convention marker. Reads like Gentoo's emerge / Arch's makepkg: a
# bold, colored ">>>" lead-in, the phase name, and an optional description.
# Carries the timestamp prefix (kept by operator request).
#
#   phase_banner core "Build core system (Ch 8, LFS order)"
#   -> [2026-06-17 14:00:00] >>> core — Build core system (Ch 8, LFS order)
# ----------------------------------------------------------------------------
phase_banner() {
    local phase="$1"
    local desc="${2:-}"
    local line=">>> ${phase}"
    [ -n "$desc" ] && line="${line} — ${desc}"
    printf '[%s] %s%s%s%s\n' \
        "$(igos_timestamp)" "${IGOS_C_BOLD}" "${IGOS_C_BLUE}" "$line" "${IGOS_C_RESET}"
}

# ----------------------------------------------------------------------------
# step — a phase/sub-step line with an aligned label (model: pkm's .step()).
#
#   step Build  "glibc 2.40"
#   step Verify "sha256 match"
#   -> [..] >> Build:      glibc 2.40
#
# The "  >>" indent marks it as a sub-step under the current phase banner —
# this is the one place we indent, because it is already structural.
# ----------------------------------------------------------------------------
step() {
    local label="$1"
    local detail="${2:-}"
    local lab="${label}:"
    # Pad the label to the aligned column.
    printf -v lab '%-*s' "$IGOS_LABEL_WIDTH" "$lab"
    printf '[%s]   %s>>%s %s%s\n' \
        "$(igos_timestamp)" "${IGOS_C_DIM}" "${IGOS_C_RESET}" "$lab" "$detail"
}

# ----------------------------------------------------------------------------
# Verdict helpers — the single sanctioned ok/fail markers.
# ----------------------------------------------------------------------------
ok() {
    printf '[%s] %s%s%s %s\n' \
        "$(igos_timestamp)" "${IGOS_C_GREEN}" "${IGOS_MARK_OK}" "${IGOS_C_RESET}" "$*"
}

fail() {
    printf '[%s] %s%s%s %s\n' \
        "$(igos_timestamp)" "${IGOS_C_RED}" "${IGOS_MARK_FAIL}" "${IGOS_C_RESET}" "$*" >&2
}

# ============================================================================
# Progress counters — "package N of M", machine-parsable.
# ============================================================================
#
# WHY. Until now the only way to know how far a tier had got was to infer it
# from log age and archive counts, and that inference is exactly what the
# duration-budget tripwire had to be built around: a fresh log mtime proves
# the build is doing SOMETHING, never that it is doing the right thing. These
# lines state position in the tier's build plan outright, so a watcher and a
# person read the same fact instead of deriving it.
#
# THE LINE, one shape, emitted for every package:
#
#   progress: package <N> of <M> — <name> (<tier>) — <state>
#
# and a consumer matches it with:
#
#   ^\[[^]]*\] progress: package ([0-9]+) of ([0-9]+) — (\S+) \(([^)]+)\) — (.*)$
#
# STATES: `start`, `done`, `failed rc=<n>`, `skipped (<reason>)`. Every package
# emits exactly one opening line (`start` or `skipped`) and, when it opened
# with `start`, exactly one closing line (`done` or `failed`).
#
# THAT PAIRING IS THE POINT, and it is what makes the stream fail-closed
# rather than merely informative: a package that begins and never ends leaves
# a `start` with no matching `done`/`failed` at the same index. A hang, a
# killed build, an OOM — none of them get to look like progress, because the
# absence of the closing line IS the signal. A counter that only counted
# completions could not say that; it would simply stop, which is what log age
# already fails to distinguish.
#
# N counts POSITION IN THE PLAN, not packages built: it increments for every
# package the plan reaches, skipped ones included. So N is monotonic and M is
# the plan's own size, and "12 of 34" answers "how far through the plan is
# this" — which is the question being asked. It deliberately does not answer
# "how many were compiled"; a skipped package is reported as skipped and is
# still the 12th thing the plan reached.
#
# M is DERIVED, never written down: each tier passes the count it already
# derives from its own build plan. A hardcoded total is a number that goes
# wrong silently the first time a package is added.
# ----------------------------------------------------------------------------

# igos_progress_init <tier> <total> — begin a tier's progress accounting.
igos_progress_init() {
    IGOS_PROGRESS_TIER="$1"
    IGOS_PROGRESS_TOTAL="$2"
    IGOS_PROGRESS_INDEX=0
}

# _igos_progress_emit <name> <state> — render one line.
#
# It calls log() rather than printing directly, so the line lands wherever
# that tier's own log() sends things (its text log, the aggregated build
# stream, the trace mirror). Each tier script defines its log() AFTER sourcing
# this library and bash resolves the name at call time, so this picks up the
# tier's wrapper rather than the plain renderer above.
_igos_progress_emit() {
    local name="$1" state="$2"
    log "progress: package ${IGOS_PROGRESS_INDEX} of ${IGOS_PROGRESS_TOTAL} — ${name} (${IGOS_PROGRESS_TIER}) — ${state}"
}

# igos_progress_begin <name> — a package the plan is starting now.
igos_progress_begin() {
    IGOS_PROGRESS_INDEX=$(( ${IGOS_PROGRESS_INDEX:-0} + 1 ))
    _igos_progress_emit "$1" "start"
}

# igos_progress_end <name> <rc> — that package finished; rc decides the word.
igos_progress_end() {
    local name="$1" rc="${2:-0}"
    if [ "$rc" -eq 0 ] 2>/dev/null; then
        _igos_progress_emit "$name" "done"
    else
        _igos_progress_emit "$name" "failed rc=${rc}"
    fi
}

# igos_progress_skip <name> <reason> — the plan reached it and moved on.
# Consumes an index (see the N-counts-position note above) and is terminal:
# a skipped package never emits a closing line, because it never opened one.
igos_progress_skip() {
    IGOS_PROGRESS_INDEX=$(( ${IGOS_PROGRESS_INDEX:-0} + 1 ))
    _igos_progress_emit "$1" "skipped (${2:-no reason given})"
}

# ============================================================================
# The single tailable build stream.
# ============================================================================
#
# WHY. A multi-tier build wrote one text log per tier, so following a build
# meant knowing which tier was live and re-pointing `tail -f` at each handover
# — and the handover is exactly the moment worth watching. This is one stable
# path that every tier appends its narration to, so a single `tail -f` follows
# a whole build from the first tier to the last.
#
# It ADDS, never replaces: the per-tier logs and the per-package logs are
# written exactly as before, and this stream carries the narration lines on
# top. Nothing reads it as an authority — it is a convenience view, and the
# per-tier logs remain the record.
#
# It is APPENDED to and never truncated, matching what the per-tier logs
# already do (`<tier>-build.log` has always accumulated across runs). Each
# tier's existing start banner carries the tier name and date, so consecutive
# builds are separable by eye and by grep. Truncating here would be worse than
# useless: a later tier would erase the earlier tiers of its own build.
# ----------------------------------------------------------------------------

# igos_build_stream_path — the stable path, derived from the tier scripts'
# own log directory so there is exactly one place the location is decided.
igos_build_stream_path() {
    printf '%s/build-current.log\n' "${IGOS_LOGS:-/mnt/intergenos/build/logs}"
}
