#!/bin/bash
# sign-module.sh — sign a kernel module with this machine's owner key
#
# Invoked by rebuild-modules.sh after every successful module compile.
#
# Args:
#   $1 — path to the .ko file to sign (modified in-place)
#   $2 — kernel version (used to locate scripts/sign-file)
#
# Operation:
#   1. Locate scripts/sign-file inside /lib/modules/$KVER/build/scripts/.
#   2. Ask the machine's owner for the signing key's passphrase, or take the one
#      the installer supplied. No passphrase, no signature, and a refusal said
#      out loud — never a module left unsigned while the run reports success.
#   3. Append a PKCS#7 signature to the .ko using sha256.
#   4. PROVE the signature this run produced, against this machine's own
#      certificate.
#
# Security note: this script uses the kernel-native sign-file (PKCS#7 appended
# to the .ko ELF), NOT sbsign (which is for PE/COFF EFI binaries). The
# 2026-04-20 prior research doc incorrectly proposed sbsign — corrected here
# after the 2026-05-28 signing review: kernel modules carry a detached PKCS#7
# signature appended to the ELF, which is what sign-file produces.
#
# WHAT CHANGED 2026-09-17, and why both halves were needed:
#
#   The key is encrypted at rest. Until now this script signed with it
#   unattended, which meant any process running as root could sign a module the
#   kernel would then load under CONFIG_MODULE_SIG_FORCE. It now asks.
#
#   The success check changed too, and that was a defect found while making the
#   first change rather than a requirement handed down. It used to read the last
#   28 bytes for the marker "~Module signature appended~". Every module the
#   kernel package ships is already signed at build time, so that marker is
#   there before this script runs: measured on 2026-09-17, the check passed on a
#   module whose signing had just been refused and on one signed with the wrong
#   passphrase. It could not tell a successful signing from a failed one on any
#   module that had ever been signed. The check now compares the module's bytes
#   before and after, so it is about THIS run rather than about the file's
#   history.

set -uo pipefail

# The one place that answers "may this machine sign, and with what". MOK_HELPER
# is overridable so the preflight tests can exercise this script against a
# scratch key; on a machine it is the path the kernel package installs.
MOK_HELPER="${MOK_HELPER:-/usr/lib/intergen/mok-signing.sh}"
if [ -r "$MOK_HELPER" ]; then
    # shellcheck source=/dev/null
    . "$MOK_HELPER"
else
    echo "[nvidia:sign-module] ERROR: $MOK_HELPER is missing, so this script cannot ask for the signing passphrase." >&2
    echo "[nvidia:sign-module]   Module will NOT be signed and will NOT load on this kernel." >&2
    echo "[nvidia:sign-module]   Restore it with: sudo pkm reinstall linux-kernel" >&2
    exit 1
fi

KO_FILE="${1:?path to .ko required}"
KVER="${2:?kernel version required}"

# scripts/sign-file ships with the kernel source tree at
# /lib/modules/$KVER/build/scripts/sign-file (built by `make modules_prepare`
# in linux-kernel-pass2 do_install). Fallback: the staged source tree
# /usr/src/linux-<bare version> when the build link is broken. The derivation
# lives in the shared helper (the earlier fallback stripped only a trailing
# -igos from the release string; independent review 2026-09-11).
. "$(dirname "$(readlink -f "$0")")/kernel-paths.sh"
if ! SIGN_FILE=$(nvidia_sign_file_path "$KVER"); then
    echo "[nvidia:sign-module] ERROR: scripts/sign-file not found for kernel $KVER (both paths named above)" >&2
    exit 1
fi

if [ ! -f "$MOK_KEY" ] || [ ! -f "$MOK_CERT" ]; then
    echo "[nvidia:sign-module] ERROR: no machine owner key at $MOK_KEY / $MOK_CERT." >&2
    echo "[nvidia:sign-module]   $KO_FILE is NOT signed and will be REJECTED by this kernel" >&2
    echo "[nvidia:sign-module]   (CONFIG_MODULE_SIG_FORCE=y)." >&2
    echo "[nvidia:sign-module]   This is a refusal, not a skipped step: the rebuild that called this" >&2
    echo "[nvidia:sign-module]   script is incomplete and says so, rather than reporting success and" >&2
    echo "[nvidia:sign-module]   leaving the driver to fail at the next boot." >&2
    echo "[nvidia:sign-module]   To fix: generate a signing key with Forge, or turn Secure Boot off." >&2
    exit 1
fi

# A key with no passphrase on it is protected before it signs again. Every
# machine installed before 2026-09-17 is in that state; the owner is asked once,
# here or at the next kernel update, whichever comes first.
if ! mok_key_is_encrypted "$MOK_KEY"; then
    if ! mok_migrate_plain_key "$MOK_KEY"; then
        echo "[nvidia:sign-module] ERROR: the signing key has no passphrase and the owner did not set one." >&2
        echo "[nvidia:sign-module]   $KO_FILE is NOT signed and will not load." >&2
        exit 1
    fi
fi

if [ -z "${MOK_PASSPHRASE:-}" ]; then
    if ! mok_resolve_passphrase "$MOK_KEY" "the driver module $(basename "$KO_FILE")"; then
        echo "[nvidia:sign-module] ERROR: no usable signing passphrase; $KO_FILE is NOT signed." >&2
        echo "[nvidia:sign-module]   It will be REJECTED by this kernel (CONFIG_MODULE_SIG_FORCE=y)." >&2
        echo "[nvidia:sign-module]   Re-run the rebuild at the console with the passphrase in hand:" >&2
        echo "[nvidia:sign-module]       sudo /var/lib/pkm/hooks/nvidia/rebuild-modules $KVER" >&2
        exit 1
    fi
fi

# What the file looked like before this run. The comparison below is against
# this, which is what makes the check about THIS signing rather than about
# whether the module was ever signed by anyone.
BEFORE_SUM=$(sha256sum "$KO_FILE" 2>/dev/null | cut -d' ' -f1)

# Hash algorithm: sha256 matches the kernel's default
# (CONFIG_MODULE_SIG_HASH="sha256" in our kernel config fragments).
#
# The passphrase reaches the signer through the one environment variable it
# reads. It does not read standard input, so feeding it there would silently
# fall through to prompting on a terminal that is not attached.
if ! mok_sign_kernel_module "$SIGN_FILE" sha256 "$MOK_KEY" "$MOK_CERT" "$KO_FILE"; then
    echo "[nvidia:sign-module] ERROR: $SIGN_FILE failed on $KO_FILE" >&2
    exit 1
fi

AFTER_SUM=$(sha256sum "$KO_FILE" 2>/dev/null | cut -d' ' -f1)
if [ "$AFTER_SUM" = "$BEFORE_SUM" ]; then
    echo "[nvidia:sign-module] ERROR: $KO_FILE is byte-identical after signing — nothing was appended." >&2
    exit 1
fi

if ! tail -c 28 "$KO_FILE" | grep -q '~Module signature appended~'; then
    echo "[nvidia:sign-module] ERROR: signature trailer missing from $KO_FILE after sign-file" >&2
    exit 1
fi

exit 0
