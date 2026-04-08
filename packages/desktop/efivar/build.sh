#!/bin/bash
# efivar 39 — EFI variable library and tools
# BLFS 13.0

configure() {
    : # No configure step — uses GNU Make directly
}

build() {
    # efivar hardcodes -Werror via ERRORS variable in defaults.mk
    # GCC 15 triggers new warnings that break the build. Override.
    # Set empty SUBDIRS for docs to skip mandoc dependency
    sed -i 's/SUBDIRS = src docs/SUBDIRS = src/' Makefile
    make ERRORS="" -j${IGOS_JOBS}
}

do_install() {
    make ERRORS="" DESTDIR="$DESTDIR" install
}
