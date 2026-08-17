#!/usr/bin/env bash
# =============================================================================
# build-watcher.sh — the canonical duration-budget build watcher (work-plan 1.15).
#
# WHAT IT IS
#   The run_in_background EVENT-TRIGGER half of the framework §3 belt-and-suspenders
#   monitoring pair (the ScheduleWakeup heartbeat is the coordinator's own half).
#   It ssh'es the build VM READ-ONLY, blocks until exactly ONE actionable event,
#   prints ONE structured line, and exits 0. The caller RE-ARMS a fresh instance
#   after every firing. It encodes doctrine §7's tripwire IN the script, so the
#   verdict never lives in a session transcript's judgment again.
#
# HOW TO RUN (live, on the burn)
#   bash /mnt/intergenos/scripts/build-watcher.sh \
#        --unit <systemd-unit> --vm <ssh-host> \
#        --log  /mnt/intergenos/build/logs/build-intergenos-<ts>.log \
#        [--budgets /mnt/intergenos/scripts/data/sbu-budgets.tsv] \
#        [--calibration <seconds-per-SBU>] [--poll <sec>] [--checkin <sec>]
#   Re-arm rule: on any printed line the script EXITS 0; launch a NEW instance to
#   keep watching. At a genuine halt (UNIT-DEAD / HALT-LINE / BUDGET-HALT) do NOT
#   re-arm — hold and hand to the halt-handler.
#
#   ⛔ NO `&` ANYWHERE INSIDE THIS SCRIPT. Backgrounding an ssh here orphans it and
#      the watcher dies instantly (the orphaned-ssh class, bit twice on 2026-07-09;
#      see reference_build_monitor_background_watcher). The script is itself the
#      thing the CALLER backgrounds — it never backgrounds anything internally.
#
# OFFLINE / TEST MODES (no ssh)
#   --replay <logfile>        scan one log file, print the detected event line
#                             (RECURSION-SIGNATURE / HALT-LINE / BUDGET-ALARM /
#                             BUDGET-HALT) or REPLAY-CLEAN. Used by the fixture suite
#                             and by the one-time full-log acceptance replay.
#   --classify-halt <logfile> print "clean --stop-after <phase>" or "failure: <line>"
#                             — the UNIT-DEAD verdict classifier, exercised directly.
#
# LIVENESS != HEALTH: a fresh log mtime proves the build is DOING something, not the
#   RIGHT thing (an infinite loop writes continuously too — the launch-4 glibc
#   recursion burned ~5 h behind a perpetually-fresh log). This script never emits a
#   "healthy" verdict from freshness; compiler-CPU presence appears only as CHECKIN
#   context, never as a pass.
# =============================================================================
set -uo pipefail

# ---- thresholds / defaults (doctrine §7, spec §3/§4) ------------------------
CONFIGURE_RUNS_MAX=2          # glibc dual-width legitimate max = 2 (launch-4 hit 57)
MAKE_SYSCALLS_MAX=120         # healthy dual-width ~51-53 once; launch-4 = 2909
GLIBC_WALL_CAP_S=2400         # 40-min hard cap for glibc
ALARM_RATIO=3                 # 3x budget -> BUDGET-ALARM
HALT_RATIO=5                  # 5x budget -> BUDGET-HALT (halt-condition)
DEFAULT_BUDGET_S=1800         # 30-min WALL default class (fixed, not sbu-scaled)
SIBLING_MULT="2.5"            # lib32 twin with no book SBU: sibling budget x2.5
POLL_S=30
CHECKIN_S=1740               # ~29 min nothing-happened heartbeat

UNIT=""; VM=""; ORCH_LOG=""; REPLAY=""; CLASSIFY=""; PKG_OVERRIDE=""
BUDGETS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/data/sbu-budgets.tsv"
CALIBRATION=""                # seconds per SBU; measured from logs if empty
STATE_FILE=""
FIRST_CHECKIN=0               # --first-checkin: emit an immediate orientation
                              # CHECKIN (calibration line) then exit. OPT-IN —
                              # a bare instance BLOCKS until a real event or the
                              # ~29-min heartbeat, so re-arms don't insta-fire
                              # (observed live 2026-07-09: every re-arm exited
                              # in seconds on the always-on first checkin).

