#!/bin/bash
# libblockdev 3.4.0 — Library for manipulating block devices
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr      \
                --sysconfdir=/etc  \
                --with-python3     \
                --without-escrow   \
                --without-gtk-doc  \
                --without-lvm      \
                --without-lvm_dbus \
                --without-nvdimm   \
                --without-tools    \
                --without-smartmontools
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
