#!/bin/bash
# npth 1.7 — New portable threads library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/^RELEASE/s|^|#|' pr/src/misc/Makefile.in
    sed -i 's|$(LIBRARY) ||' config/rules.mk
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
