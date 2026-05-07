#!/bin/bash
# poppler-data 0.4.12 — Encoding data for poppler PDF rendering
# BLFS 13.0 — data-only package, no compilation

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    make prefix=/usr DESTDIR="$DESTDIR" install
}
