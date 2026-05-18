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
    set -e
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
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
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

    # Stage kernel source + .config + Module.symvers for reproducibility
    # and out-of-tree module builds (DKMS, NVIDIA, VirtualBox, ZFS).
    # Without this, /lib/modules/<ver>/build is a dangling symlink to the
    # ephemeral build work-dir, and DKMS cannot work. Aligns with PRIME
    # DIRECTIVE: users control their machine — they get the source.
    local pkg_ver="${PKG_VERSION:-6.18.10}"
    local src_stage="${DESTDIR}/usr/src/linux-${pkg_ver}"
    install -v -dm755 "${DESTDIR}/usr/src"

    # Extract fresh source from canonical tarball (byte-identical to upstream)
    tar -xf "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" \
        -C "${DESTDIR}/usr/src/"

    # Copy our build's .config + Module.symvers (so users get the EXACT
    # config + symbol versions matching the running kernel)
    cp .config "${src_stage}/.config"
    [ -f Module.symvers ] && cp Module.symvers "${src_stage}/Module.symvers"

    # Generate auto-config headers + host scripts so source is DKMS-ready
    make -C "${src_stage}" olddefconfig
    make -C "${src_stage}" modules_prepare

    # Replace build/source symlinks (auto-emitted by modules_install
    # pointing at ephemeral $PWD) with stable /usr/src/ targets.
    # NOTE: -n is critical — without it, ln -sf with a LINK_NAME that
    # already exists as a symlink-to-directory creates the new link
    # INSIDE that directory rather than replacing it.
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${pkg_ver}-igos/build"
    ln -sfnv "/usr/src/linux-${pkg_ver}" \
             "${DESTDIR}/lib/modules/${pkg_ver}-igos/source"

    # Ship the canonical source tarball for byte-identity verification
    # against upstream + clean-rebuild scenarios
    install -vm644 "${IGOS_SOURCES}/linux-${pkg_ver}.tar.xz" \
        "${DESTDIR}/usr/src/linux-${pkg_ver}.tar.xz"

    # Ship the D-005 UKI rebuild hook. pkm/installer.py fires
    # /var/lib/pkm/hooks/<pkgname>/post-install after deploy on the
    # target system (Forge install + pkm upgrade alike). The hook
    # rebuilds the UKI from the new kernel + signs it with the user's
    # local MOK per D-005 Option A.
    install -v -dm755 "${DESTDIR}/var/lib/pkm/hooks/linux-kernel"
    install -vm755 "/mnt/intergenos/packages/core/linux-kernel/hooks/post-install.sh" \
        "${DESTDIR}/var/lib/pkm/hooks/linux-kernel/post-install"
}

# Post-install: runs on the live system AFTER deploy
# NOTE: this build.sh post_install is invoked by scripts/chroot-build-ch10.sh
# at PACKAGE BUILD time inside the build chroot — NOT at user-install time.
# The user-install-time hook is shipped via do_install above to
# /var/lib/pkm/hooks/linux-kernel/post-install and fired by pkm/installer.py.
post_install() {
    set -e
    # Regenerate module dependency files for the package's KERNELRELEASE
    # (matches the modules-dir created by `make modules_install`, i.e.
    # 6.18.10-igos per CONFIG_LOCALVERSION="-igos").
    depmod 6.18.10-igos
}
