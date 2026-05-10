#!/usr/bin/env bash
# installer/smoke/checks/signing.sh — Category 2: signing-chain validation.
#
# Confirms the master signing key is in the system keyring, the embedded
# archive manifest signature still validates, and the install-time audit
# log is intact. Each function emits exactly one check_* result.

# Master signing-key fingerprint (durable v1.0; per memory reference_v1_signing_keys).
SMOKE_MASTER_FPR="${SMOKE_MASTER_FPR:-5597A3E0587B253006D0DD7B8C50826182083050}"

check_signing_master_key() {
    if ! command -v gpg >/dev/null 2>&1; then
        check_fail "sign/master" "gpg not in PATH"
        return
    fi
    if ! gpg --list-keys "$SMOKE_MASTER_FPR" >/dev/null 2>&1; then
        check_fail "sign/master" "master fingerprint $SMOKE_MASTER_FPR not in keyring"
        return
    fi
    check_pass "sign/master" "master key in keyring"
}

check_signing_manifest_signature() {
    local manifest_dir="/var/lib/igos/manifest"
    local manifest="$manifest_dir/intergenos-archive-manifest.txt"
    local sig="$manifest.sig"
    local key="$manifest_dir/intergenos-release-key.asc"

    if [ ! -d "$manifest_dir" ]; then
        check_skip "sign/manifest" "$manifest_dir not present (older install or non-RFC-v1 build)"
        return
    fi
    if [ ! -f "$manifest" ]; then
        check_fail "sign/manifest" "manifest file missing at $manifest"
        return
    fi
    if [ ! -f "$sig" ]; then
        check_fail "sign/manifest" "signature missing at $sig"
        return
    fi
    if [ ! -f "$key" ]; then
        check_fail "sign/manifest" "release key missing at $key"
        return
    fi

    # Verify in an ephemeral keyring so we trust ONLY the embedded key for
    # this check. Mirrors scripts/check-manifest-signature.sh §2.
    local gpghome
    gpghome="$(mktemp -d)"
    trap 'rm -rf "$gpghome"' RETURN

    if ! gpg --homedir "$gpghome" --import "$key" >/dev/null 2>&1; then
        check_fail "sign/manifest" "release key $key failed import"
        return
    fi
    if ! gpg --homedir "$gpghome" --verify "$sig" "$manifest" >/dev/null 2>&1; then
        check_fail "sign/manifest" "signature does NOT validate against embedded release key"
        return
    fi
    check_pass "sign/manifest" "signature validates against embedded release key"
}

check_signing_audit_log() {
    local log="/var/lib/igos/audit/integrity-events.jsonl"

    if [ ! -f "$log" ]; then
        check_skip "sign/audit-log" "$log not present (manifest verification didn't run on this install)"
        return
    fi

    # Each line is a JSON event with prev_hash + this_hash + event payload.
    # Walk the chain: every line N's prev_hash must equal line (N-1)'s this_hash.
    # Genesis line's prev_hash is the all-zeros sentinel.
    local lineno=0 prev="" expected=""
    while IFS= read -r line; do
        lineno=$((lineno+1))
        local ph th
        ph="$(printf '%s' "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('prev_hash',''))" 2>/dev/null)"
        th="$(printf '%s' "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('this_hash',''))" 2>/dev/null)"
        if [ -z "$th" ]; then
            check_fail "sign/audit-log" "line $lineno missing this_hash"
            return
        fi
        if [ -n "$expected" ] && [ "$ph" != "$expected" ]; then
            check_fail "sign/audit-log" "hash chain broken at line $lineno"
            return
        fi
        expected="$th"
    done < "$log"

    check_pass "sign/audit-log" "$lineno events, chain unbroken"
}

run_signing_checks() {
    check_signing_master_key
    check_signing_manifest_signature
    check_signing_audit_log
}
