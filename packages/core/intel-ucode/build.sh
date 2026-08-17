#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# intel-ucode 20250211 — Intel CPU microcode firmware
# Source: Intel Linux Processor Microcode Data Files

configure() {
    set -e
    : # No configuration needed — firmware files only
}

build() {
    set -e
    : # No compilation needed — firmware files only
}

do_install() {
    set -e
    # Install microcode firmware files
    mkdir -p "${DESTDIR}/lib/firmware/intel-ucode"
    cp -v intel-ucode/* "${DESTDIR}/lib/firmware/intel-ucode/"

    # Ship the runtime post-install hook. pkm fires
    # /var/lib/pkm/hooks/<pkgname>/post-install after deploy on the target
    # system (Forge install + pkm upgrade alike). Without it, a standalone
    # intel-ucode upgrade updates /lib/firmware only and the new microcode
    # never reaches the boot path (no /boot/intel-ucode.img regen, no UKI
    # rebuild) until the next kernel install. The hook chains to the
    # linux-kernel hook, which owns the full boot-chain rebuild.
    install -v -dm755 "${DESTDIR}/var/lib/pkm/hooks/intel-ucode"
    install -vm755 "/mnt/intergenos/packages/core/intel-ucode/hooks/post-install.sh" \
        "${DESTDIR}/var/lib/pkm/hooks/intel-ucode/post-install"
}

post_install() {
    set -e
    # Generate early-load cpio image for GRUB.
    # IMPORTANT: do NOT pass -S (--scan-system). -S filters microcode to
    # ONLY the signatures matching the RUNNING CPU (the build VM / chroot
    # host), which yields an empty image whenever that CPU signature isn't
    # in Intel's pack. The early-firmware image produced here ships to
    # end-user hardware of varied vintages — we want the FULL pack, kernel
    # selects the matching signature at boot.
    # Note: phase_image regenerates this image via the canonical helper at
    # scripts/build-microcode-cpio.sh during qcow2 assembly. This post_install
    # path is not load-bearing for the live ISO's /boot/intel-ucode.img.
    if command -v iucode_tool >/dev/null 2>&1; then
        # iucode_tool --write-earlyfw refuses to overwrite an existing file;
        # remove any prior image first so a rebuild-into-a-populated-chroot is
        # idempotent (no-op on a from-scratch build where /boot is empty). This
        # image is non-load-bearing anyway — phase_image regenerates it.
        rm -f /boot/intel-ucode.img
        iucode_tool /lib/firmware/intel-ucode/ \
            --write-earlyfw=/boot/intel-ucode.img
    fi
}
