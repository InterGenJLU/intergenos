#!/bin/bash
# libssh2 1.11.1 — SSH2 client library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr          \
                --disable-docker-tests \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
