#!/bin/bash
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
# Examples:
#   # For VM (qcow2):
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.qcow2 500G
#
#   # For USB/bare metal (raw):
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.img 64G
#   dd if=/mnt/intergenos/build/intergenos.img of=/dev/sdX bs=4M status=progress

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

if [ ! -f "$CHROOT/boot/vmlinuz-"* ] 2>/dev/null; then
    err "No kernel found in $CHROOT/boot/"
    exit 1
fi

for tool in qemu-img qemu-nbd parted mkfs.ext4; do
    if ! command -v "$tool" > /dev/null 2>&1; then
        err "Required tool not found: $tool"
        exit 1
    fi
done

# ============================================================================
# Step 1: Create disk image (qcow2 for VM, raw for bare metal/USB)
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
tar -C "$CHROOT" --one-file-system -cf - . | tar -C "$MOUNT_POINT" -xf -

log "  Copy complete: $(du -sh "$MOUNT_POINT" | cut -f1)"

# Fix root directory ownership — tar preserves the chroot's ownership
# which is the build user, not root
chown root:root "$MOUNT_POINT"

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

# Generate SSH host keys (sshd won't start without them)
chroot "$MOUNT_POINT" /bin/bash -c 'ssh-keygen -A 2>/dev/null'
log "  SSH host keys generated"

# Initialize CA certificates (HTTPS/TLS requires this)
# make-ca needs network to download certdata.txt — may fail in chroot without
# network access. This is non-fatal; certs can be initialized on first boot.
if [ -x "${MOUNT_POINT}/usr/sbin/make-ca" ]; then
    chroot "$MOUNT_POINT" /bin/bash -c '/usr/sbin/make-ca -g 2>/dev/null' || \
        log "  CA certificates: make-ca failed (no network) — will initialize on first boot"
fi

# Generate Intel CPU microcode early-load image
if [ -x "${MOUNT_POINT}/usr/bin/iucode_tool" ] && \
   [ -d "${MOUNT_POINT}/lib/firmware/intel-ucode" ]; then
    chroot "$MOUNT_POINT" /bin/bash -c \
        'iucode_tool -S /lib/firmware/intel-ucode/ --write-earlyfw=/boot/intel-ucode.img 2>/dev/null'
    if [ -f "${MOUNT_POINT}/boot/intel-ucode.img" ]; then
        log "  Intel microcode image generated (/boot/intel-ucode.img)"
    else
        log "  Intel microcode: iucode_tool ran but no matching CPU signatures found (non-Intel CPU?)"
    fi
else
    log "  Intel microcode: iucode_tool or firmware not installed — skipping"
fi

# Create kernel symlink (GRUB expects /boot/vmlinuz)
KERNEL=$(ls "${MOUNT_POINT}/boot"/vmlinuz-* 2>/dev/null | head -1)
if [ -n "$KERNEL" ] && [ ! -L "${MOUNT_POINT}/boot/vmlinuz" ]; then
    ln -sf "$(basename "$KERNEL")" "${MOUNT_POINT}/boot/vmlinuz"
    log "  Kernel symlink: /boot/vmlinuz -> $(basename "$KERNEL")"
fi

# Clang config files combining:
#   --gcc-triple — clang's GCCInstallationDetector doesn't know our custom
#                  triple x86_64-igos-linux-gnu; without this, clang can't
#                  find <cstddef>, crtbeginS.o, libgcc_s. See
#                  research/build_system/clang_custom_triple_2026-04-10.md.
#   -fstack-protector-strong — BLFS SSP hardening (BLFS LLVM § Configuration).
# Both lines must be present; using `>` (overwrite) on either alone clobbers
# the other (in-chroot llvm post_install vs image-time write race).
mkdir -p "${MOUNT_POINT}/etc/clang"
for i in clang clang++; do
    cat > "${MOUNT_POINT}/etc/clang/$i.cfg" <<'CLANGCFG'
--gcc-triple=x86_64-igos-linux-gnu
-fstack-protector-strong
CLANGCFG
done
log "  Clang GCC detection + SSP hardening configured (x86_64-igos-linux-gnu)"

# Disable mDNS in systemd-resolved (Avahi is the sole mDNS handler)
mkdir -p "${MOUNT_POINT}/etc/systemd/resolved.conf.d"
cat > "${MOUNT_POINT}/etc/systemd/resolved.conf.d/no-mdns.conf" << 'MDNSEOF'
[Resolve]
MulticastDNS=no
MDNSEOF
log "  mDNS disabled in systemd-resolved (Avahi handles mDNS)"

# WirePlumber startup delay (race condition: starts before PipeWire registers factories)
mkdir -p "${MOUNT_POINT}/etc/systemd/user/wireplumber.service.d"
cat > "${MOUNT_POINT}/etc/systemd/user/wireplumber.service.d/restart.conf" << 'WPEOF'
[Service]
ExecStartPre=/bin/sleep 1
WPEOF
log "  WirePlumber startup delay configured"

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

