#!/bin/bash
# mirror-verify.sh — Daily VPS-side mirror integrity check (L-023).
#
# Runs on the mirror VPS (origin.intergenstudios.com) under a daily cron
# to detect drift between the signed InterGenOS.db index and the
# packages actually on disk in /home/intergenos/repo/x86_64/current/.
#
# Catches:
#   - Index/signature pair has been tampered with or corrupted
#   - Files referenced in the signed index are missing on disk
#   - On-disk files don't match the SHA-256 they're signed for
#   - Unexpected files present alongside the signed set
#
# Reports OK/FAIL counts to stdout + log; on FAIL exits non-zero so the
# cron's MAILTO surfaces alerts to the configured ops address.
#
# Environment overrides:
#   MIRROR_ROOT      Mirror root path (default: /home/intergenos/repo/x86_64)
#   TRUSTED_KEY      Path to keyring with master pubkey
#                    (default: /home/intergenos/.intergenos-verify-keyring/trusted.gpg)
#   ALERT_LEVEL      "strict" (default) — fail on any drift
#                    "warn"            — log drift but exit 0
#   LOG_FILE         Log file path (default: /var/log/intergenos-mirror-verify.log)
#
# First-run setup (operator, on VPS):
#   gpg --no-default-keyring --keyring /home/intergenos/.intergenos-verify-keyring/trusted.gpg \
#       --import /home/intergenos/.intergenos-verify-keyring/signing-key.asc
#
# Cron entry (operator, on VPS, as user intergenos):
#   MAILTO=legal@intergenos.org
#   30 2 * * *  /home/intergenos/bin/mirror-verify.sh
#
# Exit codes:
#   0  all packages verified (or drift logged + ALERT_LEVEL=warn)
#   1  signature failure or per-file integrity failure (any package SHA mismatch)
#   2  configuration error (mirror not present, keyring missing, etc.)

set -e -o pipefail

MIRROR_ROOT="${MIRROR_ROOT:-/home/intergenos/repo/x86_64}"
TRUSTED_KEY="${TRUSTED_KEY:-/home/intergenos/.intergenos-verify-keyring/trusted.gpg}"
ALERT_LEVEL="${ALERT_LEVEL:-strict}"
LOG_FILE="${LOG_FILE:-/var/log/intergenos-mirror-verify.log}"

CURRENT="$MIRROR_ROOT/current"
INDEX_PATH="$CURRENT/InterGenOS.db"
SIG_PATH="$CURRENT/InterGenOS.db.sig"

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG_FILE"
}

fail() {
    log "FATAL: $*"
    exit 2
}

# Preflight ---------------------------------------------------------------
[ -d "$CURRENT" ]      || fail "mirror current/ not present at $CURRENT"
[ -f "$INDEX_PATH" ]   || fail "signed index not present at $INDEX_PATH"
[ -f "$SIG_PATH" ]     || fail "signature not present at $SIG_PATH"
[ -f "$TRUSTED_KEY" ]  || fail "trusted keyring not present at $TRUSTED_KEY"
command -v gpg       >/dev/null || fail "gpg not installed"
command -v sha256sum >/dev/null || fail "sha256sum not installed"
command -v python3   >/dev/null || fail "python3 not installed (need json + gzip stdlib)"

log "BEGIN mirror-verify  root=$MIRROR_ROOT  level=$ALERT_LEVEL"

# Step 1 — Signature verification -----------------------------------------
log "[1/3] Verifying InterGenOS.db.sig against trusted keyring..."
if ! gpg --no-permission-warning --no-default-keyring \
        --keyring "$TRUSTED_KEY" \
        --verify "$SIG_PATH" "$INDEX_PATH" 2>>"$LOG_FILE"; then
    log "FAIL: signature verification failed on $INDEX_PATH"
    log "END mirror-verify status=SIG_FAIL"
    exit 1
fi
log "  OK — signature valid"

# Step 2 — Walk index, sha256-verify each package -------------------------
log "[2/3] Walking signed index packages..."

# Emit "<filename>\t<sha256>" lines from the index using stdlib only so we
# don't need PyYAML / requests / jq on the VPS.
PKG_LIST=$(python3 - "$INDEX_PATH" <<'PY'
import gzip, json, sys
path = sys.argv[1]
with gzip.open(path, "rb") as f:
    data = json.loads(f.read().decode("utf-8"))
for name, info in data.get("packages", {}).items():
    fn = info.get("filename")
    sha = info.get("sha256")
    if fn and sha:
        print(f"{fn}\t{sha}")
PY
)

OK_COUNT=0
MISSING_COUNT=0
SHA_FAIL_COUNT=0

while IFS=$'\t' read -r fn expected_sha; do
    [ -z "$fn" ] && continue
    pkg_path="$CURRENT/$fn"
    if [ ! -f "$pkg_path" ]; then
        log "FAIL: missing on disk: $fn"
        MISSING_COUNT=$((MISSING_COUNT + 1))
        continue
    fi
    actual_sha=$(sha256sum "$pkg_path" | awk '{print $1}')
    if [ "$actual_sha" != "$expected_sha" ]; then
        log "FAIL: SHA-256 mismatch on $fn"
        log "  expected $expected_sha"
        log "  actual   $actual_sha"
        SHA_FAIL_COUNT=$((SHA_FAIL_COUNT + 1))
        continue
    fi
    OK_COUNT=$((OK_COUNT + 1))
done <<< "$PKG_LIST"

# Step 3 — Stray-file check (informational, not fail-class by itself) -----
log "[3/3] Checking for unexpected files in current/..."
TRACKED_BASENAMES=$(echo "$PKG_LIST" | awk -F'\t' '{print $1}'; echo "InterGenOS.db"; echo "InterGenOS.db.sig")
STRAY_COUNT=0
while IFS= read -r fpath; do
    bn=$(basename "$fpath")
    if ! grep -qFx -- "$bn" <(echo "$TRACKED_BASENAMES"); then
        case "$bn" in
            sources)            continue ;;  # subdirectory, expected
            *)
                log "WARN: stray file in current/: $bn"
                STRAY_COUNT=$((STRAY_COUNT + 1))
                ;;
        esac
    fi
done < <(find "$CURRENT" -maxdepth 1 -mindepth 1 ! -type d -o -type d -mindepth 1)

# Summary -----------------------------------------------------------------
TOTAL=$((OK_COUNT + MISSING_COUNT + SHA_FAIL_COUNT))
log "SUMMARY  total=$TOTAL  ok=$OK_COUNT  missing=$MISSING_COUNT  sha_fail=$SHA_FAIL_COUNT  stray=$STRAY_COUNT"

if [ $MISSING_COUNT -gt 0 ] || [ $SHA_FAIL_COUNT -gt 0 ]; then
    log "END mirror-verify status=INTEGRITY_FAIL"
    [ "$ALERT_LEVEL" = "warn" ] && exit 0
    exit 1
fi

log "END mirror-verify status=OK"
exit 0
