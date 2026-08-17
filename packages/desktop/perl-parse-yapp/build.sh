#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    # Strip perllocal.pod: shared, mutable ExtUtils::MakeMaker install bookkeeping,
    # not package content. Decided 2026-07-14 (standard cross-distro practice).
    find "$DESTDIR" -name perllocal.pod -delete
}
