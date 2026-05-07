#!/bin/bash
# GNU parallel 20260322 — Parallel command execution
# Required by linux-firmware for compressed install

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
