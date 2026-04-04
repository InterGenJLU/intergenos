#!/bin/bash
# gnome-keyring 48.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i 's:"/desktop:"/org:' schema/*.xml
    ./configure --prefix=/usr \
                --with-pam-dir=/usr/lib/security
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
