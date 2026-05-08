#!/bin/bash
# OpenSSL 3.6.1
# LFS 13.0 Section 8.48

configure() {
    set -e
    ./config --prefix=/usr         \
        --openssldir=/etc/ssl      \
        --libdir=lib               \
        shared                     \
        zlib-dynamic
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # HARNESS_JOBS speeds up the test suite significantly
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        env HARNESS_JOBS=$(nproc) make test
}

do_install() {
    set -e
    sed -i '/INSTALL_LIBS/s/libcrypto.a libssl.a//' Makefile
    make DESTDIR="$DESTDIR" MANSUFFIX=ssl install

    # Add version to documentation directory
    mv -v "${DESTDIR}/usr/share/doc/openssl" "${DESTDIR}/usr/share/doc/openssl-3.6.1"
    cp -vfr doc/* "${DESTDIR}/usr/share/doc/openssl-3.6.1"
}
