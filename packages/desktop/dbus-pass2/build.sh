#!/bin/bash
# dbus-pass2 1.16.2 — Message bus (pass 2 with doxygen API docs)
# Rebuilds dbus with -Dapi_docs=enabled now that doxygen is available
# in tier:desktop. Supersedes the pass 1 (tier:core, ch8) build at
# install time via migrate-pkm-supersedes.sh.

configure() {
    set -e
    mkdir -p build
    cd build
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --sysconfdir=/etc   \
          --localstatedir=/var \
          --buildtype=release \
          -Dapi_docs=true
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
