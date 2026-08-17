#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # Strip perllocal.pod: shared, mutable ExtUtils::MakeMaker install bookkeeping,
    # not package content. Decided 2026-07-14 (standard cross-distro practice).
    find "$DESTDIR" -name perllocal.pod -delete
}
