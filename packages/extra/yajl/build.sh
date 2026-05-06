#!/bin/bash
# yajl 2.1.0 — Yet Another JSON Library
# Not in BLFS — InterGenOS extra tier

configure() {
    cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib
}

build() {
    cmake --build build
}

check() {
    cd build && ctest --output-on-failure || true
}

do_install() {
    cmake --install build --prefix "$DESTDIR/usr"
    install -d "$DESTDIR/usr/share/man/man3"
    install -v -m644 "$BUILD_DIR/libyajl.3" "$DESTDIR/usr/share/man/man3/libyajl.3"
}
BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
