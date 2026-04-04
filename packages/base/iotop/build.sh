#!/bin/bash
# iotop 1.31 — I/O monitor (C rewrite by Tomas-M)
# From upstream (not in BLFS)
# Note: this is the C rewrite, not the Python original

configure() {
    # iotop-c uses a simple Makefile
    true
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    install -D -m755 iotop "${DESTDIR}/usr/sbin/iotop"
    install -D -m644 iotop.8 "${DESTDIR}/usr/share/man/man8/iotop.8"
}
