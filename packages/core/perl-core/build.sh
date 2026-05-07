#!/bin/bash
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
    unset BUILD_ZLIB BUILD_BZIP2
}
