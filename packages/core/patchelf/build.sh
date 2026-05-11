#!/bin/bash
# patchelf 0.18.0 — RPATH/dynamic-section rewriter for ELF binaries
# Build tool used by dbus-python (and potentially other Python bindings)
# to fix RPATH on installed shared objects.

configure() {
    set -e
    ./configure --prefix=/usr \
                --docdir=/usr/share/doc/patchelf-${PKG_VERSION}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Upstream test suite uses bash + standard tools; runs quickly.
    make -j${IGOS_JOBS} check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
