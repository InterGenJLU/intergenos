#!/bin/bash
# perl-parse-yapp 1.21 — Perl parser generator (YACC for Perl)
# Standard Perl module build

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
