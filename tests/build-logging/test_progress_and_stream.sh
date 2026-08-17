#!/bin/bash
# Tests for the build-observability pair in scripts/lib/logging.sh and the
# chroot tier scripts that use it:
#
#   (1) the per-package progress counters — "package N of M — <name> (<tier>)"
#   (2) the single tailable build stream
#
# The library half is exercised directly. The tier-script half is asserted
# against the REAL scripts, because the thing that matters is not that the
# library can count — it is that each tier hands it a total derived from that
# tier's own plan and routes its narration into the stream. Those assertions
# strip comment lines first: a check that can be satisfied by a comment
# explaining the code is not checking the code.
#
# Run: bash tests/build-logging/test_progress_and_stream.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB="$REPO/scripts/lib/logging.sh"

PASS=0; FAIL=0
ck() {
    if eval "$2"; then PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi
}

# The documented consumer regex, written here exactly as the library's comment
# states it. If the emitted shape and the documented shape drift apart, this is
# what catches it.
PROGRESS_RE='^\[[^]]*\] progress: package ([0-9]+) of ([0-9]+) — (\S+) \(([^)]+)\) — (.*)$'

# A tier's log() is what routes a line to its sinks. The library emits through
# log(), so the harness supplies one, exactly as a tier script does.
emit() {
    ( # shellcheck disable=SC1090
      source "$LIB"
      log() { echo "[$(igos_timestamp)] $*"; }
      igos_progress_init "$1" "$2"
      shift 2
      eval "$@" )
}

# ---------------------------------------------------------------------------
# 1. The line shape is what the documented regex says it is.
# ---------------------------------------------------------------------------
line="$(emit core 90 'igos_progress_begin "glibc"')"
ck "start line matches the documented consumer regex" \
   '[[ "$line" =~ $PROGRESS_RE ]]'
ck "start line reports index 1"      '[ "${BASH_REMATCH[1]}" = "1" ]'
ck "start line reports the total"    '[ "${BASH_REMATCH[2]}" = "90" ]'
ck "start line reports the name"     '[ "${BASH_REMATCH[3]}" = "glibc" ]'
ck "start line reports the tier"     '[ "${BASH_REMATCH[4]}" = "core" ]'
ck "start line reports the state"    '[ "${BASH_REMATCH[5]}" = "start" ]'

# ---------------------------------------------------------------------------
# 2. N counts POSITION IN THE PLAN — skipped packages consume an index too, so
#    the number keeps meaning "how far through the plan", not "how many built".
# ---------------------------------------------------------------------------
out="$(emit base 33 'igos_progress_begin a; igos_progress_end a 0; igos_progress_skip b "already tracked"; igos_progress_begin c')"
ck "second package is index 2 even though it was skipped" \
   'echo "$out" | grep -q "package 2 of 33 — b (base) — skipped"'
ck "third package is index 3" \
   'echo "$out" | grep -q "package 3 of 33 — c (base) — start"'
ck "a skipped package states its reason" \
   'echo "$out" | grep -q "skipped (already tracked)"'
ck "a skip with no reason still says something" \
   '[ -n "$(emit base 33 "igos_progress_skip z" | grep "skipped (")" ]'

# ---------------------------------------------------------------------------
# 3. The closing word follows the return code.
# ---------------------------------------------------------------------------
ck "rc 0 closes with done" \
   'emit core 90 "igos_progress_begin g; igos_progress_end g 0" | grep -q -- "— done$"'
ck "a non-zero rc closes with failed and carries the code" \
   'emit core 90 "igos_progress_begin g; igos_progress_end g 3" | grep -q -- "— failed rc=3$"'
ck "a failed package still closes its pair" \
   '[ "$(emit core 90 "igos_progress_begin g; igos_progress_end g 3" | grep -c "package 1 of 90")" = "2" ]'
ck "a non-numeric rc is treated as a failure, not as success" \
   'emit core 90 "igos_progress_begin g; igos_progress_end g bogus" | grep -q "failed"'

# ---------------------------------------------------------------------------
# 4. THE FAIL-CLOSED PROPERTY. A package that starts and never ends must be
#    visible as such in the stream. This is the whole reason the lines come in
#    pairs, so it is asserted as a consumer would: count the opens against the
#    closes at each index.
# ---------------------------------------------------------------------------
unpaired() {
    # Reads a stream on stdin; prints the index of every start with no close.
    awk '
        /progress: package/ {
            idx = $0; sub(/.*progress: package /, "", idx); sub(/ of .*/, "", idx)
            if ($0 ~ /— start$/)                     started[idx] = 1
            if ($0 ~ /— done$/ || $0 ~ /— failed/)   ended[idx]   = 1
        }
        END { for (i in started) if (!(i in ended)) print i }
    '
}
hung="$(emit core 90 'igos_progress_begin a; igos_progress_end a 0; igos_progress_begin b' | unpaired)"
ck "a package that starts and never ends is detectable in the stream" \
   '[ "$hung" = "2" ]'
