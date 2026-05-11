#!/bin/bash
# poppler 26.02.0 — PDF rendering library
# BLFS 13.0

configure() {
    set -e
    # ENABLE_BOOST=ON: Boost-hardened splash backend (defense-in-depth for
    # PDF rendering). boost is in tree at packages/desktop/boost.
    # ENABLE_GPGME=ON: GPG signature verification on PDFs. Security-relevant
    # feature for a security-aligned distro. gpgme is in tree at
    # packages/desktop/gpgme.
    # ENABLE_QT5/QT6=OFF: GNOME/GTK desktop, no Qt frontend needed.
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -DENABLE_UNSTABLE_API_ABI_HEADERS=ON \
          -DENABLE_QT5=OFF            \
          -DENABLE_QT6=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
