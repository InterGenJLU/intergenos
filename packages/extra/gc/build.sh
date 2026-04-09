#!/bin/bash
# gc 8.2.12 — Boehm-Demers-Weiser conservative garbage collector
# BLFS 13.0

configure() {
    ./configure --prefix=/usr      \
                --enable-cplusplus \
                --disable-static   \
                --docdir=/usr/share/doc/gc-8.2.12
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # BLFS: install man page
    install -v -m644 doc/gc.man "$DESTDIR/usr/share/man/man3/gc_malloc.3"
}
