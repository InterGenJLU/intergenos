#!/bin/bash
# sign-module.sh — sign a kernel module with the per-machine MOK
#
# Invoked by rebuild-modules.sh after every successful module compile.
#
# Args:
#   $1 — path to the .ko file to sign (modified in-place)
#   $2 — kernel version (used to locate scripts/sign-file)
#
# Operation:
#   1. Locate scripts/sign-file inside /lib/modules/$KVER/build/scripts/.
#   2. Verify MOK keypair at /var/lib/intergen/mok/mok.{key,crt} present.
#   3. Append PKCS#7 signature to the .ko using sha256.
#   4. Verify the appended trailer "~Module signature appended~" landed.
#
# Security note: this script uses the kernel-native sign-file (PKCS#7
# appended to the .ko ELF), NOT sbsign (which is for PE/COFF EFI binaries).
# The 2026-04-20 prior research doc incorrectly proposed sbsign — corrected
# here after the 2026-05-28 signing review: kernel modules carry a detached
# PKCS#7 signature appended to the ELF, which is what sign-file produces.

set -uo pipefail

MOK_KEY=/var/lib/intergen/mok/mok.key
MOK_CERT=/var/lib/intergen/mok/mok.crt
KO_FILE="${1:?path to .ko required}"
KVER="${2:?kernel version required}"

# scripts/sign-file ships with the kernel source tree at
# /lib/modules/$KVER/build/scripts/sign-file (built by `make modules_prepare`
# in linux-kernel-pass2 do_install). Fallback to /usr/src/linux-$KVER if
# the symlink is broken.
SIGN_FILE="/lib/modules/${KVER}/build/scripts/sign-file"
if [ ! -x "$SIGN_FILE" ]; then
    SIGN_FILE="/usr/src/linux-${KVER%-igos}/scripts/sign-file"
fi
if [ ! -x "$SIGN_FILE" ]; then
    echo "[nvidia:sign-module] ERROR: scripts/sign-file not found for kernel $KVER" >&2
    echo "[nvidia:sign-module]   Tried: /lib/modules/$KVER/build/scripts/sign-file" >&2
    echo "[nvidia:sign-module]   Tried: /usr/src/linux-${KVER%-igos}/scripts/sign-file" >&2
    exit 1
fi

if [ ! -f "$MOK_KEY" ] || [ ! -f "$MOK_CERT" ]; then
    echo "[nvidia:sign-module] WARNING: MOK keypair missing at $MOK_KEY / $MOK_CERT" >&2
    echo "[nvidia:sign-module]   Module $KO_FILE will be UNSIGNED." >&2
    echo "[nvidia:sign-module]   Unsigned modules will be REJECTED on this kernel (CONFIG_MODULE_SIG_FORCE=y)." >&2
    echo "[nvidia:sign-module]   To fix: generate a signing key with Forge, or turn Secure Boot off." >&2
    # Non-fatal exit: the calling hook continues so the user sees the
    # complete state. Manual recovery path documented in
    # /usr/share/doc/nvidia/SIGNING-CHAIN.md.
    exit 0
fi

# Hash algorithm: sha256 matches the kernel's default
# (CONFIG_MODULE_SIG_HASH="sha256" in our kernel config fragments).
if ! "$SIGN_FILE" sha256 "$MOK_KEY" "$MOK_CERT" "$KO_FILE"; then
    echo "[nvidia:sign-module] ERROR: $SIGN_FILE failed on $KO_FILE" >&2
    exit 1
fi

# Verify the signature trailer was actually appended (sign-file is
# silent on success; tail -c 28 must show the magic string).
if ! tail -c 28 "$KO_FILE" | grep -q '~Module signature appended~'; then
    echo "[nvidia:sign-module] ERROR: signature trailer missing from $KO_FILE after sign-file" >&2
    exit 1
fi

exit 0
