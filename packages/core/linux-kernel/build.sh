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
# kernel config — this is deliberate; the kernel config is a
# user-owned decision.

configure() {
    make mrproper

    # Apply kernel patches (e.g., CVE mitigations)
    local patch_dir="/mnt/intergenos/packages/core/linux-kernel/patches"
    if [ -d "$patch_dir" ] && ls "$patch_dir"/*.patch >/dev/null 2>&1; then
        echo "  Applying kernel patches..."
        for patch in "$patch_dir"/*.patch; do
            echo "    $(basename "$patch")"
            patch -Np1 < "$patch" || {
                echo "  ERROR: failed to apply $(basename "$patch")"
                return 1
            }
        done
    fi

    # Merge kernel config fragments (baseline + overrides)
    # Overrides are concatenated AFTER baseline so they win in olddefconfig
    local config_dir="/mnt/intergenos/config/kernel"
    local frag_dir="$config_dir/fragments"
    if [ -d "$frag_dir" ] && ls "$frag_dir"/*.config >/dev/null 2>&1; then
        echo "  Merging kernel config fragments..."
        cat "$frag_dir"/*.config > .config
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
