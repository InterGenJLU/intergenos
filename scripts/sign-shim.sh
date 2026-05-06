#!/usr/bin/env bash
# sign-shim.sh — sign an unsigned shim EFI binary using NK#1 PIV slot 9c.
#
# The container build (docker/shim-build/Dockerfile) produces an unsigned
# shimx64.efi. Final signing happens here, on the workstation, against the
# hardware-bound private key on Nitrokey #1 PIV slot 9c via OpenSC + sbsign's
# pkcs11 engine. The private key never leaves the token — that's the whole
# point of doing the keygen on-card during the ceremony.
#
# Two output paths:
#   - Self-signed (for our own dev / MOK-enrolled testing during shim-review wait)
#   - The Microsoft-signed binary that ships in production comes from the
#     shim-review process post-PR-merge, not from this script
#
# Prerequisites:
#   - Nitrokey #1 plugged in
#   - opensc + sbsigntool packages installed
#   - pcscd running (or pcsclite-managed)
#
# Usage:
#   scripts/sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>

set -euo pipefail

UNSIGNED="${1:?usage: sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>}"
SIGNED="${2:?usage: sign-shim.sh <unsigned-shim.efi> <signed-shim.efi>}"

CERT="${VENDOR_CERT:-docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem}"

# OpenSC PKCS#11 module path varies by distro. Common paths:
#   Ubuntu/Debian: /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so
#   Fedora/RHEL:   /usr/lib64/pkcs11/opensc-pkcs11.so
PKCS11_MODULE="${PKCS11_MODULE:-/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so}"

# PKCS#11 URI for the private key. PIV slot 9c on NK#1 is typically exposed
# by OpenSC's PKCS#11 module as id=02 (digital-signature key). If the default
# fails, discover the actual URI via:
#   pkcs11-tool --module "$PKCS11_MODULE" --list-objects --type privkey
# and override:
#   PKCS11_KEY_URI='pkcs11:id=%XX;type=private' scripts/sign-shim.sh ...
PKCS11_KEY_URI="${PKCS11_KEY_URI:-pkcs11:id=%02;type=private}"

if [ ! -f "$UNSIGNED" ]; then
    echo "ERROR: unsigned binary not found: $UNSIGNED" >&2
    exit 1
fi

if [ ! -f "$CERT" ]; then
    echo "ERROR: vendor cert not found: $CERT" >&2
    exit 1
fi

if [ ! -f "$PKCS11_MODULE" ]; then
    echo "ERROR: PKCS#11 module not found: $PKCS11_MODULE" >&2
    echo "Install opensc-pkcs11 or override PKCS11_MODULE env var." >&2
    exit 1
fi

if ! pkcs11-tool --module "$PKCS11_MODULE" --list-slots > /dev/null 2>&1; then
    echo "ERROR: NK#1 not detected. Plug in NK#1 + verify pcscd is running." >&2
    exit 1
fi

echo "Signing $UNSIGNED -> $SIGNED"
echo "  Cert: $CERT"
echo "  Key:  $PKCS11_KEY_URI (via $PKCS11_MODULE)"
echo "  Touch NK#1 when prompted."

sbsign \
    --engine pkcs11 \
    --key "$PKCS11_KEY_URI" \
    --cert "$CERT" \
    --output "$SIGNED" \
    "$UNSIGNED"

if sbverify --cert "$CERT" "$SIGNED" 2>&1 | grep -q "Signature verification OK"; then
    echo "PASS: signature verifies"
    echo "  Unsigned SHA-256: $(sha256sum "$UNSIGNED" | awk '{print $1}')"
    echo "  Signed SHA-256:   $(sha256sum "$SIGNED" | awk '{print $1}')"
else
    echo "FAIL: signature does not verify against $CERT" >&2
    exit 1
fi
