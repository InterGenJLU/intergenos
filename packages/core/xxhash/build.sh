#!/bin/bash
# xxhash 0.8.3 — extremely fast non-cryptographic hash algorithm library
# Authored 2026-05-10 to provide the system-library dep that rsync's
# --disable-xxhash flag had been bypassing. Promotes rsync's checksum
# performance to the xxhash3 path (Build Development Rulebook Rule 3:
# missing deps get packaged, not bypassed via feature-disable flags).

configure() {
    set -e
    # No configure step — Makefile reads PREFIX/DESTDIR/LIBDIR from env.
    :
}

build() {
    set -e
    make -j${IGOS_JOBS} PREFIX=/usr
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" PREFIX=/usr install
}
