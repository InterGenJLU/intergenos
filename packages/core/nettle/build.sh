#!/bin/bash
# nettle 3.10.2 — Low-level cryptographic library
# BLFS 13.0

configure() {
    set -e
    # --libdir=/usr/lib: nettle's autoconf auto-picks /usr/lib64 on x86_64,
    # but chroot pkg-config only searches /usr/lib/pkgconfig:/usr/share/pkgconfig
    # (LFS-13.0 tree is lib-only). Same class as efivar 047c67c.
    ./configure --prefix=/usr \
                --libdir=/usr/lib \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