die() { echo "build-watcher: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --unit)        UNIT="$2"; shift 2 ;;
        --vm)          VM="$2"; shift 2 ;;
        --log)         ORCH_LOG="$2"; shift 2 ;;
        --budgets)     BUDGETS="$2"; shift 2 ;;
        --calibration) CALIBRATION="$2"; shift 2 ;;
        --poll)        POLL_S="$2"; shift 2 ;;
        --checkin)     CHECKIN_S="$2"; shift 2 ;;
        --first-checkin) FIRST_CHECKIN=1; shift ;;
        --state)       STATE_FILE="$2"; shift 2 ;;
        --replay)      REPLAY="$2"; shift 2 ;;
        --pkg)         PKG_OVERRIDE="$2"; shift 2 ;;
        --classify-halt) CLASSIFY="$2"; shift 2 ;;
        -h|--help)     sed -n '2,45p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)             die "unknown argument: $1" ;;
    esac
done

# ---- orchestrator phase vocabulary (build-intergenos.sh PHASES) -------------
PHASES="validate verify-sources setup toolchain chroot-prep chroot-tools core config core-extra base kernel desktop extra compute ai bootloader image manifest squashfs ukis-verity iso"

# HALT patterns — orchestrator-specific phrasings, NOT a bare "error" (a package
# compile log legitimately contains "error" strings; these are the build's own
# halt/failure/signing markers, from the proven hand-rolled watchers).
HALT_RE='FAILED in |error: .*(halt|fail-closed)|tier validator exited nonzero|Enforced pause:|halting before|✗'

# =============================================================================
# Pure detection functions — shared by live mode and --replay/--classify-halt.
# Each takes a file path and reads it; none has side effects.
# =============================================================================

# pkg name from a per-package log basename "<pkg>-<YYYYMMDD>-<HHMMSS>.log" (or "<pkg>-<ts>.log")
pkg_from_logname() {
    local b; b="$(basename "$1")"; b="${b%.log}"
    # strip a trailing -<8digits>-<6digits> or -<digits> timestamp
    b="$(printf '%s' "$b" | sed -E 's/-[0-9]{8}-[0-9]{6}$//; s/-[0-9]{9,}$//')"
    # strip the drivers' phase marker so names hit the budget rows: chroot-tools
    # logs are "<pkg>-chroot-<ts>.log", ch8/ch10 logs are "<pkg>-ch8-<ts>.log" /
    # "<pkg>-ch10-<ts>.log" (live-defect #3, 2026-07-09: "gcc-ch8" missed the gcc
    # budget row and fell to the 1800s default). "-pass<N>" is NOT a phase marker
    # (binutils-pass1 is the calibration anchor's real name) — never strip it.
    b="$(printf '%s' "$b" | sed -E 's/-(chroot|ch[0-9]+|core-extra|base)$//')"
    printf '%s' "$b"
}

is_glibc() { case "$1" in glibc|glibc-*) return 0 ;; *) return 1 ;; esac; }

count_marker() { grep -c -- "$1" "$2" 2>/dev/null || echo 0; }

# RECURSION-SIGNATURE discriminators (glibc-scoped, spec §3.6). Echoes the first
# tripped "<discriminator>=<count>" (empty if none). Wall-cap is checked live.
recursion_signature() {
    local pkg="$1" file="$2" n
    is_glibc "$pkg" || return 0
    n=$(count_marker "checking build system type" "$file")
    if [ "$n" -gt "$CONFIGURE_RUNS_MAX" ]; then printf 'configure_runs=%s' "$n"; return 0; fi
    n=$(count_marker "make-syscalls" "$file")
    if [ "$n" -gt "$MAKE_SYSCALLS_MAX" ]; then printf 'make_syscalls=%s' "$n"; return 0; fi
    return 0
}

# HALT-LINE — first matching orchestrator halt/failure/signing line (empty if none).
halt_line() { grep -m1 -E -- "$HALT_RE" "$1" 2>/dev/null | sed -E 's/^\[[^]]*\] *//'; }

# Clean --stop-after vs failure classifier for the UNIT-DEAD verdict line.
classify_halt() {
    local file="$1" line phase
    line=$(grep -E 'Stopping after phase:' "$file" 2>/dev/null | tail -1)
    if [ -n "$line" ]; then
        phase=$(printf '%s' "$line" | sed -E 's/.*Stopping after phase: *([^ ]+).*/\1/')
        printf 'clean --stop-after %s' "$phase"; return 0
    fi
    line=$(halt_line "$file")
    if [ -n "$line" ]; then printf 'failure: %s' "$line"; return 0; fi
    printf 'unknown'
}

# first and last "[YYYY-MM-DD HH:MM:SS]" epochs in a file -> "FIRST LAST" (empty if <2).
first_last_epoch() {
    local file="$1" fts lts
    fts=$(grep -oE '^\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\]' "$file" 2>/dev/null | head -1 | tr -d '[]')
    lts=$(grep -oE '^\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\]' "$file" 2>/dev/null | tail -1 | tr -d '[]')
    [ -n "$fts" ] && [ -n "$lts" ] || return 0
    printf '%s %s' "$(date -d "$fts" +%s 2>/dev/null)" "$(date -d "$lts" +%s 2>/dev/null)"
}

