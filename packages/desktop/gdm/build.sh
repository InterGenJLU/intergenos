#!/bin/bash
# gdm 49.2 — GNOME Display Manager
# BLFS 13.0

configure() {
    set -e
    # Create gdm system user/group
    groupadd -g 21 gdm 2>/dev/null || true
    useradd -c "GDM Daemon Owner" -d /var/lib/gdm \
            -u 21 -g gdm -s /bin/false gdm 2>/dev/null || true
    passwd -ql gdm 2>/dev/null || true

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgdm-xsession=true \
          -Drun-dir=/run/gdm \
          -Ddefault-pam-config=lfs
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
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
