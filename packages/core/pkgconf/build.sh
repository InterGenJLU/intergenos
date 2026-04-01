#!/bin/bash
# Pkgconf 2.5.1
# LFS 13.0 Section 8.20

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/pkgconf-2.5.1
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR="$DESTDIR" install

    # pkg-config compatibility symlinks
    ln -sv pkgconf "${DESTDIR}/usr/bin/pkg-config"
    ln -sv pkgconf.1 "${DESTDIR}/usr/share/man/man1/pkg-config.1"
}
