#!/bin/bash
# build-squashfs.sh — build the live-ISO root filesystem squashfs from the
# InterGenOS chroot at /mnt/igos.
#
# Real-distro lineage:
#   - archiso's `_build_iso_squashfs` + `airootfs/root/customize_airootfs.sh`
#   - debian-live's `lb_binary_rootfs`
#   - lorax's livemedia-creator template chain
#
# The flow:
#   1. Mount /proc /sys /dev /run inside chroot
#   2. Run customize-airootfs hooks inside chroot (CA trust, caches, presets,
#      ldconfig, schema/icon/desktop databases) — this is where the systemd
#      presets we ship (90-gdm.preset, 90-nftables.preset, ...) get activated
#      via `systemctl preset-all`, creating the display-manager symlink and
#      the .wants/ links for the rest.
#   3. Clean runtime trash (logs, tmp, caches, machine-id reset)
#   4. Unmount chroot pseudo-fs
#   5. mksquashfs with content-exclusions that PRESERVE empty mount-point
#      directories (/sys /proc /dev /run /tmp as empty dirs in the output)
#
# Usage:
#   sudo ./build-squashfs.sh
#
# Optional env:
#   CHROOT=/mnt/igos              # path to the InterGenOS chroot
#   OUTPUT=<chroot>/mnt/intergenos/build/filesystem.squashfs
#   COMP=xz                       # mksquashfs compressor
#   JOBS=$(nproc)                 # parallel mksquashfs workers
#   SKIP_CUSTOMIZE=0              # set 1 to skip the customize-airootfs hooks
#   SOURCE_DATE_EPOCH=<unix>      # reproducible-build epoch (falls back to now)
#
# Idempotency notes:
#   - Safe to re-run. Mounts are guarded with `mountpoint -q`. Customize hooks
#     are idempotent (the tools they invoke handle re-runs).
#   - The unmount step uses `umount -l` (lazy) defensively in case anything
#     inside the chroot is still holding a reference.

set -euo pipefail

CHROOT="${CHROOT:-/mnt/igos}"
OUTPUT="${OUTPUT:-${CHROOT}/mnt/intergenos/build/filesystem.squashfs}"
COMP="${COMP:-xz}"
JOBS="${JOBS:-$(nproc)}"
SKIP_CUSTOMIZE="${SKIP_CUSTOMIZE:-0}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"