# budget seconds for a package. Needs calibration (sec/SBU) for sbu rows; the default
# class is a fixed 1800 s wall and needs no calibration. sibling:<name> resolves x2.5.
budget_seconds() {
    local pkg="$1" cal="$2" row sbu src
    row=$(awk -F'\t' -v p="$pkg" '$1==p{print; exit}' "$BUDGETS" 2>/dev/null)
    if [ -z "$row" ]; then echo "$DEFAULT_BUDGET_S"; return 0; fi
    sbu=$(printf '%s' "$row" | cut -f2); src=$(printf '%s' "$row" | cut -f3)
    case "$src" in
        sibling:*)
            local sib; sib="${src#sibling:}"
            local sibrow sibsbu; sibrow=$(awk -F'\t' -v p="$sib" '$1==p{print;exit}' "$BUDGETS" 2>/dev/null)
            sibsbu=$(printf '%s' "$sibrow" | cut -f2)
            [ -n "$sibsbu" ] && [ -n "$cal" ] || { echo "$DEFAULT_BUDGET_S"; return 0; }
            awk -v s="$sibsbu" -v c="$cal" -v m="$SIBLING_MULT" 'BEGIN{printf "%d", s*c*m}'; return 0 ;;
    esac
    [ -n "$cal" ] || { echo "$DEFAULT_BUDGET_S"; return 0; }
    awk -v s="$sbu" -v c="$cal" 'BEGIN{printf "%d", s*c}'
}

# integer ratio class of elapsed/budget: echoes "HALT", "ALARM", or "" .
ratio_class() {
    local elapsed="$1" budget="$2"
    [ "$budget" -gt 0 ] 2>/dev/null || return 0
    awk -v e="$elapsed" -v b="$budget" -v h="$HALT_RATIO" -v a="$ALARM_RATIO" \
        'BEGIN{r=e/b; if(r>=h)print"HALT"; else if(r>=a)print"ALARM"}'
}

# =============================================================================
# --classify-halt : print the clean/failure verdict for one log and exit.
# =============================================================================
if [ -n "$CLASSIFY" ]; then
    [ -f "$CLASSIFY" ] || die "--classify-halt: no such file: $CLASSIFY"
    classify_halt "$CLASSIFY"; echo; exit 0
fi

# =============================================================================
# --replay : scan one file, print the highest-relevance event line, exit 0.
#   Order for a single-file replay: RECURSION (package-scoped, the acceptance
#   signal) -> HALT-LINE -> BUDGET (needs timestamps) -> REPLAY-CLEAN.
# =============================================================================
if [ -n "$REPLAY" ]; then
    [ -f "$REPLAY" ] || die "--replay: no such file: $REPLAY"
    pkg="${PKG_OVERRIDE:-$(pkg_from_logname "$REPLAY")}"
    sig="$(recursion_signature "$pkg" "$REPLAY")"
    if [ -n "$sig" ]; then echo "RECURSION-SIGNATURE $pkg $sig"; exit 0; fi
    h="$(halt_line "$REPLAY")"
    if [ -n "$h" ]; then echo "HALT-LINE $h"; exit 0; fi
    fl="$(first_last_epoch "$REPLAY")"
    if [ -n "$fl" ]; then
        first="${fl% *}"; last="${fl#* }"
        if [ -n "$first" ] && [ -n "$last" ]; then
            elapsed=$(( last - first ))
            budget="$(budget_seconds "$pkg" "$CALIBRATION")"
            cls="$(ratio_class "$elapsed" "$budget")"
            if [ -n "$cls" ]; then
                ratio=$(awk -v e="$elapsed" -v b="$budget" 'BEGIN{printf "%.1f", e/b}')
                echo "BUDGET-$cls $pkg elapsed=$elapsed budget=$budget ratio=$ratio"
                exit 0
            fi
        fi
    fi
    echo "REPLAY-CLEAN $(basename "$REPLAY")"; exit 0
fi

# =============================================================================
# LIVE mode — poll the build VM until ONE event, print it, exit 0. Caller re-arms.
# =============================================================================
[ -n "$UNIT" ] || die "live mode requires --unit"
[ -n "$ORCH_LOG" ] || die "live mode requires --log"
LOG_ROOT="$(dirname "$ORCH_LOG")"
# The chroot COPY's log dir on the VM — where chroot-phase drivers write their
# per-package logs (see newest_pkg_log). Absolute by rule; override via env.
CHROOT_LOG_ROOT="${CHROOT_LOG_ROOT:-/mnt/igos/mnt/intergenos/build/logs}"
[ -n "$STATE_FILE" ] || STATE_FILE="/tmp/build-watcher-${UNIT}.state"

