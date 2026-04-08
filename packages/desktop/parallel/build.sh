#!/bin/bash
# GNU parallel 20260322 — Parallel command execution
# Required by linux-firmware for compressed install

configure() {
    ./configure --prefix=/usr
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
