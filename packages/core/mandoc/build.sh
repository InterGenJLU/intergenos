#!/bin/bash
# mandoc 1.14.6 — BSD man page formatter
# Required by efivar for man page generation

configure() {
    set -e
    # BSD-style configure — set install paths to avoid conflicts with man-db
    cat > configure.local << "EOF"
PREFIX=/usr
MANDIR=/usr/share/man
SBINDIR=/usr/sbin
MANM_MANCONF="mandoc.conf"
BINM_MAN=mandoc-man
BINM_APROPOS=mandoc-apropos
BINM_WHATIS=mandoc-whatis
BINM_MAKEWHATIS=mandoc-makewhatis
BINM_SOELIM=mandoc-soelim
EOF

    ./configure
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
