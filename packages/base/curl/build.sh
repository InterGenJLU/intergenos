#!/bin/bash
# cURL 8.19.0 — URL transfer library and tool
# BLFS 13.0

configure() {
    ./configure --prefix=/usr    \
                --disable-static \
                --with-openssl   \
                --with-ca-path=/etc/ssl/certs
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Install documentation
    rm -rf docs/examples/.deps

    find docs \( -name Makefile\* -o  \
                 -name \*.1       -o  \
                 -name \*.3       -o  \
                 -name CMakeLists.txt \) -delete

    cp -v -R docs -T "${DESTDIR}/usr/share/doc/curl-8.19.0"
}
