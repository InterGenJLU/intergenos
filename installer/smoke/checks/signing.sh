#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
# The DER form is derived from SMOKE_MOK_CERT at the point of use rather than
# read from a second path: the enrollment test needs DER, and deriving it from
# the very certificate the check is about removes any chance of testing a stale
# sibling file. (A SMOKE_MOK_DER override variable was declared here and never
# read by anything — an override that did nothing — so it is gone rather than
# left standing as an affordance the file does not honour.)
SMOKE_MOK_CERT="${SMOKE_MOK_CERT:-/var/lib/intergen/mok/mok.crt}"

# Standard EFI binary paths under the system partition. The shim-signed
# package and GRUB install hooks stage binaries here at install time.
SMOKE_SHIM_EFI="${SMOKE_SHIM_EFI:-/boot/efi/EFI/InterGenOS/shimx64.efi}"
SMOKE_GRUB_EFI="${SMOKE_GRUB_EFI:-/boot/efi/EFI/InterGenOS/grubx64.efi}"

# Secure-Boot state probe shared by the venue-aware checks (3.0-F47): several
# expectations differ BY DESIGN between an SB-on and an SB-off install, so
# those checks must know which venue they are reading before ruling FAIL.
# Echoes exactly one of: enabled | disabled | noefi | unknown.
_smoke_sb_state() {
    if [ ! -d /sys/firmware/efi ]; then
        echo noefi
        return
    fi
    local out
    out="$(mokutil --sb-state 2>&1 || true)"
    case "$out" in
        *"SecureBoot enabled"*)  echo enabled ;;
        *"SecureBoot disabled"*) echo disabled ;;
        *) echo unknown ;;
    esac
}

