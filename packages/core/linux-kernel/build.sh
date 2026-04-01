#!/bin/bash
# Linux Kernel 6.18.10
# LFS 13.0 Section 10.3
#
# IMPORTANT: Kernel configuration requires a .config file.
# Use 'make menuconfig' or copy a known-good config to .config
# before building. The build system does NOT provide a default
# kernel config — this is deliberate per the PRIME DIRECTIVE.

configure() {
    make mrproper

    # If a pre-built config exists, use it
    if [ -f "$IGOS/config/kernel/intergenos.config" ]; then
        cp -v $IGOS/config/kernel/intergenos.config .config
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

install() {
    make modules_install

    # Install the kernel
    cp -iv arch/x86/boot/bzImage /boot/vmlinuz-6.18.10-igos
    cp -iv System.map /boot/System.map-6.18.10
    cp -iv .config /boot/config-6.18.10

    # Install kernel documentation
    install -d /usr/share/doc/linux-6.18.10
    cp -r Documentation/* /usr/share/doc/linux-6.18.10
}
