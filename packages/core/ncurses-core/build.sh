#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Ncurses 6.6
# LFS 13.0 Section 8.31
#
# Ncurses has a unique install pattern — LFS already uses DESTDIR=$PWD/dest
# to avoid crashing the running shell that depends on ncurses. We adapt this
# to stage into $DESTDIR instead.

configure() {
    set -e
    ./configure --prefix=/usr           \
        --mandir=/usr/share/man         \
        --with-shared                   \
        --without-debug                 \
        --without-normal                \
        --with-cxx-shared               \
        --enable-pc-files               \
        --with-pkg-config-libdir=/usr/lib/pkgconfig
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Stage into a local dest first (LFS pattern), then copy to $DESTDIR
    make DESTDIR=$PWD/dest install

    # Fix header for wide-character compatibility
    sed -e 's/^#if.*XOPEN.*$/#if 1/' -i dest/usr/include/curses.h

    # Copy staged files to our DESTDIR
    mkdir -pv "$DESTDIR"
    cp --remove-destination -av dest/* "$DESTDIR"

    # Compatibility symlinks for non-wide-character programs
    for lib in ncurses form panel menu; do
        ln -sfv lib${lib}w.so "${DESTDIR}/usr/lib/lib${lib}.so"
        ln -sfv ${lib}w.pc "${DESTDIR}/usr/lib/pkgconfig/${lib}.pc"
    done

    # Ensure old -lcurses apps still build
    ln -sfv libncursesw.so "${DESTDIR}/usr/lib/libcurses.so"

    # Prebuilt third-party binaries ask the loader for libtinfo.so.6 by that exact
    # name. This build configures without --with-termlib, so the terminfo entry
    # points (setupterm, tgetent, tigetstr) are exported by libncursesw.so.6 itself
    # and no file of that name is produced; the symbols are here, only the name is
    # missing. These two links supply the name: the versioned one is what a binary's
    # dynamic section asks for, the unversioned one is what -ltinfo needs at link time.
    ln -sfv libncursesw.so.6 "${DESTDIR}/usr/lib/libtinfo.so.6"
    ln -sfv libtinfo.so.6 "${DESTDIR}/usr/lib/libtinfo.so"

    # Remove static libraries
    rm -fv "${DESTDIR}/usr/lib/libncursesw.a"
    rm -fv "${DESTDIR}/usr/lib"/lib{ncurses,form,panel,menu}.a
}
