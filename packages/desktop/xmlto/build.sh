#!/bin/bash
# xmlto 0.0.29 — XML-to-format conversion tool
# BLFS 13.0

configure() {
    # Source tarball lacks pre-generated configure script
    autoreconf -fiv

    # BLFS: set LINKS to avoid confusion with elinks
    LINKS="/usr/bin/links" \
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