LOG_PREFIX="[build-squashfs]"
log()  { echo "$LOG_PREFIX $*"; }
warn() { echo "$LOG_PREFIX [WARN] $*" >&2; }
die()  { echo "$LOG_PREFIX [FATAL] $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (need to chroot + mount inside)"
[ -d "$CHROOT" ]      || die "chroot directory not found: $CHROOT"
[ -x "$CHROOT/bin/bash" ] || die "chroot does not appear bootable: missing $CHROOT/bin/bash"

OUTPUT_DIR=$(dirname "$OUTPUT")
mkdir -p "$OUTPUT_DIR"

log "chroot:           $CHROOT"
log "output:           $OUTPUT"
log "compressor:       $COMP"
log "parallel jobs:    $JOBS"
log "SOURCE_DATE_EPOCH: $SOURCE_DATE_EPOCH"
log ""

# ----------------------------------------------------------------------------
# Step 1: Mount pseudo-fs inside chroot
# ----------------------------------------------------------------------------
log "[1/5] mounting pseudo-fs inside chroot..."

mount_if_needed() {
    local mnt="$1" type="$2" src="$3" opts="${4:-}"
    if mountpoint -q "$mnt"; then
        log "  $mnt already mounted, skipping"
    else
        mount -t "$type" ${opts:+-o "$opts"} "$src" "$mnt"
        log "  mounted $type at $mnt"
    fi
}

mkdir -p "$CHROOT"/{proc,sys,dev,run}
mount_if_needed "$CHROOT/proc" proc proc
mount_if_needed "$CHROOT/sys"  sysfs sysfs
mount_if_needed "$CHROOT/dev"  devtmpfs udev "mode=0755,nr_inodes=0"
mount_if_needed "$CHROOT/run"  tmpfs tmpfs "mode=0755,nr_inodes=800k"

# Bind /dev/pts for chroot exec sanity (some scripts expect it).
if [ -d "$CHROOT/dev/pts" ] && ! mountpoint -q "$CHROOT/dev/pts"; then
    mount -t devpts devpts "$CHROOT/dev/pts" -o "gid=5,mode=620"
    log "  mounted devpts at $CHROOT/dev/pts"
fi

# ----------------------------------------------------------------------------
# Cleanup trap — always unmount even on failure.
# ----------------------------------------------------------------------------
cleanup_mounts() {
    log "cleanup: unmounting chroot pseudo-fs..."
    for mnt in "$CHROOT/dev/pts" "$CHROOT/run" "$CHROOT/dev" "$CHROOT/sys" "$CHROOT/proc"; do
        if mountpoint -q "$mnt"; then
            umount -l "$mnt" || warn "lazy-unmount failed on $mnt (will be reaped by VM reboot)"
        fi
    done
}
trap cleanup_mounts EXIT

# ----------------------------------------------------------------------------
# Step 2: Customize-airootfs hooks (chroot context)
# ----------------------------------------------------------------------------
if [ "$SKIP_CUSTOMIZE" = "1" ]; then
    log "[2/5] customize-airootfs hooks SKIPPED (SKIP_CUSTOMIZE=1)"
else
    log "[2/5] running customize-airootfs hooks inside chroot..."

    chroot "$CHROOT" /bin/bash -eu <<'CUSTOMIZE_AIROOTFS'
log() { echo "  [airootfs] $*"; }

# --- CA trust bundle -------------------------------------------------------
# Wave B.1 — verify /etc/ssl/certs/ca-certificates.crt is present + non-empty
# so curl/wget/git over TLS in the live boot can validate certificates against
# Mozilla's root store. The ca-certificates package (packages/core/ca-certificates)
# ships the curl.se snapshot of cacert.pem at this exact path on install, so
# the file should already be in place by squashfs build time. If it's missing
# or empty, halt — silently shipping a TLS-broken ISO is worse than a build
# failure.
CABUNDLE=/etc/ssl/certs/ca-certificates.crt
if [ -s "$CABUNDLE" ]; then
    log "CA bundle present: $CABUNDLE ($(wc -c < "$CABUNDLE") bytes)"
elif command -v update-ca-certificates >/dev/null 2>&1; then
    log "CA bundle missing — running update-ca-certificates --fresh"
    update-ca-certificates --fresh 2>&1 | tail -3 || true
    [ -s "$CABUNDLE" ] || { echo "  [airootfs] FATAL: CA bundle still empty after update-ca-certificates" >&2; exit 1; }
else
    echo "  [airootfs] FATAL: CA bundle missing AND update-ca-certificates unavailable — ca-certificates package not installed?" >&2
    exit 1
fi

# --- Dynamic linker cache --------------------------------------------------
log "ldconfig"
ldconfig

# --- GLib/GTK/icon/desktop caches -----------------------------------------
# Each cache only refreshes if its corresponding directory exists; tolerate
# missing tools or directories on minimal-build profiles.
if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
    log "glib-compile-schemas"
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>&1 | tail -3 || true
fi

if command -v update-desktop-database >/dev/null 2>&1 && [ -d /usr/share/applications ]; then
    log "update-desktop-database"
    update-desktop-database -q /usr/share/applications || true
fi

if command -v update-mime-database >/dev/null 2>&1 && [ -d /usr/share/mime ]; then
    log "update-mime-database"
    update-mime-database /usr/share/mime 2>&1 | tail -3 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d /usr/share/icons/hicolor ]; then
    log "gtk-update-icon-cache (hicolor)"
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>&1 | tail -3 || true
fi

if command -v fc-cache >/dev/null 2>&1; then
    log "fc-cache"
    fc-cache -f 2>&1 | tail -3 || true
fi