clean="$(emit core 90 'igos_progress_begin a; igos_progress_end a 0; igos_progress_begin b; igos_progress_end b 1' | unpaired)"
ck "a stream where every start closed reports nothing unpaired" \
   '[ -z "$clean" ]'
skipped_only="$(emit core 90 'igos_progress_skip a "resuming"' | unpaired)"
ck "a skipped package is not mistaken for a hang" \
   '[ -z "$skipped_only" ]'

# ---------------------------------------------------------------------------
# 5. The stream path is derived from the tier scripts' own log directory.
# ---------------------------------------------------------------------------
ck "the stream path sits in the given log directory" \
   '[ "$( IGOS_LOGS=/tmp/xyz bash -c "source $LIB; igos_build_stream_path" )" = "/tmp/xyz/build-current.log" ]'
ck "the stream path falls back to the guest build log dir" \
   '[ "$( env -u IGOS_LOGS bash -c "source $LIB; igos_build_stream_path" )" = "/mnt/intergenos/build/logs/build-current.log" ]'

# ---------------------------------------------------------------------------
# 6. THE TIER SCRIPTS THEMSELVES. Comment lines are stripped first so none of
#    these can be satisfied by prose describing the code.
# ---------------------------------------------------------------------------
code_of() { grep -v '^[[:space:]]*#' "$REPO/scripts/$1"; }

# Fixed-string helpers, called directly rather than through eval. The needles
# below are full of quotes and dollar signs; passing them through an eval'd
# string meant escaping them twice, which grep itself complained about ("stray
# \ before ..."). A warning coming out of the instrument is the last thing that
# should be tolerated in the instrument, so these take their arguments as
# arguments.
has_code()   { code_of "$1" | grep -qF -- "$2"; }
lacks_code() { ! code_of "$1" | grep -qF -- "$2"; }
ckc() {
    local desc="$1"; shift
    if "$@"; then PASS=$((PASS+1)); else echo "  FAIL: $desc"; FAIL=$((FAIL+1)); fi
}

# ANSI-C quoting so the embedded quotes are literal and "$0" is NOT expanded.
NEEDLE_CALLSITE_COUNT=$'grep -c \'^run_package "\''
NEEDLE_OLD_BARE_COUNT=$'grep -c \'^run_package\' "$0"'

for spec in "chroot-build-ch8.sh core PKG_COUNT 90" \
            "chroot-build-core-extra.sh core-extra EXTRA_PKG_COUNT 224" \
            "chroot-build-base.sh base BASE_PKG_COUNT 33"; do
    set -- $spec
    script="$1"; tier="$2"; var="$3"; expected="$4"

    # The total is DERIVED from the plan, and the derivation counts call sites
    # only — the bare '^run_package' pattern also matched the function's own
    # definition line, which is why every tier used to over-report by one.
    ckc "$script derives its total from run_package CALL SITES" \
        has_code "$script" "$NEEDLE_CALLSITE_COUNT"
    ckc "$script no longer counts its own run_package definition line" \
        lacks_code "$script" "$NEEDLE_OLD_BARE_COUNT"
    # The derived number is the real one: what the plan actually holds.
    ck "$script's derived total equals its real call-site count ($expected)" \
       "[ \"\$(grep -c '^run_package \"' \"$REPO/scripts/$script\")\" = \"$expected\" ]"
    ckc "$script's total is not a literal" \
        lacks_code "$script" "$var=$expected"

    # The counter is started with that derived total, under the tier's
    # orchestrator-phase name.
    ckc "$script starts progress accounting for tier '$tier'" \
        has_code "$script" "igos_progress_init $tier \"\$$var\""

    # Every package the plan reaches emits an opening line, and every started
    # package emits a closing one.
    ckc "$script opens a progress pair before building" \
        has_code "$script" 'igos_progress_begin "$name"'
    ckc "$script closes the pair with the build's return code" \
        has_code "$script" 'igos_progress_end "$name"'
    # Pin the REASON, not merely that some skip call exists. base has TWO skip
    # paths (resume-past and already-tracked); an assertion that only proved
    # "a skip call is present" passed with one of them deleted — measured, this
    # mutation survived until the reasons were pinned individually.
    ckc "$script reports a package skipped by a resume, with the reason" \
        has_code "$script" 'igos_progress_skip "$name" "resuming from $IGOS_START_AT"'

    # Narration reaches the aggregated stream.
    ckc "$script routes its narration into the aggregated stream" \
        has_code "$script" 'IGOS_BUILD_STREAM:+'
    ckc "$script still writes its own per-tier log" \
        has_code "$script" 'tee -a "$IGOS_LOGS/'
