#!/bin/bash
# rdfind 1.8.0 — Redundant data finder
# Required by linux-firmware for deduplication

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
