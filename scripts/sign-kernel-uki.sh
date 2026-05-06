#!/usr/bin/env bash
# sign-kernel-uki.sh — sign the kernel UKI using NK#1 PIV slot 9c.
#
# The UKI (Unified Kernel Image) is built by scripts/build-uki.sh from
# vmlinuz + initramfs cpio + cmdline + os-release using systemd-stub +
# objcopy. This script signs the resulting PE binary against NK#1's
# hardware-bound private key via OpenSC + sbsign's pkcs11 engine.
#
# UKI fuses kernel + initramfs + cmdline into a single signed binary —
# one signature envelope covers everything the firmware verifies.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wrapping
# happens via systemd-stub + objcopy (NOT dracut --uefi).
#
# Mirror of scripts/sign-shim.sh and scripts/sign-grub.sh. Three sign helpers,
# one pattern: workstation-side, NK#1 plugged in, sbsign + pkcs11 engine.
#
# Prerequisites:
#   - Nitrokey #1 plugged in
#   - opensc + sbsigntool packages installed
#   - pcscd running (or pcsclite-managed)
#
# Usage:
#   scripts/sign-kernel-uki.sh <unsigned-uki.efi> <signed-uki.efi>

set -euo pipefail

UNSIGNED="${1:?usage: sign-kernel-uki.sh <unsigned-uki.efi> <signed-uki.efi>}"
SIGNED="${2:?usage: sign-kernel-uki.sh <unsigned-uki.efi> <signed-uki.efi>}"

CERT="${VENDOR_CERT:-docker/shim-build/vendor-cert/intergenos-secure-boot-ca.pem}"
PKCS11_MODULE="${PKCS11_MODULE:-/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so}"
PKCS11_KEY_URI="${PKCS11_KEY_URI:-pkcs11:id=%02;type=private}"

if [ ! -f "$UNSIGNED" ]; then
    echo "ERROR: unsigned UKI not found: $UNSIGNED" >&2
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

# Sanity-check UKI shape: must be a PE binary with .linux + .initrd sections
if ! objdump -h "$UNSIGNED" 2>/dev/null | grep -q '\.linux'; then
    echo "ERROR: $UNSIGNED does not appear to be a UKI (no .linux section found)" >&2
    echo "Build via scripts/build-uki.sh before signing." >&2
    exit 1
fi
if ! objdump -h "$UNSIGNED" 2>/dev/null | grep -q '\.initrd'; then
    echo "ERROR: $UNSIGNED is missing .initrd section" >&2
    exit 1
fi

UKI_SIZE=$(stat -c%s "$UNSIGNED")
UKI_SIZE_MB=$((UKI_SIZE / 1024 / 1024))
if [ "$UKI_SIZE_MB" -gt 200 ]; then
    echo "WARNING: UKI is ${UKI_SIZE_MB}MB — exceeds typical OVMF firmware-load limit of ~200MB" >&2
    echo "Consider trimming initramfs contents (see installer/init/build-initramfs.sh)" >&2
fi

echo "Signing $UNSIGNED -> $SIGNED"
echo "  Cert:   $CERT"
echo "  Key:    $PKCS11_KEY_URI (via $PKCS11_MODULE)"
echo "  Size:   ${UKI_SIZE_MB} MB"
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
