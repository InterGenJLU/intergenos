#!/bin/bash
# serd 0.32.8 — drobilla RDF syntax (Turtle/NTriples/NQuads/TriG) C library.
#
# Modular RDF I/O. Standalone — no external library deps beyond libc.
# Provides libserd-0.so and the `serdi` CLI tool used by sord and sratom
# down the chain. BLFS does not carry serd — drobilla.net is canonical.

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
    # serd ships a comprehensive offline RDF round-trip test suite (test/)
    # gated by the `tests` feature (default auto/enabled). All fixtures
    # are bundled in the tarball; no network access required.
    meson test -C build --print-errorlogs
}
