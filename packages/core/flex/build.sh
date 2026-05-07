#!/bin/bash
# Flex 2.6.4
# LFS 13.0 Section 8.16

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/flex-2.6.4
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Lex compatibility symlinks
    ln -sv flex "${DESTDIR}/usr/bin/lex"
    ln -sv flex.1 "${DESTDIR}/usr/share/man/man1/lex.1"
}
