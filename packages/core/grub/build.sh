#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GRUB 2.14
# LFS 13.0 Section 8.66
#
# Builds both BIOS (i386-pc) and EFI (x86_64-efi) platforms.
# BIOS build is the primary; EFI is built separately and merged.

configure() {
    set -e
    # Unset any GRUB-related environment variables
    unset {C,CXX,CPP,LD}FLAGS

    # Fix a bug introduced in grub-2.14
    sed 's/--image-base/--nonexist-linker-option/' -i configure

    # Build BIOS platform first
    mkdir -p build-bios
    cd build-bios

    ../configure --prefix=/usr          \
        --sysconfdir=/etc              \
        --disable-efiemu               \
        --disable-werror               \
        --with-platform=pc 2>&1 | tee cfg-summary-bios.txt

    # FONT GATE (2026-06-01). GRUB only builds unicode.pf2 when build-time
    # grub-mkfont (FreeType) AND unifont are detected at configure. Both are now
    # installed in Ch8 BEFORE grub (freetype-grub + unifont packages). If this
    # ever regresses, GRUB ships fontless and the gfxterm menu renders
    # missing-glyph "?"/"@" TOFU blocks (install attempt #21 — repeated). HALT
    # loudly here rather than ship an unreadable boot menu.
    if ! grep -q 'grub-mkfont: Yes' cfg-summary-bios.txt; then
        echo "FATAL: GRUB configure reports build-time grub-mkfont DISABLED (FreeType not detected)."
        echo "       unicode.pf2 cannot be built -> unreadable boot menu. freetype-grub must build before grub in Ch8."
        grep -i -e 'grub-mkfont' -e 'unifont' -e 'freetype' cfg-summary-bios.txt || true
        exit 1
    fi
    grep -qi 'unifont' cfg-summary-bios.txt || echo "WARNING: grub configure summary did not mention unifont — verify /usr/share/fonts/unifont/unifont.pcf is installed"
    echo "GRUB font gate OK: build-time grub-mkfont ENABLED (unicode.pf2 will be generated)"
}

build() {
    set -e
    cd build-bios
    make -j${IGOS_JOBS}

    # Build EFI platform
    cd ..
    mkdir -p build-efi
    cd build-efi

    unset {C,CXX,CPP,LD}FLAGS
    ../configure --prefix=/usr          \
        --sysconfdir=/etc              \
        --disable-efiemu               \
        --disable-werror               \
        --with-platform=efi

    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build-bios
    make DESTDIR="$DESTDIR" install

    # Install EFI modules alongside BIOS modules
    cd ../build-efi
    make DESTDIR="$DESTDIR" install
    cd ..

    # Install GRUB's natively-built console font. Because FreeType (freetype-grub)
    # and unifont are installed in Ch8 BEFORE grub, grub's `make` compiles
    # unicode.pf2 from unifont via build-time grub-mkfont — the canonical BLFS GRUB
    # font procedure. We install it to BOTH canonical locations so a default
    # grub-install lands it on the ESP and the gfxterm `loadfont unicode` succeeds,
    # preventing the missing-glyph "?"/"@" TOFU blocks that made the boot menu
    # unreadable (install attempt #21, a repeated regression).
    #
    # HARD requirement — no silent fontless ship. The configure-time gate already
    # asserted grub-mkfont is enabled; if unicode.pf2 still was not produced,
    # something is wrong and we HALT rather than ship an unreadable bootloader.
    local _pf2
    _pf2=$(find build-bios build-efi "${DESTDIR}/usr/share/grub" -name 'unicode.pf2' -size +0c 2>/dev/null | head -1)
    if [ -z "$_pf2" ]; then
        echo "FATAL: GRUB build did not produce unicode.pf2 despite the configure font gate passing."
        echo "       Refusing to ship a fontless GRUB (would render TOFU '?'/'@' blocks). Halting."
        exit 1
    fi
    install -dm755 "${DESTDIR}/usr/share/grub"
    install -m644 "$_pf2" "${DESTDIR}/usr/share/grub/unicode.pf2"
    install -dm755 "${DESTDIR}/boot/grub/fonts"
    install -m644 "$_pf2" "${DESTDIR}/boot/grub/fonts/unicode.pf2"
    echo "GRUB unicode.pf2 installed from ${_pf2} ($(stat -c%s "$_pf2") bytes) -> /usr/share/grub + /boot/grub/fonts"

    # Fallback-menu FDE recovery: the installed system carries ONE unversioned
    # /boot/initramfs.img beside a versioned vmlinuz (the kernel post-install
    # hook owns it), and stock 10_linux probes only versioned initramfs names —
    # so every fallback menu entry carried ucode-only initrd lines and the GRUB
    # recovery path could not unlock FDE. Append the unversioned name as the
    # LAST candidate: any versioned initramfs, if one ever exists, still wins.
    # Fail-closed: halt when the anchor is absent so a grub version bump cannot
    # silently drop the fix.
    local _tenlinux="${DESTDIR}/etc/grub.d/10_linux"
    if ! grep -q 'initramfs-genkernel-${GENKERNEL_ARCH}-${alt_version}"; do' "$_tenlinux"; then
        echo "FATAL: 10_linux initramfs candidate-list anchor not found — upstream changed the loop."
        echo "       Refusing to ship a fallback menu that cannot find /boot/initramfs.img. Halting."
        exit 1
    fi
    sed -i 's|initramfs-genkernel-${GENKERNEL_ARCH}-${alt_version}"; do|initramfs-genkernel-${GENKERNEL_ARCH}-${alt_version}" \\\n\t   "initramfs.img"; do|' "$_tenlinux"
    grep -q '"initramfs.img"; do' "$_tenlinux" || { echo "FATAL: initramfs.img candidate not inserted into 10_linux"; exit 1; }
    echo "10_linux: unversioned initramfs.img appended to the initrd candidate list (fallback-menu FDE recovery)"
}
