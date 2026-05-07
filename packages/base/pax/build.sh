#!/bin/bash
# Pax 20240817 — POSIX standard archive utility
# BLFS 13.0
# Note: extracts to "pax/" directory, not paxmirabilis-*

build() {
    set -e
    bash Build.sh
}

do_install() {
    set -e
    install -D -m755 pax "${DESTDIR}/usr/bin/pax"
    install -D -m644 pax.1 "${DESTDIR}/usr/share/man/man1/pax.1"
}
