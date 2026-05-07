#!/bin/bash
# ncurses-compat 6.6 — LSB ABI 5 compatibility libraries
# LFS 13.0 Section 8.31 (optional instructions)
#
# Builds ncurses with --with-abi-version=5 to produce libncurses.so.5
# and libncursesw.so.5 alongside the existing ABI 6 libraries.

configure() {
    set -e
    ./configure --prefix=/usr        \
        --with-shared                \
        --without-normal             \
        --without-debug              \
        --without-cxx-binding        \
        --with-abi-version=5
}

build() {
    set -e
    make sources libs
}

do_install() {
    set -e
    # Only install the ABI 5 compat libraries — do NOT overwrite ABI 6
    cp -av lib/lib*.so.5* "$DESTDIR/usr/lib/"
}
