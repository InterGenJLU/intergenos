#!/bin/sh
# fde-init.sh — InterGenOS FDE (Full Disk Encryption) initramfs entry point
#
# Loaded by kernel as initrd entry point ONLY on installed systems where
# the root filesystem is wrapped in LUKS2. Wired into the UKI via
# scripts/build-fde-initramfs.sh (Phase D activation; see note below) per
# D-005 Option A's UKI-bundles-FDE-initramfs composition with D-001
# (LUKS-at-install v1.0 ratified opt-in encryption).
#
# Scope: prompt user for LUKS passphrase, open cryptsetup mapping, mount
# the unlocked root, switch_root into it. ~50 lines per D-005's
# implementation-backlog text: "custom busybox + cryptsetup-static; ~50
# lines of init in the spirit of installer/init/init.sh; only built and
# installed for LUKS-enabled installs."
#
# Plain (non-LUKS) installs do NOT use this script. Per 2026-04-09
# ratification (narrowed by D-001/D-005), plain installs boot with
# kernel-builtin storage drivers + PARTUUID + rootwait; the UKI's only
# bundled cpio for those is microcode (intel-ucode.img).
#
# Phase D ACTIVATION DEPENDENCY CHAIN (this script is foundational; the
# below pieces are required for the script to actually run on a user
# system):
#   1. packages/core/cryptsetup-static (NEW PACKAGE — not yet in tree;
#      mirror the packages/core/busybox-static pattern but for
#      cryptsetup, statically linked against json-c + popt +
#      libdevmapper)
#   2. installer/init/build-fde-initramfs.sh (NEW PACKAGER — mirror
#      build-initramfs.sh but bundling cryptsetup-static + dm_crypt +
#      ext4 + storage drivers; fde-init.sh as /init)
#   3. packages/core/linux-kernel/hooks/post-install.sh (EXISTING — extend
#      to detect LUKS install via /etc/crypttab presence; pass the
#      FDE initramfs cpio to ukify --initrd= instead of the plain
#      install's empty/microcode-only initramfs)
#   4. Forge installer/backend/disks.py (EXISTING — wire LUKS opt-in path
#      per D-001's "Opt-in encryption checkbox in Forge" backlog;
#      passphrase capture + LUKS2 format + /etc/crypttab write)

set -e

# ---- Setup pseudo-filesystems ----------------------------------------------
mount -t proc     none /proc
mount -t sysfs    none /sys
mount -t devtmpfs none /dev

# ---- Locate the LUKS volume ------------------------------------------------
# Source-of-truth: /etc/crypttab (Forge writes this at install). Fallback:
# kernel cmdline `cryptdev=` (operator override during boot).
CRYPT_NAME=cryptroot
CRYPT_DEV=$(awk -v n="$CRYPT_NAME" '$1==n {print $2}' /etc/crypttab 2>/dev/null || true)

if [ -z "$CRYPT_DEV" ]; then
    for arg in $(cat /proc/cmdline); do
        case "$arg" in
            cryptdev=*) CRYPT_DEV="${arg#cryptdev=}" ;;
        esac
    done
fi

if [ -z "$CRYPT_DEV" ]; then
    echo "[fde-init] FATAL: no LUKS volume specified (/etc/crypttab + cryptdev= cmdline both empty)"
    echo "[fde-init] Dropping to recovery shell. Type 'exit' to retry init."
    exec /bin/sh
fi

# Resolve PARTUUID/UUID/LABEL forms via /dev/disk/by-*
case "$CRYPT_DEV" in
    PARTUUID=*) CRYPT_DEV="/dev/disk/by-partuuid/${CRYPT_DEV#PARTUUID=}" ;;
    UUID=*)     CRYPT_DEV="/dev/disk/by-uuid/${CRYPT_DEV#UUID=}" ;;
    LABEL=*)    CRYPT_DEV="/dev/disk/by-label/${CRYPT_DEV#LABEL=}" ;;
esac

# Wait briefly for the device node to appear (udev-less environment)
i=0
while [ ! -e "$CRYPT_DEV" ] && [ "$i" -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done
if [ ! -e "$CRYPT_DEV" ]; then
    echo "[fde-init] FATAL: LUKS volume $CRYPT_DEV not found after 30s wait"
    exec /bin/sh
fi

# ---- Prompt + unlock -------------------------------------------------------
echo ""
echo "  InterGenOS — encrypted root unlock"
echo ""
# cryptsetup open prompts on /dev/tty by default; passphrase entry is
# interactive. Max 3 attempts before falling through to recovery shell.
attempts=0
until cryptsetup open "$CRYPT_DEV" "$CRYPT_NAME"; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 3 ]; then
        echo "[fde-init] FATAL: 3 failed passphrase attempts."
        echo "[fde-init] Dropping to recovery shell. Type 'cryptsetup open $CRYPT_DEV $CRYPT_NAME' to retry."
        exec /bin/sh
    fi
    echo "[fde-init] Wrong passphrase. $((3 - attempts)) attempts remaining."
done

# ---- Mount the unlocked root + handoff -------------------------------------
mkdir -p /newroot
if ! mount "/dev/mapper/$CRYPT_NAME" /newroot; then
    echo "[fde-init] FATAL: mount /dev/mapper/$CRYPT_NAME -> /newroot failed"
    exec /bin/sh
fi

mount --move /proc /newroot/proc
mount --move /sys  /newroot/sys
mount --move /dev  /newroot/dev

# Handoff to systemd (or whatever /sbin/init resolves to on the rootfs)
exec switch_root /newroot /sbin/init
