#!/bin/bash
# fontconfig 2.17.1 — Font configuration and customization library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --disable-docs
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # One test known to fail per BLFS; some tests download fonts via Internet
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
