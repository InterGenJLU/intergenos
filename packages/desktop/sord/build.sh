#!/bin/bash
# sord 0.16.22 — in-memory RDF store, drobilla.
#
# Provides libsord-0.so plus the `sordi` and `sord_validate` CLI tools.
# Builds against zix (data structures) and serd (RDF I/O). pcre2 is an
# optional dep that enables the `sord_validate` schema-checker tool —
# pcre2 is in our `core` tier, so it is always available and the tool
# is always built. BLFS does not carry sord.

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

check() {
    # sord ships an offline unit-test suite (test/) gated by the `tests`
    # feature (default auto/enabled). Tests exercise libsord internals
    # against bundled fixtures — no network access required.
    meson test -C build --print-errorlogs
}
