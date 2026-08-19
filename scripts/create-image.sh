#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS — Package chroot into bootable disk image
#
# Takes the completed chroot at /mnt/igos and creates a bootable disk
# image. Supports both VM (qcow2) and bare metal (raw) targets.
#
# Must run on the HOST (not inside the chroot).
# Requires: qemu-img, qemu-nbd, parted, mkfs.ext4, dosfstools (mkfs.fat)
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/create-image.sh <output-path> [disk-size]
#
# This produces an UNSIGNED, Secure-Boot-OFF disk image for VM / dev testing
# ONLY. It is NOT a release medium and must never be dd'd to a USB stick as an
# installer — the bootable USB / real-hardware installer is the SIGNED live ISO
# from phase_iso (build/*.iso), written per the dd discipline (RM=1/TRAN=usb +
# read-back verify). See DEVELOPMENT-FRAMEWORK §6.
#
# Examples (dev VM only):
#   # qcow2 for a libvirt VM:
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.qcow2 500G
#
#   # raw disk to attach to a dev VM:
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.img 64G

set -euo pipefail

CHROOT=/mnt/igos
IMAGE="${1:?Usage: create-image.sh <output-path.qcow2> [disk-size]}"
DISK_SIZE="${2:-500G}"
NBD_DEV=/dev/nbd0
MOUNT_POINT=/mnt/image-root

log() {
    echo "[IMAGE] $*"
}

err() {
    echo "[ERROR] $*" >&2
}

cleanup() {
    log "Cleaning up..."
    umount "${MOUNT_POINT}/sys" 2>/dev/null || true
    umount "${MOUNT_POINT}/proc" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev/pts" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev" 2>/dev/null || true
    umount "$MOUNT_POINT" 2>/dev/null || true
    qemu-nbd --disconnect "$NBD_DEV" 2>/dev/null || true
}

trap cleanup EXIT

# ============================================================================
# Preflight checks
# ============================================================================

if [ "$(id -u)" -ne 0 ]; then
    err "Must run as root"
    exit 1
fi

if [ ! -d "$CHROOT/usr/bin" ]; then
    err "Chroot at $CHROOT doesn't look valid (no /usr/bin)"
    exit 1
fi

# Staged-kernel exclusivity (gate wave, decided 2026-07-12):
# the old presence test tolerated a superseded kernel twin, and the
# symlink pick below used to take the FIRST glob match (alphabetical —
# the OLD release sorts first). Exactly one staged kernel, fail-closed.
if ! bash "$(dirname "$0")/preflight-single-kernel.sh" --root "$CHROOT"; then
    err "staged-kernel exclusivity gate failed for $CHROOT (superseded kernel twin? see listing above)"
    exit 1
fi

for tool in qemu-img qemu-nbd parted mkfs.ext4; do
    if ! command -v "$tool" > /dev/null 2>&1; then
        err "Required tool not found: $tool"
        exit 1
    fi
done

# ============================================================================
# Step 1: Create disk image (qcow2 or raw — UNSIGNED, Secure-Boot-OFF, VM/dev-test
# ONLY; NOT an install medium. The bootable USB / installer is the signed live ISO.)
# ============================================================================

# Detect format from file extension
case "$IMAGE" in
    *.qcow2) IMAGE_FORMAT="qcow2" ;;
    *.img|*.raw) IMAGE_FORMAT="raw" ;;
    *) IMAGE_FORMAT="qcow2" ;;  # default to qcow2
esac

log "Creating ${DISK_SIZE} ${IMAGE_FORMAT} image at ${IMAGE}..."
qemu-img create -f "$IMAGE_FORMAT" "$IMAGE" "$DISK_SIZE"

# ============================================================================
# Step 2: Connect image as block device
# ============================================================================

log "Loading nbd module and connecting image..."
modprobe nbd max_part=8
qemu-nbd --connect="$NBD_DEV" -f "$IMAGE_FORMAT" "$IMAGE"

# Wait for device to appear
sleep 1

# ============================================================================
# Step 3: Partition the disk (GPT + BIOS boot)
# ============================================================================

log "Creating partition table (GPT with BIOS + EFI support)..."
parted -s "$NBD_DEV" mklabel gpt
parted -s "$NBD_DEV" mkpart bios_grub 1MiB 2MiB
parted -s "$NBD_DEV" set 1 bios_grub on
parted -s "$NBD_DEV" mkpart ESP fat32 2MiB 514MiB
parted -s "$NBD_DEV" set 2 esp on
parted -s "$NBD_DEV" mkpart root ext4 514MiB 100%

# Wait for partition devices
sleep 1
partprobe "$NBD_DEV" 2>/dev/null || true
sleep 1

# ============================================================================
# Step 4: Format partitions
# ============================================================================

log "Formatting partitions..."
mkfs.fat -F32 -n ESP "${NBD_DEV}p2"
mkfs.ext4 -L intergenos "${NBD_DEV}p3"

# ============================================================================
# Step 5: Mount and copy chroot contents
# ============================================================================

log "Mounting image and copying chroot..."
mkdir -p "$MOUNT_POINT"
mount "${NBD_DEV}p3" "$MOUNT_POINT"

