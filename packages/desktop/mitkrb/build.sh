#!/bin/bash
# mitkrb 1.22.2 — MIT Kerberos V5 authentication
# BLFS 13.0

configure() {
    set -e
    # Apply upstream fix

    cd src &&

    sed -i -e '/eq 0/{N;s/12 //}' plugins/kdb/db2/libdb2/test/run.test &&

    ./configure --prefix=/usr            \
                --sysconfdir=/etc        \
                --localstatedir=/var/lib \
                --runstatedir=/run       \
                --with-system-et         \
                --with-system-ss         \
                --with-system-verto=no   \
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
    cd src &&
    make -j1 -k check || true
}

do_install() {
    set -e
    cd src &&
    make DESTDIR="$DESTDIR" install

    install -v -d -m755 "${DESTDIR}/usr/share/doc/krb5-${version}"
    cp -vfr ../doc/* "${DESTDIR}/usr/share/doc/krb5-${version}"
}
