#!/bin/bash
# cracklib 2.10.2 — Password checking library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --with-default-dict=/usr/lib/cracklib/pw_dict
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
