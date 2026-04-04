#!/bin/bash
# libblockdev 3.4.0 — Library for manipulating block devices
# BLFS 13.0

configure() {
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
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
