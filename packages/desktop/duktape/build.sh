#!/bin/bash
# duktape 2.7.0 — Embeddable JavaScript engine
# BLFS 13.0

configure() {
    sed -i 's/-Os/-O2/' Makefile.sharedlibrary
}

build() {
    make -j${IGOS_JOBS} -f Makefile.sharedlibrary INSTALL_PREFIX=/usr
}

do_install() {
    make -f Makefile.sharedlibrary INSTALL_PREFIX=/usr DESTDIR="$DESTDIR" install
}
