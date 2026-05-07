#!/bin/bash
# perl-parse-yapp 1.21 — Perl parser generator (YACC for Perl)
# Standard Perl module build

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
