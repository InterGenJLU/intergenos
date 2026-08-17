#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Perl 5.42.0
# LFS 13.0 Section 8.43

configure() {
    set -e
    export BUILD_ZLIB=False
    export BUILD_BZIP2=0

    sh Configure -des                                        \
        -D prefix=/usr                                       \
        -D vendorprefix=/usr                                 \
        -D privlib=/usr/lib/perl5/5.42/core_perl             \
        -D archlib=/usr/lib/perl5/5.42/core_perl             \
        -D sitelib=/usr/lib/perl5/5.42/site_perl             \
        -D sitearch=/usr/lib/perl5/5.42/site_perl            \
        -D vendorlib=/usr/lib/perl5/5.42/vendor_perl         \
        -D vendorarch=/usr/lib/perl5/5.42/vendor_perl        \
        -D man1dir=/usr/share/man/man1                       \
        -D man3dir=/usr/share/man/man3                       \
        -D pager="/usr/bin/less -isR"                        \
        -D useshrplib                                        \
        -D usethreads
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    TEST_JOBS=$(nproc) make test_harness
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    # Strip perllocal.pod: shared, mutable installperl/MakeMaker install bookkeeping,
    # not package content. Decided 2026-07-14 (standard cross-distro practice).
    find "$DESTDIR" -name perllocal.pod -delete
    unset BUILD_ZLIB BUILD_BZIP2
}
