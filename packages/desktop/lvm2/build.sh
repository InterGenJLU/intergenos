#!/bin/bash
# lvm2 2.03.28 — Logical Volume Manager
# BLFS 13.0

configure() {
    PATH+=:/usr/sbin                \
    ./configure --prefix=/usr       \
                --enable-cmdlib     \
                --enable-pkgconfig  \
                --enable-udev_sync
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install_systemd_units
}
