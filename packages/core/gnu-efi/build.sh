#!/bin/bash
# gnu-efi 3.0.18 — UEFI development headers and libraries
# Required by efitools, sbsigntool, and shim for EFI binary builds
# Upstream: https://sourceforge.net/projects/gnu-efi/

configure() {
    # gnu-efi uses a plain Makefile, no autotools. Make.defaults sets
    # reasonable defaults for x86_64 detection. INSTALLROOT defaults to
    # $(DESTDIR), which the build framework already exports — so we let
    # the env value drive staging instead of overriding PREFIX with a
    # DESTDIR-prefixed path (which doubles the staging path).
    :
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # gnu-efi has no test suite
    :
}

do_install() {
    # PREFIX = in-system prefix (/usr); LIBDIR = where libs land within
    # PREFIX. DESTDIR (exported by build framework as $PKG_DEST) is
    # picked up by INSTALLROOT in Make.defaults — do not duplicate it
    # in PREFIX.
    make install PREFIX=/usr LIBDIR=/usr/lib
}