# vm_exec: run a read-only command on the build VM (ssh) or locally when --vm absent.
vm_exec() {
    if [ -n "$VM" ]; then
        ssh -o ConnectTimeout=10 -o BatchMode=yes "$VM" "$1"
    else
        bash -c "$1"
    fi
}

current_phase() {
    local pf="$LOG_ROOT/.build-phase" p
    p="$(vm_exec "cat '$pf' 2>/dev/null" | tr -d '[:space:]')"
    if [ -n "$p" ]; then printf '%s' "$p"; return 0; fi
    vm_exec "grep -oE '>>> ($(echo "$PHASES" | tr ' ' '|')) ' '$ORCH_LOG' 2>/dev/null | tail -1" \
        | sed -E 's/^>>> //; s/ $//'
}

# Newest TIMESTAMPED per-package log across BOTH log roots. Chroot-phase drivers
# (chroot-tools/core/kernel + the Python tiers) write per-package logs into the
# chroot COPY at /mnt/igos/mnt/intergenos/build/logs — invisible to a "$LOG_ROOT"-
# only scan, which pinned a stale pre-chroot tier log as "current" from
# chroot-tools onward (live-defect #3, 2026-07-09: BUDGET-ALARM misattributed to
# temp-tools-build while gcc-ch8 ran healthy; the glibc recursion tripwire was
# blind for the same window). Bash-tier logs carry no -<ts> suffix (ch8-build.log,
# pkg-install.log, temp-tools-build.log) and are excluded by the timestamp filter —
# but the PYTHON tier-driver logs DO carry one (<tier>-build-<ts>.log, e.g.
# desktop-build-20260710-051110.log), so they must be excluded by name shape or the
# tier-consolidated log gets clocked as a "package" against the 30-min default
# budget (live-defect #5, 2026-07-10: false BUDGET-ALARM ratio=3.0 on desktop-build
# while gtk4 built healthy underneath). No package name ends in "-build"
# (verified against packages/*/ 2026-07-10), so the shape exclusion is safe.
newest_pkg_log() { vm_exec "ls -t '$LOG_ROOT'/*.log '$CHROOT_LOG_ROOT'/*.log 2>/dev/null | grep -E -- '-[0-9]{8}-[0-9]{6}\.log\$' | grep -Ev -- '-build-[0-9]{8}-[0-9]{6}\.log\$' | grep -vF '$(basename "$ORCH_LOG")' | head -1"; }

now_epoch() { date +%s; }

# record + echo first-seen epoch for ONE BUILD ATTEMPT (survives re-arm via the
# state file). Keyed by the LOG BASENAME, not the derived pkg name: bare names
# repeat across phases (Ch7 toolchain ncurses/bash/sed/... rebuild in Ch8), so a
# name-keyed clock inherits the EARLIER phase's epoch and false-alarms the whole
# later phase (live-defect #4, 2026-07-10: ch8 ncurses read the 22:06 toolchain
# row -> elapsed 6910s -> false 3.8x ALARM). A timestamped log basename is unique
# per attempt; rows for finished attempts go inert on their own.
first_seen() {
    local key="$1" now; now="$(now_epoch)"
    touch "$STATE_FILE" 2>/dev/null
    local rec; rec="$(awk -F'\t' -v p="$key" '$1==p{print $2; exit}' "$STATE_FILE" 2>/dev/null)"
    if [ -n "$rec" ]; then printf '%s' "$rec"; return 0; fi
    printf '%s\t%s\n' "$key" "$now" >> "$STATE_FILE"
    printf '%s' "$now"
}

# Calibrate seconds-per-SBU from binutils-pass1's own log window if not provided.
calibrate() {
    [ -n "$CALIBRATION" ] && { printf '%s' "$CALIBRATION"; return 0; }
    local blog; blog="$(vm_exec "ls -t '$LOG_ROOT'/binutils-pass1-*.log 2>/dev/null | head -1")"
    if [ -n "$blog" ]; then
        local fl; fl="$(vm_exec "grep -oE '^\[[0-9-]{10} [0-9:]{8}\]' '$blog' 2>/dev/null | sed -n '1p;\$p' | tr -d '[]'")"
        # best-effort; if unparseable, fall through to the anchor default below
        local f l; f="$(printf '%s\n' "$fl" | sed -n 1p)"; l="$(printf '%s\n' "$fl" | sed -n 2p)"
        if [ -n "$f" ] && [ -n "$l" ]; then
            local fe le; fe="$(date -d "$f" +%s 2>/dev/null)"; le="$(date -d "$l" +%s 2>/dev/null)"
            if [ -n "$fe" ] && [ -n "$le" ] && [ "$le" -gt "$fe" ]; then printf '%s' "$(( le - fe ))"; return 0; fi
        fi
    fi
    printf '193'   # launch-5 anchor: binutils-pass1 = 3m13s = 193 s
}

