#!/bin/bash
# efivar 39 — EFI variable library and tools
# BLFS 13.0

configure() {
    # Fix const qualifier warnings that GCC 15 promotes to errors via -Werror
    sed -i 's/guid_aliases\[i\]\.name = /*(char **)(\&guid_aliases[i].name) = /' src/guid.c
    sed -i 's/ret->letter = /*(char **)(\&ret->letter) = /' src/linux.c
    sed -i 's/ret->letter = /*(char **)(\&ret->letter) = /' src/linux-acpi-root.c
}

build() {
    # Override -Werror (ERRORS variable in defaults.mk)
    make ERRORS="" -j${IGOS_JOBS}
}

do_install() {
    make ERRORS="" DESTDIR="$DESTDIR" install
}
