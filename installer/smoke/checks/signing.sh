#!/usr/bin/env bash
# installer/smoke/checks/signing.sh — Category 2: signing-chain validation.
#
# Confirms the master signing key is in the system keyring, the embedded
# archive manifest signature still validates, and the install-time audit
# log is intact. Each function emits exactly one check_* result.

# Master signing-key fingerprint (durable v1.0; per memory reference_v1_signing_keys).
SMOKE_MASTER_FPR="${SMOKE_MASTER_FPR:-5597A3E0587B253006D0DD7B8C50826182083050}"

# MOK (Machine Owner Key) defaults. Forge installs the user's MOK cert and
# key under /var/lib/intergen/mok/ — see installer/backend/mok.py and the
# end-user procedure at docs/mok-enrollment.md.
SMOKE_MOK_CERT="${SMOKE_MOK_CERT:-/var/lib/intergen/mok/mok.crt}"
SMOKE_MOK_DER="${SMOKE_MOK_DER:-/var/lib/intergen/mok/mok.der}"

# Standard EFI binary paths under the system partition. The shim-signed
# package and GRUB install hooks stage binaries here at install time.
SMOKE_SHIM_EFI="${SMOKE_SHIM_EFI:-/boot/efi/EFI/intergenos/shimx64.efi}"
SMOKE_GRUB_EFI="${SMOKE_GRUB_EFI:-/boot/efi/EFI/intergenos/grubx64.efi}"

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
    # The hash-chained JSONL audit log produced by integrity.append_event()
    # and copied onto the installed target by integrity.copy_audit_log_to_target().
    # Path matches backend/integrity.py + frontend/{tui.py,gui/screens/progress.py}
    # INTEGRITY_AUDIT_LOG constants (single source of truth).
    local log="/var/log/igos-integrity-override.log"

    if [ ! -f "$log" ]; then
        check_skip "sign/audit-log" "$log not present (manifest verification didn't run on this install)"
        return
    fi

    # Each line is a JSON event with prev + entry_sha256 + event payload.
    # Walk the chain: every line N's prev must equal line (N-1)'s entry_sha256.
    # Genesis line's prev is the "GENESIS" sentinel (per integrity.py:_last_chain_hash).
    #
    # C-008: integrity.py is the source-of-truth for field names ("prev" +
    # "entry_sha256" at integrity.py:227-235 + :269-276). Earlier this check
    # read "prev_hash" + "this_hash" which never matched any audit-log entry,
    # so every smoke run with a real audit log hit "missing this_hash" → fail
    # — silently masking any actual chain break. Renaming integrity.py fields
    # instead would break prior installs' audit logs; check side updated to
    # match the on-disk schema.
    local lineno=0 prev="" expected=""
    while IFS= read -r line; do
        lineno=$((lineno+1))
        local ph th
        ph="$(printf '%s' "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('prev',''))" 2>/dev/null)"
        th="$(printf '%s' "$line" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('entry_sha256',''))" 2>/dev/null)"
        if [ -z "$th" ]; then
            check_fail "sign/audit-log" "line $lineno missing entry_sha256"
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

# ---------------------------------------------------------------------------
# Boot-chain + MOK checks. Validate that the shim → grub → kernel → modules
# chain is intact at runtime and the user's MOK is properly enrolled into
# the kernel's secondary trusted keyring. The end-user procedure these
# checks validate is documented in docs/mok-enrollment.md.
# ---------------------------------------------------------------------------

