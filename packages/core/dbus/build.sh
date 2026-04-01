#!/bin/bash
# D-Bus 1.16.2
# LFS 13.0 Section 8.79
#
# Uses meson. DESTDIR supported.

configure() {
    mkdir build
    cd    build

    meson setup --prefix=/usr     \
        --buildtype=release       \
        --wrap-mode=nofallback ..
}

build() {
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    cd build
    ninja test
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Symlink machine-id for D-Bus compatibility
    mkdir -pv "${DESTDIR}/var/lib/dbus"
    ln -sfv /etc/machine-id "${DESTDIR}/var/lib/dbus"
}
