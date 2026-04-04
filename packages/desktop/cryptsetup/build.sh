#!/bin/bash
# cryptsetup 2.8.4 — Transparent disk encryption
# BLFS 13.0

configure() {
    ./configure --prefix=/usr       \
                --disable-ssh-token \
                --disable-asciidoc
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
