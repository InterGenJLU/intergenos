#!/bin/bash
# rdfind 1.8.0 — Redundant data finder
# Required by linux-firmware for deduplication

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
