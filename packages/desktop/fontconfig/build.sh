#!/bin/bash
# fontconfig 2.17.1 — Font configuration and customization library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-docs
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # One test known to fail per BLFS; some tests download fonts via Internet
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
