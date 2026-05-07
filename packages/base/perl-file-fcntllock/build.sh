#!/bin/bash
# File::FcntlLock 0.22 — Perl file locking module
# Required by Exim

configure() {
    set -e
    perl Makefile.PL
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make test || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
