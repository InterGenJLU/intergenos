#!/bin/bash
# Bzip2 1.0.8
# LFS 13.0 Section 8.7
#
# DESTDIR exception: Bzip2 uses PREFIX, not DESTDIR.
# We redirect PREFIX to $DESTDIR/usr for staging.

configure() {
    patch -Np1 -i $IGOS_PATCHES/bzip2-1.0.8-install_docs-1.patch

    # Ensure symlinks are relative
    sed -i 's@\(ln -s -f \)$(PREFIX)/bin/@\1@' Makefile
    # Fix man page location
    sed -i "s@(PREFIX)/man@(PREFIX)/share/man@g" Makefile
}

build() {
    # Build shared library first
    make -f Makefile-libbz2_so
    make clean
    make -j${IGOS_JOBS}
}

do_install() {
    # Bzip2 uses PREFIX, not DESTDIR
    make PREFIX="${DESTDIR}/usr" install

    # Install shared library
    mkdir -pv "${DESTDIR}/usr/lib"
    cp -av libbz2.so.* "${DESTDIR}/usr/lib"
    ln -sfv libbz2.so.1.0.8 "${DESTDIR}/usr/lib/libbz2.so"
    ln -sfv libbz2.so.1.0.8 "${DESTDIR}/usr/lib/libbz2.so.1"

    # Install shared bzip2 binary and symlinks
    mkdir -pv "${DESTDIR}/usr/bin"
    cp -v bzip2-shared "${DESTDIR}/usr/bin/bzip2"
    for i in bzcat bunzip2; do
        ln -sfv bzip2 "${DESTDIR}/usr/bin/$i"
    done

    rm -fv "${DESTDIR}/usr/lib/libbz2.a"
}