# Enable networking (systemd-networkd + resolved)
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/systemd-networkd.service \
        /etc/systemd/system/multi-user.target.wants/systemd-networkd.service
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
# Path 4 (S1/S2 fleet vote A 2026-04-29): the literal "intergenos" default
# has been retired permanently. build-intergenos.sh requires
# --root-password to be provided and exports ROOT_PASSWORD before invoking
# this script. Path 3's first-boot greeter overwrites this on the end
# user's first boot — the value here is a brief-window fallback nobody
# normally encounters.
if [ -z "${ROOT_PASSWORD:-}" ]; then
    err "ROOT_PASSWORD env var is required (no default permitted)"
    err "  Invoke via build-intergenos.sh --root-password <value> ..."
    err "  Or set ROOT_PASSWORD=<value> in the environment if calling this script directly."
    exit 1
fi
echo "root:${ROOT_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
# Note: passwd --expire is NOT used here. GDM cannot handle forced
# password changes at the login screen — it just fails. Path 3
# first-boot greeter (systemd unit + tty prompt) handles the user-side
# change on first boot.

# Create default user account — IMAGE_USER name can default; password CANNOT.
IMAGE_USER="${IMAGE_USER:-intergenos}"
if [ -z "${IMAGE_USER_PASSWORD:-}" ]; then
    err "IMAGE_USER_PASSWORD env var is required (no default permitted)"
    err "  Invoke via build-intergenos.sh --user-password <value> ..."
    exit 1
fi
if ! chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    chroot "$MOUNT_POINT" useradd -m -G wheel,video,audio,input -s /bin/bash "$IMAGE_USER"
    echo "${IMAGE_USER}:${IMAGE_USER_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
    # Copy skel files
    if [ -d "${MOUNT_POINT}/etc/skel" ]; then
        cp -a "${MOUNT_POINT}/etc/skel/." "${MOUNT_POINT}/home/${IMAGE_USER}/"
        chroot "$MOUNT_POINT" chown -R "${IMAGE_USER}:${IMAGE_USER}" "/home/${IMAGE_USER}"
    fi
    log "  User '${IMAGE_USER}' created (groups: wheel,video,audio,input)"
fi

# Install first-boot password greeter (Path 3 of S1/S2 fleet vote A 2026-04-29).
# The systemd unit + tty prompt script combine with Path 4 (build-time required
# credentials, already enforced above) to give zero guessable defaults at any
# point: builder picks at build, end-user picks on first boot. The greeter
# overwrites both the root and the IMAGE_USER passwords with the user's own
# choices, then writes /etc/intergenos/first-boot-completed so subsequent boots
# skip past the greeter and proceed to login normally.
GREETER_SRC="/mnt/intergenos/installer/data"
if [ -f "${GREETER_SRC}/intergenos-first-boot-greeter.service" ] && \
   [ -f "${GREETER_SRC}/first-boot-greeter" ]; then
    log "Installing first-boot password greeter (Path 3)..."
    install -d -m 0755 "${MOUNT_POINT}/usr/libexec/intergenos"
    install -m 0755 "${GREETER_SRC}/first-boot-greeter" \
        "${MOUNT_POINT}/usr/libexec/intergenos/first-boot-greeter"
    install -m 0644 "${GREETER_SRC}/intergenos-first-boot-greeter.service" \
        "${MOUNT_POINT}/etc/systemd/system/intergenos-first-boot-greeter.service"
    install -d -m 0755 "${MOUNT_POINT}/etc/intergenos"
    # Pass the IMAGE_USER name through via a drop-in so the greeter
    # prompts for the account the build actually created (rather than
    # hardcoding "intergenos" in the script).
    install -d -m 0755 "${MOUNT_POINT}/etc/systemd/system/intergenos-first-boot-greeter.service.d"
    cat > "${MOUNT_POINT}/etc/systemd/system/intergenos-first-boot-greeter.service.d/intergenos-user.conf" << EOF
[Service]
Environment=INTERGENOS_USER=${IMAGE_USER}
EOF
    chroot "$MOUNT_POINT" systemctl enable intergenos-first-boot-greeter.service
    # Belt-and-suspenders: ensure the done flag does NOT exist in the image
    # (it would short-circuit the greeter and ship the image with builder
    # credentials as the live ones — exactly what Path 3 prevents).
    rm -f "${MOUNT_POINT}/etc/intergenos/first-boot-completed"
    log "  First-boot greeter installed and enabled (account: ${IMAGE_USER})"
else
    err "First-boot greeter assets missing at ${GREETER_SRC}/ — image will"
    err "  ship with builder-set credentials only. Path 3 is REQUIRED per"
    err "  S1/S2 fleet vote 2026-04-29; this is a build inconsistency."
    exit 1
fi

# Enable GDM and set graphical target for desktop boot
if [ -f "${MOUNT_POINT}/usr/lib/systemd/system/gdm.service" ]; then
    chroot "$MOUNT_POINT" /bin/bash -c '
        systemctl enable gdm
        systemctl set-default graphical.target
    '
    log "  GDM enabled, default target set to graphical"
fi