check_signing_mok_enrolled() {
    if ! command -v mokutil >/dev/null 2>&1; then
        check_skip "sign/mok-enrolled" "mokutil not in PATH"
        return
    fi

    # No point checking MOK enrollment if there's no local cert to enroll.
    if [ ! -f "$SMOKE_MOK_CERT" ]; then
        check_skip "sign/mok-enrolled" "$SMOKE_MOK_CERT not present (no Forge-provisioned MOK)"
        return
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        check_skip "sign/mok-enrolled" "openssl not in PATH (cannot compute cert fingerprint)"
        return
    fi

    # Compute SHA-256 fingerprint of the local MOK cert. Strip the
    # "SHA256 Fingerprint=" prefix and colons so we can substring-match
    # against mokutil's enrolled-keys output (mokutil prints colon-separated
    # hex; we normalize both sides).
    local local_fpr
    local_fpr="$(openssl x509 -in "$SMOKE_MOK_CERT" -noout -fingerprint -sha256 2>/dev/null \
        | sed -E 's/^[^=]*=//' | tr -d ':' | tr 'A-Z' 'a-z')"
    if [ -z "$local_fpr" ]; then
        check_fail "sign/mok-enrolled" "cannot read fingerprint from $SMOKE_MOK_CERT"
        return
    fi

    # mokutil --list-enrolled emits the SHA1 + SHA256 fingerprints per cert.
    # Some mokutil versions need root for the EFI variable read; gracefully
    # WARN rather than FAIL on permission denial.
    local enrolled_out
    enrolled_out="$(mokutil --list-enrolled 2>&1)"
    local rc=$?
    if [ $rc -ne 0 ]; then
        case "$enrolled_out" in
            *"Permission denied"*|*"permission denied"*|*"EACCES"*)
                check_warn "sign/mok-enrolled" "mokutil --list-enrolled needs root; re-run as root for full validation"
                return
                ;;
            *)
                check_fail "sign/mok-enrolled" "mokutil --list-enrolled failed: $(echo "$enrolled_out" | head -1)"
                return
                ;;
        esac
    fi

    # Normalize the enrolled output's fingerprints the same way (strip colons,
    # lowercase) and search for our local fingerprint.
    local normalized
    normalized="$(echo "$enrolled_out" | tr -d ':' | tr 'A-Z' 'a-z')"
    if echo "$normalized" | grep -qF "$local_fpr"; then
        check_pass "sign/mok-enrolled" "local MOK fingerprint matches enrolled key"
    else
        check_fail "sign/mok-enrolled" "local MOK cert at $SMOKE_MOK_CERT is NOT in the enrolled list"
    fi
}

check_signing_secondary_keyring() {
    if ! command -v keyctl >/dev/null 2>&1; then
        check_skip "sign/secondary-keyring" "keyctl not in PATH (install keyutils for module-signing validation)"
        return
    fi

    # Reading keyrings requires the calling process to either own them or
    # have CAP_SYS_ADMIN. As a regular user we usually get EPERM. Also: if
    # the running kernel was built without CONFIG_SECONDARY_TRUSTED_KEYRING=y
    # the keyring won't exist at all — keyctl emits "Can't find …" in that
    # case. Both are non-FAIL: WARN with a precise message.
    local out
    out="$(keyctl list %:.secondary_trusted_keys 2>&1)"
    local rc=$?
    if [ $rc -ne 0 ]; then
        case "$out" in
            *"Permission denied"*|*"permission denied"*|*"EACCES"*)
                check_warn "sign/secondary-keyring" "needs root to read .secondary_trusted_keys; re-run as root"
                return
                ;;
            *"Required key not available"*|*"Operation not permitted"*)
                check_warn "sign/secondary-keyring" "keyring access denied — likely needs root"
                return
                ;;
            *"Can't find"*|*"can't find"*|*"No such key"*|*"Requested key not available"*)
                check_warn "sign/secondary-keyring" "no .secondary_trusted_keys keyring (kernel built without CONFIG_SECONDARY_TRUSTED_KEYRING=y)"
                return
                ;;
            *)
                check_fail "sign/secondary-keyring" "keyctl list failed: $(echo "$out" | head -1)"
                return
                ;;
        esac
    fi

    # Output looks like:
    #     0 keys in keyring        (empty)
    # or:
    #     2 keys in keyring:
    #     <hex>: asymmetric: InterGenOS Machine Owner Key
    if echo "$out" | head -1 | grep -qE "^0 keys"; then
        check_warn "sign/secondary-keyring" "secondary keyring is empty (no MOK in kernel trust chain yet)"
        return
    fi

    local count
    count="$(echo "$out" | head -1 | sed -E 's/^([0-9]+).*/\1/')"
    if [ -n "$count" ] && [ "$count" -gt 0 ] 2>/dev/null; then
        check_pass "sign/secondary-keyring" "$count key(s) in .secondary_trusted_keys"
    else
        check_warn "sign/secondary-keyring" "unexpected keyctl output (head: $(echo "$out" | head -1))"
    fi
}

