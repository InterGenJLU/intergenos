#!/bin/bash
# Grep 3.12
# LFS 13.0 Section 8.36

configure() {
    # Suppress egrep/fgrep deprecation warning that breaks some test suites
    sed -i "s/echo/#echo/" src/egrep.sh

    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
