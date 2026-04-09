#!/bin/bash
# luajit 20260213 — Just-In-Time compiler for Lua
# BLFS 13.0

configure() {
    :
}

build() {
    make PREFIX=/usr amalg -j${IGOS_JOBS}
}

do_install() {
    make PREFIX=/usr DESTDIR="$DESTDIR" install

    # Remove static library per BLFS
    rm -vf "$DESTDIR/usr/lib/libluajit-5.1.a"
}
