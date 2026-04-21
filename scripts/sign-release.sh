#!/bin/bash
# sign-release.sh — sign an InterGenOS release
#
# Runs on the signing workstation (NOT the build VM) with the hardware
# token plugged in, PIN unlocked. Signs three classes of artifact:
#
#   1. pkm repo index (GPG detached signature, --detach-sign --armor)
#   2. Kernel vmlinuz       (sbsign with PKCS#11 URI to PIV slot 9c)
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
#                     contents: InterGenOS.db (pkm index), vmlinuz-*,
#                     grubx64.efi. Missing files skip their sign step
#                     unless --strict is set.
#   --output DIR      Directory where signed artifacts + detached sigs
#                     are written. Created if missing.
#   --gpg-key-id      Fingerprint of the signing subkey on the token.
#                     Defaults to $INTERGENOS_GPG_KEY_ID env var.
#   --pkcs11-uri      PKCS#11 URI for the sbsign key on PIV slot 9c.
#                     Defaults to $INTERGENOS_PKCS11_URI env var.
#   --strict          Fail if any expected artifact is missing.
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
PKCS11_URI="${INTERGENOS_PKCS11_URI:-}"
STRICT=0

# -------- arg parsing --------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifacts)   ARTIFACTS="$2"; shift 2 ;;
        --output)      OUTPUT="$2"; shift 2 ;;
        --gpg-key-id)  GPG_KEY_ID="$2"; shift 2 ;;
        --pkcs11-uri)  PKCS11_URI="$2"; shift 2 ;;
        --strict)      STRICT=1; shift ;;
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

# -------- step 1: pkm repo index --------
# Distro GPG key signs the pkm repository index. One touch per sign.
# Output filename convention: InterGenOS.db.sig (per pkm/repo.py L438).
INDEX="$ARTIFACTS/InterGenOS.db"
if [[ -f "$INDEX" ]]; then
    echo "[*] signing pkm repo index: $INDEX"
    gpg --batch --yes \
        --local-user "$GPG_KEY_ID" \
        --detach-sign --armor \
        --output "$OUTPUT/InterGenOS.db.sig" \
        "$INDEX"
    cp "$INDEX" "$OUTPUT/InterGenOS.db"
    echo "    -> $OUTPUT/InterGenOS.db.sig"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: missing $INDEX" 4
else
    echo "[-] skipping pkm repo index (not present)"
fi

# -------- step 2: kernel vmlinuz --------
# Distro EFI X.509 key (PIV slot 9c) signs each kernel image so the
# shim-signed GRUB verifies it under check_signatures=enforce.
# One touch per kernel. Multiple vmlinuz-* files sign in sequence.
shopt -s nullglob
kernels=( "$ARTIFACTS"/vmlinuz-* )
if [[ ${#kernels[@]} -gt 0 ]]; then
    for kern in "${kernels[@]}"; do
        kname=$(basename "$kern")
        echo "[*] sbsigning kernel: $kname"
        sbsign --engine pkcs11 \
               --key "$PKCS11_URI" \
               --cert "$ARTIFACTS/vendor-cert.pem" \
               --output "$OUTPUT/$kname" \
               "$kern"
        echo "    -> $OUTPUT/$kname"
    done
elif [[ "$STRICT" == "1" ]]; then
    die "strict: no vmlinuz-* in $ARTIFACTS" 4
else
    echo "[-] skipping kernels (none present)"
fi

# -------- step 3: GRUB EFI binary --------
# Distro EFI X.509 key signs our custom GRUB so shim's vendor_db
# recognises it. One touch per sign.
GRUB="$ARTIFACTS/grubx64.efi"
if [[ -f "$GRUB" ]]; then
    echo "[*] sbsigning GRUB: $GRUB"
    sbsign --engine pkcs11 \
           --key "$PKCS11_URI" \
           --cert "$ARTIFACTS/vendor-cert.pem" \
           --output "$OUTPUT/grubx64.efi" \
           "$GRUB"
    echo "    -> $OUTPUT/grubx64.efi"
elif [[ "$STRICT" == "1" ]]; then
    die "strict: missing $GRUB" 4
else
    echo "[-] skipping GRUB (not present)"
fi

# -------- done --------
echo
echo "[*] sign-release.sh complete."
echo "    signed artifacts: $OUTPUT"
echo
echo "Next: hand signed artifacts back to the build orchestrator for"
echo "      image-creation phase. Verify with:"
echo "        gpg --verify $OUTPUT/InterGenOS.db.sig $OUTPUT/InterGenOS.db"
echo "        sbverify --cert $ARTIFACTS/vendor-cert.pem $OUTPUT/vmlinuz-*"
echo "        sbverify --cert $ARTIFACTS/vendor-cert.pem $OUTPUT/grubx64.efi"
