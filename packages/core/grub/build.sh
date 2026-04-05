#!/bin/bash
# GRUB 2.14
# LFS 13.0 Section 8.66

configure() {
    # Unset any GRUB-related environment variables
    unset {C,CXX,CPP,LD}FLAGS

    # Fix a bug introduced in grub-2.14
    sed 's/--image-base/--nonexist-linker-option/' -i configure

    ./configure --prefix=/usr          \
        --sysconfdir=/etc              \
        --disable-efiemu               \
        --disable-werror
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
