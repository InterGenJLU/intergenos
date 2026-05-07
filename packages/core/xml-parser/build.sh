#!/bin/bash
# XML::Parser 2.47
# LFS 13.0 Section 8.45
#
# Perl module — uses Makefile.PL instead of autotools.

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
    make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
