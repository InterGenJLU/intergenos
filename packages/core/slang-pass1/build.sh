#!/bin/bash
# slang-pass1 2.3.3 — S-Lang library (bootstrap, no PNG rendering)
# First pass of 2-pass build. --with-png=NO breaks the cycle to libpng
# (tier:desktop) so newt's installer-dialog use of slang doesn't pull
# the entire desktop stack into tier:core. Full slang with PNG image
# rendering is built later in tier:desktop and supersedes via
# migrate-pkm-supersedes.sh.

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-readline=gnu \
                --with-png=NO
}

build() {
    set -e
    # BLFS: slang does not support parallel build; RPATH= prevents hardcoded paths
    make -j1 RPATH=
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" RPATH= install
}
