#!/bin/bash
# LZO 2.10 — real-time data compression library
# Required by btrfs-progs (LZO compression mode for btrfs filesystems)

configure() {
    set -e
    ./configure --prefix=/usr --enable-shared --disable-static --docdir=/usr/share/doc/lzo-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
