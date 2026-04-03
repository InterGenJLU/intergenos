#!/bin/bash
# unifdef 2.12 — Conditional compilation directive remover
# BLFS 13.0

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make prefix=/usr DESTDIR="$DESTDIR" install
}
