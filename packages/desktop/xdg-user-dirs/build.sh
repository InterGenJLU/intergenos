#!/bin/bash
# xdg-user-dirs 0.18 — XDG user directory management
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
