#!/bin/bash
# D-Bus 1.16.2
# LFS 13.0 Section 8.79
#
# Uses meson. DESTDIR supported.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr     \
        --libdir=/usr/lib         \
        --buildtype=release       \
        --wrap-mode=nofallback ..
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Symlink machine-id for D-Bus compatibility
    mkdir -pv "${DESTDIR}/var/lib/dbus"
    ln -sfv /etc/machine-id "${DESTDIR}/var/lib/dbus"
}
