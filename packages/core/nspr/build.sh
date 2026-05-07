#!/bin/bash
# NSPR 4.38.2 — Netscape Portable Runtime
# BLFS 13.0
# Note: source extracts with nspr/ subdirectory

configure() {
    set -e
    cd nspr

    # Disable installing unneeded static libs and scripts
    sed -i '/^RELEASE/s|^|#|' pr/src/misc/Makefile.in
    sed -i 's|$(LIBRARY) ||'  config/rules.mk

    ./configure --prefix=/usr   \
                --with-mozilla  \
                --with-pthreads \
                $([ $(uname -m) = x86_64 ] && echo --enable-64bit)
}

build() {
    set -e
    cd nspr
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd nspr
    make DESTDIR="$DESTDIR" install
}
