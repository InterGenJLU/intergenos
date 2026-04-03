#!/bin/bash
# accountsservice 23.13.9 — D-Bus interface for user account management
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '1i#include <stdio.h>' src/user.c
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dadmin_group=wheel \
          -Dsystemdsystemunitdir=/usr/lib/systemd/system
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
