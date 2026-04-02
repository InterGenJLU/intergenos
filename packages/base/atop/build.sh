#!/bin/bash
# atop 2.12.1 — Advanced system and process monitor
# From upstream (not in BLFS)

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
