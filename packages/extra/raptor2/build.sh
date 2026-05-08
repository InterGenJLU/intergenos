#!/bin/bash
# raptor2 2.0.16 — RDF parser and serializer library
# BLFS 13.0

configure() {
    set -e
    # BLFS: fix incompatibility with libxml2-2.11.x
    sed -i 's/20627/20627 \&\& LIBXML_VERSION < 21100/' src/raptor_libxml.c

    ./configure --prefix=/usr --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # BLFS: several XML tests may fail
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