check_signing_module_sig_force() {
    local enforce="/proc/sys/kernel/module_sig_enforce"
    local lockdown="/sys/kernel/security/lockdown"

    if [ ! -r "$enforce" ]; then
        check_skip "sign/module-sig-force" "$enforce not readable (kernel built without CONFIG_MODULE_SIG?)"
        return
    fi

    local sig_state
    sig_state="$(cat "$enforce" 2>/dev/null)"
    if [ "$sig_state" != "1" ]; then
        check_fail "sign/module-sig-force" "module_sig_enforce=$sig_state (expected 1 for signed-only enforcement)"
        return
    fi

    # Lockdown is a softer signal — informational rather than load-bearing.
    # Either [integrity] or [confidentiality] indicates lockdown active.
    if [ -r "$lockdown" ]; then
        local lockdown_state
        lockdown_state="$(cat "$lockdown" 2>/dev/null)"
        case "$lockdown_state" in
            *"[integrity]"*|*"[confidentiality]"*)
                check_pass "sign/module-sig-force" "module_sig_enforce=1, lockdown=$lockdown_state"
                ;;
            *"[none]"*)
                check_warn "sign/module-sig-force" "module_sig_enforce=1 but lockdown=[none] (signed-only modules ok, but lockdown not active)"
                ;;
            *)
                check_warn "sign/module-sig-force" "module_sig_enforce=1, unrecognized lockdown=$lockdown_state"
                ;;
        esac
    else
        # No lockdown sysfs entry — pre-5.4 kernel or no CONFIG_SECURITY_LOCKDOWN.
        # Module-sig-enforce on its own is the load-bearing assertion.
        check_pass "sign/module-sig-force" "module_sig_enforce=1 (lockdown sysfs not present)"
    fi
}

check_signing_chain_root() {
    if [ ! -d /sys/firmware/efi ]; then
        check_skip "sign/chain-root" "not booted via EFI (BIOS install — signing chain not applicable)"
        return
    fi
    if ! command -v sbverify >/dev/null 2>&1; then
        check_skip "sign/chain-root" "sbverify not in PATH (install sbsigntool)"
        return
    fi

    local shim_present=0 grub_present=0
    [ -f "$SMOKE_SHIM_EFI" ] && shim_present=1
    [ -f "$SMOKE_GRUB_EFI" ] && grub_present=1

    if [ $shim_present -eq 0 ] && [ $grub_present -eq 0 ]; then
        check_skip "sign/chain-root" "neither $SMOKE_SHIM_EFI nor $SMOKE_GRUB_EFI present"
        return
    fi

    # sbverify --list reports the signers present on the binary without
    # requiring the trust-root cert on disk. The presence of an InterGenOS-
    # signed grubx64.efi and a Microsoft-signed shimx64.efi is the runtime
    # truth-claim we validate here.
    local shim_signer="" grub_signer=""
    if [ $shim_present -eq 1 ]; then
        shim_signer="$(sbverify --list "$SMOKE_SHIM_EFI" 2>/dev/null \
            | grep -E "image signature issuer|Microsoft|CN=" | head -3 | tr '\n' ' | ')"
        if [ -z "$shim_signer" ]; then
            check_warn "sign/chain-root" "$SMOKE_SHIM_EFI present but no signers reported by sbverify --list"
            return
        fi
    fi
    if [ $grub_present -eq 1 ]; then
        grub_signer="$(sbverify --list "$SMOKE_GRUB_EFI" 2>/dev/null \
            | grep -E "image signature issuer|InterGenOS|CN=" | head -3 | tr '\n' ' | ')"
        if [ -z "$grub_signer" ]; then
            check_fail "sign/chain-root" "$SMOKE_GRUB_EFI present but unsigned (chain broken)"
            return
        fi
    fi

    # Both signed (or only one binary present + signed) — pass.
    local msg=""
    [ $shim_present -eq 1 ] && msg="shim signed"
    [ $shim_present -eq 1 ] && [ $grub_present -eq 1 ] && msg="$msg + grub signed"
    [ $shim_present -eq 0 ] && [ $grub_present -eq 1 ] && msg="grub signed (shim path absent)"
    check_pass "sign/chain-root" "$msg"
}

run_signing_checks() {
    check_signing_master_key
    check_signing_manifest_signature
    check_signing_audit_log
    check_signing_mok_enrolled
    check_signing_secondary_keyring
    check_signing_module_sig_force
    check_signing_chain_root
}
