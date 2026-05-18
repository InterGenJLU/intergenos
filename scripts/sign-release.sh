#!/bin/bash
# sign-release.sh — sign an InterGenOS release
#
# Runs on the signing workstation (NOT the build VM) with the hardware
# token plugged in, PIN unlocked. Signs three classes of artifact:
#
#   1. pkm repo index       (GPG detached signature, --detach-sign --armor)
#   2. Kernel UKI binaries  (sbsign with PKCS#11 URI to PIV slot 9c)
#                           — UKI = vmlinuz + initramfs + cmdline bundled
#                             by systemd-stub; one signature covers all.
#                             We sign the UKI envelope, NOT bare vmlinuz.
#                             Per Q-INIT resolved 2026-05-05/06: April-10
#                             custom-init stands; UKI wrapping via
#                             systemd-stub + objcopy.
#   3. GRUB EFI binary      (sbsign with same PKCS#11 URI)
#
# Per D1-5 (split build/sign) and D1-4 (touch-to-sign on release subkey
# + PIV 9c). Module signing is NOT handled here — it runs inside the
# kernel build with an ephemeral per-build key that never touches the
# token.
#
# Invoked by the build orchestrator between the final package-build
# phase and the image-creation phase, or manually against a staged
# artifacts directory.
#
# Usage:
#   sign-release.sh --artifacts DIR --output DIR
#                   [--gpg-key-id FINGERPRINT]
#                   [--pkcs11-uri URI]
#                   [--strict]
#
# Arguments:
#   --artifacts DIR   Directory of unsigned release artifacts. Expected
#                     contents: InterGenOS.db (pkm index), *.uki.efi
#                     (Unified Kernel Images from build-uki.sh) plus
#                     optionally igos-live.efi, grubx64.efi. Missing
#                     files skip their sign step unless --strict is set.
#   --output DIR      Directory where signed artifacts + detached sigs
#                     are written. Created if missing.
#   --gpg-key-id      Fingerprint of the signing subkey on the token.
#                     Defaults to $INTERGENOS_GPG_KEY_ID env var.
#   --pkcs11-uri      PKCS#11 URI for the sbsign key on PIV slot 9c.
#                     Defaults to $INTERGENOS_PKCS11_URI env var.
#   --vendor-cert FILE X.509 certificate matching the PKCS#11 key.
#                     Defaults to /etc/intergenos/signing/vendor-cert.pem
#                     (pre-positioned on signing workstation, not transported
#                     with unsigned artifacts). (SR3)
#
# Exit codes:
#   0   all signing steps succeeded (or were skipped non-strict)
#   1   token not present or unlocked
#   2   required key material not configured
#   3   a sign step failed
#   4   missing artifact under --strict
#
# Environment defaults (may be overridden by flags):
#   INTERGENOS_GPG_KEY_ID   — GPG fingerprint of the release subkey
#   INTERGENOS_PKCS11_URI   — e.g. pkcs11:object=InterGenOS%20SB;type=private
#
# Pre-flight discipline (per signing_key_custody §7 step 7):
#   Treat every invocation as a signing ceremony. Close browsers + dev
#   tools + non-essential background processes before running. See
#   docs/signing-procedure.md for the full checklist.

set -euo pipefail

# -------- defaults --------
ARTIFACTS=""
OUTPUT=""
GPG_KEY_ID="${INTERGENOS_GPG_KEY_ID:-}"
GPG_MASTER_KEY_ID="${INTERGENOS_GPG_MASTER_KEY_ID:-}"
PKCS11_URI="${INTERGENOS_PKCS11_URI:-}"
VENDOR_CERT="${INTERGENOS_VENDOR_CERT:-/etc/intergenos/signing/vendor-cert.pem}"
# B-005 (T0-2): patched OpenSC 0.27.1 PKCS#11 module is required for
# RSA-4096 PIV support — stock OpenSC fails at first UKI sign. The
# patched module lives at /usr/local/lib/opensc-pkcs11.so on a properly
# provisioned signing workstation (see docs/signing-procedure.md).
PKCS11_MODULE="${INTERGENOS_PKCS11_MODULE:-/usr/local/lib/opensc-pkcs11.so}"
STRICT=0
MANIFEST_PATH=""

