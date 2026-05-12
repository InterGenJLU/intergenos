#!/bin/bash
# slang-pass1 2.3.3 — S-Lang library (bootstrap, no PNG rendering)
# First pass of 2-pass build. --without-png breaks the cycle to libpng
# (tier:desktop) so newt's installer-dialog use of slang doesn't pull
# the entire desktop stack into tier:core. Full slang with PNG image
# rendering is built later in tier:desktop and supersedes via
# migrate-pkm-supersedes.sh.
#
# NOTE: slang's autoconf treats --with-png=<val> as a literal path prefix
# (CFLAGS gain -I<val>/include and LDFLAGS -L<val>/lib). So --with-png=NO
# expands to -INO/include + -LNO/lib, which is the wrong fix. The correct
# disable form is --without-png (no value).

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-readline=gnu \
                --without-png
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
