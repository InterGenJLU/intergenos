#!/bin/bash
# efivar 39 — EFI variable library and tools
# BLFS 13.0

configure() {
    : # No configure step — uses GNU Make directly
}

build() {
    # efivar hardcodes -Werror via ERRORS variable in defaults.mk
    # GCC 15 triggers new const-qualifier warnings in bsearch/strrchr/strchr
    # return value assignments. These are harmless — suppress -Werror.
    make ERRORS="" -j${IGOS_JOBS}
}

do_install() {
    make ERRORS="" DESTDIR="$DESTDIR" install
}
