#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sgml-common 0.6.3 — SGML common files
# BLFS 13.0

configure() {
    set -e
    autoreconf -f -i &&

    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" docdir=/usr/share/doc install
}

post_install() {
    set -e
    install-catalog --add /etc/sgml/sgml-ent.cat \
        /usr/share/sgml/sgml-iso-entities-8879.1986/catalog &&

    install-catalog --add /etc/sgml/sgml-docbook.cat \
        /etc/sgml/sgml-ent.cat
}
