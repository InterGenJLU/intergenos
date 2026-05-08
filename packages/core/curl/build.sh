#!/bin/bash
# cURL 8.19.0 — URL transfer library and tool
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr    \
                --disable-static \
                --with-openssl   \
                --with-libssh2   \
                --with-ca-path=/etc/ssl/certs
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Install documentation
    rm -rf docs/examples/.deps

    find docs \( -name Makefile\* -o  \
                 -name \*.1       -o  \
                 -name \*.3       -o  \
                 -name CMakeLists.txt \) -delete

    install -v -d -m755 "${DESTDIR}/usr/share/doc/curl-${PKG_VERSION}"
    cp -v -R docs/* "${DESTDIR}/usr/share/doc/curl-${PKG_VERSION}/"
}
