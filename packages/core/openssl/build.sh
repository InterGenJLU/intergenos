#!/bin/bash
# OpenSSL 3.6.1
# LFS 13.0 Section 8.48

configure() {
    ./config --prefix=/usr         \
        --openssldir=/etc/ssl      \
        --libdir=lib               \
        shared                     \
        zlib-dynamic
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # HARNESS_JOBS speeds up the test suite significantly
    HARNESS_JOBS=$(nproc) make test || true
}

do_install() {
    sed -i '/INSTALL_LIBS/s/libcrypto.a libssl.a//' Makefile
    make DESTDIR="$DESTDIR" MANSUFFIX=ssl install

    # Add version to documentation directory
    mv -v "${DESTDIR}/usr/share/doc/openssl" "${DESTDIR}/usr/share/doc/openssl-3.6.1"
    cp -vfr doc/* "${DESTDIR}/usr/share/doc/openssl-3.6.1"
}
