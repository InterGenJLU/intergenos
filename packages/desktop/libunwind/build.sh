#!/bin/bash
# libunwind 1.8.3 — Call-chain determination library
# Required by: sysprof

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    # Build only the library subdirs; skip src/tests/ which contains
    # K&R-style function pointer declarations that fail to compile under
    # gcc 15 strict checking (Gtest-nomalloc.c:49 — 'func' declared as
    # void *(*)() then called with one arg, which gcc 15 rejects). Tests
    # aren't needed at runtime and aren't installed.
    make -C src
}

do_install() {
    make -C src DESTDIR="$DESTDIR" install
}
