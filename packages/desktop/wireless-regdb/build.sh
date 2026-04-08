#!/bin/bash
# wireless-regdb — Wireless regulatory database
# Data-only package — installs regulatory.db for WiFi compliance

configure() {
    : # No configure step
}

build() {
    : # No build step — pre-compiled database
}

do_install() {
    install -vDm644 regulatory.db "${DESTDIR}/usr/lib/firmware/regulatory.db"
    install -vDm644 regulatory.db.p7s "${DESTDIR}/usr/lib/firmware/regulatory.db.p7s"
}
