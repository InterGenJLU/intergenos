#!/bin/bash
# cryptsetup 2.8.4 — Transparent disk encryption
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr       \
                --disable-ssh-token \
                --disable-asciidoc
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
