#!/bin/bash
# zip 3.0 — Info-ZIP archiver for creating ZIP archives
# BLFS 13.0

configure() {
    # No configure step — uses unix/Makefile directly
    :
}

build() {
    # BLFS: CC="gcc -std=gnu89" required — Zip uses pre-C99 constructs
    # Use 'generic' target (not generic_gcc) per BLFS 13.0
    make -f unix/Makefile generic \
        CC="gcc -std=gnu89"
}

do_install() {
    make prefix="$DESTDIR/usr"                   \
         MANDIR="$DESTDIR/usr/share/man/man1"    \
         -f unix/Makefile install
}
