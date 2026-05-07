#!/bin/bash
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
        --with-platform=pc
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
}
