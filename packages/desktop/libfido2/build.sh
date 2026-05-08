#!/bin/bash
# libfido2 1.17.0 — Yubico FIDO2 (CTAP 2.x + U2F) library
# Required by systemd for `systemd-cryptenroll --fido2-device` and the
# fido2 LUKS unlock path. Holy Grail: hardware second factor for disk
# encryption.
#
# Build #5 audit: not in package set → systemd-pass2 dropped fido2.
#
# Upstream uses CMake.

configure() {
    set -e
    cmake -S . -B build                       \
        -DCMAKE_BUILD_TYPE=Release            \
        -DCMAKE_INSTALL_PREFIX=/usr           \
        -DCMAKE_INSTALL_LIBDIR=lib            \
        -DBUILD_MANPAGES=ON                   \
        -DBUILD_EXAMPLES=OFF                  \
        -DBUILD_TOOLS=ON                      \
        -DUSE_HIDAPI=OFF                      \
        -Wno-dev
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        cmake --build build --target check
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
