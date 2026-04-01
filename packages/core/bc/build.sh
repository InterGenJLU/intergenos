#!/bin/bash
# Bc 7.0.3
# LFS 13.0 Section 8.15

configure() {
    # LFS specifies CC='gcc -std=c99' but bc 7.0.3 uses bare true/false
    # in macros (concatenated with UL). These are keywords in C23 but not
    # in C99/C11. Bc's configure adds -D_POSIX_C_SOURCE=200809L which
    # disables C23 keywords even under GCC 15. Force C23 explicitly.
    CC='gcc -std=c23' ./configure --prefix=/usr -G -O3 -r
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
