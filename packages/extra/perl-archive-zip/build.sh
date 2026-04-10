#!/bin/bash
# perl-archive-zip — Pure Perl module, standard Makefile.PL install

configure() {
    perl Makefile.PL
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