# Fix /tmp/.X11-unix ownership (must be root-owned with sticky bit)
# Create tmpfiles.d config so systemd maintains it across reboots
mkdir -p "${MOUNT_POINT}/etc/tmpfiles.d"
cat > "${MOUNT_POINT}/etc/tmpfiles.d/x11.conf" << 'TMPEOF'
d /tmp/.X11-unix 1777 root root -
TMPEOF
mkdir -p "${MOUNT_POINT}/tmp/.X11-unix"
chown root:root "${MOUNT_POINT}/tmp/.X11-unix"
chmod 1777 "${MOUNT_POINT}/tmp/.X11-unix"

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

    # Enable essential desktop services
    systemctl enable avahi-daemon.service 2>/dev/null || true
    # Only enable CUPS if cupsd binary actually exists
    if [ -x /usr/sbin/cupsd ] || [ -x /usr/bin/cupsd ]; then
        systemctl enable cups.service 2>/dev/null || true
    else
        systemctl disable cups.service cups.socket cups.path 2>/dev/null || true
    fi
    systemctl enable bluetooth.service 2>/dev/null || true
    systemctl enable sshd.service 2>/dev/null || true

    # Disable NetworkManager-wait-online — it blocks boot indefinitely
    # when no network interface is immediately available (USB NIC unplugged,
    # WiFi not configured). NetworkManager still manages interfaces
    # asynchronously without this service.
    systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

    # Disable remote-fs.target and machines.target — not needed for desktop,
    # can cause boot hangs waiting for network mounts
    rm -f /etc/systemd/system/multi-user.target.wants/remote-fs.target 2>/dev/null || true
    rm -f /etc/systemd/system/multi-user.target.wants/machines.target 2>/dev/null || true
' 2>/dev/null
log "  Caches built (icons, fonts, schemas, GIO, pixbuf, MIME, desktop, ldconfig)"

# Disable Copy Fail (CVE-2026-31431) — blacklist algif_aead module
# LPE via AF_ALG splice() page-cache write. See https://copy.fail
mkdir -p "${MOUNT_POINT}/etc/modprobe.d"
echo "install algif_aead /bin/false" > "${MOUNT_POINT}/etc/modprobe.d/disable-algif.conf"
log "  algif_aead kernel module blacklisted (CVE-2026-31431)"

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
# Step 8d: Install theming (extensions, themes, icons, cursors, configs)
# ============================================================================

if [ -d "/mnt/intergenos/assets/theming" ]; then
    log "Installing theming assets..."
    # Re-mount bind mounts for chroot execution
    mount --bind /dev "${MOUNT_POINT}/dev"
    mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"
    mount -t proc proc "${MOUNT_POINT}/proc"
    mount -t sysfs sysfs "${MOUNT_POINT}/sys"

    chroot "$MOUNT_POINT" /bin/bash /mnt/intergenos/scripts/install-theming.sh

    umount "${MOUNT_POINT}/sys"
    umount "${MOUNT_POINT}/proc"
    umount "${MOUNT_POINT}/dev/pts"
    umount "${MOUNT_POINT}/dev"
else
    log "  No theming assets found at /mnt/intergenos/assets/theming/"
    log "  Run scripts/download-theming.sh to populate, then commit"
fi

# Apply Burn My Windows profile for the default user
IMAGE_USER="${IMAGE_USER:-intergenos}"
if [ -d "${MOUNT_POINT}/etc/skel/.config/burn-my-windows" ] && \
   chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    mkdir -p "${MOUNT_POINT}/home/${IMAGE_USER}/.config/burn-my-windows/profiles"
    cp "${MOUNT_POINT}/etc/skel/.config/burn-my-windows/profiles/default.conf" \
       "${MOUNT_POINT}/home/${IMAGE_USER}/.config/burn-my-windows/profiles/default.conf"
    chroot "$MOUNT_POINT" chown -R "${IMAGE_USER}:${IMAGE_USER}" "/home/${IMAGE_USER}/.config/burn-my-windows"
    log "  Burn My Windows profile applied for ${IMAGE_USER}"
fi

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
if [ "$IMAGE_FORMAT" = "qcow2" ]; then
    log "  Create a VM with:"
    log "    virt-install --name intergenos --ram 12288 --vcpus 12 \\"
    log "      --cpu host-passthrough --machine q35 --os-variant linux2022 \\"
    log "      --disk path=$IMAGE,format=qcow2,bus=virtio \\"
    log "      --import --network network=default,model=virtio \\"
    log "      --graphics vnc,listen=0.0.0.0 --video virtio --noautoconsole"
    log ""
    log "  Convert to raw for USB:"
    log "    qemu-img convert -f qcow2 -O raw $IMAGE intergenos.img"
    log "    sudo dd if=intergenos.img of=/dev/sdX bs=4M status=progress"
else
    log "  Write to USB drive:"
    log "    sudo dd if=$IMAGE of=/dev/sdX bs=4M status=progress"
    log ""
    log "  Or create a VM from raw:"
    log "    qemu-img convert -f raw -O qcow2 $IMAGE intergenos.qcow2"
fi
log ""
