#!/bin/bash
# lmdb 0.9.35 — Lightning Memory-Mapped Database
# BLFS 13.0

configure() {
    cd libraries/liblmdb
}

build() {
    cd libraries/liblmdb
    make
}

do_install() {
    cd libraries/liblmdb

    # Remove static library from install target per BLFS
    sed -i 's| liblmdb.a||' Makefile

    make prefix=/usr DESTDIR="$DESTDIR" install
}
