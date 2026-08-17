#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # Strip perllocal.pod: shared, mutable ExtUtils::MakeMaker install bookkeeping,
    # not package content. Decided 2026-07-14 (standard cross-distro practice).
    find "$DESTDIR" -name perllocal.pod -delete
}