done

# base alone also skips a package that is already tracked; that path is its own
# instruction to the reader of the stream and so its own assertion here.
ckc "chroot-build-base.sh reports an already-tracked skip, with the reason" \
    has_code chroot-build-base.sh 'igos_progress_skip "$name" "already tracked"'

ckc "the unified tier script routes narration into the aggregated stream" \
    has_code chroot-build-tier.sh 'IGOS_BUILD_STREAM:+'
ckc "the unified tier script still writes its own per-tier log" \
    has_code chroot-build-tier.sh 'tee -a "$TIER_LOG"'

# The stream is an addition. Per-package logs must be untouched by this work.
for script in chroot-build-ch8.sh chroot-build-core-extra.sh chroot-build-base.sh; do
    ckc "$script still writes per-package logs" has_code "$script" 'pkg_log='
done

# ---------------------------------------------------------------------------
# 7. Guest-context paths are correct here and must stay literal: these scripts
#    run inside the build VM, where the tree is mounted at /mnt/intergenos.
# ---------------------------------------------------------------------------
ck "the library's stream fallback is the guest path" \
   "grep -q '/mnt/intergenos/build/logs' $LIB"
for script in chroot-build-ch8.sh chroot-build-core-extra.sh chroot-build-base.sh chroot-build-tier.sh; do
    ckc "$script keeps its guest-context log directory" \
        has_code "$script" 'IGOS_LOGS=/mnt/intergenos/build/logs'
done

# ---------------------------------------------------------------------------
# 7b. The dual sink actually WRITES BOTH FILES.
#
# Everything above proves the shipped scripts CONTAIN a tee with two
# destinations. That is not the same claim as "a logged line lands in both
# files", and the difference is where this kind of change fails. So the tier
# script's own log() definition is lifted out of the shipped file and run —
# these are the shipped bytes, not a copy written to agree with them.
# ---------------------------------------------------------------------------
lifted_log_test() {
    local script="$1" tierlog="$2"
    local T; T="$(mktemp -d)"
    local body; body="$(sed -n '/^log() {/,/^}/p' "$REPO/scripts/$script")"
    [ -n "$body" ] || { rm -rf "$T"; return 1; }
    (
        # shellcheck disable=SC1090
        source "$LIB"
        IGOS_LOGS="$T"
        TIER_LOG="$T/$tierlog"
        IGOS_BUILD_STREAM="$(igos_build_stream_path)"
        eval "$body"
        # `|| true` is retained for a reason that has CHANGED. It used to be
        # load-bearing: the shipped log() ended with
        # `[ trace-loaded ] && trace_event ...`, so it returned NON-ZERO
        # whenever the trace library was absent. That shape is now fixed (see
        # section 8b, which asserts the return status directly), so this `||`
        # no longer masks anything here — it stays only so this test keeps
        # asserting what it is about, the tee, rather than doubling as the
        # return-status test that 8b owns.
        log "canary line" >/dev/null || true
    ) || { rm -rf "$T"; return 1; }
    local rc=0
    grep -q "canary line" "$T/$tierlog"          || rc=1
    grep -q "canary line" "$T/build-current.log" || rc=1
    rm -rf "$T"
    return "$rc"
}

ckc "chroot-build-ch8.sh's own log() writes the tier log AND the stream" \
    lifted_log_test chroot-build-ch8.sh ch8-build.log
ckc "chroot-build-core-extra.sh's own log() writes both" \
    lifted_log_test chroot-build-core-extra.sh core-extra-build.log
ckc "chroot-build-base.sh's own log() writes both" \
    lifted_log_test chroot-build-base.sh base-build.log
ckc "chroot-build-tier.sh's own log() writes both" \
    lifted_log_test chroot-build-tier.sh tier-build.log

# And with the library absent the stream argument disappears rather than
# breaking the tier log — the tolerated-absence contract the tee relies on.
no_stream_test() {
    local T; T="$(mktemp -d)"
    local body; body="$(sed -n '/^log() {/,/^}/p' "$REPO/scripts/chroot-build-base.sh")"
    (
        igos_timestamp() { echo "TS"; }
        IGOS_LOGS="$T"
        IGOS_BUILD_STREAM=""
        eval "$body"
        log "canary" >/dev/null || true
    ) || { rm -rf "$T"; return 1; }
    local rc=0
    grep -q "canary" "$T/base-build.log" || rc=1
    [ -e "$T/build-current.log" ] && rc=1
    rm -rf "$T"
    return "$rc"
}
ckc "with no stream path set, the tier log still works and no stray file appears" \
    no_stream_test

