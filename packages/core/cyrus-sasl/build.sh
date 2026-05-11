#!/bin/bash
# cyrus-sasl 2.1.28 — Cyrus Simple Authentication and Security Layer
# BLFS 13.0
# Note: does NOT support parallel build (make -j1)

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # Additional fixes for gcc-14+
    sed '/saslint/a #include <time.h>'       -i lib/saslutil.c
    sed '/plugin_common/a #include <time.h>' -i plugins/cram.c

    autoreconf -fiv

    ./configure --prefix=/usr                       \
                --sysconfdir=/etc                   \
                --enable-auth-sasldb                \
                --with-dblib=lmdb                   \
                --with-dbpath=/var/lib/sasl/sasldb2 \
                --with-sphinx-build=no              \
                --with-saslauthd=/var/run/saslauthd
}

build() {
    set -e
    # Cyrus SASL does NOT support parallel build
    make -j1
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -v -dm755 "${DESTDIR}/usr/share/doc/cyrus-sasl-${PKG_VERSION}/html"
    install -v -m644  saslauthd/LDAP_SASLAUTHD \
                      "${DESTDIR}/usr/share/doc/cyrus-sasl-${PKG_VERSION}"
    install -v -m644  doc/legacy/*.html \
                      "${DESTDIR}/usr/share/doc/cyrus-sasl-${PKG_VERSION}/html"
}

post_install() {
    set -e
    # Create SASL database directory
    install -v -dm700 /var/lib/sasl
}
