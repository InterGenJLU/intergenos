#!/bin/bash
# libevent 2.1.12 — Event notification library
# BLFS 13.0

configure() {
    set -e
    # Fix Python script shebang
    sed -i 's/python/&3/' event_rpcgen.py

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
