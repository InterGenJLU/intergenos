#!/bin/bash
# Kbd 2.9.0
# LFS 13.0 Section 8.69

configure() {
    set -e
    # Fix backspace/delete key behavior
    patch -Np1 -i ${IGOS_PATCHES}/kbd-2.9.0-backspace-1.patch

    # Remove resizecons (requires a defunct video library)
    sed -i '/RESIZECONS_PROGS=/s/yes/no/' configure
    sed -i 's/resizecons.8 //' docs/man/man8/Makefile.in

    ./configure --prefix=/usr \
        --disable-vlock
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