# -------- arg parsing --------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifacts)         ARTIFACTS="$2"; shift 2 ;;
        --output)            OUTPUT="$2"; shift 2 ;;
        --gpg-key-id)        GPG_KEY_ID="$2"; shift 2 ;;
        --gpg-master-key-id) GPG_MASTER_KEY_ID="$2"; shift 2 ;;
        --pkcs11-uri)        PKCS11_URI="$2"; shift 2 ;;
        --pkcs11-module)     PKCS11_MODULE="$2"; shift 2 ;;
        --vendor-cert)       VENDOR_CERT="$2"; shift 2 ;;
        --manifest)          MANIFEST_PATH="$2"; shift 2 ;;
        --strict)            STRICT=1; shift ;;
        -h|--help)
            sed -n '2,45p' "$0"
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

die() { echo "error: $*" >&2; exit "${2:-3}"; }

[[ -n "$ARTIFACTS" ]] || die "--artifacts required" 2
[[ -n "$OUTPUT"    ]] || die "--output required"    2
[[ -d "$ARTIFACTS" ]] || die "artifacts dir not found: $ARTIFACTS" 2
mkdir -p "$OUTPUT"

# -------- SBAT generation precheck (Q14 — Tails-6.5-class footgun mitigation) --------
# Block before signing if any vendor entry in shim/grub SBAT CSVs fell below
# the upstream baseline. Source-of-truth CSVs are in-repo; precheck operates
# on the working tree. If invoked from outside the repo (manual operator run
# against staged artifacts), the precheck is best-effort and will warn-only.
SR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$SR_SCRIPT_DIR/check-sbat-generations.sh" ] && \
   [ -f "$SR_SCRIPT_DIR/../packages/core/grub/sbat.csv" ]; then
    (cd "$SR_SCRIPT_DIR/.." && bash scripts/check-sbat-generations.sh) || {
        echo "ERROR: SBAT generation precheck failed; refusing to sign regressed entries." >&2
        exit 1
    }
fi

# -------- staging for atomic sign (SR1) --------
OUTPUT_STAGING="$OUTPUT/.signing-$$"
mkdir -p "$OUTPUT_STAGING"
trap 'rm -rf "$OUTPUT_STAGING"' EXIT

