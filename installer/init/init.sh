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
# Strategy modeled on Debian-live / Ubuntu / Arch live discovery:
#   1. Fast path: try canonical label `IGOS_LIVE` via blkid.
#   2. Slow path: scan block devices for `/live/filesystem.squashfs` — the
#      authoritative artifact regardless of how the media is labeled.
# This survives user-renamed media (USB stick reformatted to a different
# label, etc.) and matches real-distro behavior. Retry loop accommodates
# slow USB enumeration on some firmware.
find_live_device() {
    # Fast path: canonical label
    local dev
    dev=$(blkid -L IGOS_LIVE 2>/dev/null)
    if [ -n "$dev" ]; then
        echo "$dev"
        return 0
    fi

    # Slow path: scan all block devices for /live/filesystem.squashfs
    local d tmpdir
    tmpdir=$(mktemp -d 2>/dev/null) || tmpdir=/tmp/scan-live
    mkdir -p "$tmpdir"
    for d in /dev/sd[a-z][0-9]* /dev/vd[a-z][0-9]* /dev/sr[0-9]* \
             /dev/nvme[0-9]n[0-9]p[0-9]* /dev/mmcblk[0-9]p[0-9]*; do
        [ -b "$d" ] || continue
        if mount -t auto -o ro "$d" "$tmpdir" 2>/dev/null; then
            if [ -f "$tmpdir/live/filesystem.squashfs" ]; then
                umount "$tmpdir" 2>/dev/null
                rmdir "$tmpdir" 2>/dev/null
                echo "$d"
                return 0
            fi
            umount "$tmpdir" 2>/dev/null
        fi
    done
    rmdir "$tmpdir" 2>/dev/null
    return 1
}

ISO_DEV=""
for tries in 1 2 3 4 5 6 7 8 9 10; do
    ISO_DEV=$(find_live_device) && [ -n "$ISO_DEV" ] && break
    sleep 1
done
[ -z "$ISO_DEV" ] && fatal "live media not found: no block device has /live/filesystem.squashfs (canonical label IGOS_LIVE also tried)"
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
# The squashfs is built with -e proc -e sys -e dev -e run -e tmp (those are
# runtime pseudo-fs paths, intentionally excluded from the static image),
# so we must create the mount-point directories in /newroot's upper layer
# (overlayfs upperdir is tmpfs, writable) before mount --move can target them.
mkdir -p /newroot/run/iso /newroot/run/squashfs \
         /newroot/sys /newroot/proc /newroot/dev /newroot/tmp
mount --move /run/iso       /newroot/run/iso
mount --move /run/squashfs  /newroot/run/squashfs
mount --move /sys           /newroot/sys
mount --move /proc          /newroot/proc
mount --move /dev           /newroot/dev

# ---- Mode-specific overlay setup -------------------------------------------
# These writes land in the overlayfs upper layer (tmpfs), not the squashfs
# itself (read-only). systemd in /newroot reads them as if they were always
# there. Lets us correct squashfs gaps without rebuilding the squashfs.
#
# The squashfs is built from the chroot root, which is an INSTALLED-SYSTEM
# layout — full set of multi-user.target.wants/ symlinks (mariadb, httpd,
# caddy, influxdb, etcd, valkey, lighttpd, ...) plus the systemd-firstboot
# machinery for first-boot-of-installed-system flow. Both are wrong context
# for a live ISO: the persistent services fail because the live filesystem
# lacks their users / config / mount namespaces, and systemd-firstboot fires
# the interactive user-creation prompt because the squashfs has no
# /etc/machine-id. These overlay writes patch the live ISO into something
# minimally usable WITHOUT modifying the squashfs (which is shared with
# install modes + ends up on the installed target post-install). Proper
# live.target architecture is a v1.0 design arc.
if [ "$MODE" = "live" ]; then
    info "live mode: writing overlay setup for non-interactive live boot"

    # Generate a valid 32-hex-char machine-id. Writing literal "uninitialized"
    # TRIGGERS systemd's ConditionFirstBoot=yes path (which fires
    # systemd-firstboot interactive); a real ID suppresses it. This ID is
    # per-boot (regenerated each live session) since the overlay is tmpfs.
    #
    # Kernel's UUID source produces a 36-char string of form
    # xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx; sed strips the dashes leaving
    # the required 32 hex chars. busybox-static in this initramfs has
    # `cat` and `sed` but not `tr` or `head`, hence this form rather than
    # the `tr -dc ... | head -c 32 < /dev/urandom` idiom that's common in
    # full-userland live-init scripts.
    cat /proc/sys/kernel/random/uuid | sed 's/-//g' > /newroot/etc/machine-id

    # Pre-set hostname so any downstream firstboot machinery has no field
    # left to prompt for.
    echo "intergenos-live" > /newroot/etc/hostname

    # Belt-and-suspenders: explicitly mask the firstboot services even
    # though a valid machine-id should suppress them. Symlink to /dev/null
    # is systemd's "never start this unit" idiom.
    ln -sf /dev/null /newroot/etc/systemd/system/systemd-firstboot.service
    ln -sf /dev/null /newroot/etc/systemd/system/systemd-homed-firstboot.service

    # Mask the Wave 1b databases + webservers that auto-start at
    # multi-user.target.wants/. In live context they fail noisily with
    # USER / NAMESPACE errors (the live filesystem has no per-service users,
    # no /var/log/<svc>/, no Restart=no policy, etc.) and systemd's Restart=
    # spins them in tight loops, drowning the console. Mask each in the
    # overlay so the live boot stays quiet. Installed systems get these
    # back because the masks live only in the tmpfs overlay.
    for svc in mariadb httpd caddy influxdb etcd valkey lighttpd nginx apache postgresql memcached; do
        ln -sf /dev/null "/newroot/etc/systemd/system/${svc}.service"
    done

    # Auto-login root on tty1. Arch-ISO text-mode-live pattern: immediate
    # console access for demonstration / debugging / installer launch. GDM +
    # liveuser autologin is a v1.0+ polish arc on top of this — for now,
    # root shell on tty1 with the build-time root password as the safety net.
    mkdir -p /newroot/etc/systemd/system/getty@tty1.service.d
    cat > /newroot/etc/systemd/system/getty@tty1.service.d/autologin.conf <<'AUTOLOGIN'
[Service]
ExecStart=
ExecStart=-/sbin/agetty -o '-p -f -- \\u' --noclear --autologin root --keep-baud 115200,38400,9600 %I $TERM
AUTOLOGIN

    # default.target -> multi-user.target. graphical.target requires
    # display-manager.service which isn't enabled in this image yet, so
    # attempting graphical would just fail and fall back to multi-user
    # anyway. Explicit symlink avoids the ambiguity.
    ln -sf /usr/lib/systemd/system/multi-user.target /newroot/etc/systemd/system/default.target
fi

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
