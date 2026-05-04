#!/bin/bash
# zix 0.8.0 — drobilla portability/data-structure C library.
#
# Foundation of the drobilla RDF stack (sord/lilv depend on zix). Pure
# meson build, no external library deps beyond libc + threads + (POSIX).
# BLFS does not carry zix — drobilla.net is the source of truth.

configure() {
    meson setup build         \
          --prefix=/usr       \
          --buildtype=release \
          --strip
}

build() {
    ninja -C build
}

do_install() {
    DESTDIR="$DESTDIR" ninja -C build install
}

do_test() {
    # zix ships an offline unit-test suite (test/) gated by the `tests`
    # feature (default auto/enabled). All tests are pure-libc and run
    # without network access.
    meson test -C build --print-errorlogs
}
