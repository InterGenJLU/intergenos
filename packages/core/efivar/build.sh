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
    #
    # LIBDIR=/usr/lib overrides the default `$(PREFIX)/lib64` in
    # src/include/defaults.mk:3 so the .pc files (built at this stage
    # via the @@LIBDIR@@ template in src/include/rules.mk) embed the
    # correct lib path. LFS-13.0 is lib-only; lib64 is not in our
    # PKG_CONFIG_PATH and broke mokutil's configure at Build #9 resume
    # #3 — see commit message + docs/research/build_system/preflight_
    # undeclared_deps_v1.md for context.
    make ERRORS="" LIBDIR=/usr/lib -j${IGOS_JOBS}
}

do_install() {
    set -e
    # LIBDIR=/usr/lib must match build() for consistent .so + .pc
    # install destination (default would be /usr/lib64).
    make ERRORS="" LIBDIR=/usr/lib DESTDIR="$DESTDIR" install
}