# Use tar to preserve everything correctly
# --one-file-system avoids copying virtual filesystems (/proc, /sys, etc.)
# Progress heartbeat: this copy moves the whole chroot (tens of GB) with zero
# output — dead air reads as a hang (progress-indicator candidate #3,
# 2026-07-18). A background poller reports copied/total every 30s; killed the
# moment the copy finishes, and a dead parent takes the poller with it.
CHROOT_BYTES=$(du -sb --one-file-system "$CHROOT" 2>/dev/null | cut -f1)
COPY_T0=$(date +%s)
(
    while sleep 30; do
        COPIED=$(du -sb "$MOUNT_POINT" 2>/dev/null | cut -f1)
        [ -n "$COPIED" ] && [ -n "$CHROOT_BYTES" ] && [ "$CHROOT_BYTES" -gt 0 ] || continue
        log "  copy progress: $((COPIED / 1073741824))G / $((CHROOT_BYTES / 1073741824))G ($((COPIED * 100 / CHROOT_BYTES))%), elapsed $(( $(date +%s) - COPY_T0 ))s"
    done
) &
COPY_POLL_PID=$!
tar -C "$CHROOT" --one-file-system -cf - . | tar -C "$MOUNT_POINT" -xf -
kill "$COPY_POLL_PID" 2>/dev/null || true
wait "$COPY_POLL_PID" 2>/dev/null || true

log "  Copy complete: $(du -sh "$MOUNT_POINT" | cut -f1) in $(( $(date +%s) - COPY_T0 ))s"

# Fix root directory ownership — tar preserves the chroot's ownership
# which is the build user, not root
chown root:root "$MOUNT_POINT"

# Build-artifact hygiene (3.0-F26): the disk-image tar above copies the whole
# chroot, INCLUDING the /mnt/intergenos build tree (unlike the squashfs/ISO
# path, which excludes mnt/intergenos entirely). In-chroot Python builder runs
# left root-owned __pycache__/*.pyc there (e.g. igos-build/__pycache__/
# license_bundle.cpython-*.pyc), which shipped root-owned and blocked user git
# operations under /mnt/intergenos on target boxes. chroot-enter.sh now sets
# PYTHONDONTWRITEBYTECODE=1 so fresh builds no longer create these, but prune
# defensively here so images built from a pre-existing (already-polluted)
# chroot ship clean too. Scoped to the build tree — runtime bytecode caches
# under /usr are legitimate and left intact.
if [ -d "${MOUNT_POINT}/mnt/intergenos" ]; then
    log "Pruning build-tree bytecode caches under /mnt/intergenos..."
    find "${MOUNT_POINT}/mnt/intergenos" -type d -name __pycache__ -prune -exec rm -rf {} +
    find "${MOUNT_POINT}/mnt/intergenos" -type f -name '*.pyc' -delete
    # Fail-loud verify: a prune that silently failed would ship the
    # root-owned cache anyway — assert the image tree is actually clean.
    LEFTOVER=$(find "${MOUNT_POINT}/mnt/intergenos" \( -type d -name __pycache__ \) -o \( -type f -name '*.pyc' \) | head -5)
    if [ -n "$LEFTOVER" ]; then
        log "ERROR: bytecode cache prune left entries under /mnt/intergenos:"
        log "$LEFTOVER"
        exit 1
    fi
fi

# ============================================================================
# Step 6: Create /etc/fstab
# ============================================================================

log "Writing /etc/fstab..."
# Use PARTUUIDs for portability across VM and bare metal.
# Filesystem UUIDs (blkid UUID=) fail on some hardware at early boot;
# GPT PARTUUIDs are resolved by the kernel directly from the partition table.
ROOT_UUID=$(blkid -s UUID -o value "${NBD_DEV}p3")
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "${NBD_DEV}p3")
ESP_UUID=$(blkid -s UUID -o value "${NBD_DEV}p2")
ESP_PARTUUID=$(blkid -s PARTUUID -o value "${NBD_DEV}p2")
cat > "${MOUNT_POINT}/etc/fstab" << FSTABEOF
# /etc/fstab — InterGenOS
# <file system>                            <mount point>  <type>  <options>              <dump>  <pass>
UUID=${ROOT_UUID}  /              ext4    defaults,noatime       1       1
UUID=${ESP_UUID}  /boot/efi      vfat    fmask=0077,dmask=0077  0       2
FSTABEOF
log "  Root UUID:     ${ROOT_UUID}"
log "  Root PARTUUID: ${ROOT_PARTUUID}"
log "  ESP UUID:      ${ESP_UUID}"

# ============================================================================
# Step 7: Create /etc/default/grub
# ============================================================================

log "Writing GRUB defaults..."
mkdir -p "${MOUNT_POINT}/etc/default"
cat > "${MOUNT_POINT}/etc/default/grub" << GRUBEOF
# GRUB defaults for InterGenOS
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="InterGenOS"
GRUB_CMDLINE_LINUX_DEFAULT=""
GRUB_CMDLINE_LINUX="root=PARTUUID=${ROOT_PARTUUID} rootwait console=tty0 console=ttyS0,115200"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --speed=115200"
GRUB_DISABLE_OS_PROBER=true
GRUBEOF

