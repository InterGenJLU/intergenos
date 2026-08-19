#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mitkrb 1.22.2 — MIT Kerberos V5 authentication
# BLFS 13.0

configure() {
    set -e
    # Apply upstream fix

    cd src &&

    sed -i -e '/eq 0/{N;s/12 //}' plugins/kdb/db2/libdb2/test/run.test &&

    # --with-cracklib + --with-ldap enable two declared deps that were
    # silently disabled in the original configure invocation. Build #7
    # halt 2026-05-10 surfaced the gap. Decided fix.
    ./configure --prefix=/usr            \
                --sysconfdir=/etc        \
                --localstatedir=/var/lib \
                --runstatedir=/run       \
                --with-system-et         \
                --with-system-ss         \
                --with-system-verto=no   \
                --with-cracklib          \
                --with-ldap              \
                --enable-dns-for-realm   \
                --disable-rpath
}

build() {
    set -e
    cd src &&
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # The blanket suite mask was retired here 2026-08-19. This package is
    # deliberately NOT given a known-failures waiver: the trace audit of the
    # first release recorded a segmentation fault in this suite that nobody
    # has characterized, and declaring a suite "expected to fail" while one
    # of its failures is an uncharacterized crash would be exactly the
    # unverified claim the mask already was.
    #
    # So the policy is strict: pkg_run_tests reports the real status, the
    # Chapter-8 driver logs it and records it in the build trace, and the
    # build continues because a check failure is informational on that lane
    # by design. Nothing is hidden and nothing is asserted. The next build's
    # log is where the crash gets characterized; if it turns out to be
    # environmental, the finding is then declared with its reason rather
    # than assumed now.
    cd src
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make -j1 -k check
}

do_install() {
    set -e
    cd src &&
    make DESTDIR="$DESTDIR" install

    install -v -d -m755 "${DESTDIR}/usr/share/doc/krb5-${version}"
    cp -vfr ../doc/* "${DESTDIR}/usr/share/doc/krb5-${version}"
}
