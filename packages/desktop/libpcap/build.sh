#!/bin/bash
# libpcap 1.10.6 — Packet capture library (full, with Bluetooth)
# Second pass of 2-pass build. Builds with bluez present at configure time,
# so libpcap auto-enables Bluetooth packet capture. Supersedes libpcap-pass1
# via migrate-pkm-supersedes.sh.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
