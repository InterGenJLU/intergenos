#!/bin/bash
# cups-filters 2.0.1 — CUPS print filters
# BLFS 13.0

configure() {
    # Fix for GCC 15 — function pointer type mismatch
    sed -i '/proc_func)()/s/()/(FILE*, FILE*, void*)/' filter/foomatic-rip/process.h

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
