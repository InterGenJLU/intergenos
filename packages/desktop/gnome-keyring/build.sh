#!/bin/bash
# gnome-keyring 48.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D selinux=disabled
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true

    # PAM config for gnome-keyring auto-unlock on login
    if [ -d /etc/pam.d ]; then
        cat > /etc/pam.d/gnome-keyring << "GKPAM"
auth     optional    pam_gnome_keyring.so
session  optional    pam_gnome_keyring.so auto_start
GKPAM
    fi
}