# --- Systemd preset activation --------------------------------------------
# This is what consumes /usr/lib/systemd/system-preset/*.preset (the files
# we ship from packages like gdm + nftables), creating the .wants/ symlinks
# (including /etc/systemd/system/display-manager.service -> gdm.service via
# our 90-gdm.preset).
if command -v systemctl >/dev/null 2>&1; then
    log "systemctl preset-all"
    systemctl preset-all 2>&1 | tail -5 || true
fi

# --- Man-page index --------------------------------------------------------
# mandb is large + slow; skip if not installed. Operators wanting it can
# run `mandb -c` on first boot.
if command -v mandb >/dev/null 2>&1; then
    # /var/cache/man/ is normally created at boot by systemd-tmpfiles via
    # /usr/lib/tmpfiles.d/man-db.conf. We run mandb pre-boot inside the
    # chroot, so apply that rule manually here — otherwise mandb fails with
    # "can't create index cache ... No such file or directory".
    if [ -f /usr/lib/tmpfiles.d/man-db.conf ] && command -v systemd-tmpfiles >/dev/null 2>&1; then
        systemd-tmpfiles --create /usr/lib/tmpfiles.d/man-db.conf 2>&1 | tail -3 || true
    fi
    log "mandb -q (background-quiet)"
    mandb -q 2>&1 | tail -3 || true
fi

CUSTOMIZE_AIROOTFS

    log "  customize-airootfs hooks complete"
fi

# ----------------------------------------------------------------------------
# Step 3: Clean runtime trash
# ----------------------------------------------------------------------------
log "[3/5] cleaning runtime trash..."

# Truncate logs (don't delete the files — services may have open fds).
if [ -d "$CHROOT/var/log" ]; then
    find "$CHROOT/var/log" -type f -exec truncate -s 0 {} + 2>/dev/null || true
    log "  truncated $CHROOT/var/log/*"
fi

# Clean /tmp + /var/tmp (preserving the directories themselves).
for d in "$CHROOT/tmp" "$CHROOT/var/tmp"; do
    [ -d "$d" ] && find "$d" -mindepth 1 -delete 2>/dev/null || true
done
log "  emptied /tmp and /var/tmp"

# Reset machine-id. Real distros write the literal string "uninitialized" so
# systemd-machine-id-setup generates a real one non-interactively on first
# boot of an installed system. Live ISO's init.sh overlay writes a real ID
# per-boot, so this default is correct for both paths.
echo "uninitialized" > "$CHROOT/etc/machine-id"
log "  reset /etc/machine-id to 'uninitialized' (installed-system default)"

# Clear bash histories that may have leaked from build operations.
rm -f "$CHROOT/root/.bash_history"
find "$CHROOT/home" -maxdepth 2 -name '.bash_history' -delete 2>/dev/null || true

# ----------------------------------------------------------------------------
# Step 4: Unmount pseudo-fs (via trap on exit; do an early unmount here so
#         mksquashfs sees clean empty dirs instead of bind-mount artifacts).
# ----------------------------------------------------------------------------
log "[4/5] unmounting chroot pseudo-fs..."
cleanup_mounts
trap - EXIT   # trap fulfilled; clear to avoid double-unmount on script exit

# Verify the mount-point directories survived as EMPTY dirs in the chroot.
# This is what real distros do — empty /sys /proc /dev /run /tmp as part of
# the rootfs so init.sh's `mount --move` finds destinations.
for mnt in proc sys dev run tmp; do
    if [ ! -d "$CHROOT/$mnt" ]; then
        warn "  $CHROOT/$mnt MISSING — recreating as empty dir"
        mkdir -p "$CHROOT/$mnt"
    fi
done

