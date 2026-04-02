#!/bin/bash
# unifdef 2.12 — Remove
# BLFS 13.0

build() {
    make  -j${IGOS_JOBS}
}

do_install() {
    make  DESTDIR="$DESTDIR" install
}
