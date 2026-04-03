#!/bin/bash
# sgml-common 0.6.3 — SGML common files
# BLFS 13.0

configure() {
    autoreconf -f -i &&

    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" docdir=/usr/share/doc install
}

post_install() {
    install-catalog --add /etc/sgml/sgml-ent.cat \
        /usr/share/sgml/sgml-iso-entities-8879.1986/catalog &&

    install-catalog --add /etc/sgml/sgml-docbook.cat \
        /etc/sgml/sgml-ent.cat
}
