#!/bin/bash
# gnome-keyring 47.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --with-pam-dir=/usr/lib/security
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
