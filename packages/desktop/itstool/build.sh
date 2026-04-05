#!/bin/bash
# itstool 2.0.7 — ITS-based XML translation tool
# BLFS 13.0

configure() {
    # BLFS patch — apply with --force to skip hunks for test files
    # not present in the release tarball (only in GitHub archive)
    patch -Np1 --force -i $IGOS_PATCHES/itstool-2.0.7-lxml-1.patch || true

    PYTHON=/usr/bin/python3 \
    ./autogen.sh --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
