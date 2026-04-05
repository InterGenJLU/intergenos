#!/bin/bash
# samba 4.23.5 — SMB/CIFS file and print server
# BLFS 13.0

configure() {
    ./configure                                \
        --prefix=/usr                          \
        --sysconfdir=/etc                      \
        --localstatedir=/var                   \
        --with-piddir=/run/samba               \
        --with-pammodulesdir=/usr/lib/security \
        --enable-fhs                           \
        --without-ad-dc                        \
        --without-ldap                         \
        --with-system-mitkrb5                  \
        --enable-selftest                      \
        --disable-rpath-install
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make quicktest || true
}

do_install() {
    # Fix hard coded Python paths
    sed '1s@^.*$@#!/usr/bin/python3@' \
        -i ./bin/default/source4/scripting/bin/*.inst

    make DESTDIR="$DESTDIR" install

    install -v -m644 examples/smb.conf.default "${DESTDIR}/etc/samba/"

    sed -e "s;log file =.*;log file = /var/log/samba/%m.log;"   \
        -e "s;path = /usr/spool/samba;path = /var/spool/samba;" \
        -i "${DESTDIR}/etc/samba/smb.conf.default"

    # LDAP schema install omitted — built --without-ldap
}
