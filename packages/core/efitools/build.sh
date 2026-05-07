#!/bin/bash
# efitools — UEFI variable + key management
# Build dep of: sbsigntool

configure() {
    set -e
    return 0
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" PREFIX=/usr install
}
