#!/bin/bash
# sessreg 1.1.3 — Manage utmpx/wtmpx entries for non-init sessions
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
