#!/bin/bash
# efivar 39 — EFI variable library and tools
# BLFS 13.0

configure() {
    set -e
    : # No configure step — uses GNU Make directly
}

build() {
    set -e
    # efivar hardcodes -Werror via ERRORS variable in defaults.mk
    # GCC 15 triggers new const-qualifier warnings in bsearch/strrchr/strchr
    # return value assignments. These are harmless — suppress -Werror.
    make ERRORS="" -j${IGOS_JOBS}
}

do_install() {
    set -e
    make ERRORS="" DESTDIR="$DESTDIR" install
}