START_EPOCH="$(now_epoch)"
PREV_PHASE="$(current_phase)"
CAL="$(calibrate)"

while :; do
    # 1. UNIT-DEAD (highest priority) --------------------------------------
    # Parse by KEY, never by output position: `systemctl show -p A,B --value`
    # emits properties in systemd's own order, not the requested order (observed
    # live 2026-07-09: Result printed before ActiveState -> "success/active"
    # -> a false UNIT-DEAD on a healthy running unit, first plant of the burn).
    show_out="$(vm_exec "systemctl show '$UNIT' -p ActiveState,Result 2>/dev/null")"
    active="$(printf '%s\n' "$show_out" | sed -n 's/^ActiveState=//p')"
    result="$(printf '%s\n' "$show_out" | sed -n 's/^Result=//p')"
    state="${active}/${result}"
    if [ -n "$state" ] && [ "$active" != "active" ] && [ "$active" != "activating" ]; then
        tail_tmp="$(vm_exec "tail -80 '$ORCH_LOG' 2>/dev/null")"
        verdict="$(printf '%s\n' "$tail_tmp" | { tmp="$(mktemp)"; cat > "$tmp"; classify_halt "$tmp"; rm -f "$tmp"; })"
        echo "UNIT-DEAD $UNIT $state ($verdict)"; exit 0
    fi

    # 2. HALT-LINE ----------------------------------------------------------
    hl="$(vm_exec "grep -m1 -E -- '$HALT_RE' '$ORCH_LOG' 2>/dev/null | tail -1" | sed -E 's/^\[[^]]*\] *//')"
    if [ -n "$hl" ]; then echo "HALT-LINE $hl"; exit 0; fi

    # 3. PHASE-CHANGE -------------------------------------------------------
    cur_phase="$(current_phase)"
    if [ -n "$cur_phase" ] && [ "$cur_phase" != "$PREV_PHASE" ]; then
        echo "PHASE-CHANGE ${PREV_PHASE:-none} -> $cur_phase"; exit 0
    fi

    # 4/5. BUDGET (ALARM 3x / HALT 5x) --------------------------------------
    plog="$(newest_pkg_log)"
    pkg=""; elapsed=0
    if [ -n "$plog" ]; then
        pkg="$(pkg_from_logname "$plog")"
        fs="$(first_seen "$(basename "$plog")")"; elapsed=$(( $(now_epoch) - fs ))
        budget="$(budget_seconds "$pkg" "$CAL")"
        cls="$(ratio_class "$elapsed" "$budget")"
        if [ -n "$cls" ]; then
            ratio=$(awk -v e="$elapsed" -v b="$budget" 'BEGIN{printf "%.1f", e/b}')
            echo "BUDGET-$cls $pkg elapsed=$elapsed budget=$budget ratio=$ratio"; exit 0
        fi
    fi

    # 6. RECURSION-SIGNATURE (glibc-scoped: counts + 40-min wall cap) -------
    if [ -n "$plog" ]; then
        localtmp="$(mktemp)"; vm_exec "cat '$plog' 2>/dev/null" > "$localtmp"
        sig="$(recursion_signature "$pkg" "$localtmp")"; rm -f "$localtmp"
        if [ -n "$sig" ]; then echo "RECURSION-SIGNATURE $pkg $sig"; exit 0; fi
        if is_glibc "$pkg" && [ "$elapsed" -gt "$GLIBC_WALL_CAP_S" ]; then
            echo "RECURSION-SIGNATURE $pkg wall_seconds=$elapsed"; exit 0
        fi
    fi

    # 7. CHECKIN (~29-min heartbeat; also the first-poll orientation line) --
    now="$(now_epoch)"; since=$(( now - START_EPOCH ))
    if [ "$FIRST_CHECKIN" -eq 1 ] || [ "$since" -ge "$CHECKIN_S" ]; then
        echo "CHECKIN phase=${cur_phase:-unknown} pkg=${pkg:-none} elapsed=${elapsed}s sec_per_sbu=$CAL"
        exit 0
    fi

    sleep "$POLL_S"
done
