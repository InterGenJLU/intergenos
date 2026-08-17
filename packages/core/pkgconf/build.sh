#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Pkgconf 2.5.1
# LFS 13.0 Section 8.20

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/pkgconf-2.5.1
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # pkg-config compatibility symlinks
    ln -sv pkgconf "${DESTDIR}/usr/bin/pkg-config"
    ln -sv pkgconf.1 "${DESTDIR}/usr/share/man/man1/pkg-config.1"

    # lib32 cross personality (GE arc, RT-7/G2): pkgconf invoked under the
    # triplet name answers EXCLUSIVELY from the 32-bit pkg-config world —
    # this is the pinned pkg-config the lib32 cross file names, upstream
    # pkgconf's own personality mechanism (the same shape the reference
    # multilib distro ships). /usr/share/pkgconfig stays on the search path:
    # arch-independent .pc files are valid for both widths.
    install -dm755 "${DESTDIR}/usr/share/pkgconfig/personality.d"
    cat > "${DESTDIR}/usr/share/pkgconfig/personality.d/i686-igos-linux-gnu.personality" << "EOF"
Triplet: i686-igos-linux-gnu
SysrootDir: /
DefaultSearchPaths: /usr/lib32/pkgconfig:/usr/share/pkgconfig
SystemIncludePaths: /usr/include
SystemLibraryPaths: /usr/lib32
EOF
    ln -sv pkgconf "${DESTDIR}/usr/bin/i686-igos-linux-gnu-pkg-config"
}
