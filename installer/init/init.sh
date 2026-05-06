#!/bin/sh
# /init — InterGenOS live-boot dispatcher (custom 100-line initramfs entry point)
#
# Loaded by kernel as initrd entry point. Resolves the live-boot squashfs,
# sets up overlayfs root, dispatches to live GNOME desktop OR Forge installer
# based on `igos.mode=` kernel cmdline.
#
# Q-INIT resolved 2026-05-05/06: April-10 custom-init stands. UKI wraps this
# initramfs via systemd-stub + objcopy + sbsign (NK#1 PIV slot 9c).
#
# Q-PERSISTENCE resolved: off by default for v1.0; design document at
# `docs/research/installer/forge_iso_concept_design_2026-05-04.md`.
# Q-DM-VERITY: deferred to v1.x.
#
# Use of busybox-static for the userland inside this initramfs is intentional:
# minimal attack surface, no glibc dependency in the early-boot envelope.

set -e

# ---- Utilities -------------------------------------------------------------
fatal() {
    echo "[init] FATAL: $*" >&2
    echo "[init] Dropping to recovery shell. Type 'exit' to continue (no-op)."
    exec /bin/sh
}

info() {
    echo "[init] $*"
}

# ---- Mount essential virtual filesystems -----------------------------------
mount -t proc     -o nosuid,noexec,nodev proc     /proc
mount -t sysfs    -o nosuid,noexec,nodev sysfs    /sys
mount -t devtmpfs -o nosuid             devtmpfs  /dev

# ---- Parse cmdline ---------------------------------------------------------
# `igos.mode=` selects the userspace dispatch:
#   live         -> live GNOME desktop (liveuser autologin)
#   install-gui  -> auto-launch Forge GUI installer (skips GNOME shell)
#   install-tui  -> auto-launch Forge TUI declarative-builder on tty1
MODE=$(awk -v RS=' ' '/^igos\.mode=/ {sub(/^igos\.mode=/, ""); print}' /proc/cmdline)
[ -z "$MODE" ] && MODE=live
case "$MODE" in
    live|install-gui|install-tui) ;;
    *) fatal "unknown igos.mode=$MODE (expected: live|install-gui|install-tui)" ;;
esac
info "boot mode: $MODE"

# ---- Load required modules -------------------------------------------------
# These modules are present in the initramfs cpio (see build-initramfs.sh).
# squashfs: read-only root filesystem
# overlay:  writable upper layer over squashfs
# loop:     squashfs is loop-mounted off the ISO
for mod in squashfs overlay loop isofs vfat; do
    modprobe "$mod" 2>/dev/null || info "module $mod not loadable (may be built-in)"
done

# ---- Locate ISO live filesystem --------------------------------------------
# ISO assembly labels the data partition `IGOS_LIVE`. Retry briefly to give
# USB devices time to enumerate (some firmware is slow).
ISO_DEV=""
for tries in 1 2 3 4 5 6 7 8 9 10; do
    ISO_DEV=$(blkid -L IGOS_LIVE 2>/dev/null) && break
    sleep 1
done
[ -z "$ISO_DEV" ] && fatal "IGOS_LIVE label not found on any block device after 10s"
info "ISO device: $ISO_DEV"

# ---- Mount ISO + squashfs --------------------------------------------------
mkdir -p /run/iso /run/squashfs /run/overlay
mount -o ro "$ISO_DEV" /run/iso || fatal "cannot mount ISO at $ISO_DEV"

SQUASHFS_PATH=/run/iso/live/filesystem.squashfs
[ -f "$SQUASHFS_PATH" ] || fatal "squashfs not found at $SQUASHFS_PATH"
mount -t squashfs -o ro,loop "$SQUASHFS_PATH" /run/squashfs || fatal "cannot mount squashfs"

# ---- Set up overlayfs root -------------------------------------------------
mount -t tmpfs tmpfs /run/overlay
mkdir -p /run/overlay/upper /run/overlay/work
mkdir -p /newroot
mount -t overlay overlay \
    -o lowerdir=/run/squashfs,upperdir=/run/overlay/upper,workdir=/run/overlay/work \
    /newroot \
    || fatal "cannot create overlayfs root"

# ---- Move mounts into newroot ----------------------------------------------
# Preserve our virtual + ISO mounts so userspace can find them.
mkdir -p /newroot/run/iso /newroot/run/squashfs
mount --move /run/iso       /newroot/run/iso
mount --move /run/squashfs  /newroot/run/squashfs
mount --move /sys           /newroot/sys
mount --move /proc          /newroot/proc
mount --move /dev           /newroot/dev

# ---- Hand off mode to userspace --------------------------------------------
mkdir -p /newroot/run/intergenos
echo "$MODE" > /newroot/run/intergenos/boot-mode

# ---- Switch root and exec PID 1 --------------------------------------------
# systemd reads /run/intergenos/boot-mode via a generator (installed by the
# squashfs payload at /usr/lib/systemd/system-generators/igos-mode-generator)
# and selects the appropriate target:
#   live         -> graphical.target with autologin
#   install-gui  -> graphical.target with forge-gui.service overlay
#   install-tui  -> multi-user.target with forge-tui@tty1.service
info "switching root to /newroot, PID 1 = systemd"
exec switch_root /newroot /sbin/init
