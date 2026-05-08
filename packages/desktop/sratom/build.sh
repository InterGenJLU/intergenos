#!/bin/bash
# sratom 0.6.22 — LV2 atom <-> RDF serialisation, drobilla.
#
# Provides libsratom-0.so. Bridges the LV2 atom type system (binary
# plugin state/messages) to RDF Turtle for save/restore. Direct deps:
# serd (RDF I/O), sord (in-memory RDF store), lv2 (atom headers).
#
# NOTE: sratom 0.6.22 meson.build also requires sord_dep at line 126
# (not just serd + lv2 — task brief was incomplete; sord is in our
# build_deps). BLFS does not carry sratom.

configure() {
    set -e
    meson setup build         \
          --prefix=/usr       \
          --buildtype=release \
          --strip
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}

check() {
    set -e
    # Refresh ld.so.cache so test binaries can find libzix/libserd/
    # libsord (installed to /usr/lib64 by earlier meson builds whose
    # cache wasn't refreshed). Belt-and-braces with the orchestrator's
    # --start-at ldconfig refresh.
    ldconfig 2>/dev/null || true
    # sratom ships an offline atom-roundtrip test suite (test/) gated by
    # the `tests` feature (default auto/enabled). Uses bundled fixtures;
    # no network access required.
    meson test -C build --print-errorlogs
}
