#!/bin/bash
# XML::Parser 2.47
# LFS 13.0 Section 8.45
#
# Perl module — uses Makefile.PL instead of autotools.

configure() {
    perl Makefile.PL
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test
}

install() {
    make DESTDIR="$DESTDIR" install
}
