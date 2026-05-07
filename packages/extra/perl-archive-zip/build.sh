#!/bin/bash
# perl-archive-zip — Pure Perl module, standard Makefile.PL install

configure() {
    set -e
    perl Makefile.PL
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
