#!/bin/bash
# efivar 39 — EFI variable library and tools
# BLFS 13.0

configure() {
    : # No configure step — uses GNU Make directly
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
