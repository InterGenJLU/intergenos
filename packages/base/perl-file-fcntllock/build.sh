#!/bin/bash
# File::FcntlLock 0.22 — Perl file locking module
# Required by Exim

configure() {
    perl Makefile.PL
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