# -------- cert pre-positioning check (SR3) --------
shopt -s nullglob
_uki_check=( "$ARTIFACTS"/*.uki.efi "$ARTIFACTS"/igos-live.efi "$ARTIFACTS"/*-live.efi "$ARTIFACTS"/igos-install-*.efi )
if [[ ${#_uki_check[@]} -gt 0 ]] || [[ -f "$ARTIFACTS/grubx64.efi" ]]; then
    [[ -f "$VENDOR_CERT" ]] || die "vendor cert not found: $VENDOR_CERT" 2
fi
unset _uki_check

# -------- token presence check --------
# GPG side: gpg --card-status lists the token if connected + readable.
# If the card is missing we want to fail before we touch any artifact.
echo "[*] checking hardware token"
if ! gpg --card-status >/dev/null 2>&1; then
    die "no OpenPGP card detected — plug in the signing token" 1
fi

# sbsign / PKCS#11 side: the token advertises its objects via p11tool.
# If p11tool isn't installed we accept the GPG card as sufficient
# evidence for now; the sbsign step below will fail fast if the URI
# doesn't resolve.
if command -v p11tool >/dev/null 2>&1; then
    if ! p11tool --list-tokens 2>/dev/null | grep -q "Nitrokey\|YubiKey\|PIV"; then
        echo "warning: p11tool saw no PIV-capable token; sbsign may fail" >&2
    fi
fi

# -------- key-material configuration check --------
[[ -n "$GPG_KEY_ID"  ]] || die "GPG key id not set (flag or \$INTERGENOS_GPG_KEY_ID)"  2
[[ -n "$PKCS11_URI"  ]] || die "PKCS#11 URI not set (flag or \$INTERGENOS_PKCS11_URI)" 2

# B-049 (T0-2): refuse PIN-in-URI. PIN as a URI fragment leaks into
# process listings, env dumps, and any error output that echoes the URI.
# Canonical URI per docs/signing-procedure.md is `pkcs11:id=%02;type=private`;
# the OpenSSL pkcs11 engine prompts for PIN interactively from the
# operator's terminal during the signing ceremony.
if [[ "$PKCS11_URI" == *"pin-value="* ]] || [[ "$PKCS11_URI" == *"pin-source="* ]]; then
    die "PKCS11_URI must not embed PIN material (B-049). Use canonical 'pkcs11:id=%02;type=private' and let the engine prompt." 2
fi

# B-005 (T0-2): verify patched OpenSC PKCS#11 module is present. The
# stock OpenSC build packaged with most distros pre-0.27.1 cannot drive
# RSA-4096 PIV — sbsign fails at the first UKI. The patched module
# lives at $PKCS11_MODULE (see signing-procedure.md for build steps).
if [[ ! -f "$PKCS11_MODULE" ]]; then
    die "patched OpenSC PKCS#11 module not found at $PKCS11_MODULE (B-005). See docs/signing-procedure.md." 2
fi

# B-023 (T0-2): modulus-match guard. The cert stored at $VENDOR_CERT
# (which we'll pass to sbsign as --cert and which clients use as
# `sbverify --cert`) MUST match the key currently on PIV slot 9c by
# modulus. If they diverge we'd produce signatures against an unknown
# key — a silent ceremony failure that wouldn't show up until first
# Secure Boot verification by a user. Catch it before the first sign.
if command -v pkcs11-tool >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1; then
    _card_cert_der="$OUTPUT_STAGING/.card-cert.der"
    _card_cert_pem="$OUTPUT_STAGING/.card-cert.pem"
    if pkcs11-tool --module "$PKCS11_MODULE" --read-object --type cert --id 02 \
            -o "$_card_cert_der" >/dev/null 2>&1; then
        openssl x509 -inform DER -in "$_card_cert_der" -outform PEM -out "$_card_cert_pem" 2>/dev/null
        _card_modulus=$(openssl x509 -in "$_card_cert_pem" -noout -modulus 2>/dev/null | sed 's/Modulus=//')
        _repo_modulus=$(openssl x509 -in "$VENDOR_CERT" -noout -modulus 2>/dev/null | sed 's/Modulus=//')
        if [[ -z "$_card_modulus" ]] || [[ -z "$_repo_modulus" ]]; then
            die "modulus extraction failed (card or vendor cert unreadable) — refusing to sign" 2
        fi
        if [[ "$_card_modulus" != "$_repo_modulus" ]]; then
            die "modulus mismatch: PIV slot 9c cert does not match $VENDOR_CERT (B-023). Signing would produce verify-broken artifacts." 2
        fi
        echo "[OK] modulus match: PIV slot 9c <-> $VENDOR_CERT"
        rm -f "$_card_cert_der" "$_card_cert_pem"
    else
        echo "warning: could not read cert from PIV slot 9c via $PKCS11_MODULE; skipping modulus guard" >&2
    fi
else
    echo "warning: pkcs11-tool or openssl not available; skipping B-023 modulus guard" >&2
fi

# B-005 (T0-2 continued): set OPENSSL_CONF to load the patched OpenSC
# module via the pkcs11 engine. Without this, OpenSSL picks up the
# system pkcs11 provider config which on most workstations points at
# unpatched OpenSC.
_ssl_conf="$OUTPUT_STAGING/.openssl-pkcs11.cnf"
cat > "$_ssl_conf" <<CONF
openssl_conf = openssl_init

[openssl_init]
engines = engine_section

[engine_section]
pkcs11 = pkcs11_section

[pkcs11_section]
engine_id = pkcs11
dynamic_path = /usr/lib/x86_64-linux-gnu/engines-3/pkcs11.so
MODULE_PATH = $PKCS11_MODULE
init = 0
CONF
export OPENSSL_CONF="$_ssl_conf"
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"

# -------- step 1: pkm repo index --------
# Distro GPG key signs the pkm repository index. One touch per sign.
# Output filename convention: InterGenOS.db.sig (per pkm/repo.py L438).
INDEX="$ARTIFACTS/InterGenOS.db"
if [[ -f "$INDEX" ]]; then
    echo "[*] signing pkm repo index: $INDEX"
    gpg --batch --yes \
        --local-user "$GPG_KEY_ID" \
        --detach-sign --armor \
        --output "$OUTPUT_STAGING/InterGenOS.db.sig" \
        "$INDEX"
    cp "$INDEX" "$OUTPUT_STAGING/InterGenOS.db"
    # SR2: verify signature
    gpg --verify "$OUTPUT_STAGING/InterGenOS.db.sig" "$OUTPUT_STAGING/InterGenOS.db" \
        || die "GPG signature verification failed" 3
    echo "    -> $OUTPUT_STAGING/InterGenOS.db.sig"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: missing $INDEX" 4
else
    echo "[-] skipping pkm repo index (not present)"
fi

# -------- step 2: kernel UKI(s) --------
# Distro EFI X.509 key (PIV slot 9c) signs each UKI envelope. The UKI
# bundles vmlinuz + initramfs + cmdline + os-release into a single PE
# binary via systemd-stub; one signature covers all the bundled content.
# shim-signed GRUB verifies the UKI under check_signatures=enforce via
# the embedded shim_lock module. Bare vmlinuz is NEVER loaded directly —
# only via the UKI envelope.
#
# UKI naming conventions accepted in artifacts dir:
#   *.uki.efi              — explicit UKI suffix (preferred for build pipelines)
#   igos-live.efi          — convention used by ESP grub.cfg menu entries
#   *-live.efi             — variants for other live-mode UKIs (recovery, etc.)
#   igos-install-gui.efi   — Forge GUI installer UKI (B-002 T0-2)
#   igos-install-tui.efi   — Forge TUI installer UKI (B-002 T0-2)
#   igos-install-*.efi     — future installer UKI variants
#
# B-002 (T0-2): the install-gui + install-tui globs were added 2026-05-18.
# Pre-fix shipped ISOs had unsigned install UKIs because the glob silently
# dropped them while the signing loop reported success.
#
# Verifies post-sign that the signed binary still has .linux + .initrd
# sections (UKI shape preserved through the sign operation). The post-loop
# count assertion (B-025) catches future regressions where a new UKI
# class is added to the build but the glob is not extended.
ukis=( "$ARTIFACTS"/*.uki.efi "$ARTIFACTS"/igos-live.efi "$ARTIFACTS"/*-live.efi "$ARTIFACTS"/igos-install-*.efi )
# Deduplicate (e.g., igos-live.efi may match multiple globs)
declare -A _seen=()
unique_ukis=()
for u in "${ukis[@]}"; do
    [[ -f "$u" ]] || continue
    bn=$(basename "$u")
    [[ -n "${_seen[$bn]:-}" ]] && continue
    _seen[$bn]=1
    unique_ukis+=( "$u" )
done
unset _seen
signed_uki_count=0
if [[ ${#unique_ukis[@]} -gt 0 ]]; then
    for uki in "${unique_ukis[@]}"; do
        uname=$(basename "$uki")
        echo "[*] sbsigning UKI: $uname"
        # Sanity-check UKI shape pre-sign
        if ! objdump -h "$uki" 2>/dev/null | grep -q '\.linux'; then
            die "$uname does not appear to be a UKI (no .linux section)" 3
        fi
        if ! objdump -h "$uki" 2>/dev/null | grep -q '\.initrd'; then
            die "$uname missing .initrd section" 3
        fi
        sbsign --engine pkcs11 \
               --key "$PKCS11_URI" \
               --cert "$VENDOR_CERT" \
               --output "$OUTPUT_STAGING/$uname" \
               "$uki"
        # SR2: verify signature + UKI shape post-sign
        sbverify --cert "$VENDOR_CERT" "$OUTPUT_STAGING/$uname" \
            || die "sbsign verification failed for $uname" 3
        if ! objdump -h "$OUTPUT_STAGING/$uname" 2>/dev/null | grep -q '\.linux'; then
            die "post-sign UKI shape broken for $uname (.linux missing)" 3
        fi
        echo "    -> $OUTPUT_STAGING/$uname"
        signed_uki_count=$((signed_uki_count + 1))
    done
    # B-025 (T0-2): per-loop count assertion — every UKI we enumerated
    # pre-sign MUST have produced a signed artifact post-sign. If
    # signed_uki_count diverges from ${#unique_ukis[@]}, a future loop
    # body change silently short-circuited a sign step.
    if (( signed_uki_count != ${#unique_ukis[@]} )); then
        die "post-sign UKI count mismatch: enumerated ${#unique_ukis[@]} input UKIs but signed $signed_uki_count (B-025)" 3
    fi
    echo "[OK] B-025 UKI count: signed $signed_uki_count of ${#unique_ukis[@]} input"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: no UKI files (*.uki.efi / igos-live.efi / *-live.efi / igos-install-*.efi) in $ARTIFACTS" 4
else
    echo "[-] skipping UKIs (none present)"
fi

# -------- step 3: GRUB EFI binary --------
# Distro EFI X.509 key signs our custom GRUB so shim's vendor_db
# recognises it. One touch per sign.
GRUB="$ARTIFACTS/grubx64.efi"
if [[ -f "$GRUB" ]]; then
    echo "[*] sbsigning GRUB: $GRUB"
    sbsign --engine pkcs11 \
           --key "$PKCS11_URI" \
           --cert "$VENDOR_CERT" \
           --output "$OUTPUT_STAGING/grubx64.efi" \
           "$GRUB"
    # SR2: verify signature
    sbverify --cert "$VENDOR_CERT" "$OUTPUT_STAGING/grubx64.efi" \
        || die "sbsign verification failed for grubx64.efi" 3
    echo "    -> $OUTPUT_STAGING/grubx64.efi"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: missing $GRUB" 4
else
    echo "[-] skipping GRUB (not present)"
fi

# -------- step 4: archive integrity manifest --------
# Per install-time integrity verification design doc §5.2: sign the
# build-emitted intergenos-archive-manifest.txt with master + [S1] so
# install-time PHASE_VERIFY can validate the manifest signature before
# trusting any per-archive sha256. Produces:
#
#   $OUTPUT/intergenos-archive-manifest.txt        (canonical copy)
#   $OUTPUT/intergenos-archive-manifest.txt.sig    (detached, multi-sig
#                                                   if INTERGENOS_GPG_MASTER_KEY_ID
#                                                   is set; S1-only otherwise)
#   $OUTPUT/intergenos-release-key.asc             (public key export of
#                                                   $GPG_KEY_ID — embedded
#                                                   in the ISO so the user
#                                                   can self-validate)
#
# Master key cosignature: by design, the master key lives offline (Drive
# #3) and is exhumed only during release ceremonies. For routine builds,
# only [S1] signs (still cryptographically authoritative for build-
# integrity). For tagged releases, the operator provides
# INTERGENOS_GPG_MASTER_KEY_ID (or --gpg-master-key-id) to add the
# master signature in the same call. See docs/signing-procedure.md.
MANIFEST="${MANIFEST_PATH:-$ARTIFACTS/intergenos-archive-manifest.txt}"
if [[ -f "$MANIFEST" ]]; then
    echo "[*] signing archive manifest: $MANIFEST"

    # Verify the manifest looks sane before committing a signature to it.
    # A malformed or empty manifest signed at this stage would compromise
    # the install-time integrity surface.
    if ! grep -q '^# Manifest-version: 1$' "$MANIFEST"; then
        die "manifest missing 'Manifest-version: 1' header — refusing to sign" 5
    fi
    if ! grep -q '^# End of manifest\.$' "$MANIFEST"; then
        die "manifest missing '# End of manifest.' terminator — refusing to sign" 5
    fi
    if ! grep -q '^SHA256 ' "$MANIFEST"; then
        die "manifest contains no SHA256 entries — refusing to sign empty manifest" 5
    fi

    cp "$MANIFEST" "$OUTPUT_STAGING/intergenos-archive-manifest.txt"

    # Build sign-args: always S1; conditionally master-cosign.
    sign_args=(--batch --yes --detach-sign --armor
               --local-user "$GPG_KEY_ID")
    sig_label="[S1]"
    if [[ -n "$GPG_MASTER_KEY_ID" ]]; then
        sign_args+=(--local-user "$GPG_MASTER_KEY_ID")
        sig_label="master + [S1]"
    fi
    sign_args+=(--output "$OUTPUT_STAGING/intergenos-archive-manifest.txt.sig"
                "$OUTPUT_STAGING/intergenos-archive-manifest.txt")

    gpg "${sign_args[@]}"
    echo "    signed by: $sig_label"

    # SR2: verify signature against canonical copy
    gpg --verify "$OUTPUT_STAGING/intergenos-archive-manifest.txt.sig" \
                "$OUTPUT_STAGING/intergenos-archive-manifest.txt" \
        || die "GPG signature verification failed for manifest" 3

    # Export the public key ($GPG_KEY_ID's enclosing primary) so the
    # install-time verifier can self-validate without external network.
    # Per design doc §5.2: "Place release-key public component at
    # /install/intergenos-release-key.asc".
    gpg --batch --yes --armor --export "$GPG_KEY_ID" \
        > "$OUTPUT_STAGING/intergenos-release-key.asc"

    echo "    -> $OUTPUT_STAGING/intergenos-archive-manifest.txt"
    echo "    -> $OUTPUT_STAGING/intergenos-archive-manifest.txt.sig"
    echo "    -> $OUTPUT_STAGING/intergenos-release-key.asc"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: missing manifest at $MANIFEST" 4
else
    echo "[-] skipping archive manifest (not present at $MANIFEST)"
fi

# -------- B-025 (T0-2) cross-cutting .efi count assertion --------
# Final defensive check: every .efi input in $ARTIFACTS must have produced
# a corresponding .efi output in $OUTPUT_STAGING. Catches the case where
# a new EFI artifact class is added to the build but no signing branch is
# added to this script. (Distinct from the per-loop assertion above which
# guards the UKI loop alone.)
shopt -s nullglob
_in_efi=( "$ARTIFACTS"/*.efi )
_out_efi=( "$OUTPUT_STAGING"/*.efi )
if (( ${#_in_efi[@]} != ${#_out_efi[@]} )); then
    _in_names=$(cd "$ARTIFACTS" && ls *.efi 2>/dev/null | sort | tr '\n' ' ')
    _out_names=$(cd "$OUTPUT_STAGING" && ls *.efi 2>/dev/null | sort | tr '\n' ' ')
    die "post-sign .efi count mismatch: ${#_in_efi[@]} input(s) [$_in_names] vs ${#_out_efi[@]} output(s) [$_out_names] (B-025)" 3
fi
echo "[OK] B-025 .efi count: ${#_out_efi[@]} signed = ${#_in_efi[@]} input"

# -------- atomic promotion (SR1) --------
mv "$OUTPUT_STAGING"/* "$OUTPUT/" 2>/dev/null || true
rmdir "$OUTPUT_STAGING" 2>/dev/null || true
trap - EXIT

# Clean up transient OPENSSL_CONF (no secrets, but no need to persist)
rm -f "$_ssl_conf" 2>/dev/null || true
unset OPENSSL_CONF

# -------- done --------
echo
echo "[*] sign-release.sh complete."
echo "    signed artifacts: $OUTPUT"
echo
echo "Next: hand signed artifacts back to the build orchestrator for"
echo "      image-creation phase. Verify with:"
echo "        gpg --verify $OUTPUT/InterGenOS.db.sig $OUTPUT/InterGenOS.db"
echo "        for u in $OUTPUT/*.uki.efi $OUTPUT/igos-live.efi $OUTPUT/*-live.efi; do"
echo "            [ -f \"\$u\" ] && sbverify --cert $VENDOR_CERT \"\$u\""
echo "        done"
echo "        sbverify --cert $VENDOR_CERT $OUTPUT/grubx64.efi"
echo "        bash scripts/check-manifest-signature.sh \\"
echo "             $OUTPUT/intergenos-archive-manifest.txt \\"
echo "             $OUTPUT/intergenos-archive-manifest.txt.sig \\"
echo "             $OUTPUT/intergenos-release-key.asc"
