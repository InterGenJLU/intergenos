#!/bin/bash
# raptor2 2.0.16 — RDF parser and serializer library
# BLFS 13.0

configure() {
    # BLFS: fix incompatibility with libxml2-2.11.x
    sed -i 's/20627/20627 \&\& LIBXML_VERSION < 21100/' src/raptor_libxml.c

    ./configure --prefix=/usr --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # BLFS: several XML tests may fail
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
