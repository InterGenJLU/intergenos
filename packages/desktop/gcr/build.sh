#!/bin/bash
# gcr 3.41.2 — GLib crypto and PKCS#11 framework (GTK3 version)
# BLFS 13.0

configure() {
    # BLFS: fix build without OpenSSH installed
    sed '/ssh.add/d; /ssh.agent/d' -i meson.build

    # BLFS: fix schema path
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -Dgtk_doc=false       \
          -Dssh_agent=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
