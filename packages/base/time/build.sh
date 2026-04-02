#!/bin/bash
# Time 1.9 — GNU time command
# BLFS 13.0

configure() {
    # GCC-15 fix
    sed -i 's/sighandler interrupt_signal/__sighandler_t interrupt_signal/' src/time.c

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