# ============================================================================
# Step 8: Install GRUB bootloader
# ============================================================================

log "Installing GRUB (BIOS + EFI)..."

# Mount ESP
mkdir -p "${MOUNT_POINT}/boot/efi"
mount "${NBD_DEV}p2" "${MOUNT_POINT}/boot/efi"

# Bind mount host filesystems into the image
mount --bind /dev "${MOUNT_POINT}/dev"
mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"
mount -t proc proc "${MOUNT_POINT}/proc"
mount -t sysfs sysfs "${MOUNT_POINT}/sys"

# Install GRUB for BIOS boot
#
# B5 (USA-1 audit S-W2 closure): qcow2 trust-chain posture is INTENTIONALLY
# different from the ISO trust chain. The ISO chain (shim -> GRUB -> UKI,
# all signed; assembled in phase_iso via scripts/build-iso.sh) is the
# production deliverable that ships to end users. The qcow2 + raw images
# produced by THIS script are pre-installed-system artifacts for VM/test/
# golden-snapshot use; they are NOT published to the mirror (no matches in
# sign-release.sh / publish-* per S-W3 mirror-infra worker). End-user
# production deploy path is ISO -> Forge installer -> installer-managed
# bootloader-setup (which IS the shim+GRUB+UKI signed chain per
# scripts/chroot-build-bootloader.sh).
#
# The grub-install calls below produce a working unsigned chain suitable
# for non-SB testing. If SB-bootable qcow2 ever becomes a requirement,
# this section needs to be rewritten to use the same shim+GRUB+UKI staging
# pattern as phase_iso. Until then: divergence is by-design, documented
# here so reviewers + future maintainers see the intent.
chroot "$MOUNT_POINT" grub-install --target=i386-pc "$NBD_DEV"

# Install GRUB for EFI boot (skip if x86_64-efi modules not built)
if [ -d "${MOUNT_POINT}/usr/lib/grub/x86_64-efi" ]; then
    chroot "$MOUNT_POINT" grub-install --target=x86_64-efi \
        --efi-directory=/boot/efi --bootloader-id=InterGenOS --removable
else
    log "  WARNING: x86_64-efi GRUB modules not found — skipping EFI install"
fi

# Generate GRUB config.
# grub-mkconfig runs inside the chroot where root is mounted via NBD,
# so it detects /dev/nbd0pN as the root device. Override with PARTUUID.
chroot "$MOUNT_POINT" /bin/bash -c \
    "GRUB_DEVICE=PARTUUID=${ROOT_PARTUUID} grub-mkconfig -o /boot/grub/grub.cfg"

# Belt and suspenders: ensure no NBD or UUID root= references leaked through.
# Multi-pass sed: strip any existing root= or PARTUUID= params, then insert
# the correct PARTUUID on all linux command lines.
sed -i -E \
    -e 's/\broot=[^[:space:]]+//g' \
    -e 's/\bPARTUUID=[^[:space:]]+//g' \
    -e "/^[[:space:]]*linux/ s|$| root=PARTUUID=${ROOT_PARTUUID} rootwait|" \
    "${MOUNT_POINT}/boot/grub/grub.cfg"
# Clean up any double spaces left by the removal passes
sed -i -E 's/[[:space:]]+/ /g' "${MOUNT_POINT}/boot/grub/grub.cfg"
log "  GRUB config: all root= replaced with PARTUUID=${ROOT_PARTUUID}"

# Add Intel microcode early-load initrd if the image exists
if [ -f "${MOUNT_POINT}/boot/intel-ucode.img" ]; then
    # Insert initrd /boot/intel-ucode.img on lines that have linux but no existing initrd
    # grub-mkconfig may not know about the microcode image
    sed -i '/^[[:space:]]*linux /a\\tinitrd /boot/intel-ucode.img' \
        "${MOUNT_POINT}/boot/grub/grub.cfg"
    log "  GRUB config: Intel microcode early-load initrd added"
fi

# Add AMD microcode early-load initrd if the image exists (audit F-005 fix)
# The kernel selects the right microcode image at boot based on CPU vendor ID,
# so shipping both Intel and AMD images is safe on either vendor's hardware.
if [ -f "${MOUNT_POINT}/boot/amd-ucode.img" ]; then
    sed -i '/^[[:space:]]*linux /a\\tinitrd /boot/amd-ucode.img' \
        "${MOUNT_POINT}/boot/grub/grub.cfg"
    log "  GRUB config: AMD microcode early-load initrd added"
fi

# Unmount bind mounts and ESP
umount "${MOUNT_POINT}/sys"
umount "${MOUNT_POINT}/proc"
umount "${MOUNT_POINT}/dev/pts"
umount "${MOUNT_POINT}/dev"
umount "${MOUNT_POINT}/boot/efi"

# ============================================================================
# Step 8b: Apply post-deploy fixes for VM boot
# ============================================================================

log "Applying post-deploy fixes..."