# ----------------------------------------------------------------------------
# Step 4.5: pre-squashfs audit — verify every declared package landed
# ----------------------------------------------------------------------------
# Each packages/<tier>/<name>/package.yml declares verify_paths: — load-
# bearing files the package produces. Audit fails if any are missing from
# the chroot. This catches the linux-firmware-class regression where a
# package recipe exists in tree but the build silently produced no files
# (orchestrator-skip-built footgun, build-host-state-dependency, etc.).
# See feedback_orchestrator_skip_built_footgun memory.
AUDIT_SCRIPT="$(dirname "$0")/pre-squashfs-audit.py"
if [ -x "$AUDIT_SCRIPT" ] || [ -f "$AUDIT_SCRIPT" ]; then
    log "[4.5/5] pre-squashfs audit (verify_paths) against chroot..."
    # Resolve packages-dir relative to the script's project root (one level up).
    PKGS_DIR="$(cd "$(dirname "$0")/.." && pwd)/packages"
    if python3 "$AUDIT_SCRIPT" --packages-dir "$PKGS_DIR" --chroot "$CHROOT" --quiet; then
        log "  audit PASS — all declared verify_paths present"
    else
        rc=$?
        log "  audit FAILED with exit $rc — refusing to build squashfs"
        log "  Either fix the regression (build the missing packages) or"
        log "  correct the verify_paths declarations. Run the audit script"
        log "  directly to see the full diagnostic."
        exit $rc
    fi
else
    log "[4.5/5] pre-squashfs audit SKIPPED (script not found at $AUDIT_SCRIPT)"
fi

# ----------------------------------------------------------------------------
# Step 5: mksquashfs
# ----------------------------------------------------------------------------
log "[5/5] running mksquashfs..."
log "       (this is the slow step — 3-10 minutes depending on tier scope)"

# Stale output: mksquashfs APPENDS by default unless told otherwise. Use
# `-noappend` to force a fresh filesystem.
NOAPPEND="-noappend"
[ ! -f "$OUTPUT" ] && NOAPPEND=""   # nothing to append to anyway

# Exclusion semantics: mksquashfs's `-e <path>` excludes the path entry +
# everything under it. To preserve mount-point dirs we use `-e <path>/*` with
# `-wildcards`, which excludes CONTENTS while leaving the directory intact.
#
# Excluded entirely (path + contents):
#   - mnt/intergenos    — build tree + sources, not part of installed system
#   - sources           — LFS build sources (if any leaked into root)
#   - var/cache         — package-manager + tool caches; rebuilt at first use
#   - var/log/journal/* — wipe journal but keep var/log/ structure
#   - root/.bash_history, home/*/.bash_history — already removed in step 3
#
# Excluded contents-only (directory preserved as empty mount point):
#   - tmp/*             — runtime tmpfs target
#   - var/tmp/*         — package-build tmpfs target
#   - proc/*, sys/*     — defensive (should be empty post-unmount anyway)
#   - dev/*, run/*      — defensive (same)

mksquashfs "$CHROOT" "$OUTPUT" \
    $NOAPPEND \
    -comp "$COMP" \
    -b 1M \
    -Xbcj x86 \
    -processors "$JOBS" \
    -no-progress \
    -wildcards \
    -e mnt/intergenos \
    -e sources \
    -e var/cache \
    -e var/log/journal \
    -e 'tmp/*' \
    -e 'var/tmp/*' \
    -e 'proc/*' \
    -e 'sys/*' \
    -e 'dev/*' \
    -e 'run/*' \
    -e 'gid_Module_*' \
    -e 'root/.bash_history' \
    -e 'home/*/.bash_history'

# Post-build sanity check: verify the mount-point dirs are present in the
# output (this is the regression detector for feedback_mksquashfs_keep_pseudofs_dirs).
log ""
log "verifying mount-point directories in output..."
MISSING=""
for mnt in proc sys dev run tmp; do
    if ! unsquashfs -l "$OUTPUT" 2>/dev/null | grep -qE "^squashfs-root/${mnt}\$"; then
        MISSING="$MISSING $mnt"
    fi
done
if [ -n "$MISSING" ]; then
    die "mount-point dirs MISSING from squashfs:$MISSING — regression of feedback_mksquashfs_keep_pseudofs_dirs"
fi
log "  all mount-point dirs present: /proc /sys /dev /run /tmp"

# Summary.
SIZE_MB=$(($(stat -c%s "$OUTPUT") / 1024 / 1024))
SHA=$(sha256sum "$OUTPUT" | awk '{print $1}')
log ""
log "DONE."
log "  path:   $OUTPUT"
log "  size:   ${SIZE_MB} MB"
log "  sha256: $SHA"
