#!/bin/bash
# libunwind 1.8.3 — Call-chain determination library
# Required by: sysprof, gstreamer (via #include <libunwind.h> in gst/gstinfo.c)

configure() {
    # --disable-tests: skips src/tests/ which contains K&R-style function
    # pointer declarations that fail under gcc 15 strict checking
    # (Gtest-nomalloc.c:49 — 'func' declared as void *(*)() then called with
    # one arg). Earlier `make -C src` workaround skipped tests but ALSO
    # skipped top-level Makefile install rules, dropping include_HEADERS
    # (libunwind.h, libunwind-x86_64.h, etc.) — leaving consumers like
    # gstreamer with pkg-config files but no headers.
    ./configure --prefix=/usr \
                --disable-static \
                --disable-tests
}

build() {
    make
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
