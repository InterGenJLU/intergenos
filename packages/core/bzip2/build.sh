#!/bin/bash
# Bzip2 1.0.8
# LFS 13.0 Section 8.7

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

install() {
    make PREFIX=/usr install

    # Install shared library
    cp -av libbz2.so.* /usr/lib
    ln -sfv libbz2.so.1.0.8 /usr/lib/libbz2.so
    ln -sfv libbz2.so.1.0.8 /usr/lib/libbz2.so.1

    # Install shared bzip2 binary and symlinks
    cp -v bzip2-shared /usr/bin/bzip2
    for i in /usr/bin/{bzcat,bunzip2}; do
        ln -sfv bzip2 $i
    done

    rm -fv /usr/lib/libbz2.a
}
