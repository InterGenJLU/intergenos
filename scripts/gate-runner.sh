#!/bin/bash
# gate-runner.sh — run a sequence of gates/steps with named [N/M] progress,
# per-step duration, a 30s still-running heartbeat, and a fail-fast summary.
#
# Born from the ge9b-05 mint's manual gate walk (progress-indicator candidate
# #1, 2026-07-18): long fail-closed gates (the silent-loss chroot scan, the
# audit re-aggregation) run minutes with no output, so the operator cannot
# tell "working" from "hung" without tailing logs. This wrapper owns the
# narration so the gates themselves stay untouched.
#
# Usage:
#   bash scripts/gate-runner.sh 'label :: command' 'label :: command' ...
#   bash scripts/gate-runner.sh --file <gates.txt>   # one 'label :: command' per line;
#                                                    # blank lines + #comments skipped
#
# Every command runs via bash -c from the CALLER's cwd — absolute paths in
# commands per Rule H. Exit: 0 = all gates passed; on the first failure the
# runner stops and exits with that gate's code (fail-fast, fail-closed).

set -u

GATES=()
if [[ "${1:-}" == "--file" ]]; then
    [[ -n "${2:-}" && -f "$2" ]] || { echo "gate-runner: gates file not found: ${2:-<missing>}" >&2; exit 97; }
    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        GATES+=("$line")
    done < "$2"
else
    GATES=("$@")
fi
TOTAL=${#GATES[@]}
[[ "$TOTAL" -gt 0 ]] || { echo "gate-runner: no gates given" >&2; exit 97; }

T0=$(date +%s)
N=0
for entry in "${GATES[@]}"; do
    N=$((N + 1))
    LABEL="${entry%%::*}"
    CMD="${entry#*::}"
    LABEL="$(echo "$LABEL" | sed 's/[[:space:]]*$//')"
    CMD="$(echo "$CMD" | sed 's/^[[:space:]]*//')"
    if [[ -z "$CMD" || "$CMD" == "$entry" && "$entry" != *"::"* ]]; then
        echo "gate-runner: malformed entry (need 'label :: command'): $entry" >&2
        exit 97
    fi

    echo ">>> [gate $N/$TOTAL] $LABEL — starting (total elapsed $(( $(date +%s) - T0 ))s)"
    GT0=$(date +%s)

    # Still-running heartbeat: proves liveness of the WRAPPER's wait, not the
    # gate's health (liveness is not health — duration budgets still apply).
    (
        while sleep 30; do
            echo "    [gate $N/$TOTAL] $LABEL — still running ($(( $(date +%s) - GT0 ))s)"
        done
    ) &
    HB_PID=$!

    bash -c "$CMD"
    RC=$?

    kill "$HB_PID" 2>/dev/null || true
    wait "$HB_PID" 2>/dev/null || true

    GDUR=$(( $(date +%s) - GT0 ))
    if [[ $RC -ne 0 ]]; then
        echo "✗ [gate $N/$TOTAL] $LABEL — FAILED rc=$RC after ${GDUR}s (total $(( $(date +%s) - T0 ))s)"
        echo "gate-runner: stopping at the first failure (fail-closed); $((N - 1))/$TOTAL passed."
        exit $RC
    fi
    echo "✓ [gate $N/$TOTAL] $LABEL — passed in ${GDUR}s"
done

echo "✓ gate-runner: all $TOTAL gates passed in $(( $(date +%s) - T0 ))s"
exit 0
