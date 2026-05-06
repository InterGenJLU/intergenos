#!/bin/bash
# libslirp 4.7.0 — General purpose TCP-IP emulator library
# Not in BLFS — InterGenOS extra tier
#
# Provides user-mode networking stack used by QEMU and slirp4netns.
# Requires glib-2.0. Meson build system. Installs shared library,
# headers, and pkg-config file.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release
}

build() {
    ninja -C build
}

do_install() {
    DESTDIR="$DESTDIR" ninja -C build install
    install -d "$DESTDIR/usr/share/man/man3"
    install -v -m644 "$BUILD_DIR/libslirp.3" "$DESTDIR/usr/share/man/man3/libslirp.3"
}