# Install all gsettings overrides from the repo
for override in /mnt/intergenos/config/gsettings/*.gschema.override; do
    if [ -f "$override" ]; then
        cp "$override" "${MOUNT_POINT}/usr/share/glib-2.0/schemas/"
        log "  Installed $(basename "$override")"
    fi
done

# Fix sudo setuid bit (tar strips setuid during copy)
if [ -f "${MOUNT_POINT}/usr/bin/sudo" ]; then
    chmod 4755 "${MOUNT_POINT}/usr/bin/sudo"
    log "  sudo setuid bit restored"
fi

# Also fix other setuid binaries that tar strips
for suid_bin in /usr/bin/passwd /usr/bin/chsh /usr/bin/chfn /usr/bin/newgrp \
                /usr/bin/su /usr/bin/mount /usr/bin/umount /usr/bin/chage \
                /usr/bin/expiry /usr/bin/fusermount3 /usr/bin/pkexec \
                /usr/libexec/polkit-agent-helper-1; do
    if [ -f "${MOUNT_POINT}${suid_bin}" ]; then
        chmod 4755 "${MOUNT_POINT}${suid_bin}"
    fi
done
# polkit-agent-helper-1 uses 4711 (execute only, not read)
if [ -f "${MOUNT_POINT}/usr/libexec/polkit-agent-helper-1" ]; then
    chmod 4711 "${MOUNT_POINT}/usr/libexec/polkit-agent-helper-1"
fi
log "  setuid bits restored for all critical binaries"

# D-007 — SSH host keys are generated at first boot, NOT at build time.
# Generating them here would bake the SAME keys into every qcow2 image
# (trivially-exploitable impersonation across every installed system).
# The sshd.service unit at config/systemd/sshd.service:29 has the
# canonical guarded first-boot keygen
# ('test -f /etc/ssh/ssh_host_ed25519_key || ssh-keygen -A').
# The requirement is stated in scripts/check-d007-compliance.sh; the
# shipped sshd posture is
# packages/core/openssh/files/etc/ssh/sshd_config.d/00-intergenos-d007.conf.
log "  SSH host keys: deferred to first boot (D-007 compliance)"

# CA certificates: NO build-time fetch.
# Bundle is deployed hermetically by the `ca-certificates` package, which
# consumes the in-tree-pinned cacert.pem (curl.se snapshot 2026-04-30,
# sha256-locked at build/sources/ca-certificates-2026.04.30.tar.gz per
# commit 789c7e32 "pin derived source artifacts in tree (Item #2)").
# Bundle lands at /etc/ssl/certs/ca-certificates.crt + canonical symlinks
# during phase_core. The previous `make-ca -g` invocation here was the
# parallel-mechanism path that fetched Mozilla's certdata.txt from the
# network — redundant with the pinned bundle AND a moving-target hole in
# build hermeticity. Removed 2026-05-24 as Item #2 follow-on after the
# parallel-mechanism gap surfaced during phase_image. Updates to the
# bundle ship as normal `pkm update` (a deliberate version bump of the
# ca-certificates package; no auto-fetch).

# Generate Intel + AMD CPU microcode early-load images via the canonical
# helper at scripts/build-microcode-cpio.sh. Centralizing the cpio
# assembly here means image-build, chroot-build-bootloader's UKI path, and
# the kernel post-install hook all use the same logic — no drift between
# the GRUB-loaded path (this script) and the UKI-bundled path
# (build-uki.sh + chroot-build-bootloader.sh + post-install.sh).
#
# Helper is staged into the chroot's /tmp temporarily because /mnt/intergenos
# isn't mounted in MOUNT_POINT (this is the assembled image filesystem,
# not the build chroot). cp -p preserves executable bit; rm on the way out
# leaves no trace in the final image.
HELPER_SRC=/mnt/intergenos/scripts/build-microcode-cpio.sh
HELPER_DST=/tmp/build-microcode-cpio.sh.tmp
if [ -x "$HELPER_SRC" ]; then
    cp -p "$HELPER_SRC" "${MOUNT_POINT}${HELPER_DST}"
    chroot "$MOUNT_POINT" /bin/bash -c "OUTPUT_DIR=/boot $HELPER_DST" >/dev/null 2>&1 || true
    rm -f "${MOUNT_POINT}${HELPER_DST}"
    if [ -f "${MOUNT_POINT}/boot/intel-ucode.img" ]; then
        log "  Intel microcode image generated (/boot/intel-ucode.img)"
    else
        log "  Intel microcode: iucode_tool or firmware not installed in image — skipping"
    fi
    if [ -f "${MOUNT_POINT}/boot/amd-ucode.img" ]; then
        log "  AMD microcode image generated (/boot/amd-ucode.img)"
    else
        log "  AMD microcode: no amd-ucode blobs found in image — skipping"
    fi
else
    log "  WARNING: $HELPER_SRC missing — microcode cpios not generated"
fi

# Create kernel symlink (GRUB expects /boot/vmlinuz) — fail-closed pick
# (gate wave, decided 2026-07-12): the old `head -1` silently
# symlinked the first glob match, which with a superseded twin present is
# the OLD release (alphabetical order). Exactly one match or halt.
mapfile -t _staged_kernels < <(ls "${MOUNT_POINT}/boot"/vmlinuz-* 2>/dev/null)
if [ "${#_staged_kernels[@]}" -ne 1 ]; then
    err "expected exactly one /boot/vmlinuz-* in the image, found ${#_staged_kernels[@]}: ${_staged_kernels[*]:-<none>}"
    exit 1
fi
KERNEL="${_staged_kernels[0]}"
if [ ! -L "${MOUNT_POINT}/boot/vmlinuz" ]; then
    ln -sf "$(basename "$KERNEL")" "${MOUNT_POINT}/boot/vmlinuz"
    log "  Kernel symlink: /boot/vmlinuz -> $(basename "$KERNEL")"
fi

# Clang config + resolved no-mdns + wireplumber restart blocks deleted
# per plan v2 (2026-05-27, bilateral review APPROVE-clean at 06:41Z).
# - /etc/clang/{clang,clang++}.cfg: owned by packages/core/llvm/ build.sh
#   post_install (same content). verify_paths in llvm/package.yml made
#   ownership explicit.
# - /etc/systemd/resolved.conf.d/no-mdns.conf: owned by packages/core/
#   intergenos-base-files/ (Block E).
# - /etc/systemd/user/wireplumber.service.d/restart.conf: owned by
#   packages/core/intergenos-base-files/ (Block E).
# All three lands on every install path now (live ISO + Forge target)
# via the package archive extraction. Half-rename-drift hazard from
# duplicated heredocs eliminated structurally.

# Create XDG user directories for the default user
IMAGE_USER="${IMAGE_USER:-intergenos}"
if chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    chroot "$MOUNT_POINT" su - "$IMAGE_USER" -c 'xdg-user-dirs-update 2>/dev/null' || true
    log "  XDG user directories created for ${IMAGE_USER}"
fi

# Create swapfile (2GB)
if [ ! -f "${MOUNT_POINT}/swapfile" ]; then
    fallocate -l 2G "${MOUNT_POINT}/swapfile" 2>/dev/null || \
        dd if=/dev/zero of="${MOUNT_POINT}/swapfile" bs=1M count=2048 2>/dev/null
    chmod 600 "${MOUNT_POINT}/swapfile"
    mkswap "${MOUNT_POINT}/swapfile" > /dev/null
    echo '/swapfile none swap sw 0 0' >> "${MOUNT_POINT}/etc/fstab"
    log "  2GB swapfile created"
fi

# Enable serial console for VM management
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/serial-getty@.service \
        /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service
'

# Enable networking (NetworkManager + resolved). NetworkManager owns
# networking on InterGenOS; systemd-networkd is redundant and its wait-online
# unit delays network-online.target (gates GDM). Kept consistent with the
# shipped system, where 80-intergenos-enable.preset enables NM + disables
# networkd. (GBC001.5, 2026-06-05 — was systemd-networkd.)
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/NetworkManager.service \
        /etc/systemd/system/multi-user.target.wants/NetworkManager.service
    ln -sf /usr/lib/systemd/system/systemd-resolved.service \
        /etc/systemd/system/multi-user.target.wants/systemd-resolved.service
'

# Create DHCP network config
mkdir -p "${MOUNT_POINT}/etc/systemd/network"
cat > "${MOUNT_POINT}/etc/systemd/network/10-dhcp.network" << 'NETEOF'
[Match]
Name=en*

[Network]
DHCP=yes
NETEOF

# Set up DNS resolution via systemd-resolved
ln -sf /run/systemd/resolve/stub-resolv.conf "${MOUNT_POINT}/etc/resolv.conf"

# Set root password — REQUIRED via ROOT_PASSWORD env var. No default.
# D-007 — root is locked on every shipped artifact (ISO, qcow2,
# installed system). No password, no SSH-as-root, no console-as-root
# via known credential. Privilege escalation routes exclusively through
# the user-chosen sudo-capable account (Forge TUI/GUI install) or the
# build-pipeline-created sudo-capable user (qcow2 IMAGE_USER, below).
# The pre-D-007 ROOT_PASSWORD env-var requirement is retired; the
# previously-required --root-password flag on build-intergenos.sh is
# no longer used and can be removed in a follow-on cleanup.
# The requirement is stated in scripts/check-d007-compliance.sh.
chroot "$MOUNT_POINT" passwd -l root
log "  root: locked (D-007 — sudo-only escalation path)"

# Create default user account — IMAGE_USER name can default; password CANNOT.
IMAGE_USER="${IMAGE_USER:-intergenos}"
if [ -z "${IMAGE_USER_PASSWORD:-}" ]; then
    err "IMAGE_USER_PASSWORD env var is required (no default permitted)"
    err "  Invoke via build-intergenos.sh --user-password <value> ..."
    exit 1
fi
# The live user needs the Chronicle access group for the same reason the
# installed system's user does: the backup engine's socket is root:chronicle
# mode 0660, so an account outside the group cannot open the backup
# application at all. The group is declared by intergenos-backup's
# /usr/lib/sysusers.d/chronicle.conf, but systemd-sysusers does not run
# against this image until phase_squashfs — after this point — so the group is
# created here if it is not already present. useradd -G aborts the whole
# account creation on a group that does not exist, which would leave the live
# medium with no user at all; a loud failure here is far better than that.
IMAGE_USER_GROUPS="wheel,video,audio,input,chronicle"
if ! chroot "$MOUNT_POINT" getent group chronicle > /dev/null 2>&1; then
    if ! chroot "$MOUNT_POINT" groupadd -r chronicle; then
        err "Failed to create the 'chronicle' group in the image"
        err "  The live backup application cannot reach its engine without it."
        exit 1
    fi
    log "  Group 'chronicle' created (Chronicle engine socket access)"
fi
if ! chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    chroot "$MOUNT_POINT" useradd -m -G "$IMAGE_USER_GROUPS" -s /bin/bash "$IMAGE_USER"
    # Pre-hash the password and use chpasswd -e (encrypted input) to bypass
    # PAM's password stack. pwquality (T0-4-B, /etc/pam.d/system-password)
    # enforces complexity rules on USER-CHOSEN passwords — Forge install
    # lanes pre-validate at installer/backend/users.py:55 before submitting
    # to chpasswd, so the user gets a friendly re-prompt on weak input.
    # D-007 mandates the live-ISO bootstrap credential pair by directive,
    # not by user choice — bypassing the password stack here writes the
    # directive-mandated hash directly to /etc/shadow. Runtime password
    # changes (passwd, gnome-control-center, Forge) continue to enforce
    # pwquality unchanged.
    HASHED_PW=$(printf '%s' "$IMAGE_USER_PASSWORD" | chroot "$MOUNT_POINT" openssl passwd -6 -stdin)
    if [ -z "$HASHED_PW" ]; then
        err "Failed to hash IMAGE_USER_PASSWORD via openssl passwd -6"
        exit 1
    fi
    echo "${IMAGE_USER}:${HASHED_PW}" | chroot "$MOUNT_POINT" chpasswd -e
    # Copy skel files
    if [ -d "${MOUNT_POINT}/etc/skel" ]; then
        cp -a "${MOUNT_POINT}/etc/skel/." "${MOUNT_POINT}/home/${IMAGE_USER}/"
        chroot "$MOUNT_POINT" chown -R "${IMAGE_USER}:${IMAGE_USER}" "/home/${IMAGE_USER}"
    fi
    log "  User '${IMAGE_USER}' created (groups: ${IMAGE_USER_GROUPS})"
else
    # The account survived from an earlier image build. Group membership is
    # not part of "the user exists", so grant it explicitly rather than
    # assuming the earlier build's group list matches this one.
    chroot "$MOUNT_POINT" usermod -aG "$IMAGE_USER_GROUPS" "$IMAGE_USER"
    log "  User '${IMAGE_USER}' already present; groups refreshed (${IMAGE_USER_GROUPS})"
fi

# NOTE: TTY1 first-boot password greeter (Path 3 of the 2026-04-29 S1/S2
# default-credentials remediation) DELETED 2026-05-22 per audit-row D-002
# Path B execution. The greeter became architecturally orphaned after
# D-007 ratified the credential model: live ISO ships fixed intergenos:
# intergenos credentials (Debian Live precedent); Forge install lanes
# prompt the user for username+password during install; root is locked on
# shipped systems (passwd -l root); SSH host keys are first-boot-generated.
# With credentials handled at install + live-boot time, the TTY1 greeter
# became a redundant double-prompt (Forge already collected the password,
# then the greeter would prompt for it again on first boot).

# Set the graphical default target for desktop boot.
#
# The `systemctl enable gdm` that used to sit beside this line was removed: the
# gdm package ships its own preset file (90-gdm.preset, `enable gdm.service`)
# and the `systemctl preset-all` this phase runs against the chroot already
# applies it, so the enable here was a second voice for a decision the preset
# owns. Measured before removing it, against a root holding only the preset
# files and the real gdm.service and with no enable called anywhere: the unit
# starts disabled with no enablement symlinks, and preset-all alone resolves it
# to enabled and writes display-manager.service. Decided 2026-08-19.
#
# set-default is NOT preset-owned — no preset file can express the default
# target — so it stays here.
if [ -f "${MOUNT_POINT}/usr/lib/systemd/system/gdm.service" ]; then
    chroot "$MOUNT_POINT" /bin/bash -c '
        systemctl set-default graphical.target
    '
    log "  Default target set to graphical (gdm enablement is preset-owned)"
fi

# /tmp/.X11-unix is managed by upstream systemd-tmpfiles via
# /usr/lib/tmpfiles.d/x11.conf, which ships with systemd and contains
# verbatim: "D! /tmp/.X11-unix 1777 root root 10d" plus matching D!
# entries for /tmp/.ICE-unix /tmp/.XIM-unix /tmp/.font-unix. The D!
# directive creates the directory at boot with the specified perms +
# cleans/recreates if state diverges. No InterGenOS-shipped /etc
# override is needed; the previous block at this location (removed
# in v7 commit 4 of 4 per D6, decided 2026-05-21) wrote a
# weaker 'd' (no clean-on-boot) directive that ALSO shadowed upstream's
# .ICE-unix + .XIM-unix + .font-unix rules and emitted "Duplicate line
# for path /tmp/.X11-unix, ignoring" on every boot since the original
# block shipped. Cross-distro convention (Fedora/Ubuntu/Debian/Arch/
# openSUSE): all use upstream /usr/lib/tmpfiles.d/x11.conf as-is
# without /etc overrides.

# Ensure /tmp itself has correct permissions
chmod 1777 "${MOUNT_POINT}/tmp"

# Build icon caches, font caches, and compile GSettings schemas
chroot "$MOUNT_POINT" /bin/bash -c '
    # GSettings schemas
    if [ -d /usr/share/glib-2.0/schemas ]; then
        glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null
    fi

    # Icon caches
    for theme_dir in /usr/share/icons/*/; do
        if [ -f "${theme_dir}index.theme" ]; then
            gtk-update-icon-cache -q "${theme_dir}" 2>/dev/null || true
        fi
    done

    # Font cache
    if command -v fc-cache >/dev/null 2>&1; then
        fc-cache -f 2>/dev/null
    fi

    # GIO module cache
    if command -v gio-querymodules >/dev/null 2>&1; then
        gio-querymodules /usr/lib/gio/modules 2>/dev/null || true
    fi

    # gdk-pixbuf loader cache
    if command -v gdk-pixbuf-query-loaders >/dev/null 2>&1; then
        gdk-pixbuf-query-loaders --update-cache 2>/dev/null || true
    fi

    # MIME database
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database /usr/share/mime 2>/dev/null || true
    fi

    # Desktop database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications 2>/dev/null || true
    fi

    # Linker cache — must run after all libraries are installed
    ldconfig 2>/dev/null

    # No service enablement here. Which services are on by default is decided
    # in one place — intergenos-base-files'
    # /usr/lib/systemd/system-preset/80-intergenos-enable.preset, applied by the
    # `systemctl preset-all` this phase already runs against the chroot before
    # the image root is populated from it. This block used to enable avahi,
    # cups and bluetooth and disable NetworkManager-wait-online here, after
    # that pass and against the image root instead of the chroot, so the image
    # this script wrote disagreed with the ISO and with every installed system
    # for the same tree. Two of those four decisions also contradicted the
    # recipes that shipped the same units. Decided 2026-08-19: the preset files
    # own default enablement; this script no longer has a say.
    #
    # The retired NetworkManager-wait-online disable carried the reason "it
    # blocks boot indefinitely". The unit as shipped sets
    # Environment=NM_ONLINE_TIMEOUT=60 and is Type=oneshot, so it is bounded at
    # 60 seconds and boot continues afterwards; and it only runs at all when
    # something pulls network-online.target in, which nothing does on a default
    # install. The preset entry for that unit records both measurements.
    #
    # sshd stays off, and that is unchanged by this edit: it is off because no
    # preset file enables it, so the 99- catch-all disables it. SSH is opt-in
    # (decided 2026-05-22, amending the earlier sshd-default position) — the
    # installer offers it during setup, defaulting to off, and the welcome
    # application offers the same switch afterwards. A live user can still run
    # `systemctl enable --now sshd` by hand for debugging.

    # Disable remote-fs.target and machines.target — not needed for desktop,
    # can cause boot hangs waiting for network mounts
    rm -f /etc/systemd/system/multi-user.target.wants/remote-fs.target 2>/dev/null || true
    rm -f /etc/systemd/system/multi-user.target.wants/machines.target 2>/dev/null || true
