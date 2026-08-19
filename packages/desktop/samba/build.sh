#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# samba 4.23.5 — SMB/CIFS file and print server
# BLFS 13.0

configure() {
    set -e
    ./configure                                \
        --prefix=/usr                          \
        --sysconfdir=/etc                      \
        --localstatedir=/var                   \
        --with-piddir=/run/samba               \
        --with-pammodulesdir=/usr/lib/security \
        --enable-fhs                           \
        --without-ad-dc                        \
        --with-system-mitkrb5                  \
        --disable-rpath-install
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # The blanket suite mask was retired here 2026-08-19: it accepted every
    # failure quicktest can produce, new ones included. The suite still runs;
    # the tests: block in package.yml states the policy and pkg_run_tests
    # reports the outcome, so the log carries a named waiver instead of
    # silence.
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make quicktest
}

do_install() {
    set -e
    # Fix hard coded Python paths
    sed '1s@^.*$@#!/usr/bin/python3@' \
        -i ./bin/default/source4/scripting/bin/*.inst

    make DESTDIR="$DESTDIR" install

    # Upstream's install target creates var/run/samba + var/lock/samba.
    # Never ship var/run/ or var/lock/ members: both are symlinks into /run
    # on installed systems (base-files r9) and archive dir members would
    # materialize them as real dirs at install time (split-brain runtime
    # dirs). The tmpfiles.d entries recreate the runtime dirs every boot.
    rm -rf "$DESTDIR/var/run" "$DESTDIR/var/lock"
    install -d -m 755 "$DESTDIR/usr/lib/tmpfiles.d"
    printf '%s\n' "d /run/samba 0755 root root -" \
                   "d /run/lock/samba 0755 root root -" \
        > "$DESTDIR/usr/lib/tmpfiles.d/samba.conf"

    install -v -m644 examples/smb.conf.default "${DESTDIR}/etc/samba/"

    sed -e "s;log file =.*;log file = /var/log/samba/%m.log;"   \
        -e "s;path = /usr/spool/samba;path = /var/spool/samba;" \
        -i "${DESTDIR}/etc/samba/smb.conf.default"

    # Install LDAP schema files
    mkdir -pv "${DESTDIR}/etc/openldap/schema"
    install -v -m644 examples/LDAP/README \
                     "${DESTDIR}/etc/openldap/schema/README.samba"
    install -v -m644 examples/LDAP/samba* \
                     "${DESTDIR}/etc/openldap/schema"
    install -v -m755 examples/LDAP/{get*,ol*} \
                     "${DESTDIR}/etc/openldap/schema"
}