# ---------------------------------------------------------------------------
# 8b. log() MUST RETURN ZERO WHEN THE TRACE LIBRARY IS ABSENT.
#
# The shipped log() used to end with an AND-list:
#
#     [ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_event ...
#
# When the trace library is not loaded the test fails, the && short-circuits,
# and THE AND-LIST IS THE FUNCTION'S LAST STATEMENT — so log() returned 1.
# Every one of these scripts runs under `set -e`, where a function returning
# non-zero as a bare statement aborts the caller. The tier therefore died at
# its FIRST narration line on any build without tracing enabled. Measured
# before the fix: ch8, desktop and tier each exited rc=1 having produced no
# output at all.
#
# The fix is if/fi rather than `|| true`, deliberately: `|| true` would also
# swallow a genuine trace_event failure, which is masking. if/fi changes
# exactly the broken case and leaves the trace-loaded path's status identical.
#
# This asserts the property on the SHIPPED bytes of every script that defines
# a log(), under the same `set -e` those scripts set — not on a copy written
# to agree with them.
# ---------------------------------------------------------------------------
# ⚠️ THIS RUNS IN A SEPARATE bash PROCESS, AND THAT IS LOAD-BEARING — the check
# CANNOT be done in-process. `set -e` is suppressed for the entire dynamic
# extent of a command used as an `if` condition, and every assertion here is
# invoked through ckc, which is `if "$@"; then`. The suppression reaches into
# nested subshells too, so neither `$( set -e; … )` nor `( set -e; … )` engages.
# Measured while writing this:
#
#     f(){ ( set -e; false; echo INNER-CONTINUED ); }
#     if f; then …            ->  prints INNER-CONTINUED
#     bash -c 'set -e; false; echo X'   ->  prints nothing, rc=1
#
# The first two versions of this check were therefore VACUOUS: they passed
# whether or not the bug was present. That was caught only by the negative
# control — reverting one script showed the shape assertion going red while
# this one stayed green. A behavioural test that cannot fail is worse than no
# test, because it reads as coverage. The control is what earns the assertion.
log_survives_without_trace() {
    local script="$1"
    local T; T="$(mktemp -d)"
    local body; body="$(sed -n '/^log() {/,/^}/p' "$REPO/scripts/$script")"
    [ -n "$body" ] || { rm -rf "$T"; return 1; }
    {
        echo 'set -e'
        echo 'igos_timestamp() { echo TS; }'
        echo "IGOS_LOGS=$T; TIER_LOG=$T/t.log; DESKTOP_LOG=$T/t.log"
        echo "AI_LOG=$T/t.log; EXTRA_LOG=$T/t.log; COMPUTE_LOG=$T/t.log"
        echo 'IGOS_BUILD_STREAM=""'
        echo "$body"
        echo 'log "first narration" >/dev/null'
        # Reaching here at all is the property under test: under set -e a
        # non-zero log() aborts this script before the marker is written.
        echo ": > $T/reached"
    } > "$T/driver.sh"
    bash "$T/driver.sh" >/dev/null 2>&1
    local rc=0
    [ -f "$T/reached" ] || rc=1
    rm -rf "$T"
    return "$rc"
}

for script in chroot-build-ch8.sh chroot-build-core-extra.sh chroot-build-base.sh \
              chroot-build-tier.sh chroot-build-ch10.sh chroot-build-desktop.sh \
              chroot-build-ai.sh chroot-build-extra.sh chroot-build-compute.sh; do
    ckc "$script's log() survives under set -e with the trace library absent" \
        log_survives_without_trace "$script"
    # And the shape itself is pinned, so a future edit cannot quietly
    # reintroduce the AND-list and still pass the behavioural check above by
    # accident of some other change.
    ckc "$script's log() does not end in a bare trace AND-list" \
        lacks_code "$script" '"1" ] && trace_event'
done

# ---------------------------------------------------------------------------
# 8. Every touched script still parses.
# ---------------------------------------------------------------------------
for script in lib/logging.sh chroot-build-ch8.sh chroot-build-core-extra.sh \
              chroot-build-base.sh chroot-build-tier.sh; do
    ck "$script parses" "bash -n $REPO/scripts/$script 2>/dev/null"
done

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
