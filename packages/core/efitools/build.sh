#!/bin/bash
# efitools — UEFI variable + key management
# Build dep of: sbsigntool

configure() {
    return 0
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" PREFIX=/usr install
}