check_signing_master_key() {
    if ! command -v gpg >/dev/null 2>&1; then
        check_fail "sign/master" "gpg not in PATH"
        return
    fi
    if ! gpg --list-keys "$SMOKE_MASTER_FPR" >/dev/null 2>&1; then
        # BY DESIGN, not a failure: the master is air-gapped and is NOT shipped
        # in any persistent keyring. release-keys.json pins the SUBKEYS, and
        # install-integrity verifies the manifest against the master via an
        # EPHEMERAL keyring (RELEASE_MASTER_FPR) — see the check_integrity_*
        # matrix below, which is the real master-trust assertion. This stale
        # "is the master in the keyring" check therefore SKIPs rather than
        # fails the smoke run.
        check_skip "sign/master" "master fingerprint not in a persistent keyring (by design — master is air-gapped; subkeys pinned, master verified via ephemeral keyring in the install-integrity checks)"
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

    # Verify with gpgv (keyboxd-immune) and REQUIRE the signature to chain to
    # the PINNED master fingerprint — not merely a good exit code, which would
    # accept any key shipped alongside the media. `gpg --dearmor` is a pure
    # transform (no keyring/keyboxd machinery), so it builds the binary keyring
    # gpgv reads without the gpg 2.5.x `--keyring`-ignored-under-keyboxd hazard.
    local kr rc status
    kr="$(mktemp)"
    if ! gpg --batch --yes --dearmor -o "$kr" "$key" >/dev/null 2>&1; then
        rm -f "$kr"
        check_fail "sign/manifest" "release key $key failed to dearmor"
        return
    fi
    status="$(gpgv --keyring "$kr" --status-fd=1 "$sig" "$manifest" 2>/dev/null)"
    rc=$?
    rm -f "$kr"
    if [ "$rc" -ne 0 ] || ! printf '%s\n' "$status" | grep -qiE "^\[GNUPG:\] VALIDSIG .* ${SMOKE_MASTER_FPR}\$"; then
        check_fail "sign/manifest" "signature does NOT validate to the pinned master $SMOKE_MASTER_FPR"
        return
    fi
    check_pass "sign/manifest" "signature validates to pinned master $SMOKE_MASTER_FPR (gpgv VALIDSIG)"
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

    # Fingerprints of the local cert, both digests.
    #
    # SHA-1 is here because that is the ONLY digest `mokutil --list-new` prints
    # (measured on mokutil 0.7.2: one `SHA1 Fingerprint:` line per key and no
    # SHA-256 line anywhere in the output). It is used to READ mokutil's own
    # listing, never as a trust decision — the enrollment decision below is a
    # whole-certificate comparison, not a digest match.
    local local_fpr256 local_fpr1
    local_fpr256="$(openssl x509 -in "$SMOKE_MOK_CERT" -noout -fingerprint -sha256 2>/dev/null \
        | sed -E 's/^[^=]*=//' | tr -d ':' | tr 'A-Z' 'a-z')"
    local_fpr1="$(openssl x509 -in "$SMOKE_MOK_CERT" -noout -fingerprint -sha1 2>/dev/null \
        | sed -E 's/^[^=]*=//' | tr -d ':' | tr 'A-Z' 'a-z')"
    if [ -z "$local_fpr256" ]; then
        check_fail "sign/mok-enrolled" "cannot read fingerprint from $SMOKE_MOK_CERT"
        return
    fi

    # Ask mokutil directly whether this certificate is enrolled.
    #
    # The previous implementation computed the local cert's SHA-256 and searched
    # for it in `mokutil --list-enrolled` output. mokutil prints only a SHA-1
    # fingerprint per key, so that search could never match and the check FAILed
    # on every Secure-Boot-ON system whose MOK was correctly enrolled (measured
    # on an installed system: the local cert is byte-identical to enrolled key 4,
    # the installed UKI verifies against it, and `mokutil --test-key` reports it
    # already enrolled — while the check reported FAIL).
    #
    # `--test-key` is the purpose-built answer and compares the whole DER, not a
    # digest of it. It takes DER only (a PEM gives "Not a valid x509
    # certificate"), so convert first. Where --test-key is unavailable, fall back
    # to exporting the enrolled keys and comparing certificate fingerprints
    # ourselves rather than scraping the listing's text.
    local mok_dir mok_der
    mok_dir="$(mktemp -d 2>/dev/null)" || {
        check_skip "sign/mok-enrolled" "mktemp failed (cannot stage the DER form for the enrollment test)"
        return
    }
    mok_der="$mok_dir/mok.der"
    if ! openssl x509 -in "$SMOKE_MOK_CERT" -outform DER -out "$mok_der" 2>/dev/null; then
        rm -rf "$mok_dir"
        check_fail "sign/mok-enrolled" "cannot convert $SMOKE_MOK_CERT to DER for the enrollment test"
        return
    fi

    # Read the ANSWER from --test-key's message, not from its exit status:
    # measured on mokutil 0.7.2, "<file> is already enrolled" comes back with
    # rc=1. Anyone simplifying this to an exit-code test would invert the
    # verdict.
    local enrolled=unknown probe_out probe_rc
    probe_out="$(mokutil --test-key "$mok_der" 2>&1)"; probe_rc=$?
    case "$probe_out" in
        *"is already enrolled"*)  enrolled=yes ;;
        *"is not enrolled"*)      enrolled=no ;;
        *"Permission denied"*|*"permission denied"*|*"EACCES"*)
            rm -rf "$mok_dir"
            check_warn "sign/mok-enrolled" "mokutil needs root to read the enrollment state; re-run as root for full validation"
            return
            ;;
    esac

    if [ "$enrolled" = "unknown" ]; then
        # Fallback: export the enrolled certificates and compare fingerprints of
        # the certificates themselves.
        local exp_dir f fpr
        exp_dir="$mok_dir/exported"
        mkdir -p "$exp_dir"
        if ( cd "$exp_dir" && mokutil --export >/dev/null 2>&1 ); then
            for f in "$exp_dir"/*.der; do
                [ -f "$f" ] || continue
                fpr="$(openssl x509 -inform DER -in "$f" -noout -fingerprint -sha256 2>/dev/null \
                    | sed -E 's/^[^=]*=//' | tr -d ':' | tr 'A-Z' 'a-z')"
                if [ -n "$fpr" ] && [ "$fpr" = "$local_fpr256" ]; then
                    enrolled=yes
                    break
                fi
            done
            [ "$enrolled" = "yes" ] || enrolled=no
        fi
    fi
    rm -rf "$mok_dir"

    if [ "$enrolled" = "yes" ]; then
        check_pass "sign/mok-enrolled" "local MOK certificate is enrolled (sha256 ${local_fpr256:0:16}…)"
        return
    fi
    if [ "$enrolled" = "unknown" ]; then
        check_warn "sign/mok-enrolled" "could not determine enrollment state: mokutil --test-key said \"$(echo "$probe_out" | head -1)\" (rc=$probe_rc) and --export produced nothing to compare"
        return
    fi

    # 3.0-F47(1): on an SB-off install, MOK enrollment is SKIPPED BY DESIGN —
    # MokManager only fires at the SB re-enable trigger (docs/mok-enrollment.md;
    # the installer trace logs the skip and its consequence). A not-yet-enrolled
    # MOK there is the sanctioned state, not a defect; the unconditional FAIL
    # here was a retired expectation that read as noise on every SB-off install.
    case "$(_smoke_sb_state)" in
        disabled)
            # --list-new prints a SHA-1 fingerprint per staged key and nothing
            # else that identifies it, so this note reads the SHA-1. It only
            # annotates an already-decided SKIP; it decides nothing itself.
            local pending=""
            if [ -n "$local_fpr1" ] && mokutil --list-new 2>/dev/null | tr -d ':' | tr 'A-Z' 'a-z' | grep -qF "$local_fpr1"; then
                pending=" (enrollment request already staged — MokManager fires on the SB-on reboot)"
            fi
            check_skip "sign/mok-enrolled" "Secure Boot disabled — MOK enrollment pends the SB re-enable trigger by design$pending"
            ;;
        enabled)
            check_fail "sign/mok-enrolled" "Secure Boot ENABLED but local MOK cert at $SMOKE_MOK_CERT is NOT in the enrolled list"
            ;;
        *)
            check_warn "sign/mok-enrolled" "MOK not in the enrolled list and Secure Boot state undeterminable — re-run as root and check mokutil --sb-state"
            ;;
    esac
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
        # The sysctl is ABSENT when enforcement is compiled in unconditionally
        # (CONFIG_MODULE_SIG_FORCE=y) — the STRONGEST posture, not a missing
        # one. Distinguish "compiled-in enforcement" from "no module signing"
        # by reading the kernel config rather than emitting a misleading skip.
        local kconfig=""
        if [ -r /proc/config.gz ]; then
            kconfig="$(zcat /proc/config.gz 2>/dev/null)"
        elif [ -r "/boot/config-$(uname -r)" ]; then
            kconfig="$(cat "/boot/config-$(uname -r)" 2>/dev/null)"
        fi
        if printf '%s\n' "$kconfig" | grep -qx "CONFIG_MODULE_SIG_FORCE=y"; then
            check_pass "sign/module-sig-force" "module_sig_enforce sysctl absent BY DESIGN — CONFIG_MODULE_SIG_FORCE=y compiles enforcement in unconditionally (strongest posture)"
        elif [ -n "$kconfig" ]; then
            check_fail "sign/module-sig-force" "$enforce unreadable AND CONFIG_MODULE_SIG_FORCE not =y — module signing is not enforced"
        else
            check_skip "sign/module-sig-force" "$enforce unreadable and kernel config unavailable (/proc/config.gz + /boot/config-* absent) — cannot determine enforcement"
        fi
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

# ===========================================================================
# Install-integrity acceptance matrix (M1-M7) — turns "the integrity gate runs
# and fails closed" into checked artifacts (install-integrity acceptance-harness
# spec §M1-M7). Per that spec's layering: M1/M4/M5/M6 assert real-install
# runtime artifacts and SKIP gracefully when absent (they run for real on a
# from-scratch ISO install); M3 + the build-time layer run offline against a
# synthesized fixture; M2 is validated by the Python verify+ack flow + a real
# negative install (honest skip-with-pointer here — faking a pass would mask a
# regression). The single most important assertion is M1: a real install whose
# audit log lacks verify_started proves the gate went dark — the exact
# re-dormancy this effort closes.
# ===========================================================================

_ii_audit_log="/var/log/igos-integrity-override.log"

_ii_latest_forge_trace() {
    ls -1t /var/log/forge-install-*.log 2>/dev/null | head -1
}

# The forge install trace is written 0600 root:root, so a non-root smoke run
# can SEE the file and not READ it. A check that greps it regardless prints a
# raw "Permission denied" into the report and then draws its verdict from a
# read that never happened. Checks that need the trace CONTENT gate on this
# and say plainly that root is required; the one check that only needs to know
# an install happened keeps testing the path itself, so an unreadable trace
# can never soften a fail-closed branch into a skip.
_ii_forge_trace_readable() {
    [ -n "${1:-}" ] && [ -r "${1:-}" ]
}

# M1 — the gate actually ran + the walk was complete.
check_integrity_gate_ran() {
    if [ ! -f "$_ii_audit_log" ]; then
        # No audit log => verify did not run. Distinguish a sanctioned dev/
        # dry-run skip from a DARKENED RELEASE install (the re-dormancy this
        # guards against). On a booted RELEASE live root the /install/ trust
        # triplet is present and there is NO dev-allow marker; a missing audit
        # log there means the gate went dark -> FAIL. (On the installed target
        # /install/ is absent and verify-ran evidence is the audit log itself,
        # so absence there legitimately SKIPs.)
        local _rel_manifest="/install/intergenos-archive-manifest.txt"
        local _dev_marker="/install/IGOS_DEV_ALLOW_UNVERIFIED"
        if [ -f "$_rel_manifest" ] && [ ! -f "$_dev_marker" ]; then
            # Release media, no audit log. Only a DARKENED install (verify never
            # ran) is a FAIL — and verify only writes the audit log DURING an
            # install. So gate the FAIL on an install having actually run (a
            # forge-install trace exists); a pristine release live boot with no
            # install yet legitimately has no log -> SKIP (order-independent; no
            # false-FAIL when the smoke runs before install).
            if [ -n "$(_ii_latest_forge_trace)" ]; then
                check_fail "integrity/gate-ran" "RELEASE media + an install ran (forge-install trace present) but NO integrity audit log — verify never ran (gate dark on release media)"
            else
                check_skip "integrity/gate-ran" "RELEASE media but no install has run yet (no forge-install trace) — verify writes the audit log during install; nothing to assert pre-install"
            fi
            return
        fi
        check_skip "integrity/gate-ran" "no integrity audit log + not release media (dev/unsigned-test marker, or no /install/ triplet) — verify legitimately did not run"
        return
    fi
    if ! grep -q '"verify_started"' "$_ii_audit_log"; then
        check_fail "integrity/gate-ran" "audit log present but NO verify_started event — the gate went dark (re-dormancy)"
        return
    fi
    if grep -q '"abort"' "$_ii_audit_log"; then
        check_pass "integrity/gate-ran" "verify ran and fail-closed (abort recorded) — gate active"
        return
    fi
    if ! grep -q '"verify_completed"' "$_ii_audit_log"; then
        check_fail "integrity/gate-ran" "verify_started present but no terminal verify_completed/abort event"
        return
    fi
    local mec ac
    mec="$(grep '"verify_started"' "$_ii_audit_log" | tail -1 | python3 -c "import sys,json;print(json.loads(sys.stdin.read()).get('manifest_entry_count',''))" 2>/dev/null)"
    ac="$(grep '"verify_completed"' "$_ii_audit_log" | tail -1 | python3 -c "import sys,json;print(json.loads(sys.stdin.read()).get('archives_checked',''))" 2>/dev/null)"
    if [ -n "$mec" ] && [ -n "$ac" ] && [ "$mec" = "$ac" ]; then
        check_pass "integrity/gate-ran" "verify ran: archives_checked=$ac == manifest_entry_count=$mec (full corpus)"
    elif [ -n "$mec" ] && [ -n "$ac" ] && [ "$ac" -lt "$mec" ] 2>/dev/null; then
        # 3.0-F47(2): post-F41 the signed manifest covers the FULL archive
        # corpus while install media carries only the iso_include:true subset,
        # so checked < manifest is the correct release shape. The assertion is
        # present-subset-of-manifest + sha-ok, not count equality (the retired
        # full-corpus expectation): a sha mismatch or aborted walk is already
        # caught by the abort/verify_completed branches above.
        check_pass "integrity/gate-ran" "verify ran: archives_checked=$ac of manifest_entry_count=$mec (post-F41 iso_include subset — every present archive verified)"
    elif [ -n "$mec" ] && [ -n "$ac" ]; then
        check_fail "integrity/gate-ran" "archives_checked=$ac EXCEEDS manifest_entry_count=$mec — an archive outside the signed manifest was walked"
    else
        check_pass "integrity/gate-ran" "verify_started + verify_completed present (counts unparsed)"
    fi
}

# M2 — tampered archive => typed-phrase ack => decline aborts (no disk write).
check_integrity_tamper_aborts() {
    # Faithfully exercising this needs the Python verify_archives flow driven
    # with a declining ack_callback against a tampered fixture — not assertable
    # from a clean post-install smoke run. Validated by the verify_archives
    # unit path + a real negative install on the from-scratch seal.
    check_skip "integrity/tamper-aborts" "negative scenario (tampered archive -> declined ack -> abort) — validated by the verify_archives flow + a real negative install on the seal, not a clean post-install"
}

# M3 — attacker-signed manifest => signature fails (offline gpg fixture).
check_integrity_badsig_rejected() {
    if ! command -v gpg >/dev/null 2>&1; then
        check_skip "integrity/badsig" "gpg not in PATH (offline fixture needs gpg)"
        return
    fi
    local d; d="$(mktemp -d)" || { check_skip "integrity/badsig" "mktemp failed"; return; }
    trap '[ -n "${d:-}" ] && rm -rf "$d"' RETURN
    local home="$d/home"; mkdir -p "$home"; chmod 700 "$home"
    # Two independent keys: an "attacker" key signs the manifest; a "release"
    # key is the trust root we verify against. A foreign signature MUST fail.
    local kp
    for kp in attacker release; do
        cat > "$d/$kp.params" <<EOF
%no-protection
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: igos-$kp
Expire-Date: 0
%commit
EOF
        if ! GNUPGHOME="$home" gpg --batch --gen-key "$d/$kp.params" >/dev/null 2>&1; then
            check_skip "integrity/badsig" "ephemeral key generation unavailable (no entropy / gpg batch keygen failed)"
            return
        fi
    done
    printf '# manifest\nSHA256 (core/x.igos.tar.gz) = %064d\n# End of manifest.\n' 0 > "$d/manifest.txt"
    GNUPGHOME="$home" gpg --batch --yes --pinentry-mode loopback -u "igos-attacker" \
        --detach-sign --armor --output "$d/manifest.txt.sig" "$d/manifest.txt" >/dev/null 2>&1
    # Export ONLY the release pubkey as the trust root.
    GNUPGHOME="$home" gpg --batch --yes --armor --export "igos-release" > "$d/release-key.asc" 2>/dev/null
    # Verify the attacker-signed manifest against the release key with gpgv
    # (same shape as verify_manifest_signature) — MUST fail. The attacker signed
    # with a DIFFERENT key than the release trust root, so gpgv emits NO VALIDSIG
    # → correctly rejected. (This fixture's release key is a throwaway, not the
    # real master, so we assert rejection rather than the production fpr pin.)
    gpg --batch --yes --dearmor -o "$d/r.gpg" "$d/release-key.asc" >/dev/null 2>&1
    if gpgv --keyring "$d/r.gpg" --status-fd=1 "$d/manifest.txt.sig" "$d/manifest.txt" 2>/dev/null \
         | grep -qE "^\[GNUPG:\] VALIDSIG "; then
        check_fail "integrity/badsig" "attacker-signed manifest VERIFIED against the release key — signature gate is broken"
    else
        check_pass "integrity/badsig" "attacker-signed manifest correctly REJECTED by the release-key trust root"
    fi
}

# M4 — absent trust set on release media => build-side fail-closed (offline).
check_integrity_absent_failclosed() {
    local gate="scripts/check-install-integrity-staging.sh"
    if [ ! -f "$gate" ]; then
        check_skip "integrity/absent-failclosed" "$gate not reachable from CWD (run from repo root for the offline build-layer assertion)"
        return
    fi
    local d; d="$(mktemp -d)" || { check_skip "integrity/absent-failclosed" "mktemp failed"; return; }
    trap '[ -n "${d:-}" ] && rm -rf "$d"' RETURN
    mkdir -p "$d/install"  # present dir, NO triplet
    if bash "$gate" --install-dir "$d/install" >/dev/null 2>&1; then
        check_fail "integrity/absent-failclosed" "staging gate PASSED with an absent trust triplet — build-side fail-closed broken (red-team R1/M4)"
    else
        check_pass "integrity/absent-failclosed" "staging gate refuses to seal an absent trust triplet (build-side fail-closed)"
    fi
}

# M5 — the trust triplet is verity-sealed read-only (R5 closed by construction).
check_integrity_key_immutable() {
    if ! grep -q 'igos.verity.roothash=' /proc/cmdline 2>/dev/null; then
        check_skip "integrity/key-immutable" "not booted via the dm-verity roothash chain (live-ISO verity not active here)"
        return
    fi
    check_pass "integrity/key-immutable" "booted with igos.verity.roothash active — /install/ trust triplet is verity-sealed read-only (R5: a key swap breaks dm-verity)"
}

# M6 — verify is skipped ONLY explicitly (dev marker / --dry-run), never silently.
check_integrity_dev_skip_explicit() {
    local trace; trace="$(_ii_latest_forge_trace)"
    if [ -z "$trace" ]; then
        check_skip "integrity/dev-skip" "no forge-install trace present (not a real install)"
        return
    fi
    if ! _ii_forge_trace_readable "$trace"; then
        check_warn "integrity/dev-skip" "forge-install trace $trace is present but not readable by this user (0600 root) — re-run as root to assert whether verify was explicitly skipped"
        return
    fi
    # The §4C gating skips verify ONLY for an explicit IGOS_DEV_ALLOW_UNVERIFIED
    # marker or --dry-run; absent-no-marker now hard-aborts (install never
    # completes). So a completed install whose trace shows a verify skip is a
    # SANCTIONED skip by construction — never the silent absence-skip (R1).
    if grep -qE 'verify phase skipped' "$trace"; then
        check_pass "integrity/dev-skip" "verify explicitly skipped + logged (dev/unsigned-test or --dry-run) — sanctioned, not silent"
    elif grep -qE 'verifying archive integrity' "$trace"; then
        check_skip "integrity/dev-skip" "verify ran on this install (not a dev-skip scenario) — see integrity/gate-ran"
    else
        check_skip "integrity/dev-skip" "no verify-phase marker in the trace"
    fi
}

# :460 — the installed target can self-revalidate (complete triplet) OR the
# install logged the incomplete copy loudly (never a silent skip).
check_signing_manifest_self_reval() {
    local manifest_dir="/var/lib/igos/manifest"
    local trace; trace="$(_ii_latest_forge_trace)"
    local have_all=1 f
    for f in intergenos-archive-manifest.txt intergenos-archive-manifest.txt.sig intergenos-release-key.asc; do
        [ -f "$manifest_dir/$f" ] || have_all=0
    done
    if [ "$have_all" = "1" ]; then
        check_pass "integrity/self-reval" "complete trust triplet preserved at $manifest_dir (post-install self-revalidation possible)"
        return
    fi
    # A copied triplet is only EXPECTED when verify actually ran (a release
    # install with a verify_config copies it from PHASE_CLEANUP). The audit
    # log's verify_started is that precondition. Without it — dev/dry-run
    # install, or a build/dev box with stale artifacts — no triplet copy is
    # expected, so SKIP rather than false-FAIL.
    if [ ! -f "$_ii_audit_log" ] || ! grep -q '"verify_started"' "$_ii_audit_log"; then
        check_skip "integrity/self-reval" "verify did not run on this system (dev/dry-run or not a real release install) — no triplet copy expected"
        return
    fi
    if [ -n "$trace" ] && ! _ii_forge_trace_readable "$trace"; then
        check_warn "integrity/self-reval" "incomplete triplet at $manifest_dir and the forge-install trace $trace is not readable by this user (0600 root) — re-run as root to tell a logged incomplete copy from a silent one"
    elif [ -n "$trace" ] && grep -qiE 'incomplete trust set|signed manifest not copied' "$trace"; then
        check_warn "integrity/self-reval" "incomplete triplet at $manifest_dir but the install logged it loudly (:460) — visible, not silent"
    else
        check_fail "integrity/self-reval" "incomplete/absent triplet at $manifest_dir with no loud copy-warning in the trace (silent skip — :460 regression)"
    fi
}

# M7 — the single guided ceremony covered BOTH mechanisms (manifest GPG sig AND
# UKI PE sigs). Assert the OUTCOME, not the UX: a release artifact carries both.
# Venue semantics (3.0-F47(3)): the UKI SIGNER differs by venue BY DESIGN —
# ISO media carries release-PIV-signed igos-*.efi UKIs, while an INSTALLED
# system carries a user-MOK-signed /boot/efi/EFI/Linux/intergenos-<kver>.efi
# (linux-kernel post-install hook; the release PIV key NEVER touches user
# systems). The old igos-*-only glob could not match an installed system at
# all, so this check failed on every install — the retired expectation.
check_integrity_dual_ceremony() {
    local manifest_dir="/var/lib/igos/manifest"
    local manifest="$manifest_dir/intergenos-archive-manifest.txt"
    # Mechanism 1: a verifiable manifest GPG signature is the job of
    # check_signing_manifest_signature (above). Here assert the SECOND
    # mechanism is present alongside it, so a half-completed ceremony (one but
    # not the other) is caught.
    if [ ! -d /sys/firmware/efi ]; then
        check_skip "integrity/dual-ceremony" "not booted via EFI (BIOS install — no UKI half to assert)"
        return
    fi
    if [ ! -f "$manifest" ]; then
        check_skip "integrity/dual-ceremony" "no manifest at $manifest_dir (not a real signed install — M7 confirmed on the seal)"
        return
    fi
    if ! command -v sbverify >/dev/null 2>&1; then
        check_skip "integrity/dual-ceremony" "manifest present but sbverify not in PATH — cannot confirm the UKI-PE half of the ceremony"
        return
    fi
    # The ESP mounts fmask/dmask 0077, so a non-root run cannot tell "no UKI"
    # from "cannot read the ESP" — WARN rather than emit a false half-ceremony
    # FAIL (same needs-root venue as the MOK/keyring checks above).
    if ! ls /boot/efi >/dev/null 2>&1; then
        check_warn "integrity/dual-ceremony" "cannot read the ESP (0077 fmask mount) — re-run as root to assert the UKI half"
        return
    fi
    # Any UKI with a PE signature proves the second mechanism: release-PIV
    # igos-*.efi on media, user-MOK intergenos-<kver>.efi on installed targets.
    # shimx64/grubx64 are checked by check_signing_chain_root; here we want a
    # UKI with a PE signature.
    local uki found_signed_uki=0 any_uki=0 u
    for uki in /boot/efi/EFI/InterGenOS/igos-*.efi /boot/efi/EFI/Linux/igos-*.efi \
               /boot/efi/EFI/Linux/intergenos-*.efi; do
        [ -f "$uki" ] || continue
        any_uki=1
        if sbverify --list "$uki" 2>/dev/null | grep -qiE 'signature|certificate|CN='; then
            found_signed_uki=1; u="$uki"; break
        fi
    done
    if [ "$found_signed_uki" = "1" ]; then
        case "$u" in
            */EFI/Linux/intergenos-*)
                check_pass "integrity/dual-ceremony" "both ceremony mechanisms present: manifest signature + user-MOK-signed installed UKI ($(basename "$u"))"
                ;;
            *)
                check_pass "integrity/dual-ceremony" "both ceremony mechanisms present: manifest signature + release-signed UKI ($(basename "$u"))"
                ;;
        esac
        return
    fi
    if [ "$any_uki" = "1" ] && [ "$(_smoke_sb_state)" = "disabled" ] && [ ! -f "$SMOKE_MOK_CERT" ]; then
        # The kernel hook builds the UKI UNSIGNED when no MOK keypair exists,
        # and logs it — the sanctioned SB-off degrade, not a half ceremony.
        check_skip "integrity/dual-ceremony" "UKI present but unsigned — sanctioned SB-off degrade (no MOK keypair; the kernel hook logs it)"
        return
    fi
    if [ "$any_uki" = "0" ]; then
        check_fail "integrity/dual-ceremony" "manifest signed but NO UKI found on the ESP (igos-*.efi media UKIs + EFI/Linux/intergenos-*.efi installed UKIs both absent)"
    else
        check_fail "integrity/dual-ceremony" "manifest signed but no UKI carries a PE signature — half-completed ceremony (manifest GPG without UKI PE signatures)"
    fi
}

run_signing_checks() {
    check_signing_master_key
    check_signing_manifest_signature
    check_signing_audit_log
    check_signing_mok_enrolled
    check_signing_secondary_keyring
    check_signing_module_sig_force
    check_signing_chain_root
    # Install-integrity acceptance matrix (M1-M7 + :460).
    check_integrity_gate_ran
    check_integrity_tamper_aborts
    check_integrity_badsig_rejected
    check_integrity_absent_failclosed
    check_integrity_key_immutable
    check_integrity_dev_skip_explicit
    check_signing_manifest_self_reval
    check_integrity_dual_ceremony
}