' 2>/dev/null
log "  Caches built (icons, fonts, schemas, GIO, pixbuf, MIME, desktop, ldconfig)"

# /etc/modprobe.d/disable-algif.conf (Copy Fail CVE-2026-31431 mitigation)
# moved to packages/core/intergenos-base-files/ per plan v2 (2026-05-27,
# Class 11 chroot-state-not-packaged closure). Lands on every install
# path via the package archive extraction.

# Locale — en_US.UTF-8 as build default. Forge installer will override
# with the user's choice. Without this, LANG is empty and many programs
# fall back to POSIX/C locale (broken sorting, missing translations).
echo 'LANG=en_US.UTF-8' > "${MOUNT_POINT}/etc/locale.conf"
log "  Locale set to en_US.UTF-8 (Forge placeholder)"

# Timezone — UTC is the correct default for a distribution image.
# Forge installer will set the user's actual timezone.
ln -sf /usr/share/zoneinfo/UTC "${MOUNT_POINT}/etc/localtime"
log "  Timezone set to UTC (Forge placeholder)"

# Rust toolchain PATH — rustc is installed to /opt/rustc/bin
mkdir -p "${MOUNT_POINT}/etc/profile.d"
cat > "${MOUNT_POINT}/etc/profile.d/rustc.sh" << 'RUSTEOF'
# Rust toolchain installed to /opt/rustc
if [ -d /opt/rustc/bin ]; then
    export PATH="/opt/rustc/bin:$PATH"
