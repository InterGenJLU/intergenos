#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# perl-file-which — pure perl module, standard Makefile.PL install

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
    # Strip perllocal.pod: shared, mutable ExtUtils::MakeMaker install
    # bookkeeping, not package content (same as perl-archive-zip).
    find "$DESTDIR" -name perllocal.pod -delete
}
