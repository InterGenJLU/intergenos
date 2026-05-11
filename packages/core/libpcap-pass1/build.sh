#!/bin/bash
# libpcap-pass1 1.10.6 — Packet capture library (bootstrap, no Bluetooth)
# First pass of 2-pass build. Satisfies iptables's libpcap dep in tier:core
# without pulling in bluez (tier:desktop). Full libpcap with Bluetooth
# capture is built later in tier:desktop and supersedes this pass1 via
# migrate-pkm-supersedes.sh.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --disable-bluetooth
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
