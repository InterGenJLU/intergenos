#!/bin/bash
# Linux Kernel 6.18.10 — Pass 2 rebuild with merged config fragments
#
# Pass 1 (core) builds with whatever intergenos.config existed at the time.
# This pass rebuilds with all fragments merged, ensuring USB_STORAGE=y,
# RTW88 bus parents, RTW89, and all overrides are applied.
#
# SKIP LOGIC: Only rebuilds if the config fragments have changed since
# the last successful build. A 16-minute kernel rebuild that produces
# an identical kernel wastes the user's time.

FRAG_DIR="/mnt/intergenos/config/kernel/fragments"
CHECKSUM_FILE="/var/lib/igos/.kernel-pass2-config-checksum"

configure() {
    # Check if config fragments changed since last successful build
    local current_checksum
    current_checksum=$(cat "$FRAG_DIR"/*.config | sha256sum | cut -d' ' -f1)

    if [ -f "$CHECKSUM_FILE" ]; then
        local stored_checksum
        stored_checksum=$(cat "$CHECKSUM_FILE")
        if [ "$current_checksum" = "$stored_checksum" ]; then
            echo "  Kernel config fragments unchanged — skipping rebuild"
            echo "  (stored: ${stored_checksum:0:16}... current: ${current_checksum:0:16}...)"
            # Signal to build/install that we should skip
            touch /tmp/.kernel-pass2-skip
            return 0
        fi
    fi

    echo "  Config fragments changed — rebuilding kernel"
    rm -f /tmp/.kernel-pass2-skip

    make mrproper

    # Merge ALL config fragments (baseline + overrides)
    cat "$FRAG_DIR"/*.config > .config
    make olddefconfig

    # Verify critical options
    echo "  Verify: USB_STORAGE=$(grep CONFIG_USB_STORAGE= .config | head -1)"
    echo "  Verify: RTW88_PCI=$(grep CONFIG_RTW88_PCI .config | head -1)"
    echo "  Verify: RTW88_8821CE=$(grep CONFIG_RTW88_8821CE .config | head -1)"
}

build() {
    [ -f /tmp/.kernel-pass2-skip ] && return 0
    make -j${IGOS_JOBS}
}

do_install() {
    [ -f /tmp/.kernel-pass2-skip ] && return 0

    # Overwrite pass 1 kernel and modules
    make modules_install

    install -vm755 -d /boot
    cp -v arch/x86/boot/bzImage /boot/vmlinuz-6.18.10-igos
    cp -v System.map /boot/System.map-6.18.10
    cp -v .config /boot/config-6.18.10

    depmod 6.18.10

    # Store checksum so next run knows nothing changed
    cat "$FRAG_DIR"/*.config | sha256sum | cut -d' ' -f1 > "$CHECKSUM_FILE"
}