fi
RUSTEOF
chmod 644 "${MOUNT_POINT}/etc/profile.d/rustc.sh"
log "  Rust toolchain PATH configured (/opt/rustc/bin)"

log "  Post-deploy fixes applied (serial console, networking, DNS, root password, GDM, services, caches)"

# ============================================================================
# Step 8c: Install pkm package manager
# ============================================================================

log "Installing pkm package manager..."
if [ -d "/mnt/intergenos/pkm" ]; then
    mkdir -p "${MOUNT_POINT}/usr/lib/pkm"
    cp /mnt/intergenos/pkm/*.py "${MOUNT_POINT}/usr/lib/pkm/"

    # Create /usr/bin/pkm wrapper
    cat > "${MOUNT_POINT}/usr/bin/pkm" << 'PKMEOF'
#!/bin/bash
exec /usr/bin/python3 -m pkm "$@"
PKMEOF
    chmod 755 "${MOUNT_POINT}/usr/bin/pkm"

    # Create pkm data directories
    mkdir -p "${MOUNT_POINT}/var/lib/pkm/packages"
    mkdir -p "${MOUNT_POINT}/var/cache/pkm/packages"
    mkdir -p "${MOUNT_POINT}/etc/pkm"

    log "  pkm installed to /usr/lib/pkm, wrapper at /usr/bin/pkm"
else
    log "  pkm source not found — skipping"
fi

# ============================================================================
# Step 8d: Theming -- owned entirely by tier:desktop packages
# ============================================================================
# The former install-theming.sh chroot-invocation block was retired 2026-05-22
# evening CST. Every theme/icon/cursor previously extracted by the script is
# now shipped by a dedicated tier:desktop package (orchis-theme,
# whitesur-{gtk,icon}-theme, graphite-gtk-theme, fluent-{gtk,icon}-theme,
# nordic-theme, dracula-gtk-theme, catppuccin-gtk-theme, intergenos-theme,
# adw-gtk3-theme, papirus-icon-theme, tela-icon-theme, cybernetic-icon-theme,
# bibata-cursor-theme, macos-cursor-theme, phinger-cursors). The 4 GNOME
# Shell extensions packages (intergenos-extensions-{appearance,layout,
# productivity,utilities}) own the extensions surface. user-theme owns its
# own UUID. Closes the install-theming.sh divestment trajectory documented
# in 6+ prior directive cross-refs (J-001 / J-010 / D-006 / D-011 / D-015 /
# Item A).
#
# No chroot bind-mount needed here -- packages install their own assets
# during the desktop-tier build, before phase_image runs.

# Burn My Windows profile propagation to ${IMAGE_USER}'s home is owned by
# the generic `cp -a /etc/skel/. -> /home/${IMAGE_USER}/` step in Step 5
# (user creation), not by a special-cased block here. The previous block
# at this location hardcoded `default.conf` as the profile filename — a
# leftover from the retired `install-theming.sh` era. burn-my-windows v48
# uses microsecond-ID profile names (e.g. `1775735161994164.conf`); the
# packaging path (`packages/core/intergenos-default-settings/build.sh`)
# was deliberately refactored to ship the upstream-native filename with
# a FATAL assert enforcing it. The hardcoded-`default.conf` cp was the
# parallel-mechanism path the 2026-05-22 install-theming.sh retire sweep
# (commit f08981ab) missed; surfaced 2026-05-24 phase_image run as
# `cp: cannot stat .../profiles/default.conf`. Block removed; the generic
# /etc/skel/. propagation at Step 5 already copies the
# burn-my-windows/profiles/ subtree with the correct upstream-native
# filename intact.

# ============================================================================
# Step 9: Unmount and disconnect
# ============================================================================

log "Unmounting image..."
umount "$MOUNT_POINT"

log "Disconnecting NBD..."
qemu-nbd --disconnect "$NBD_DEV"

# Clear the trap since we cleaned up manually
trap - EXIT

# ============================================================================
# Done
# ============================================================================

FINAL_SIZE=$(du -h "$IMAGE" | cut -f1)

log ""
log "============================================"
log "  InterGenOS disk image created"
log "  Image: $IMAGE"
log "  Size:  $FINAL_SIZE"
log "============================================"
log "  Format: ${IMAGE_FORMAT}"
log ""
log "  NOTE: this is an UNSIGNED, Secure-Boot-OFF VM/test-only disk image."
log "  It is NOT a release medium and must NOT be dd'd to a USB stick as an"
log "  installer. For a bootable USB / real-hardware install, use the SIGNED"
log "  live ISO from phase_iso (build/*.iso), per the dd discipline."
log ""
if [ "$IMAGE_FORMAT" = "qcow2" ]; then
    log "  Boot it in a (non-Secure-Boot) dev VM with:"
    log "    virt-install --name intergenos --ram 12288 --vcpus 12 \\"
    log "      --cpu host-passthrough --machine q35 --os-variant linux2022 \\"
    log "      --disk path=$IMAGE,format=qcow2,bus=virtio \\"
    log "      --import --network network=default,model=virtio \\"
    log "      --graphics vnc,listen=0.0.0.0 --video virtio --noautoconsole"
else
    log "  Attach it to a (non-Secure-Boot) dev VM as a raw virtio disk, or"
    log "  convert it for a qcow2 VM:"
    log "    qemu-img convert -f raw -O qcow2 $IMAGE intergenos.qcow2"
fi
log ""
