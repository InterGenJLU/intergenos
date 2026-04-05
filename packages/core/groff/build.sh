#!/bin/bash
# Groff 1.23.0
# LFS 13.0 Section 8.65
#
# CRITICAL: PAGE must be set at configure time.
# letter = US, A4 = everywhere else.

configure() {
    PAGE=letter ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
