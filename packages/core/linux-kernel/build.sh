#!/bin/bash
# Linux Kernel 6.18.10
# LFS 13.0 Section 10.3
#
# DESTDIR exception: Kernel uses INSTALL_MOD_PATH and INSTALL_PATH,
# not DESTDIR.
#
# IMPORTANT: Kernel configuration requires a .config file.
# Use 'make menuconfig' or copy a known-good config to .config
# before building. The build system does NOT provide a default
# kernel config — this is deliberate per the PRIME DIRECTIVE.

configure() {
    make mrproper

    # If a pre-built config exists, use it
    # Config is at /mnt/intergenos/config/kernel/ inside the chroot
    local config_dir="/mnt/intergenos/config/kernel"
    if [ -f "$config_dir/intergenos.config" ]; then
        cp -v "$config_dir/intergenos.config" .config
        make olddefconfig
    else
        echo ""
        echo "=========================================="
        echo "  WARNING: No kernel config found."
        echo "  Run 'make menuconfig' to configure."
        echo "=========================================="
        echo ""
        return 1
    fi
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # Kernel uses INSTALL_MOD_PATH, not DESTDIR
    make INSTALL_MOD_PATH="$DESTDIR" modules_install

    # Install kernel image, System.map, and config
    install -vm755 -d "${DESTDIR}/boot"
    cp -iv arch/x86/boot/bzImage "${DESTDIR}/boot/vmlinuz-6.18.10-igos"
    cp -iv System.map "${DESTDIR}/boot/System.map-6.18.10"
    cp -iv .config "${DESTDIR}/boot/config-6.18.10"

    # Install kernel documentation
    install -v -dm755 "${DESTDIR}/usr/share/doc/linux-6.18.10"
    cp -r Documentation/* "${DESTDIR}/usr/share/doc/linux-6.18.10"
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Regenerate module dependency files
    depmod 6.18.10
}
